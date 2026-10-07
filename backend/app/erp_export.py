"""Opt-in, sheet-scoped ERP reads and atomic operating-payment ownership.

Full exports materialize candidate metadata before choosing a page, with linear
relation indexing and one audit pass. This initial opt-in implementation does
not claim bounded metadata memory; only file hydration is limited to the page.
Exact-source reads restrict request and relation queries to that logical root.
Employee company resolution is current-only and provided by the separate POST.
Replacement imports retain no historical employee rows, so current directory
values never participate in watermarked GET facts, versions or pagination.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool, StrictStr

from . import db
from .employee_departments import request_applicant_identity, resolve_employee_department
from .file_storage import resolve_attachment_path
from .external_expenses import _workflow_original_url, classify_dingtalk_payment_event, is_application_type_component
from .fx_rates import CURRENCY_ALIASES
from .mexico_tracking import _normalized_token

router = APIRouter(prefix='/api/integrations/erp', tags=['ERP export'])
SOURCE_SYSTEM = 'cashier-payment-archive'
UNCERTAIN_PAYMENTS = {'legacy_migration', 'snapshot_legacy', 'excel_summary', 'rollover'}
RECORDED_PAYMENTS = {'manual','excel_detail','dingtalk_workflow'}


class ApplicantIdentity(BaseModel):
    user_id: StrictStr = Field(default='', max_length=200)
    employee_name: StrictStr = Field(default='', max_length=200)


class ApplicantCompanyLookup(BaseModel):
    applicants: List[ApplicantIdentity] = Field(max_length=500)


class OperatingTakeoverPreview(BaseModel):
    source_id: StrictStr = Field(min_length=1, max_length=140)
    expected_version: Optional[StrictStr] = Field(default=None, min_length=64, max_length=64)
    zero_history_confirmed: StrictBool = False
    confirmed_by: Optional[StrictStr] = Field(default=None, min_length=1, max_length=140)


class OperatingTakeoverClaim(OperatingTakeoverPreview):
    expected_version: StrictStr = Field(min_length=64, max_length=64)
    request_id: StrictStr = Field(min_length=1, max_length=140)


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


def dingtalk_expense_source(row, source_type, *, allow_unmatched=False):
    raw = row['_export_raw'] if '_export_raw' in row else object_json(row.get('raw_extra_json'))
    external = raw.get('external_source') or {}
    if not isinstance(external, dict):
        return None
    if external.get('system') != 'dingtalk_expense_database' or not (external.get('record_id') or external.get('source_id')):
        return None
    if external.get('source_type') != source_type and not (not external.get('source_type') and external.get('table') == 'approval_expense_' + source_type):
        return None
    if not allow_unmatched and external.get('lookup_status') in {'unmatched', 'conflict'}:
        return None
    return external


def operation_source(row):
    return dingtalk_expense_source(row, 'operation')


def purchase_source(row):
    external = dingtalk_expense_source(row, 'purchase')
    if external is None:
        return None
    # Only the original execution-region field can establish China scope.
    # Department mappings and currency never fill missing source evidence.
    # The general region resolver accepts substrings for historical tracking;
    # this export requires a complete, verified China label.
    region = ' '.join(_normalized_token(external.get('execution_region')).split())
    if region not in {'中国', 'china', '中国china', '中国 china'}:
        return None
    try:
        request_date = date.fromisoformat(external.get('application_date'))
    except (ValueError, TypeError):
        return None
    if request_date.year != 2026:
        return None
    return external


def application_type(external, raw):
    value = external.get('application_type_raw')
    if value is None:
        value = next((container[key] for container in (external,raw) for key in container if is_application_type_component(key)),None)
    for component in external.get('formComponentValues', []) or []:
        if isinstance(component, dict) and is_application_type_component(component.get('name')):
            value = component.get('value')
    normalized = ' '.join(str(value or '').split()).casefold()
    return {'付款':'payment','请款':'payment','payment':'payment','pago':'payment',
            '付款申请':'payment','solicitud de pago':'payment','付款申请solicitud de pago':'payment','付款申请 solicitud de pago':'payment',
            '费用报销':'reimbursement','reembolso de gastos':'reimbursement','费用报销reembolso de gastos':'reimbursement','费用报销 reembolso de gastos':'reimbursement',
            '报销':'reimbursement','reimbursement':'reimbursement','reembolso':'reimbursement'}.get(normalized, 'unclassified'), value


def attachment_item(row, conn, kind='attachment', include_version=True, source_type='operation'):
    try:
        path, _ = resolve_attachment_path(row, conn)
    except (ValueError, sqlite3.Error):
        return None
    if path is None:
        return None
    source_id = f"{kind}:{row['id']}"
    if row.get('source_attachment_id'):
        source_id = kind + ':' + digest([row.get('source_system'),row.get('source_instance_id'),row['source_attachment_id']])
    file_scope = 'purchase-expenses/' if source_type == 'purchase' else ''
    value = {'source_id':source_id, 'filename':row.get('original_filename') or path.name,
             'url':f"/api/integrations/erp/{file_scope}{'payment-vouchers' if kind == 'payment-voucher' else 'attachments'}/{row['id']}"}
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


def applicant_company_resolution(conn, sheets, user_id, employee_name, mappings_available):
    """Resolve only a supplied person and disclose only configured organizations."""
    mapping, match_source = resolve_employee_department(
        conn, applicant_id=user_id, applicant_name=employee_name,
    ) if mappings_available else (None, 'unavailable')
    status = 'matched' if mapping else match_source
    if mapping and mapping['assigned_department'] not in sheets:
        mapping, status = None, 'out_of_scope'
    return {'user_id': user_id, 'employee_name': employee_name,
            'status': status, 'match_source': match_source,
            **{field: mapping[field] if mapping and mapping.get(field) in sheets else None
               for field in ('assigned_department', 'second_level_department', 'third_level_department')}}


def employee_mappings_available(conn):
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(employee_department_mappings)')}
    return {'id', 'user_id', 'employee_name', 'second_level_department', 'third_level_department'} <= columns


@router.post('/resolve-applicant-companies')
def resolve_applicant_companies(body: ApplicantCompanyLookup, sheets=Depends(export_scope)):
    with read_database() as conn:
        available = employee_mappings_available(conn)
        items = [applicant_company_resolution(conn, sheets, person.user_id, person.employee_name, available)
                 for person in body.applicants]
    return {'schema_version': 1, 'source_system': SOURCE_SYSTEM, 'resolution_mode': 'current', 'items': items}


def dingtalk_identity(selected, external):
    original_url = None
    instance_evidence = []
    for key in ('process_instance_id', 'workflow_process_instance_id'):
        if isinstance(external.get(key), str) and external[key].strip():
            instance_evidence.append({'value': external[key].strip(), 'source': 'external_source.' + key})
    for key in ('workflow_url', 'original_url'):
        vetted_url = safe_original_url(external.get(key))
        if vetted_url:
            original_url = original_url or vetted_url
            instance_evidence.append({'value': parse_qs(urlsplit(vetted_url).query)['procInstId'][0],
                                      'source': 'vetted_original_url.procInstId', 'field': 'external_source.' + key})
    conflict = len({item['value'] for item in instance_evidence}) > 1
    instance_id = instance_evidence[0]['value'] if instance_evidence and not conflict else None
    instance_source = instance_evidence[0]['source'] if instance_id else None
    explicit_approval = str(external.get('approval_no') or '').strip() or None
    return {'external_source_id': str(external.get('record_id') or external.get('source_id')),
            'approval_no': explicit_approval or selected.get('dingding_id'),
            'dingding_id': selected.get('dingding_id') or explicit_approval,
            'originator_user_id': external.get('applicant_id') or None,
            'originator_name': external.get('applicant') or None,
            'corp_id': external.get('corp_id') or None,
            'process_instance_id': instance_id,
            'approval_identity_status': 'conflict' if conflict else ('explicit' if explicit_approval or instance_id else 'unverified'),
            'approval_identity_provenance': {
                'approval_no': 'external_source.approval_no' if explicit_approval else (
                    'payment_request.dingding_id' if selected.get('dingding_id') else None),
                'process_instance_id': instance_source},
            'approval_identity_evidence': {'process_instance_id': instance_evidence},
            **({'original_url': original_url} if original_url else {})}


def source_identity_conflict(copies, selected, source_type='operation'):
    identities = set()
    explicit = defaultdict(set)
    for copy in copies:
        meta = object_json(copy.get('raw_extra_json')).get('external_source') or {}
        if not isinstance(meta, dict):
            meta = {}
        kind = meta.get('source_type') or meta.get('table')
        if source_type == 'purchase' and dingtalk_expense_source(copy, 'purchase') is not None:
            kind = 'purchase'
        identities.add(digest([meta.get('system'), meta.get('record_id') or meta.get('source_id'), kind,
                              meta.get('legal_company_id') or meta.get('legal_company_name') or meta.get('source_company') or meta.get('source_company_raw')]))
        for field in ('approval_no', 'applicant_id', 'corp_id'):
            value = str(meta.get(field) or '').strip()
            if value:
                explicit[field].add(value)
        identity = dingtalk_identity(copy, meta)
        explicit['process_instance_id'].update(evidence['value'] for evidence in identity['approval_identity_evidence']['process_instance_id'])
    return len(identities) > 1 or any(len(values) > 1 for values in explicit.values())


def collect(conn, sheets, source_id=None, source_type='operation'):
    source_reader = {'operation': operation_source, 'purchase': purchase_source}[source_type]
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
        max(copies,key=lambda r:r['id']).get('source_sheet') in sheets and source_reader(max(copies,key=lambda r:r['id']))}
    all_request_ids={r['id'] for copies in groups.values() for r in copies}
    scoped_ids={r['id'] for copies in groups.values() for r in copies if r.get('source_sheet') in sheets and source_reader(r)}
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
        external = source_reader(selected)
        if external is None or selected.get('source_sheet') not in sheets:
            continue
        selected_identity = dingtalk_identity(selected, external)
        # An older copy lacking metadata is compatible with later enrichment;
        # two explicit source identities are never merged by timestamp.
        source_conflict = source_identity_conflict(copies, selected, source_type)
        all_copies=copies
        copies = [r for r in copies if r.get('source_sheet') in sheets and source_reader(r)]
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
        payment_evidence_status = 'conflict' if conflicts or currency_conflict or source_conflict else (
            'recorded' if exported_payments and all(p['evidence_status'] == 'recorded' and p['amount'] is not None
                                                   for p in exported_payments) else 'unknown')
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
                'application_type':kind,'application_type_raw':type_raw,
                'source_company':external.get('legal_company_id') or external.get('legal_company_name') or external.get('source_company') or external.get('source_company_raw'),
                'source_sheet':selected.get('source_sheet'),'applicant':selected.get('applicant'), 'payee_name':selected.get('payee_name'),
                'summary':selected.get('summary'),'request_date':external.get('application_date'),
                'request_date_raw':external.get('application_date'),'needed_payment_date':selected.get('needed_payment_date'),
                'currency':selected.get('currency'),'amount':amount,'paid_amount':money(paid) if payment_evidence_status == 'recorded' else None,
                'pending_amount':money(Decimal(amount)-paid) if amount is not None and payment_evidence_status == 'recorded' else None,
                'payment_evidence_status':payment_evidence_status,
                'approvals':{'raw':approval_raw,'eligibility':'eligible' if eligible else 'blocked'},
                'source_status':selected.get('payment_status'),'payments':exported_payments,'attachments':list(attached.values()),
                'updated_at':max(times)}
        if source_type == 'purchase':
            item.update(source_type='purchase', application_type='purchase',
                        execution_region='china', execution_region_raw=external.get('execution_region'))
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
        item.update(selected_identity)
        if source_conflict:
            item['approval_identity_status'] = 'conflict'
        user_id, employee_name = request_applicant_identity({**selected, 'raw_extra': selected['_export_raw']})
        original_applicant = str(external.get('applicant') or '').strip()
        # Without the original name a populated cashier name could be a manual
        # edit. This evidence is derived only from the selected source record.
        identity_ambiguous = bool(not original_applicant and user_id and employee_name)
        item['applicant_identity'] = {'user_id': user_id, 'employee_name': employee_name,
            'status': 'ambiguous' if identity_ambiguous else ('selected' if user_id or employee_name else 'missing_applicant'),
            'manual_applicant_override': None if identity_ambiguous else bool(original_applicant and employee_name != original_applicant)}
        if identity_ambiguous:
            item['applicant_identity']['ambiguity_reason'] = 'original_applicant_name_missing'
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


def export_expenses(changed_since, until, cursor, limit, date_from, date_to, source_id,
                    sheets, source_type='operation'):
    since = timestamp(changed_since) if changed_since else None
    for date_value in (date_from,date_to):
        if date_value:
            try:
                date.fromisoformat(date_value)
            except ValueError:
                raise HTTPException(400,'Invalid request date')
    bounds = {'since':since,'date_from':date_from,'date_to':date_to,'sheets':sheets,'source_id':source_id}
    if source_type == 'purchase':
        # Keep existing operation cursors compatible while giving procurement
        # an explicit scope that neither endpoint can interchange.
        bounds['scope'] = {'source_type': 'purchase', 'execution_region': 'china', 'request_year': 2026}
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
        if source_type == 'purchase':
            return bool(in_dates)
        older_unpaid = date_from and requested and requested < date_from and (item['pending_amount'] is None or Decimal(item['pending_amount']) > 0)
        return not (date_from or date_to) or bool(in_dates or older_unpaid)
    with read_database() as conn:
        items = sorted(filter(included, collect(conn, sheets,source_id,source_type)),key=lambda i:(i['updated_at'],i['source_id']))
        page, end = items[:limit], len(items) <= limit
        for item in page:
            hydrate_export_item(conn, item, source_type)
    next_cursor = None if end else encode_cursor({'bounds':bounds,'until':watermark,'after':[page[-1]['updated_at'],page[-1]['source_id']]})
    return {'schema_version':1,'source_system':SOURCE_SYSTEM,'items':page,'until':watermark,'next_cursor':next_cursor,'end':end}


def hydrate_export_item(conn, item, source_type='operation', require_files=False):
    dedup = {}
    for candidate in item['attachments']:
        attachment = attachment_item(candidate['_row'], conn, candidate['_kind'], source_type=source_type)
        if attachment is None:
            if require_files:
                raise HTTPException(409, 'Operating expense attachment evidence is unavailable')
            continue
        provenance = {'url': attachment['url']}
        if attachment['source_id'] in dedup:
            dedup[attachment['source_id']]['provenance'].append(provenance)
        else:
            attachment['provenance'] = [provenance]
            dedup[attachment['source_id']] = attachment
    item['attachments'] = list(dedup.values())
    item['version'] = digest(item)


MAX_TAKEOVER_COPIES = 500
MAX_TAKEOVER_PAYMENTS = 2000
MAX_TAKEOVER_FILES = 1000
MAX_TAKEOVER_EVENTS = 2000


def exact_operating_rows(conn, sheets, source_id, *, for_workflow=False):
    source_reader = lambda row: dingtalk_expense_source(row, 'operation', allow_unmatched=for_workflow)
    rows = [dict(row) for row in conn.execute(
        'SELECT * FROM payment_requests WHERE logical_request_id=? ORDER BY id LIMIT ?',
        (source_id, MAX_TAKEOVER_COPIES + 1))]
    if not rows or str(rows[0]['logical_request_id']) != source_id:
        raise HTTPException(404, 'Operating expense source not found')
    selected = rows[-1]
    if selected.get('source_sheet') not in sheets or source_reader(selected) is None:
        raise HTTPException(404, 'Operating expense source not found')
    if len(rows) > MAX_TAKEOVER_COPIES:
        raise HTTPException(409, 'Operating expense history exceeds takeover limits')
    if any(row.get('source_sheet') not in sheets or source_reader(row) is None for row in rows):
        raise HTTPException(409, 'Operating expense history has conflicting source scope')
    return rows


def bounded_operating_relations(conn, source_id, tables=None):
    relations = {}
    limits = {'payment_records': MAX_TAKEOVER_PAYMENTS, 'attachment_links': MAX_TAKEOVER_FILES,
              'payment_vouchers': MAX_TAKEOVER_FILES, 'dingtalk_workflow_events': MAX_TAKEOVER_EVENTS}
    for table, maximum in limits.items():
        if tables is not None and table not in tables:
            continue
        if table == 'payment_vouchers':
            query = '''SELECT relation.* FROM payment_vouchers relation
                JOIN payment_records payment ON payment.id=relation.payment_id
                JOIN payment_requests request ON request.id=payment.request_id'''
        else:
            query = f'SELECT relation.* FROM {table} relation JOIN payment_requests request ON request.id=relation.request_id'
        rows = [dict(row) for row in conn.execute(
            query + ' WHERE request.logical_request_id=? ORDER BY relation.id LIMIT ?', (source_id, maximum + 1))]
        if len(rows) > maximum:
            raise HTTPException(409, 'Operating expense history exceeds takeover limits')
        relations[table] = rows
    return relations


def operating_takeover_item(conn, sheets, source_id, *, zero_history_confirmed=False, confirmed_by=None):
    rows = exact_operating_rows(conn, sheets, source_id)
    relations = bounded_operating_relations(conn, source_id)
    item = collect(conn, sheets, source_id)[0]
    if item['approvals']['eligibility'] != 'eligible' or item['source_conflict'] or item['currency_conflict']:
        raise HTTPException(409, 'Operating expense approval, identity or currency is conflicting')
    if (item['approval_identity_status'] != 'explicit' or not item['source_company']
            or item['application_type'] not in {'payment', 'reimbursement'}):
        raise HTTPException(409, 'Operating expense source identity is unverified')
    for row in rows:
        external = operation_source(row)
        if external.get('approval_no') and str(row.get('dingding_id') or '').strip() != str(external['approval_no']).strip():
            raise HTTPException(409, 'Operating expense approval identity is conflicting')
    raw_currency = str(item.get('original_source_currency') or '').upper()
    currencies = {currency for alias, currency in CURRENCY_ALIASES.items() if alias in raw_currency}
    if item['storage_precision_warning'] or (raw_currency and currencies != {item['currency']}):
        raise HTTPException(409, 'Operating expense original money or currency is conflicting')
    if conn.execute('''SELECT 1 FROM payment_requests request WHERE request.logical_request_id IS NOT CAST(? AS INTEGER)
            AND json_extract(CASE WHEN json_valid(request.raw_extra_json) THEN request.raw_extra_json ELSE '{}' END, '$.external_source.system')='dingtalk_expense_database'
            AND (json_extract(CASE WHEN json_valid(request.raw_extra_json) THEN request.raw_extra_json ELSE '{}' END, '$.external_source.source_type')='operation'
                OR json_extract(CASE WHEN json_valid(request.raw_extra_json) THEN request.raw_extra_json ELSE '{}' END, '$.external_source.table')='approval_expense_operation')
            AND CAST(COALESCE(json_extract(CASE WHEN json_valid(request.raw_extra_json) THEN request.raw_extra_json ELSE '{}' END, '$.external_source.record_id'),
                             json_extract(CASE WHEN json_valid(request.raw_extra_json) THEN request.raw_extra_json ELSE '{}' END, '$.external_source.source_id')) AS TEXT)=? LIMIT 1''',
            (source_id, item['external_source_id'])).fetchone():
        raise HTTPException(409, 'Operating expense external identity belongs to another logical root')
    amount = money(item['amount'])
    if amount is None or Decimal(amount) <= 0 or item['currency'] not in {'CNY', 'USD', 'MXN'}:
        raise HTTPException(409, 'Operating expense amount or currency is unverified')
    if item['original_source_amount'] is not None:
        original_amount = money(item['original_source_amount'])
        if original_amount is None or Decimal(item['original_source_amount']) != Decimal(amount):
            raise HTTPException(409, 'Operating expense original amount is malformed or differs from archived amount')
    if len({row.get('currency') for row in rows}) != 1:
        raise HTTPException(409, 'Operating expense history has conflicting currencies')
    payments = indexed(relations['payment_records'], 'request_id')
    for row in rows:
        for payment in payments[row['id']]:
            try:
                date.fromisoformat(str(payment.get('payment_date') or '').strip())
            except ValueError as exc:
                raise HTTPException(409, 'Operating expense historical payment date is missing or invalid') from exc
        amounts = [money(payment['amount']) for payment in payments[row['id']]]
        if any(value is None or Decimal(value) <= 0 for value in amounts):
            raise HTTPException(409, 'Operating expense payment evidence is incomplete')
        total = sum((Decimal(value) for value in amounts), Decimal(0))
        requested, paid, pending = (money(row.get(field)) for field in ('amount', 'paid_amount', 'pending_amount'))
        if (requested is None or paid is None or pending is None or Decimal(paid) != total
                or Decimal(pending) != Decimal(requested) - total or total > Decimal(requested)):
            raise HTTPException(409, 'Operating expense payment summaries are unverified')
    payment_roots = {payment.get('root_payment_id') or payment['id'] for payment in relations['payment_records']}
    if payment_roots and conn.execute('''SELECT 1 FROM payment_records payment JOIN payment_requests request ON request.id=payment.request_id
            WHERE COALESCE(payment.root_payment_id, payment.id) IN (SELECT value FROM json_each(?))
              AND request.logical_request_id IS NOT CAST(? AS INTEGER) LIMIT 1''',
            (json.dumps(sorted(payment_roots)), source_id)).fetchone():
        raise HTTPException(409, 'Operating expense payment root belongs to another source')
    unresolved = {'eligible', 'preview_candidate', 'review_required', 'source_missing'}
    for event in relations['dingtalk_workflow_events']:
        if event.get('process_instance_id') and item['process_instance_id'] and event['process_instance_id'] != item['process_instance_id']:
            raise HTTPException(409, 'Operating expense workflow identity is conflicting')
        classification, _ = classify_dingtalk_payment_event(
            event, approval_no=item['approval_no'], pending_amount=float(rows[-1]['pending_amount']),
            paid_amount=float(rows[-1]['paid_amount']), workflow_status=item['approvals']['raw']['status'],
            workflow_result=item['approvals']['raw']['result'])
        if not event.get('payment_record_id') and (event.get('classification') in unresolved or classification in unresolved):
            raise HTTPException(409, 'Operating expense workflow payment evidence requires review')
    if not item['payments']:
        # Import/migration defaults erase unknown-vs-zero. Only an explicit
        # upstream finance human confirmation can attest complete zero history;
        # a native numeric summary or source JSON flag cannot establish it.
        if (not zero_history_confirmed or not confirmed_by or not confirmed_by.strip()
                or confirmed_by != confirmed_by.strip()):
            raise HTTPException(409, 'Operating expense zero payment history requires explicit finance confirmation and operator attribution')
        if any(row.get('actual_payment_date') or row.get('payer') for row in rows):
            raise HTTPException(409, 'Operating expense zero payment evidence is unverified')
        item.update(paid_amount='0', pending_amount=amount, payment_evidence_status='recorded')
        item['zero_history_attestation'] = {
            'method': 'explicit_erp_finance_confirmation', 'confirmed_by': confirmed_by, 'verified_zero': True}
    elif zero_history_confirmed:
        raise HTTPException(409, 'Operating expense zero history confirmation conflicts with recorded payments')
    elif item['payment_evidence_status'] != 'recorded' or item['paid_amount'] != money(rows[-1]['paid_amount']):
        raise HTTPException(409, 'Operating expense payment evidence is incomplete')
    hydrate_export_item(conn, item, require_files=True)
    history_fingerprint = digest({'requests': rows, **relations, 'export_version': item['version']})
    item['version'] = digest({'export_version': item['version'], 'history_fingerprint': history_fingerprint})
    return item, history_fingerprint


def operating_envelope(item):
    return {'schema_version': 1, 'source_system': SOURCE_SYSTEM, 'items': [item]}


def stored_operating_takeover(conn, source_id, sheets):
    row = conn.execute('SELECT * FROM erp_operating_expense_ownership WHERE logical_request_id=?', (source_id,)).fetchone()
    if row and (str(row['logical_request_id']) != source_id or row['source_sheet'] not in sheets):
        raise HTTPException(404, 'Operating expense source not found')
    return row


def stored_operating_item(stored):
    item = json.loads(stored['snapshot_json'])
    if not item.get('payments'):
        attestation = item.get('zero_history_attestation') or {}
        operator = attestation.get('confirmed_by') if isinstance(attestation, dict) else None
        if (not isinstance(attestation, dict) or attestation.get('verified_zero') is not True
                or attestation.get('method') != 'explicit_erp_finance_confirmation'
                or not isinstance(operator, str) or not 1 <= len(operator) <= 140 or operator != operator.strip()
                or not operator.strip() or attestation.get('confirmed_at') != stored['claimed_at']):
            raise HTTPException(409, 'Operating expense claimed zero history has no verified finance attestation')
    return item


@router.post('/operating-expenses/takeover-preview')
def operating_takeover_preview(body: OperatingTakeoverPreview, sheets=Depends(export_scope)):
    with read_database() as conn:
        stored = stored_operating_takeover(conn, body.source_id, sheets)
        item = stored_operating_item(stored) if stored else operating_takeover_item(
            conn, sheets, body.source_id, zero_history_confirmed=body.zero_history_confirmed,
            confirmed_by=body.confirmed_by)[0]
        if body.expected_version and body.expected_version != item['version']:
            raise HTTPException(409, 'Operating expense source version changed')
    return operating_envelope(item)


@router.post('/operating-expenses/takeover-claim')
def operating_takeover_claim(body: OperatingTakeoverClaim, sheets=Depends(export_scope)):
    if os.environ.get('PAYMENT_ERP_TAKEOVER_ENABLED', '').strip().lower() not in {'true', '1', 'yes'}:
        raise HTTPException(503, 'ERP operating expense takeover is disabled')
    actor = hashlib.sha256(os.environ['PAYMENT_ERP_EXPORT_TOKEN'].encode()).hexdigest()
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        stored = stored_operating_takeover(conn, body.source_id, sheets)
        if stored:
            if stored['actor_fingerprint'] != actor or stored['takeover_request_id'] != body.request_id:
                raise HTTPException(409, 'Operating expense payment is already managed by ERP')
            frozen = stored_operating_item(stored)
            attestation = frozen.get('zero_history_attestation')
            if (bool(attestation) != body.zero_history_confirmed
                    or (attestation and attestation['confirmed_by'] != body.confirmed_by)):
                raise HTTPException(409, 'Operating expense zero history confirmation changed')
            return operating_envelope(frozen)
        item, history_fingerprint = operating_takeover_item(
            conn, sheets, body.source_id, zero_history_confirmed=body.zero_history_confirmed,
            confirmed_by=body.confirmed_by)
        if body.expected_version != item['version']:
            raise HTTPException(409, 'Operating expense source version changed')
        takeover = {'owner': 'deeplinkerp', 'claim_token': secrets.token_urlsafe(32),
                    'claimed_at': timestamp(datetime.now(timezone.utc)), 'history_fingerprint': history_fingerprint}
        item['takeover'] = takeover
        if item.get('zero_history_attestation'):
            item['zero_history_attestation']['confirmed_at'] = takeover['claimed_at']
        try:
            conn.execute('''INSERT INTO erp_operating_expense_ownership(logical_request_id,external_source_id,source_sheet,
                owner,actor_fingerprint,takeover_request_id,claim_token,claimed_at,history_fingerprint,snapshot_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (int(body.source_id), item['external_source_id'], item['source_sheet'], takeover['owner'], actor,
                 body.request_id, takeover['claim_token'], takeover['claimed_at'], history_fingerprint,
                 json.dumps(item, ensure_ascii=False, separators=(',', ':'))))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Operating expense source is already owned or conflicting')
        db.write_audit(conn, None, 'erp.operating_expense.takeover_claim', 'erp_operating_expense_ownership',
                       int(body.source_id), operation_id=body.request_id,
                       new_value={'source_id': body.source_id, 'owner': takeover['owner'],
                                  'confirmed_by': body.confirmed_by,
                                  'zero_history_attestation': item.get('zero_history_attestation'),
                                  'history_fingerprint': history_fingerprint},
                       reason='ERP finance takeover; zero history requires explicit human confirmation')
    return operating_envelope(item)


def workflow_descriptors(raw):
    try:
        values = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    fields = {'id', 'fileId', 'file_id', 'fileName', 'file_name', 'name', 'size', 'fileSize', 'mime_type', 'type', 'url', 'downloadUrl'}
    return [{key: value for key, value in descriptor.items() if key in fields and isinstance(value, (str, int, float))}
            for descriptor in values if isinstance(descriptor, dict)] if isinstance(values, list) else []


@router.get('/operating-expenses/workflow')
def operating_workflow(source_id: str = Query(min_length=1, max_length=140), sheets=Depends(export_scope)):
    with read_database() as conn:
        rows = exact_operating_rows(conn, sheets, source_id, for_workflow=True)
        selected = rows[-1]
        external = dingtalk_expense_source(selected, 'operation', allow_unmatched=True)
        identity = dingtalk_identity(selected, external)
        event_rows = bounded_operating_relations(conn, source_id, {'dingtalk_workflow_events'})['dingtalk_workflow_events']
        conflict = external.get('lookup_status') == 'conflict' or source_identity_conflict(rows, selected) or identity['approval_identity_status'] == 'conflict' or any(
            event.get('process_instance_id') and identity['process_instance_id']
            and event['process_instance_id'] != identity['process_instance_id'] for event in event_rows)
        events = {}
        for event in event_rows if not conflict else []:
            previous = events.get(event['event_key'])
            if previous and (previous['synced_at'], previous['id']) >= (event['synced_at'], event['id']):
                continue
            events[event['event_key']] = event
        ordered = sorted(events.values(), key=lambda event: (event.get('event_time') or '', event.get('sequence_index') or 0, event['id']))
        payload = {'source_id': source_id, 'lookup_status': 'conflict' if conflict else external.get('lookup_status') or ('matched' if ordered else 'unmatched'),
                   'last_synced_at': max((timestamp(event['synced_at']) for event in ordered), default=None),
                   'original_url': identity.get('original_url'),
                   'events': [{'id': event['event_key'], 'stage': event.get('stage_name'), 'operator': event.get('operator_name'),
                               'time': timestamp(event['event_time']) if event.get('event_time') else None,
                               'result': event.get('result'), 'comment': event.get('comment'),
                               'current': bool(event.get('is_current')), 'active': bool(event.get('active')),
                               'images': workflow_descriptors(event.get('images_json')),
                               'attachments': workflow_descriptors(event.get('attachments_json'))} for event in ordered]}
    return operating_envelope(payload)


@router.get('/operating-expenses')
def operating_expenses(changed_since: Optional[str] = None, until: Optional[str] = None,
                       cursor: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500),
                       date_from: Optional[str] = None, date_to: Optional[str] = None,
                       source_id: Optional[str] = None,
                       sheets=Depends(export_scope)):
    return export_expenses(changed_since, until, cursor, limit, date_from, date_to, source_id, sheets)


@router.get('/purchase-expenses')
def purchase_expenses(changed_since: Optional[str] = None, until: Optional[str] = None,
                      cursor: Optional[str] = None, limit: int = Query(default=100, ge=1, le=500),
                      date_from: Optional[str] = None, date_to: Optional[str] = None,
                      source_id: Optional[str] = None,
                      sheets=Depends(export_scope)):
    return export_expenses(changed_since, until, cursor, limit, date_from, date_to, source_id, sheets, 'purchase')


def logical_request_allowed(conn,request_id,sheets,source_type='operation'):
    request = conn.execute('SELECT logical_request_id FROM payment_requests WHERE id=?',(request_id,)).fetchone()
    if request is None or not request['logical_request_id']:
        return False
    latest = conn.execute('SELECT * FROM payment_requests WHERE logical_request_id=? ORDER BY id DESC LIMIT 1',(request['logical_request_id'],)).fetchone()
    source_reader = {'operation': operation_source, 'purchase': purchase_source}[source_type]
    return bool(latest and latest['source_sheet'] in sheets and source_reader(dict(latest)))


def claimed_operating_file_allowed(conn, request_id, sheets, file_url):
    # Ordinary exports remain compatible with older read-only source schemas.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_operating_expense_ownership'").fetchone():
        return False
    ownership = conn.execute('''SELECT ownership.source_sheet,ownership.snapshot_json
        FROM erp_operating_expense_ownership ownership JOIN payment_requests request
            ON ownership.logical_request_id=COALESCE(NULLIF(request.logical_request_id,0),request.id)
        WHERE request.id=?''', (request_id,)).fetchone()
    if not ownership or ownership['source_sheet'] not in sheets:
        return False
    frozen = json.loads(ownership['snapshot_json'])
    return any(file_url == attachment.get('url') or any(file_url == evidence.get('url')
               for evidence in attachment.get('provenance', [])) for attachment in frozen['attachments'])


def expense_file(file_id, sheets, source_type='operation', kind='attachment'):
    source_reader = {'operation': operation_source, 'purchase': purchase_source}[source_type]
    query = ('SELECT a.*, r.source_sheet, r.raw_extra_json FROM attachment_links a JOIN payment_requests r ON r.id=a.request_id WHERE a.id=?'
             if kind == 'attachment' else
             'SELECT v.*, r.id AS request_id, r.source_sheet, r.raw_extra_json FROM payment_vouchers v JOIN payment_records p ON p.id=v.payment_id JOIN payment_requests r ON r.id=p.request_id WHERE v.id=?')
    missing = 'Attachment not found' if kind == 'attachment' else 'Payment proof not found'
    with read_database() as conn:
        row = conn.execute(query,(file_id,)).fetchone()
        allowed = bool(row and row['source_sheet'] in sheets and source_reader(dict(row))
                       and logical_request_allowed(conn,row['request_id'],sheets,source_type))
        if not allowed and row and source_type == 'operation':
            file_url = f"/api/integrations/erp/{'attachments' if kind == 'attachment' else 'payment-vouchers'}/{file_id}"
            allowed = claimed_operating_file_allowed(conn, row['request_id'], sheets, file_url)
        if not allowed:
            raise HTTPException(404,missing)
        try:
            path, _ = resolve_attachment_path(row,conn)
        except (ValueError,sqlite3.Error):
            path = None
        if path is None:
            raise HTTPException(404,missing)
    return FileResponse(path,filename=row['original_filename'] or path.name)


@router.get('/attachments/{attachment_id}')
def attachment_file(attachment_id: int, sheets=Depends(export_scope)):
    return expense_file(attachment_id, sheets)


@router.get('/payment-vouchers/{voucher_id}')
def payment_voucher_file(voucher_id: int, sheets=Depends(export_scope)):
    return expense_file(voucher_id, sheets, kind='payment-voucher')


@router.get('/purchase-expenses/attachments/{attachment_id}')
def purchase_attachment_file(attachment_id: int, sheets=Depends(export_scope)):
    return expense_file(attachment_id, sheets, 'purchase')


@router.get('/purchase-expenses/payment-vouchers/{voucher_id}')
def purchase_payment_voucher_file(voucher_id: int, sheets=Depends(export_scope)):
    return expense_file(voucher_id, sheets, 'purchase', 'payment-voucher')
