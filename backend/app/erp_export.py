"""Opt-in, sheet-scoped ERP reads. No application write connection is used here.

Full exports materialize candidate metadata before choosing a page, with linear
relation indexing and one audit pass. This initial opt-in implementation does
not claim bounded metadata memory; only file hydration is limited to the page.
Exact-source reads restrict request and relation queries to that logical root.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from . import db
from .file_storage import resolve_attachment_path
from .external_expenses import _workflow_original_url

router = APIRouter(prefix='/api/integrations/erp', tags=['ERP export'])
SOURCE_SYSTEM = 'cashier-payment-archive'
UNCERTAIN_PAYMENTS = {'legacy_migration', 'snapshot_legacy', 'excel_summary', 'rollover'}
RECORDED_PAYMENTS = {'manual','excel_detail','dingtalk_workflow'}


def export_scope(authorization: str = Header(default='')):
    token = os.environ.get('PAYMENT_ERP_EXPORT_TOKEN', '')
    if not token:
        raise HTTPException(503, 'ERP export is disabled')
    if not hmac.compare_digest(authorization, 'Bearer ' + token):
        raise HTTPException(401, 'Invalid ERP export token')
    try:
        sheets = json.loads(os.environ.get('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', '[]'))
        if not isinstance(sheets, list) or not sheets or any(not isinstance(s, str) or not s.strip() or s == '*' for s in sheets):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, 'ERP export sheet scope is not configured')
    return sorted(set(sheets))


@contextmanager
def read_database():
    try:
        conn = sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
    except sqlite3.Error:
        raise HTTPException(503, 'ERP export database is unavailable')
    try:
        yield conn
    finally:
        conn.close()


def object_json(raw):
    try:
        parsed = json.loads(raw or '{}',parse_float=Decimal)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def money(value):
    if value is None or value == '':
        return None
    try:
        amount = Decimal(str(value))
        return format(amount.normalize(), 'f') if amount.is_finite() else None
    except InvalidOperation:
        return None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Source database naive dates follow the application's local China time.
        if parsed.tzinfo is None:
            from datetime import timedelta
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError):
        raise HTTPException(400, 'Invalid timestamp')


def operation_source(row):
    raw = row['_export_raw'] if '_export_raw' in row else object_json(row.get('raw_extra_json'))
    external = raw.get('external_source') or {}
    if not isinstance(external, dict):
        return None
    if external.get('system') != 'dingtalk_expense_database' or not (external.get('record_id') or external.get('source_id')):
        return None
    if external.get('source_type') != 'operation' and not (not external.get('source_type') and external.get('table') == 'approval_expense_operation'):
        return None
    if external.get('lookup_status') in {'unmatched', 'conflict'}:
        return None
    return external


def application_type(external, raw):
    value = external.get('application_type_raw') or external.get('申请类型') or external.get('Tipo de trámite') or raw.get('申请类型') or raw.get('Tipo de trámite')
    for component in external.get('formComponentValues', []) or []:
        if isinstance(component, dict) and component.get('name') in {'申请类型','Tipo de trámite'}:
            value = component.get('value')
    normalized = str(value or '').strip().lower()
    return {'付款':'payment','请款':'payment','payment':'payment','pago':'payment',
            '报销':'reimbursement','reimbursement':'reimbursement','reembolso':'reimbursement'}.get(normalized, 'unclassified'), value


def attachment_item(row, conn, kind='attachment', include_version=True):
    try:
        path, _ = resolve_attachment_path(row, conn)
    except (ValueError, sqlite3.Error):
        return None
    if path is None:
        return None
    source_id = f"{kind}:{row['id']}"
    if row.get('source_attachment_id'):
        source_id = kind + ':' + digest([row.get('source_system'),row.get('source_instance_id'),row['source_attachment_id']])
    value = {'source_id':source_id, 'filename':row.get('original_filename') or path.name,
             'url':f"/api/integrations/erp/{'payment-vouchers' if kind == 'payment-voucher' else 'attachments'}/{row['id']}"}
    if kind == 'payment-voucher':
        value['payment_source_id'] = row['payment_source_id']
    if not include_version:
        value['_row'] = row
        value['_kind'] = kind
        return value
    sha256 = None
    if row.get('file_object_id'):
        file = conn.execute('SELECT sha256 FROM file_objects WHERE id=?',(row['file_object_id'],)).fetchone()
        if file:
            sha256 = file['sha256']
    if not sha256:
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024 * 1024), b''):
                hasher.update(chunk)
        sha256 = hasher.hexdigest()
    if kind == 'payment-voucher':
        value['source_id'] = kind + ':' + digest([value['payment_source_id'],sha256])
    elif not row.get('source_attachment_id'):
        value['source_id'] = kind + ':' + digest([row.get('_logical_source_id'),sha256])
    source_id = value['source_id']
    value['version'] = digest({'source_id':source_id,'filename':value['filename'],'sha256':sha256,'payment_source_id':value.get('payment_source_id')})
    return value


def selected_rows(conn,table,column,ids):
    """Bound relation reads; table/column names are internal constants only."""
    rows=[]
    ids=list(ids)
    for start in range(0,len(ids),400):
        chunk=ids[start:start+400]
        marks=','.join('?' for _ in chunk)
        rows.extend(dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE {column} IN ({marks})',chunk))
    return rows


def indexed(rows,key):
    result=defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def safe_original_url(value):
    trusted=_workflow_original_url([{'pcUrl':value}])
    if not trusted:
        return None
    process_ids=parse_qs(urlsplit(trusted).query).get('procInstId',[])
    if len(process_ids)!=1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',process_ids[0]):
        return None
    return 'https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?' + urlencode({'procInstId':process_ids[0]})


def collect(conn, sheets, source_id=None):
    if source_id is not None:
        requests=[dict(r) for r in conn.execute('SELECT * FROM payment_requests WHERE logical_request_id=?',(source_id,))]
    else:
        marks=','.join('?' for _ in sheets)
        requests=[dict(r) for r in conn.execute(f'''SELECT * FROM payment_requests WHERE logical_request_id IN (
            SELECT current.logical_request_id FROM payment_requests current
            JOIN (SELECT logical_request_id,MAX(id) latest_id FROM payment_requests
                  WHERE logical_request_id IS NOT NULL GROUP BY logical_request_id) latest ON current.id=latest.latest_id
            WHERE current.source_sheet IN ({marks}))''',sheets)]
    groups = {}
    for row in requests:
        row['_export_raw']=object_json(row.get('raw_extra_json'))
        if row.get('logical_request_id'):
            groups.setdefault(str(row['logical_request_id']), []).append(row)
    request_currency={r['id']:r.get('currency') for r in requests}
    groups={logical:copies for logical,copies in groups.items() if
        max(copies,key=lambda r:r['id']).get('source_sheet') in sheets and operation_source(max(copies,key=lambda r:r['id']))}
    all_request_ids={r['id'] for copies in groups.values() for r in copies}
    scoped_ids={r['id'] for copies in groups.values() for r in copies if r.get('source_sheet') in sheets and operation_source(r)}
    payments = selected_rows(conn,'payment_records','request_id',scoped_ids)
    links = selected_rows(conn,'attachment_links','request_id',scoped_ids)
    payments_by_request=indexed(payments,'request_id')
    links_by_request=indexed(links,'request_id')
    payments_by_id={p['id']:p for p in payments}
    vouchers = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='payment_vouchers'").fetchone():
        vouchers = selected_rows(conn,'payment_vouchers','payment_id',payments_by_id)
    vouchers_by_payment=indexed(vouchers,'payment_id')
    file_ids={r['file_object_id'] for r in links+vouchers if r.get('file_object_id')}
    files={f['id']:f for f in selected_rows(conn,'file_objects','id',file_ids)} if file_ids else {}
    audit_times = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_logs'").fetchone():
        query="""SELECT old_value_json,new_value_json,created_at FROM audit_logs
                 WHERE entity_type IN ('attachment','payment','payment_record','payment_voucher')"""
        params=[]
        if source_id is not None:
            # Native schema has no JSON-expression audit index. One set-based
            # query avoids rescanning all audits for every 400 request ids.
            clauses=[]
            for field in ('old_value_json','new_value_json'):
                for key,ids in (('request_id',all_request_ids),('payment_id',set(payments_by_id))):
                    clauses.append(f"(CASE WHEN json_valid({field}) THEN json_extract({field},'$.{key}') END) IN (SELECT value FROM json_each(?))")
                    params.append(json.dumps(sorted(ids)))
            query+=' AND ('+' OR '.join(clauses)+')'
        for audit in conn.execute(query,params):
            for field in ('old_value_json','new_value_json'):
                metadata = object_json(audit[field])
                request_id = metadata.get('request_id')
                if not request_id and metadata.get('payment_id'):
                    request_id = payments_by_id.get(metadata['payment_id'],{}).get('request_id')
                if request_id in all_request_ids:
                    at=timestamp(audit['created_at'])
                    audit_times[request_id]=max(at,audit_times.get(request_id,at))
    items = []
    for logical, copies in groups.items():
        if source_id is not None and source_id != logical:
            continue
        # A later weekly copy is authoritative even when an earlier archived
        # copy was subsequently edited. Apply scope only after this selection.
        selected = max(copies, key=lambda r:r['id'])
        external = operation_source(selected)
        if external is None or selected.get('source_sheet') not in sheets:
            continue
        identities = set()
        for copy in copies:
            # Identity and legal ownership are immutable across weekly copies.
            # A later timestamp cannot authorize merging different source roots.
            meta = copy['_export_raw'].get('external_source') or {}
            if not isinstance(meta,dict):
                meta = {}
            identities.add(digest([meta.get('system'),meta.get('record_id') or meta.get('source_id'),meta.get('source_type') or meta.get('table'),
                meta.get('legal_company_id') or meta.get('legal_company_name') or meta.get('source_company') or meta.get('source_company_raw')]))
        source_conflict = len(identities) > 1
        all_copies=copies
        copies = [r for r in copies if r.get('source_sheet') in sheets and operation_source(r)]
        ids = {r['id'] for r in copies}
        roots = {}
        related_payments = [p for request_id in ids for p in payments_by_request[request_id]]
        for payment in related_payments:
            payment['_source_currency']=request_currency.get(payment['request_id'])
        related_links = [a for request_id in ids for a in links_by_request[request_id]]
        payment_ids = {p['id'] for p in related_payments}
        related_vouchers = [v for payment_id in payment_ids for v in vouchers_by_payment[payment_id]]
        by_root=defaultdict(list)
        for p in related_payments:
            by_root[str(p.get('root_payment_id') or p['id'])].append(p)
        conflicts = set()
        for root,root_copies in by_root.items():
            recorded = [r for r in root_copies if r.get('source_type') in RECORDED_PAYMENTS]
            latest_time = max((timestamp(r['updated_at']) for r in recorded), default=None)
            latest = [r for r in recorded if timestamp(r['updated_at']) == latest_time]
            fields = ('amount','payment_date','payment_account','bank_reference','payer','remark','_source_currency')
            if len({tuple(r.get(f) for f in fields) for r in latest}) > 1 or len({r.get('_source_currency') for r in root_copies})>1 or any(p.get('source_type') not in RECORDED_PAYMENTS | UNCERTAIN_PAYMENTS for p in root_copies):
                conflicts.add(root)
        for p in sorted(related_payments, key=lambda p:(timestamp(p['updated_at']),p['id'])):
            root = str(p.get('root_payment_id') or p['id'])
            # Copied rollover rows retain root identity but do not downgrade original evidence.
            if root not in roots or p.get('source_type') not in UNCERTAIN_PAYMENTS:
                roots[root] = p
        exported_payments = []
        for root, p in sorted(roots.items()):
            value = {'source_id':root,'amount':money(p['amount']), 'currency':p.get('_source_currency'),
                     'payment_date':p.get('payment_date'), 'payment_account':p.get('payment_account'),
                     'payer':p.get('payer'),'remark':p.get('remark'),
                     'source_request_id':str(p['request_id']),
                     'created_at':timestamp(p['created_at']),'updated_at':timestamp(p['updated_at']),
                     'bank_reference':p.get('bank_reference'), 'source_type':p.get('source_type'),
                     'evidence_status':'conflict' if root in conflicts else ('unverified_summary' if p.get('source_type') in UNCERTAIN_PAYMENTS else 'recorded')}
            value['version'] = digest(value)
            value['provenance'] = [{'source_request_id':str(copy['request_id']),'source_payment_id':str(copy['id']),
                'copied_from_payment_id':str(copy['copied_from_payment_id']) if copy.get('copied_from_payment_id') else None,
                'source_type':copy.get('source_type'),'created_at':timestamp(copy['created_at']),'updated_at':timestamp(copy['updated_at'])}
                for copy in by_root[root]]
            exported_payments.append(value)
        currency_conflict=any(len({p.get('_source_currency') for p in copies})>1 for copies in by_root.values()) or any(not p.get('currency') or p['currency']!=selected.get('currency') for p in exported_payments)
        attached = {}
        for a in related_links:
            a['_logical_source_id'] = logical
            attached.setdefault((a.get('source_system'), a.get('source_instance_id'),a.get('source_attachment_id') or a['id']), {'_row':a,'_kind':'attachment'})
        for voucher in related_vouchers:
            payment = payments_by_id[voucher['payment_id']]
            voucher['payment_source_id'] = str(payment.get('root_payment_id') or payment['id'])
            attached[('payment-voucher',voucher['id'])] = {'_row':voucher,'_kind':'payment-voucher'}
        paid = sum((Decimal(p['amount']) for p in exported_payments if p['evidence_status']=='recorded' and p['amount'] is not None), Decimal(0))
        amount = money(selected['amount'])
        kind, type_raw = application_type(external, object_json(selected.get('raw_extra_json')))
        approval_raw = {'status':external.get('approval_status'),'result':external.get('approval_result'),
                        **{k:selected.get(k) for k in ('owner_confirmation','finance_review','finance_manager_approval','general_manager_approval')}}
        eligible = not currency_conflict and not source_conflict and not conflicts and str(approval_raw['status'] or '').upper() == 'COMPLETED' and str(approval_raw['result'] or '').lower() in {'agree','approved'}
        times = [timestamp(r['updated_at']) for r in all_copies] + [timestamp(p['updated_at']) for p in related_payments] + [timestamp(a.get('updated_at') or a['created_at']) for a in related_links + related_vouchers]
        times.extend(audit_times[copy['id']] for copy in all_copies if copy['id'] in audit_times)
        for a in related_links + related_vouchers:
            if a.get('file_object_id'):
                file = files.get(a['file_object_id'])
                if file:
                    times.extend(timestamp(file[f]) for f in ('created_at','verified_at') if file.get(f))
        for copy in all_copies:
            meta=copy['_export_raw'].get('external_source') or {}
            if isinstance(meta,dict):
                for field in ('source_updated_at','metadata_synced_at'):
                    if meta.get(field):
                        times.append(timestamp(meta[field]))
        item = {'source_system':SOURCE_SYSTEM,'source_id':logical,'source_request_id':str(selected['id']),
                'approval_no':external.get('approval_no') or selected.get('dingding_id'),
                'dingding_id':selected.get('dingding_id') or external.get('approval_no'),
                'application_type':kind,'application_type_raw':type_raw,
                'source_company':external.get('legal_company_id') or external.get('legal_company_name') or external.get('source_company') or external.get('source_company_raw'),
                'source_sheet':selected.get('source_sheet'),'applicant':selected.get('applicant'), 'payee_name':selected.get('payee_name'),
                'summary':selected.get('summary'),'request_date':external.get('application_date'),
                'request_date_raw':external.get('application_date'),'needed_payment_date':selected.get('needed_payment_date'),
                'currency':selected.get('currency'),'amount':amount,'paid_amount':None if conflicts or currency_conflict else money(paid),
                'pending_amount':money(Decimal(amount)-paid) if amount is not None and not conflicts and not currency_conflict else None,
                'approvals':{'raw':approval_raw,'eligibility':'eligible' if eligible else 'blocked'},
                'source_status':selected.get('payment_status'),'payments':exported_payments,'attachments':list(attached.values()),
                'updated_at':max(times)}
        original_amount = external.get('original_source_amount_raw',external.get('source_amount'))
        item['original_source_amount'] = str(original_amount) if original_amount is not None else None
        item['original_source_currency'] = external.get('source_currency_raw')
        item['storage_precision_warning'] = False
        if original_amount is not None:
            try:
                exact = Decimal(str(original_amount))
                item['storage_precision_warning'] = exact.is_finite() and Decimal(str(float(exact))) != exact
            except (InvalidOperation,ValueError,OverflowError):
                item['storage_precision_warning'] = True
        item['source_conflict'] = source_conflict
        item['currency_conflict'] = currency_conflict
        original_url = safe_original_url(external.get('workflow_url') or external.get('original_url'))
        if original_url:
            item['original_url'] = original_url
        items.append(item)
    return items


def encode_cursor(value):
    data = json.dumps(value, sort_keys=True, separators=(',',':')).encode()
    signature = hmac.new(os.environ['PAYMENT_ERP_EXPORT_TOKEN'].encode(), data, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(data).decode() + '.' + signature


def decode_cursor(value):
    try:
        data, signature = value.split('.')
        raw = base64.urlsafe_b64decode(data)
        expected = hmac.new(os.environ['PAYMENT_ERP_EXPORT_TOKEN'].encode(),raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise ValueError()
        return json.loads(raw)
    except (ValueError, TypeError):
        raise HTTPException(400,'Invalid export cursor')


@router.get('/operating-expenses')
def operating_expenses(changed_since: Optional[str] = None, until: Optional[str] = None,
                       cursor: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500),
                       date_from: Optional[str] = None, date_to: Optional[str] = None,
                       source_id: Optional[str] = None,
                       sheets=Depends(export_scope)):
    since = timestamp(changed_since) if changed_since else None
    for date_value in (date_from,date_to):
        if date_value:
            try:
                from datetime import date
                date.fromisoformat(date_value)
            except ValueError:
                raise HTTPException(400,'Invalid request date')
    bounds = {'since':since,'date_from':date_from,'date_to':date_to,'sheets':sheets,'source_id':source_id}
    after = None
    if cursor:
        state = decode_cursor(cursor)
        if state['bounds'] != bounds or (until and timestamp(until) != state['until']):
            raise HTTPException(400,'Cursor filters changed')
        until, after = state['until'], state['after']
    watermark = timestamp(until) if until else timestamp(datetime.now(timezone.utc))
    if watermark > timestamp(datetime.now(timezone.utc)) or (since and since > watermark):
        raise HTTPException(400,'Invalid export time range')
    def included(item):
        key = [item['updated_at'], item['source_id']]
        if (since and key[0] < since) or key[0] > watermark or (after and key <= after):
            return False
        requested = item['request_date']
        in_dates = requested and (not date_from or requested >= date_from) and (not date_to or requested <= date_to)
        older_unpaid = date_from and requested and requested < date_from and (item['pending_amount'] is None or Decimal(item['pending_amount']) > 0)
        return not (date_from or date_to) or bool(in_dates or older_unpaid)
    with read_database() as conn:
        items = sorted(filter(included, collect(conn, sheets,source_id)),key=lambda i:(i['updated_at'],i['source_id']))
        page, end = items[:limit], len(items) <= limit
        for item in page:
            item['attachments'] = [attachment_item(a['_row'],conn,a['_kind']) for a in item['attachments']]
            dedup = {}
            for attachment in item['attachments']:
                if attachment is None:
                    continue
                provenance = {'url':attachment['url']}
                if attachment['source_id'] in dedup:
                    dedup[attachment['source_id']]['provenance'].append(provenance)
                else:
                    attachment['provenance'] = [provenance]
                    dedup[attachment['source_id']] = attachment
            item['attachments'] = list(dedup.values())
            item['version'] = digest(item)
    next_cursor = None if end else encode_cursor({'bounds':bounds,'until':watermark,'after':[page[-1]['updated_at'],page[-1]['source_id']]})
    return {'schema_version':1,'source_system':SOURCE_SYSTEM,'items':page,'until':watermark,'next_cursor':next_cursor,'end':end}


def logical_request_allowed(conn,request_id,sheets):
    request = conn.execute('SELECT logical_request_id FROM payment_requests WHERE id=?',(request_id,)).fetchone()
    if request is None or not request['logical_request_id']:
        return False
    latest = conn.execute('SELECT * FROM payment_requests WHERE logical_request_id=? ORDER BY id DESC LIMIT 1',(request['logical_request_id'],)).fetchone()
    return bool(latest and latest['source_sheet'] in sheets and operation_source(dict(latest)))


@router.get('/attachments/{attachment_id}')
def attachment_file(attachment_id: int, sheets=Depends(export_scope)):
    with read_database() as conn:
        row = conn.execute('SELECT a.*, r.source_sheet, r.raw_extra_json FROM attachment_links a JOIN payment_requests r ON r.id=a.request_id WHERE a.id=?',(attachment_id,)).fetchone()
        if row is None or row['source_sheet'] not in sheets or not operation_source(dict(row)) or not logical_request_allowed(conn,row['request_id'],sheets):
            raise HTTPException(404,'Attachment not found')
        try:
            path, _ = resolve_attachment_path(row,conn)
        except (ValueError,sqlite3.Error):
            path = None
        if path is None:
            raise HTTPException(404,'Attachment not found')
    return FileResponse(path,filename=row['original_filename'] or path.name)


@router.get('/payment-vouchers/{voucher_id}')
def payment_voucher_file(voucher_id: int, sheets=Depends(export_scope)):
    with read_database() as conn:
        row = conn.execute('SELECT v.*, r.id AS request_id, r.source_sheet, r.raw_extra_json FROM payment_vouchers v JOIN payment_records p ON p.id=v.payment_id JOIN payment_requests r ON r.id=p.request_id WHERE v.id=?',(voucher_id,)).fetchone()
        if row is None or row['source_sheet'] not in sheets or not operation_source(dict(row)) or not logical_request_allowed(conn,row['request_id'],sheets):
            raise HTTPException(404,'Payment proof not found')
        try:
            path, _ = resolve_attachment_path(row,conn)
        except (ValueError,sqlite3.Error):
            path = None
        if path is None:
            raise HTTPException(404,'Payment proof not found')
    return FileResponse(path,filename=row['original_filename'] or path.name)
