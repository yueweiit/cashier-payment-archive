import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import db


@pytest.fixture
def export_client(tmp_path, monkeypatch):
    path = tmp_path / 'export.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN', 'test-export-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', '["运营"]')
    with sqlite3.connect(path) as conn:
        conn.executescript('''
        CREATE TABLE payment_requests(id INTEGER, logical_request_id INTEGER, source_sheet TEXT,
        raw_extra_json TEXT, applicant TEXT, payee_name TEXT, summary TEXT, amount REAL, currency TEXT,
        needed_payment_date TEXT, payment_status TEXT, paid_amount REAL, created_at TEXT, updated_at TEXT);
        CREATE TABLE payment_records(id INTEGER, request_id INTEGER, root_payment_id INTEGER, amount REAL,
        payment_date TEXT, payment_account TEXT, bank_reference TEXT, source_type TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE attachment_links(id INTEGER, request_id INTEGER, source_attachment_id TEXT, source_system TEXT,
        source_instance_id TEXT, original_filename TEXT, file_path TEXT, url_path TEXT, created_at TEXT);
        ''')
        external = {'system':'dingtalk_expense_database','source_type':'operation','record_id':'abc',
                    'application_date':'2026-01-01','申请类型':'付款','approval_status':'COMPLETED','approval_result':'agree'}
        for rid, logical, sheet, typ, date in [(1,1,'运营','operation','2026-01-01'),(2,1,'运营','operation','2026-01-01'),
              (3,3,'运营','purchase','2026-01-01'),(4,4,'秘密','operation','2026-01-01'),(5,5,'运营','operation','2025-01-01')]:
            meta = dict(external, source_type=typ, application_date=date)
            if rid == 5:
                meta.pop('申请类型')
                meta['approval_result'] = None
            conn.execute('INSERT INTO payment_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (rid,logical,sheet,json.dumps({'external_source':meta}),'张三','供应商','房租',100,'CNY',None,'待支付',90,'2026-01-01T00:00:00Z',f'2026-01-0{rid}T00:00:00Z'))
        for pid, rid in [(10,1),(11,2)]:
            conn.execute('INSERT INTO payment_records VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (pid,rid,10,20,'2026-01-10','银行','REF','manual','2026-01-10T00:00:00Z','2026-01-10T00:00:00Z'))
        conn.execute("INSERT INTO attachment_links VALUES(1,2,'trusted','dingtalk',NULL,'附件.pdf',NULL,'https://evil.test/secret','2026-01-11T00:00:00Z')")
    app = FastAPI()
    from backend.app.erp_export import router
    app.include_router(router)
    return TestClient(app), path


def get(client, **params):
    return client.get('/api/integrations/erp/operating-expenses', params=params,
                      headers={'Authorization':'Bearer test-export-token'})


@pytest.fixture
def native_type_client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'native-type.db')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN','test-export-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS','["运营"]')
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(101,'native-type','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO payment_requests(id,logical_request_id,batch_id,source_sheet,amount,currency,created_at,updated_at) VALUES(2,1,101,'运营',100,'CNY','2026-01-01','2026-01-01')")
    conn.close()
    from backend.app.erp_export import router
    app=FastAPI()
    app.include_router(router)
    return TestClient(app),db.DB_PATH


def test_authentication_and_explicit_scope(export_client, monkeypatch):
    client, _ = export_client
    assert client.get('/api/integrations/erp/operating-expenses').status_code == 401
    monkeypatch.delenv('PAYMENT_ERP_EXPORT_TOKEN')
    assert get(client).status_code == 503
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN','test-export-token')
    monkeypatch.delenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS')
    assert get(client).status_code == 503


def test_dedup_type_evidence_and_read_only(export_client):
    client, path = export_client
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = get(client).json()
    assert result['schema_version'] == 1
    assert [i['source_id'] for i in result['items']] == ['5','1']
    older, item = result['items']
    assert older['application_type'] == 'unclassified'
    assert older['approvals']['eligibility'] == 'blocked'
    assert item['application_type'] == 'payment'
    assert item['source_request_id'] == '2'
    assert item['source_company'] is None
    assert item['amount'] == '100'
    assert item['paid_amount'] == '20'
    assert [p['source_id'] for p in item['payments']] == ['10']
    assert item['attachments'] == []  # Arbitrary remote URLs are never exported.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_incremental_payment_change_cursor_and_older_unpaid(export_client):
    client, path = export_client
    first = get(client,limit=1,date_from='2026-01-01',date_to='2026-12-31').json()
    assert first['items'][0]['source_id'] == '5'
    second = get(client,cursor=first['next_cursor'],limit=1,date_from='2026-01-01',date_to='2026-12-31').json()
    assert second['items'][0]['source_id'] == '1'
    assert second['until'] == first['until'] and second['end']
    assert get(client,changed_since='2026-01-10T00:00:00Z').json()['items'][0]['source_id'] == '1'
    assert get(client,cursor=first['next_cursor']+'x').status_code == 400
    assert get(client,limit=501).status_code == 422
    assert client.get('/api/integrations/erp/attachments/1',headers={'Authorization':'Bearer test-export-token'}).status_code == 404


def test_mapper_preserves_exact_form_type_and_company():
    from backend.app.external_expenses import map_external_expense
    mapped = map_external_expense({'source_type':'operation','source_id':'8','approval_no':'DT8',
        'raw_data':{'formComponentValues':[{'name':'申请类型','value':'报销'},
            {'name':'付款公司','value':'真实法人'}]}})
    source = mapped['request_data']['raw_extra']['external_source']
    assert source['application_type_raw'] == '报销'
    assert source['source_company_raw'] == '真实法人'


def test_preserved_fields_are_exported(export_client):
    client, path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'].pop('申请类型')
        raw['external_source'].update(application_type_raw='报销',source_company_raw='真实法人')
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    item = get(client).json()['items'][1]
    assert item['application_type'] == 'reimbursement'
    assert item['source_company'] == '真实法人'


def test_local_attachment_scope_and_version(export_client, tmp_path, monkeypatch):
    from backend.app import file_storage
    client, path = export_client
    monkeypatch.setattr(file_storage,'DATA_DIR',tmp_path)
    (tmp_path / 'proof.pdf').write_bytes(b'proof')
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE attachment_links SET file_path='proof.pdf' WHERE id=1")
        conn.execute("INSERT INTO attachment_links VALUES(2,4,'secret','dingtalk',NULL,'secret.pdf','proof.pdf','', '2026-01-12T00:00:00Z')")
    item = get(client).json()['items'][1]
    assert item['attachments'][0]['url'] == '/api/integrations/erp/attachments/1'
    assert len(item['attachments'][0]['version']) == 64
    assert client.get(item['attachments'][0]['url'],headers={'Authorization':'Bearer test-export-token'}).content == b'proof'
    assert client.get('/api/integrations/erp/attachments/2',headers={'Authorization':'Bearer test-export-token'}).status_code == 404
    assert client.get(item['attachments'][0]['url']).status_code == 401


def test_payment_only_update_and_watermark_no_lost_change(export_client):
    client, path = export_client
    before = get(client,until='2026-01-12T00:00:00Z').json()['items'][1]['version']
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_records SET amount=30,updated_at='2026-01-13T00:00:00Z' WHERE id=11")
    assert get(client,until='2026-01-12T00:00:00Z').json()['items'][0]['source_id'] == '5'
    updated = get(client,changed_since='2026-01-12T00:00:00Z').json()['items'][0]
    assert updated['version'] != before
    assert updated['paid_amount'] == '30'


def test_payment_keeps_own_parent_currency_and_blocks_mixed_totals(export_client):
    client,path = export_client
    before=get(client).json()['items'][1]['payments'][0]['version']
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET currency='MXN' WHERE id=2")
        conn.execute("UPDATE payment_records SET amount=80,updated_at='2026-01-12T00:00:00Z' WHERE id=10")
    item=get(client).json()['items'][1]
    assert item['currency']=='MXN'
    assert item['payments'][0]['currency']=='CNY' and item['payments'][0]['amount']=='80'
    assert item['payments'][0]['version']!=before
    assert item['approvals']['eligibility']=='blocked'
    assert item['paid_amount'] is None and item['pending_amount'] is None


def test_root_copy_currency_disagreement_stays_blocked_when_latest_matches(export_client):
    client,path=export_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET currency='MXN' WHERE id=1")
        conn.execute("UPDATE payment_records SET updated_at='2026-01-12T00:00:00Z' WHERE id=11")
    item=get(client).json()['items'][1]
    assert item['payments'][0]['currency']=='CNY'
    assert item['approvals']['eligibility']=='blocked'
    assert item['paid_amount'] is None and item['currency_conflict']


@pytest.mark.parametrize('status,result', [('RUNNING','agree'),('COMPLETED','refuse'),('TERMINATED','agree'),('COMPLETED',None)])
def test_unknown_and_rejected_approvals_block(export_client,status,result):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'].update(approval_status=status,approval_result=result)
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    assert get(client).json()['items'][1]['approvals']['eligibility'] == 'blocked'


def test_legacy_summary_not_confirmed_payment(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_records SET source_type='excel_summary'")
    item = get(client).json()['items'][1]
    assert item['paid_amount'] == '0'
    assert item['payments'][0]['evidence_status'] == 'unverified_summary'


def test_attachment_delete_change_is_incremental(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE audit_logs(entity_type TEXT, old_value_json TEXT, new_value_json TEXT, created_at TEXT)')
        conn.execute("DELETE FROM attachment_links WHERE id=1")
        conn.execute('INSERT INTO audit_logs VALUES(?,?,?,?)',('attachment',json.dumps({'request_id':2}),None,'2026-01-20T00:00:00Z'))
    item = get(client,changed_since='2026-01-19T00:00:00Z').json()['items'][0]
    assert item['source_id'] == '1' and item['updated_at'] == '2026-01-20T00:00:00.000000Z'


def test_newest_copy_cannot_revert_to_stale_operation(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source']['source_type'] = 'purchase'
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    assert [i['source_id'] for i in get(client).json()['items']] == ['5']


def test_read_database_forbids_writes_and_missing_database_creation(export_client,tmp_path,monkeypatch):
    from backend.app.erp_export import read_database
    with read_database() as conn:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            conn.execute('DELETE FROM payment_requests')
    client,_ = export_client
    missing = tmp_path / 'missing.db'
    monkeypatch.setattr(db,'DB_PATH',missing)
    assert get(client).status_code == 503
    assert not missing.exists()


def test_same_second_chronological_filter(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE attachment_links SET created_at='2026-01-11T00:00:00.100Z'")
    assert get(client,changed_since='2026-01-11T00:00:00Z',until='2026-01-11T00:00:00.200Z').json()['items'][0]['source_id'] == '1'


def test_payment_proof_export_and_acl(export_client,tmp_path,monkeypatch):
    from backend.app import file_storage
    client,path = export_client
    monkeypatch.setattr(file_storage,'DATA_DIR',tmp_path)
    (tmp_path/'proof.pdf').write_bytes(b'payment-proof')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE payment_vouchers(id INTEGER,payment_id INTEGER,file_path TEXT,original_filename TEXT,created_at TEXT)')
        conn.execute("INSERT INTO payment_vouchers VALUES(1,10,'proof.pdf','proof.pdf','2026-01-21T00:00:00Z')")
    item = get(client,changed_since='2026-01-20T00:00:00Z').json()['items'][0]
    proof = item['attachments'][0]
    assert proof['source_id'].startswith('payment-voucher:')
    assert client.get(proof['url'],headers={'Authorization':'Bearer test-export-token'}).content == b'payment-proof'
    assert client.get(proof['url']).status_code == 401


def test_conflicting_roots_block_eligibility(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_records SET amount=90 WHERE id=11")
    item = get(client).json()['items'][1]
    assert item['approvals']['eligibility'] == 'blocked'
    assert item['paid_amount'] is None


def test_deleted_payment_proof_is_incremental(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE audit_logs(entity_type TEXT,old_value_json TEXT,new_value_json TEXT,created_at TEXT)')
        conn.execute('INSERT INTO audit_logs VALUES(?,?,?,?)',('payment_voucher',json.dumps({'payment_id':10}),None,'2026-01-22T00:00:00Z'))
    assert get(client,changed_since='2026-01-21T00:00:00Z').json()['items'][0]['source_id'] == '1'


def test_export_does_not_round_stored_amount(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE payment_requests SET amount=? WHERE id=2',(1.2345678901234567,))
    assert get(client).json()['items'][1]['amount'] == '1.2345678901234567'


def test_latest_copy_scope_checked_before_export_and_download(export_client,tmp_path,monkeypatch):
    client,path = export_client
    from backend.app import file_storage
    monkeypatch.setattr(file_storage,'DATA_DIR',tmp_path)
    (tmp_path/'proof.pdf').write_bytes(b'proof')
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET source_sheet='秘密' WHERE id=2")
        conn.execute("UPDATE attachment_links SET request_id=1,file_path='proof.pdf' WHERE id=1")
    assert [i['source_id'] for i in get(client).json()['items']] == ['5']
    assert client.get('/api/integrations/erp/attachments/1',headers={'Authorization':'Bearer test-export-token'}).status_code == 404


def test_exact_source_id_freshness_filter(export_client):
    client,path = export_client
    assert [i['source_id'] for i in get(client,source_id='1').json()['items']] == ['1']
    assert get(client,source_id='missing').json()['items'] == []
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM payment_requests WHERE logical_request_id=1')
    assert get(client,source_id='1').json()['items'] == []


def test_payment_proof_links_to_root_payment(export_client,tmp_path,monkeypatch):
    client,path = export_client
    from backend.app import file_storage
    monkeypatch.setattr(file_storage,'DATA_DIR',tmp_path)
    (tmp_path/'proof.pdf').write_bytes(b'proof')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE payment_vouchers(id INTEGER,payment_id INTEGER,file_path TEXT,original_filename TEXT,created_at TEXT)')
        conn.execute("INSERT INTO payment_vouchers VALUES(1,11,'proof.pdf','proof.pdf','2026-01-21T00:00:00Z')")
    assert get(client).json()['items'][1]['attachments'][0]['payment_source_id'] == '10'


def test_original_approval_identity_and_trusted_url(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'].update(approval_no='DT-8',workflow_url='https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=8')
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    item = get(client).json()['items'][1]
    assert item['approval_no'] == 'DT-8'
    assert item['original_url'].startswith('https://aflow.dingtalk.com/')


def test_raw_source_amount_preserved_separate_from_manual_amount(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'].update(source_amount='100.000000000000000001',source_currency_raw=' CNY ')
        conn.execute('UPDATE payment_requests SET amount=120,raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    item = get(client).json()['items'][1]
    assert item['amount'] == '120'
    assert item['original_source_amount'] == '100.000000000000000001'
    assert item['original_source_currency'] == ' CNY '
    assert item['storage_precision_warning']


def test_equal_time_logical_identity_conflict_blocks(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source']['record_id']='conflicting-identity'
        conn.execute("UPDATE payment_requests SET updated_at='2026-01-02T00:00:00Z',raw_extra_json=? WHERE id=2",(json.dumps(raw),))
        conn.execute("UPDATE payment_requests SET updated_at='2026-01-02T00:00:00Z' WHERE id=1")
    assert get(client).json()['items'][1]['approvals']['eligibility'] == 'blocked'


@pytest.mark.parametrize('field,value',[('record_id','other-source'),('source_company_raw','另一法人')])
def test_unequal_time_immutable_identity_conflict_blocks(export_client,field,value):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw = json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'][field]=value
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    item = get(client).json()['items'][1]
    assert item['source_conflict']
    assert item['approvals']['eligibility']=='blocked'


def test_payment_payer_and_remark_affect_evidence_version(export_client):
    client,path = export_client
    before = get(client).json()['items'][1]['payments'][0]['version']
    with sqlite3.connect(path) as conn:
        conn.execute('ALTER TABLE payment_records ADD COLUMN payer TEXT')
        conn.execute('ALTER TABLE payment_records ADD COLUMN remark TEXT')
        conn.execute("UPDATE payment_records SET payer='出纳',remark='尾款',updated_at='2026-01-12T00:00:00Z' WHERE id=10")
    payment = get(client).json()['items'][1]['payments'][0]
    assert payment['payer']=='出纳' and payment['remark']=='尾款'
    assert payment['version'] != before
    assert payment['source_request_id']=='1' and len(payment['provenance'])==2


def test_mapper_raw_form_whitespace_is_preserved():
    from backend.app.external_expenses import map_external_expense
    mapped = map_external_expense({'source_type':'operation','source_id':'8','raw_data':{
        'formComponentValues':[{'name':'申请类型','value':' 报销 '} ]}})
    assert mapped['request_data']['raw_extra']['external_source']['application_type_raw']==' 报销 '


@pytest.mark.parametrize('value,expected',[
    ('付款申请','payment'),('费用报销','reimbursement'),
    ('Solicitud de pago','payment'),('Reembolso de gastos','reimbursement'),
    ('付款申请Solicitud de pago','payment'),('付款申请 Solicitud de pago','payment'),
    ('费用报销Reembolso de gastos','reimbursement'),('费用报销 Reembolso de gastos','reimbursement'),
    (' 费用报销  Reembolso   de gastos ','reimbursement'),
    ('付款申请等待确认','unclassified'),('业务Solicitud de pago说明','unclassified')])
def test_actual_user_type_labels_http(native_type_client,value,expected):
    from backend.app.external_expenses import map_external_expense
    client,path=native_type_client
    mapped=map_external_expense({'source_type':'operation','source_id':'abc','raw_data':{
        'formComponentValues':[{'name':'申请类型Tipo de trámite','value':value}]}})
    source=mapped['request_data']['raw_extra']['external_source']
    assert source['application_type_raw']==value
    source.update(source_type='operation',approval_status='COMPLETED',approval_result='agree')
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps({'external_source':source}),))
    item=get(client,source_id='1').json()['items'][0]
    assert item['application_type']==expected and item['application_type_raw']==value


@pytest.mark.parametrize('name',['申请类型Tipo de trámite','申请类型 Tipo de trámite',' 申请类型  Tipo de trámite '])
def test_bilingual_form_label_in_original_metadata(export_client,name):
    client,path=export_client
    with sqlite3.connect(path) as conn:
        raw=json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source'].pop('申请类型')
        raw['external_source']['formComponentValues']=[{'name':name,'value':'费用报销Reembolso de gastos'}]
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    assert get(client,source_id='1').json()['items'][0]['application_type']=='reimbursement'


def test_mapper_keeps_original_amount_precision():
    from backend.app.external_expenses import map_external_expense
    mapped=map_external_expense({'source_type':'operation','source_id':'8','source_amount':'100.000000000000000001'})
    assert mapped['request_data']['raw_extra']['external_source']['original_source_amount_raw']=='100.000000000000000001'


def test_native_schema_rollover_proof_identity_and_readonly(tmp_path,monkeypatch):
    from backend.app import file_storage
    from backend.app import main
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'native.db')
    monkeypatch.setattr(file_storage,'DATA_DIR',tmp_path)
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN','test-export-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS','["运营"]')
    db.init_db()
    (tmp_path/'proof.pdf').write_bytes(b'native-proof')
    raw = json.dumps({'external_source':{'system':'dingtalk_expense_database','source_type':'operation','record_id':'native','approval_status':'COMPLETED','approval_result':'agree'}})
    with db.connect() as conn:
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(101,'native','2026-01-01','2026-01-01')")
        for rid in (101,102):
            conn.execute('INSERT INTO payment_requests(id,logical_request_id,batch_id,source_sheet,amount,currency,raw_extra_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(rid,101,101,'运营',100,'CNY',raw,'2026-01-01','2026-01-01'))
        conn.execute("INSERT INTO payment_records(id,request_id,root_payment_id,amount,payer,remark,source_type,payment_date,created_at,updated_at) VALUES(101,101,101,20,'出纳','尾款','manual','2026-01-10','2026-01-10','2026-01-10')")
        conn.execute("INSERT INTO payment_vouchers(payment_id,file_path,original_filename,created_at) VALUES(101,'proof.pdf','proof.pdf','2026-01-10')")
    from backend.app.erp_export import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    before = get(client,source_id='101').json()['items'][0]
    with db.connect() as conn:
        main.copy_payment_records(conn,101,102,None)
    conn.close()  # Settle fixture writer's WAL before proving HTTP is read-only.
    from backend.app.erp_export import read_database
    with read_database() as conn:
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts_before={table:conn.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0] for table in tables}
    before_hash = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
    after = get(client,source_id='101').json()['items'][0]
    assert len(after['payments'])==1 and len(after['attachments'])==1
    assert after['attachments'][0]['source_id']==before['attachments'][0]['source_id']
    assert after['attachments'][0]['version']==before['attachments'][0]['version']
    assert after['payments'][0]['version']==before['payments'][0]['version']
    assert after['attachments'][0]['payment_source_id']=='101'
    assert after['approvals']['eligibility']=='eligible' and not after['source_conflict']
    assert hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()==before_hash
    with read_database() as conn:
        assert {table:conn.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0] for table in tables}==counts_before


def test_original_url_discards_tokens_and_unrelated_query(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw=json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=2').fetchone()[0])
        raw['external_source']['workflow_url']='https://aflow.dingtalk.com/a?procInstId=8&access_token=secret-test-value#secret'
        conn.execute('UPDATE payment_requests SET raw_extra_json=? WHERE id=2',(json.dumps(raw),))
    item=get(client).json()['items'][1]
    assert item['original_url']=='https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=8'
    assert 'secret-test-value' not in json.dumps(item)


def test_unallowed_copy_identity_change_has_incremental_timestamp(export_client):
    client,path = export_client
    with sqlite3.connect(path) as conn:
        raw=json.loads(conn.execute('SELECT raw_extra_json FROM payment_requests WHERE id=1').fetchone()[0])
        raw['external_source']['record_id']='changed-conflicting-identity'
        conn.execute("UPDATE payment_requests SET source_sheet='秘密',updated_at='2026-01-30T00:00:00Z',raw_extra_json=? WHERE id=1",(json.dumps(raw),))
    item=get(client,changed_since='2026-01-20T00:00:00Z').json()['items'][0]
    assert item['source_conflict'] and item['updated_at']=='2026-01-30T00:00:00.000000Z'


def seed_large_export(path):
    with sqlite3.connect(path) as conn:
        template=conn.execute('SELECT * FROM payment_requests WHERE id=2').fetchone()
        conn.executemany('INSERT INTO payment_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(rid,rid,*template[2:]) for rid in range(100,8100)])
        conn.executemany('INSERT INTO payment_records VALUES(?,?,?,?,?,?,?,?,?,?)',
            [(rid,rid,rid,1,'2026-01-10','银行','REF','manual','2026-01-10T00:00:00Z','2026-01-10T00:00:00Z') for rid in range(100,8100)])
        conn.executemany('INSERT INTO attachment_links VALUES(?,?,?,?,?,?,?,?,?)',
            [(rid,rid,str(rid),'dingtalk',None,'a.pdf',None,'https://evil.test','2026-01-11T00:00:00Z') for rid in range(100,8100)])


def test_exact_source_hydration_budget(export_client,monkeypatch):
    from contextlib import contextmanager
    from backend.app import erp_export
    client,path=export_client
    seed_large_export(path)
    original=erp_export.object_json
    original_read=erp_export.read_database
    queries=[]
    @contextmanager
    def traced_read():
        with original_read() as conn:
            conn.set_trace_callback(queries.append)
            yield conn
    monkeypatch.setattr(erp_export,'read_database',traced_read)
    loaded=[]
    def record(raw):
        loaded.append(raw)
        return original(raw)
    monkeypatch.setattr(erp_export,'object_json',record)
    assert get(client,source_id='1').json()['items'][0]['source_id']=='1'
    assert len(loaded)<30
    for table in ('payment_requests','payment_records','attachment_links'):
        selected=[q for q in queries if q.startswith('SELECT * FROM '+table)]
        assert selected and all('WHERE' in q for q in selected)


def test_eight_thousand_requests_have_bounded_page_hydration(export_client,monkeypatch):
    import time
    from backend.app import erp_export
    client,path=export_client
    seed_large_export(path)
    original=erp_export.attachment_item
    calls=[]
    def record(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(erp_export,'attachment_item',record)
    started=time.monotonic()
    result=get(client,limit=1).json()
    assert len(result['items'])==1
    assert len(calls)<=1
    assert time.monotonic()-started<4


def test_native_audit_heavy_export_uses_one_scan(tmp_path,monkeypatch,record_property):
    from contextlib import contextmanager
    import time
    from backend.app import erp_export
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'audit-native.db')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN','test-export-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS','["运营"]')
    db.init_db()
    raw=json.dumps({'external_source':{'system':'dingtalk_expense_database','source_type':'operation','record_id':'original','approval_status':'COMPLETED','approval_result':'agree'}})
    with db.connect() as conn:
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(101,'audit-heavy','2026-01-01','2026-01-01')")
    app=FastAPI()
    app.include_router(erp_export.router)
    client=TestClient(app)
    original_read=erp_export.read_database
    queries=[]
    @contextmanager
    def traced_read():
        with original_read() as conn:
            conn.set_trace_callback(queries.append)
            yield conn
    monkeypatch.setattr(erp_export,'read_database',traced_read)
    previous=0
    for count in (1000,4000,8000):
        with db.connect() as conn:
            conn.executemany('INSERT INTO payment_requests(id,logical_request_id,batch_id,source_sheet,amount,currency,raw_extra_json,created_at,updated_at) VALUES(?,?,101,\'运营\',100,\'CNY\',?,\'2026-01-01\',\'2026-01-01\')',
                [(rid,rid,raw) for rid in range(previous+101,count+101)])
            conn.executemany('INSERT INTO audit_logs(action,entity_type,old_value_json,created_at) VALUES(\'attachment.delete\',\'attachment\',?,\'2026-01-10\')',
                [(json.dumps({'request_id':rid}),) for rid in range(previous+101,count+101) for _ in range(8)])
        conn.close()
        queries.clear()
        started=time.monotonic()
        response=get(client,limit=1,changed_since='2026-01-05')
        elapsed=time.monotonic()-started
        record_property('audit_export_'+str(count)+'_seconds',round(elapsed,3))
        assert response.status_code==200 and len(response.json()['items'])==1
        audit_queries=[q for q in queries if 'FROM audit_logs' in q]
        assert len(audit_queries)==1
        queries.clear()
        exact=get(client,source_id='101')
        assert exact.status_code==200 and exact.json()['items'][0]['source_id']=='101'
        exact_audits=[q for q in queries if 'FROM audit_logs' in q]
        assert len(exact_audits)==1 and 'json_each' in exact_audits[0]
        previous=count
