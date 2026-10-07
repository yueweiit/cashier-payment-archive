"""ERP ownership is tested against the native schema and real SQLite writes."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import db
from backend.app.erp_export import router


HEADERS = {'Authorization': 'Bearer fake-takeover-token'}
PREFIX = '/api/integrations/erp/operating-expenses'


@pytest.fixture
def takeover_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'takeover.db')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN', 'fake-takeover-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', '["运营"]')
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(101,'fake-batch','2026-01-01','2026-01-01')")
        for rid, root in [(1, 1), (2, 1), (3, 3)]:
            external = {'system': 'dingtalk_expense_database', 'source_type': 'operation',
                        'record_id': f'fake-source-{root}', 'approval_no': f'FAKE-APPROVAL-{root}',
                        'process_instance_id': f'fake-process-{root}', 'lookup_status': 'matched',
                        'application_date': '2026-01-01', 'application_type_raw': '付款',
                        'approval_status': 'COMPLETED', 'approval_result': 'agree',
                        'legal_company_name': '测试法人', 'applicant_id': 'fake-user',
                        'applicant': '测试申请人', 'source_currency_raw': 'CNY', 'source_amount': '100'}
            conn.execute('''INSERT INTO payment_requests(
                id,logical_request_id,copied_from_request_id,batch_id,source_sheet,dingding_id,
                raw_extra_json,applicant,payee_name,summary,amount,paid_amount,pending_amount,
                currency,payment_status,finance_review,actual_payment_date,payer,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid, root, 1 if rid == 2 else None, 101, '运营', f'FAKE-APPROVAL-{root}',
                 json.dumps({'external_source': external}), '测试申请人', '测试收款人', '测试房租',
                 100, 20 if root == 1 else 0, 80 if root == 1 else 100, 'CNY',
                 '部分付款' if root == 1 else '未付款', '部分付款' if root == 1 else '未付款',
                 '2026-01-10' if root == 1 else None, '测试付款人' if root == 1 else None,
                 '2026-01-01', '2026-01-12'))
        for pid, rid in [(10, 1), (11, 2)]:
            conn.execute('''INSERT INTO payment_records(id,request_id,root_payment_id,amount,payment_date,
                payment_account,payer,bank_reference,source_type,created_at,updated_at)
                VALUES(?,?,10,20,'2026-01-10','测试银行','测试付款人','FAKE-REF','manual','2026-01-10','2026-01-10')''', (pid, rid))
        conn.execute('''INSERT INTO dingtalk_workflow_events(request_id,event_key,process_instance_id,
            stage_name,operator_name,event_time,result,comment,is_current,active,images_json,
            attachments_json,synced_at,created_at,updated_at)
            VALUES(2,'fake-approved','fake-process-1','财务审批','测试审批人','2026-01-08','agree',
            '测试意见',1,1,'[]','[]','2026-01-12','2026-01-12','2026-01-12')''')
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db.DB_PATH


def preview(client, source_id='1', **body):
    return client.post(PREFIX + '/takeover-preview', headers=HEADERS,
                       json={'source_id': source_id, **body})


def claim(client, item, request_id='fake-request-1', **body):
    return client.post(PREFIX + '/takeover-claim', headers=HEADERS,
                       json={'source_id': item['source_id'], 'expected_version': item['version'],
                             'request_id': request_id, **body})


def preview_item(client, source_id='1', **body):
    response = preview(client, source_id, **body)
    assert response.status_code == 200, response.text
    return response.json()['items'][0]


def test_preview_reuses_complete_exact_root_without_writing(takeover_client):
    client, path = takeover_client
    before = path.read_bytes()
    response = preview(client)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['schema_version'] == 1 and payload['source_system'] == 'cashier-payment-archive'
    item = payload['items'][0]
    assert item['source_id'] == '1' and item['source_request_id'] == '2'
    assert item['amount'] == '100' and item['paid_amount'] == '20' and item['pending_amount'] == '80'
    assert [payment['source_id'] for payment in item['payments']] == ['10']
    assert len(item['payments'][0]['provenance']) == 2
    assert item['approval_no'] == 'FAKE-APPROVAL-1' and len(item['version']) == 64
    assert path.read_bytes() == before


def test_claim_is_opt_in_authenticated_and_requires_preview_version(takeover_client, monkeypatch):
    client, _ = takeover_client
    item = preview_item(client)
    monkeypatch.delenv('PAYMENT_ERP_TAKEOVER_ENABLED')
    assert claim(client, item).status_code == 503
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    assert client.post(PREFIX + '/takeover-claim', json={'source_id': '1', 'request_id': 'fake-request'}).status_code == 401
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS,
                       json={'source_id': '1', 'request_id': 'fake-request'}).status_code == 422
    assert claim(client, item, expected_version='0' * 64).status_code == 409


def test_claim_retries_return_frozen_history_and_root_scoped_request_ids(takeover_client):
    client, path = takeover_client
    item = preview_item(client)
    response = claim(client, item)
    assert response.status_code == 200, response.text
    frozen = response.json()
    takeover = frozen['items'][0]['takeover']
    assert takeover['owner'] == 'deeplinkerp' and takeover['claim_token'] and takeover['claimed_at']
    assert len(takeover['history_fingerprint']) == 64
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET general_manager_opinion='接管后审批意见',updated_at='2026-01-20' WHERE id=2")
    assert claim(client, item).json() == frozen
    assert claim(client, item, request_id='different-request').status_code == 409
    assert preview(client, '01').status_code == 404
    assert claim(client, item, source_id='01').status_code == 404
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO payment_records(id,request_id,root_payment_id,amount,payment_date,payer,payment_account,source_type,created_at,updated_at) VALUES(12,3,12,10,'2026-01-10','测试付款人','测试银行','manual','2026-01-10','2026-01-10')")
        conn.execute("UPDATE payment_requests SET paid_amount=10,pending_amount=90,actual_payment_date='2026-01-10',payer='测试付款人' WHERE id=3")
    other = preview_item(client, '3')
    assert claim(client, other).status_code == 200
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM payment_records').fetchone()[0] == 3


@pytest.mark.parametrize('origin', ['native_zero', 'missing_excel_column', 'explicit_excel_zero', 'legacy_null_migration'])
def test_zero_summary_without_verified_complete_history_is_not_takeover_evidence(takeover_client, origin):
    from backend.app.excel_io import normalize_request_business_fields
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if origin in {'missing_excel_column', 'explicit_excel_zero'}:
            imported = {'amount': 100}
            if origin == 'explicit_excel_zero':
                imported['paid_amount'] = 0
            normalize_request_business_fields(imported)
            assert imported['paid_amount'] == 0 and imported['pending_amount'] == 100
            conn.execute('UPDATE payment_requests SET paid_amount=?,pending_amount=? WHERE id=3',
                         (imported['paid_amount'], imported['pending_amount']))
        elif origin == 'legacy_null_migration':
            conn.execute('UPDATE payment_requests SET paid_amount=NULL WHERE id=3')
            db.migrate_payment_amounts(conn)
        assert conn.execute('SELECT paid_amount,pending_amount FROM payment_requests WHERE id=3').fetchone()['paid_amount'] == 0
    response = preview(client, '3')
    assert response.status_code == 409, response.text
    assert 'zero' in response.json()['detail'].lower()
    response = client.post(PREFIX + '/takeover-claim', headers=HEADERS, json={
        'source_id': '3', 'expected_version': '0' * 64, 'request_id': 'fake-zero-claim'})
    assert response.status_code == 409
    assert 'zero' in response.json()['detail'].lower()
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM erp_operating_expense_ownership').fetchone()[0] == 0


def test_explicit_finance_zero_attestation_is_versioned_frozen_and_audited(takeover_client):
    client, path = takeover_client
    confirmation = {'zero_history_confirmed': True, 'confirmed_by': 'fake-finance@example.invalid'}
    item = preview_item(client, '3', **confirmation)
    assert item == preview_item(client, '3', **confirmation)
    assert item['paid_amount'] == '0' and item['pending_amount'] == '100' and item['payments'] == []
    assert item['zero_history_attestation'] == {
        'method': 'explicit_erp_finance_confirmation', 'confirmed_by': confirmation['confirmed_by'], 'verified_zero': True}
    response = claim(client, item, **confirmation)
    assert response.status_code == 200, response.text
    frozen = response.json()
    attestation = frozen['items'][0]['zero_history_attestation']
    assert attestation['confirmed_at'] == frozen['items'][0]['takeover']['claimed_at']
    assert claim(client, item, **confirmation).json() == frozen
    assert claim(client, item).status_code == 409
    assert claim(client, item, zero_history_confirmed=True, confirmed_by='other-finance@example.invalid').status_code == 409
    with sqlite3.connect(path) as conn:
        audit = conn.execute("SELECT new_value_json FROM audit_logs WHERE action='erp.operating_expense.takeover_claim'").fetchall()
        assert len(audit) == 1
        assert json.loads(audit[0][0])['confirmed_by'] == confirmation['confirmed_by']
        assert json.loads(audit[0][0])['zero_history_attestation'] == attestation
        assert conn.execute('SELECT COUNT(*) FROM payment_records').fetchone()[0] == 2


def test_changed_zero_confirmation_operator_cannot_claim_preview_version(takeover_client):
    client, path = takeover_client
    item = preview_item(client, '3', zero_history_confirmed=True, confirmed_by='fake-finance-a@example.invalid')
    other = preview_item(client, '3', zero_history_confirmed=True, confirmed_by='fake-finance-b@example.invalid')
    assert item['version'] != other['version']
    assert claim(client, item, zero_history_confirmed=True, confirmed_by='fake-finance-b@example.invalid').status_code == 409
    assert claim(client, item, zero_history_confirmed=False, confirmed_by='fake-finance-a@example.invalid').status_code == 409
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM erp_operating_expense_ownership').fetchone()[0] == 0


@pytest.mark.parametrize('confirmation', [1, 0, 'true', 'false'])
def test_zero_history_confirmation_must_be_a_strict_boolean(takeover_client, confirmation):
    client, _ = takeover_client
    assert preview(client, '3', zero_history_confirmed=confirmation,
                   confirmed_by='fake-finance@example.invalid').status_code == 422


@pytest.mark.parametrize('operator', [None, '   '])
def test_zero_history_confirmation_requires_attributed_operator(takeover_client, operator):
    client, _ = takeover_client
    assert preview(client, '3', zero_history_confirmed=True, confirmed_by=operator).status_code == 409


def test_zero_confirmation_cannot_override_recorded_positive_payments(takeover_client):
    client, _ = takeover_client
    assert preview(client, zero_history_confirmed=True, confirmed_by='fake-finance@example.invalid').status_code == 409


def test_legacy_claim_with_corp_but_unknown_instance_preserves_v1_compatibility(takeover_client):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET raw_extra_json=json_set(json_remove(raw_extra_json,'$.external_source.process_instance_id'),'$.external_source.corp_id','fake-corp') WHERE logical_request_id=1")
    item = preview_item(client)
    assert claim(client, item).status_code == 200
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT corp_id,process_instance_id FROM erp_operating_expense_ownership').fetchone() == (None, None)


@pytest.mark.parametrize('change', [None, 'amount', 'currency', 'company', 'applicant_id', 'applicant_name', 'type', 'payment_comment', 'manual_applicant'])
def test_numeric_v2_takeover_keeps_real_cashier_history_and_returns_canonical_identity(takeover_client, monkeypatch, change):
    from backend.app import erp_export
    client, path = takeover_client
    label, company = '悦为智能 YW Tech_Ai', '悦为智能技术（东莞）有限公司'
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', json.dumps(['运营', label]))
    monkeypatch.setenv('PAYMENT_ERP_OPERATING_OA_SCOPE', json.dumps({'corp_id': 'fake-corp', 'process_codes': ['fake-template'],
        'execution_region': '中国', 'year': 2026, 'source_sheet': label}))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET source_sheet=?,raw_extra_json=json_set(raw_extra_json,'$.external_source.corp_id','fake-corp','$.external_source.legal_company_name',?) WHERE logical_request_id=1", (label, company))
        if change == 'manual_applicant':
            conn.execute("UPDATE payment_requests SET applicant='人工确认姓名' WHERE logical_request_id=1")
    source = {'instances': [{'corp_id': 'fake-corp', 'process_instance_id': 'fake-process-1', 'process_code': 'fake-template',
            'effective_date': '2026-01-01', 'execution_region': '中国', 'status': 'COMPLETED', 'result': 'agree',
            'source_amount': '100', 'source_currency': 'CNY', 'source_company_raw': company, 'application_type_raw': '付款',
            'originator_user_id': 'fake-user', 'task_evidence_complete': True,
            'operation_records': [], 'tasks': [], 'updated_at': '2026-01-12T00:00:00Z'}], 'user_names': {'fake-user': '测试申请人'}}
    changes = {'amount': ('source_amount', '101'), 'currency': ('source_currency', 'USD'),
        'company': ('source_company_raw', '广州凌翔电子产品有限公司'), 'applicant_id': ('originator_user_id', 'another-user'),
        'type': ('application_type_raw', '报销')}
    if change in changes:
        field, value = changes[change]
        source['instances'][0][field] = value
    elif change == 'applicant_name':
        source['user_names']['fake-user'] = '另一申请人'
    elif change == 'payment_comment':
        source['instances'][0]['operation_records'] = [{'activityId': 'finance', 'showName': '财务审批',
            'type': 'EXECUTE_TASK_NORMAL', 'userId': 'finance-user', 'result': 'AGREE',
            'date': '2026-01-11T00:00:00Z', 'remark': '已支付100元'}]
    monkeypatch.setattr(erp_export, 'fetch_operating_workflow_sources', lambda identities, scopes: source)
    identity = {'source_id': '1', 'corp_id': 'fake-corp', 'process_instance_id': 'fake-process-1'}
    response = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=identity)
    if change not in {None, 'manual_applicant'}:
        assert response.status_code == 409
        return
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert response.json()['schema_version'] == 2
    assert item['source_id'] == 'oa:' + erp_export.digest(['fake-corp', 'fake-process-1'])
    assert item['source_request_id'] == '1' and item['paid_amount'] == '20' and item['pending_amount'] == '80'
    assert [payment['amount'] for payment in item['payments']] == ['20']
    if change == 'manual_applicant':
        assert item['applicant'] == '人工确认姓名'
    response = client.post(PREFIX + '/takeover-claim', headers=HEADERS, json={**identity,
        'expected_version': item['version'], 'expected_eligibility_fingerprint': item['payment_eligibility']['evidence_fingerprint'],
        'request_id': 'fake-v2-real-history'})
    assert response.status_code == 200, response.text


def test_legacy_claimed_zero_snapshot_without_attestation_cannot_be_replayed(takeover_client):
    client, path = takeover_client
    legacy = {'source_id': '3', 'source_sheet': '运营', 'payments': [], 'paid_amount': '0',
              'pending_amount': '100', 'version': '0' * 64}
    with sqlite3.connect(path) as conn:
        conn.execute('''INSERT INTO erp_operating_expense_ownership(logical_request_id,external_source_id,source_sheet,
            owner,actor_fingerprint,takeover_request_id,claim_token,claimed_at,history_fingerprint,snapshot_json)
            VALUES(3,'fake-source-3','运营','deeplinkerp','old-actor','fake-old-request','fake-old-token',
            '2026-01-12','fake-old-fingerprint',?)''', (json.dumps(legacy),))
    assert preview(client, '3').status_code == 409
    assert preview(client, '3', zero_history_confirmed=True, confirmed_by='fake-finance@example.invalid').status_code == 409


@pytest.mark.parametrize('operator', [1, 'x' * 141])
def test_zero_confirmation_operator_is_strict_and_bounded(takeover_client, operator):
    client, _ = takeover_client
    assert preview(client, '3', zero_history_confirmed=True, confirmed_by=operator).status_code == 422


@pytest.mark.parametrize('sql', [
    'UPDATE payment_requests SET amount=101 WHERE id=2',
    "UPDATE payment_requests SET currency='MXN' WHERE id=1",
    'UPDATE payment_requests SET paid_amount=21 WHERE id=2',
    'UPDATE payment_requests SET logical_request_id=3 WHERE id=2',
    "UPDATE payment_requests SET dingding_id='OTHER' WHERE id=2",
    "UPDATE payment_requests SET payee_account='OTHER' WHERE id=2",
    "UPDATE payment_requests SET payable_item_key='OTHER' WHERE id=2",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.beneficiary','OTHER') WHERE id=2",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.formComponentValues',json('[{\"name\":\"付款金额\",\"value\":101}]')) WHERE id=2",
    'DELETE FROM payment_requests WHERE id=1',
    'UPDATE payment_records SET amount=21 WHERE id=10',
    'UPDATE payment_records SET request_id=3 WHERE id=11',
    'DELETE FROM payment_records WHERE id=11',
    "INSERT INTO payment_records(request_id,amount,created_at,updated_at) VALUES(2,1,'2026-01-20','2026-01-20')",
    "INSERT INTO payment_records(request_id,copied_from_payment_id,root_payment_id,amount,created_at,updated_at) VALUES(3,10,10,1,'2026-01-20','2026-01-20')",
    "INSERT INTO payment_requests(batch_id,logical_request_id,created_at,updated_at) VALUES(101,1,'2026-01-20','2026-01-20')",
    "INSERT INTO payment_requests(batch_id,copied_from_request_id,created_at,updated_at) VALUES(101,2,'2026-01-20','2026-01-20')",
    "INSERT INTO payment_requests(batch_id,raw_extra_json,created_at,updated_at) SELECT batch_id,raw_extra_json,'2026-01-20','2026-01-20' FROM payment_requests WHERE id=2",
])
def test_database_guards_all_claimed_money_and_identity_paths(takeover_client, sql):
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='ERP_PAYMENT_OWNERSHIP_LOCKED'):
            conn.execute(sql)
        conn.execute('UPDATE payment_requests SET amount=101 WHERE id=3')
        conn.execute("UPDATE payment_requests SET general_manager_opinion='仍可审批' WHERE id=2")


@pytest.mark.parametrize('sql', [
    "INSERT OR REPLACE INTO payment_requests(id,batch_id,logical_request_id,created_at,updated_at) VALUES(2,101,3,'2026-01-20','2026-01-20')",
    "INSERT OR REPLACE INTO payment_records(id,request_id,root_payment_id,amount,created_at,updated_at) VALUES(11,3,11,99,'2026-01-20','2026-01-20')",
    "INSERT OR REPLACE INTO attachment_links(id,request_id,url_path,created_at) VALUES(91,3,'replacement','2026-01-20')",
    "INSERT OR REPLACE INTO payment_vouchers(id,payment_id,file_path,created_at) VALUES(92,12,'replacement','2026-01-20')",
    "INSERT OR REPLACE INTO payable_history_versions(logical_request_id,effective_at,recorded_at,event_type,event_key) VALUES(3,'2026-01-20','2026-01-20','replacement','fake-history')",
    "INSERT OR REPLACE INTO erp_operating_expense_ownership(logical_request_id,external_source_id,source_sheet,owner,actor_fingerprint,takeover_request_id,claim_token,claimed_at,history_fingerprint,snapshot_json) SELECT logical_request_id,external_source_id,source_sheet,owner,actor_fingerprint,takeover_request_id,'replacement-token',claimed_at,history_fingerprint,snapshot_json FROM erp_operating_expense_ownership",
    "INSERT OR REPLACE INTO file_objects(id,sha256,size_bytes,storage_path,status,created_at) VALUES(81,'replacement-hash',1,'replacement-path','ready','2026-01-20')",
    "INSERT OR REPLACE INTO file_objects(sha256,size_bytes,storage_path,status,created_at) VALUES('fake-old-hash',1,'replacement-path','ready','2026-01-20')",
])
def test_replacement_inserts_cannot_delete_and_reassign_frozen_history(takeover_client, sql, tmp_path, monkeypatch):
    from backend.app import file_storage
    client, path = takeover_client
    monkeypatch.setattr(file_storage, 'DATA_DIR', tmp_path)
    (tmp_path / 'old-proof.pdf').write_bytes(b'fake historical proof')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO file_objects(id,sha256,size_bytes,storage_path,status,created_at) VALUES(81,'fake-old-hash',21,'missing-object-path','ready','2026-01-10')")
        conn.execute("INSERT INTO attachment_links(id,request_id,url_path,file_path,file_object_id,created_at) VALUES(91,2,'old-proof','old-proof.pdf',81,'2026-01-10')")
        conn.execute("INSERT INTO payment_vouchers(id,payment_id,file_path,created_at) VALUES(92,11,'old-proof.pdf','2026-01-10')")
        conn.execute("INSERT INTO payment_records(id,request_id,amount,created_at,updated_at) VALUES(12,3,1,'2026-01-10','2026-01-10')")
        conn.execute("INSERT INTO payable_history_versions(logical_request_id,effective_at,recorded_at,event_type,event_key) VALUES(1,'2026-01-10','2026-01-10','test','fake-history')")
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match='ERP_PAYMENT_OWNERSHIP_LOCKED'):
            conn.execute(sql)
        # Existing content-addressed deduplication remains usable for other roots.
        conn.execute("INSERT OR IGNORE INTO file_objects(sha256,size_bytes,storage_backend,storage_path,status,created_at) SELECT sha256,size_bytes,storage_backend,storage_path,status,'2026-01-20' FROM file_objects WHERE id=81")
        assert conn.execute('SELECT COUNT(*) FROM file_objects').fetchone()[0] == 1
        conn.execute("INSERT OR REPLACE INTO file_objects(sha256,size_bytes,storage_backend,storage_path,status,created_at) SELECT sha256,size_bytes,storage_backend,storage_path,status,'2026-01-20' FROM file_objects WHERE id=81")
        assert conn.execute('SELECT id FROM file_objects').fetchall() == [(81,)]


@pytest.mark.parametrize('mutation', [
    "UPDATE payment_records SET source_type='legacy_migration'",
    'UPDATE payment_requests SET paid_amount=90 WHERE id=2',
    "UPDATE payment_requests SET currency='MXN' WHERE id=1",
    "UPDATE payment_requests SET source_sheet='秘密' WHERE id=1",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.approval_result','refuse') WHERE id=2",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.record_id','different') WHERE id=1",
])
def test_preview_rejects_incomplete_or_conflicting_source_facts(takeover_client, mutation):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute(mutation)
    assert preview(client).status_code == 409


def test_claim_checks_changes_without_timestamp_bump(takeover_client):
    client, path = takeover_client
    item = preview_item(client)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_records SET remark='source changed without timestamp' WHERE id=10")
    assert claim(client, item).status_code == 409


def test_workflow_transport_is_cached_exact_scoped_and_read_only(takeover_client):
    client, path = takeover_client
    before = path.read_bytes()
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': '1'})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['schema_version'] == 1 and payload['source_system'] == 'cashier-payment-archive'
    item = payload['items'][0]
    assert item['source_id'] == '1' and item['lookup_status'] == 'matched'
    assert item['last_synced_at'] and item['original_url'] is None
    assert [(event['id'], event['stage'], event['operator'], event['result'], event['current'], event['active'])
            for event in item['events']] == [('fake-approved', '财务审批', '测试审批人', 'agree', True, True)]
    assert client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': 'FAKE-APPROVAL-1'}).status_code == 404
    assert client.get(PREFIX + '/workflow', params={'source_id': '1'}).status_code == 401
    assert path.read_bytes() == before


def test_concurrent_claims_only_choose_one_owner_snapshot(takeover_client):
    client, path = takeover_client
    item = preview_item(client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda request_id: claim(client, item, request_id=request_id), ['race-a', 'race-b']))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM erp_operating_expense_ownership').fetchone()[0] == 1


@pytest.mark.parametrize('sql', [
    "INSERT INTO attachment_links(request_id,url_path,created_at) VALUES(2,'new-proof','2026-01-20')",
    "UPDATE attachment_links SET url_path='changed' WHERE id=91",
    'DELETE FROM attachment_links WHERE id=91',
    "INSERT INTO payment_vouchers(payment_id,file_path,created_at) VALUES(11,'new-proof','2026-01-20')",
    "UPDATE payment_vouchers SET file_path='changed' WHERE id=92",
    'DELETE FROM payment_vouchers WHERE id=92',
    "UPDATE payable_history_versions SET paid_amount=99 WHERE logical_request_id=1",
    'DELETE FROM payable_history_versions WHERE logical_request_id=1',
    "INSERT INTO payable_history_versions(logical_request_id,effective_at,recorded_at,event_type,event_key,amount,paid_amount,pending_amount,currency,base_amount_cny) VALUES(1,'2026-01-20','2026-01-20','manual','fake-new-money',100,20,80,'CNY',999)",
    "INSERT INTO payable_history_versions(logical_request_id,effective_at,recorded_at,event_type,event_key,amount,paid_amount,pending_amount,currency) VALUES(1,'2025-01-01','2026-01-20','manual','fake-backdated',100,20,80,'CNY')",
])
def test_historical_files_and_payable_summaries_cannot_be_rewritten(takeover_client, sql, tmp_path, monkeypatch):
    from backend.app import file_storage
    client, path = takeover_client
    monkeypatch.setattr(file_storage, 'DATA_DIR', tmp_path)
    (tmp_path / 'old-proof.pdf').write_bytes(b'fake historical proof')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO attachment_links(id,request_id,url_path,file_path,created_at) VALUES(91,2,'old-proof','old-proof.pdf','2026-01-10')")
        conn.execute("INSERT INTO payment_vouchers(id,payment_id,file_path,created_at) VALUES(92,11,'old-proof.pdf','2026-01-10')")
        conn.execute("INSERT OR IGNORE INTO payable_history_versions(logical_request_id,effective_at,recorded_at,event_type,event_key,amount,paid_amount,pending_amount) VALUES(1,'2026-01-01','2026-01-01','test','fake-history',100,20,80)")
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='ERP_PAYMENT_OWNERSHIP_LOCKED'):
            conn.execute(sql)


def test_startup_does_not_rewrite_claimed_currency_anchors(takeover_client):
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        before = conn.execute('SELECT amount,paid_amount,pending_amount,base_amount_cny,fx_rate_cny_per_unit FROM payment_requests WHERE id=2').fetchone()
    db.init_db()
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT amount,paid_amount,pending_amount,base_amount_cny,fx_rate_cny_per_unit FROM payment_requests WHERE id=2').fetchone() == before


def test_workflow_conflicting_exact_source_never_returns_another_approval_history(takeover_client):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.record_id','other-source') WHERE id=1")
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': '1'})
    assert response.status_code == 200
    assert response.json()['items'][0]['lookup_status'] == 'conflict'
    assert response.json()['items'][0]['events'] == []


def test_zero_does_not_ignore_unclassified_trusted_finance_payment_comments(takeover_client):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute('''INSERT INTO dingtalk_workflow_events(request_id,event_key,process_instance_id,
            stage_name,comment,trusted_finance,synced_at,created_at,updated_at)
            VALUES(3,'missing-payment','fake-process-3','财务审批','已付款100元',1,'2026-01-12','2026-01-12','2026-01-12')''')
    assert preview(client, '3').status_code == 409


@pytest.fixture
def cashier_client(takeover_client, monkeypatch, tmp_path):
    from backend.app import main, snapshots
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(snapshots, 'DATA_DIR', tmp_path)
    previous = main.app.dependency_overrides.get(main.current_user)
    main.app.dependency_overrides[main.current_user] = lambda: {
        'id': 1, 'role': 'admin', 'username': 'fake-admin', 'display_name': '测试管理员', 'sheet_permissions': []}
    yield TestClient(main.app, raise_server_exceptions=False)
    if previous is None:
        main.app.dependency_overrides.pop(main.current_user, None)
    else:
        main.app.dependency_overrides[main.current_user] = previous


@pytest.mark.parametrize('method,url,body', [
    ('post', '/api/batches/101/requests/2/payments', {'amount': 1, 'payment_date': '2026-01-20', 'expected_request_version': 1}),
    ('patch', '/api/batches/101/requests/1/payments/10', {'amount': 21, 'expected_version': 1, 'expected_request_version': 1}),
    ('delete', '/api/batches/101/requests/1/payments/10?expected_version=1&expected_request_version=1', None),
    ('patch', '/api/batches/101/requests/2', {'amount': 101, 'expected_version': 1}),
    ('delete', '/api/batches/101/requests/1?expected_version=1', None),
])
def test_cashier_money_api_returns_scoped_finance_conflict(takeover_client, cashier_client, method, url, body):
    client, _ = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    response = cashier_client.request(method, url, json=body)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'ERP_PAYMENT_OWNERSHIP_LOCKED'
    assert cashier_client.patch('/api/batches/101/requests/3', json={'amount': 101, 'expected_version': 1}).status_code == 200


def test_rollover_skips_claimed_roots_and_keeps_other_rows_usable(takeover_client, cashier_client):
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    response = cashier_client.post('/api/batches/101/rollover', json={
        'name': 'fake-next-week', 'copy_mode': 'all', 'expected_batch_version': 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['copied_count'] == 1 and payload['skipped_erp_owned_rows'] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT logical_request_id FROM payment_requests WHERE batch_id=?', (payload['batch']['id'],)).fetchall() == [(3,)]
        assert conn.execute('SELECT COUNT(*) FROM payment_records').fetchone()[0] == 2


def test_restore_preserves_claimed_sources_and_restores_other_rows(takeover_client, cashier_client):
    from backend.app.snapshots import create_batch_snapshot
    client, path = takeover_client
    with db.connect() as conn:
        create_batch_snapshot(conn, 101, 'baseline', 1)
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE payment_requests SET amount=120,pending_amount=120 WHERE id=3')
        before = conn.execute('SELECT * FROM payment_requests WHERE logical_request_id=1 ORDER BY id').fetchall()
    response = cashier_client.post('/api/batches/101/restore-baseline?expected_batch_version=1')
    assert response.status_code == 200, response.text
    assert response.json()['preserved_erp_owned_rows'] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM payment_requests WHERE logical_request_id=1 ORDER BY id').fetchall() == before
        assert conn.execute('SELECT amount FROM payment_requests WHERE id=3').fetchone()[0] == 100


def test_cashier_list_joins_ownership_without_disclosing_claim_token(takeover_client, cashier_client):
    client, _ = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    response = cashier_client.get('/api/batches/101/requests')
    assert response.status_code == 200, response.text
    rows = {row['id']: row for row in response.json()['requests']}
    assert rows[2]['erp_payment_owner'] == 'deeplinkerp' and rows[3]['erp_payment_owner'] is None
    assert all('claim_token' not in row for row in rows.values())


def test_approval_edits_remain_available_on_claimed_sources(takeover_client, cashier_client):
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        before = conn.execute('SELECT * FROM payable_history_versions WHERE logical_request_id=1').fetchall()
    response = cashier_client.patch('/api/batches/101/requests/2', json={
        'general_manager_opinion': '接管后继续审批', 'expected_version': 1})
    assert response.status_code == 200, response.text
    assert response.json()['request']['general_manager_opinion'] == '接管后继续审批'
    assert response.json()['request']['erp_payment_owner'] == 'deeplinkerp'
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM payable_history_versions WHERE logical_request_id=1').fetchall() == before


def test_archived_approval_correction_keeps_erp_owner_marker(takeover_client, cashier_client):
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE request_batches SET status='archived' WHERE id=101")
    response = cashier_client.post('/api/batches/101/corrections', json={
        'request_id': 2, 'changes': {'general_manager_opinion': '继续审批'},
        'reason': '测试审批意见更正', 'expected_version': 1})
    assert response.status_code == 200, response.text
    assert response.json()['request']['erp_payment_owner'] == 'deeplinkerp'


def test_automatic_dingtalk_payment_skips_claimed_roots_but_keeps_approval_cache(takeover_client, cashier_client, monkeypatch):
    from backend.app import main
    client, path = takeover_client
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        source_metadata = [json.loads(row[0])['external_source'] for row in conn.execute(
            'SELECT raw_extra_json FROM payment_requests WHERE id IN (2,3)')]
    monkeypatch.setenv('DINGTALK_AUTO_PAYMENT_MODE', 'apply')
    monkeypatch.setattr(main, 'fetch_external_expense_metadata', lambda approval_nos: source_metadata)
    monkeypatch.setattr(main, 'fetch_dingtalk_workflows', lambda approval_nos: [
        {'approval_no': source['approval_no'], 'process_instance_id': source['process_instance_id'],
         'status': 'COMPLETED', 'result': 'agree', 'events': [
             {'event_key': 'new-paid-' + source['record_id'], 'process_instance_id': source['process_instance_id'],
              'stage_name': '财务审批', 'operator_name': '测试财务', 'event_time': '2026-01-20',
              'trusted_finance': True, 'comment': '付款完成', 'current': True}]} for source in source_metadata])
    result = main._sync_external_expense_metadata_blocking(101, 0, {
        'id': 1, 'role': 'admin', 'username': 'fake-admin'}, include_attachments=False)
    assert result['auto_payments'] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM payment_records WHERE request_id IN (1,2)').fetchone()[0] == 2
        assert conn.execute('SELECT SUM(amount) FROM payment_records WHERE request_id=3').fetchone()[0] == 100
        assert conn.execute("SELECT classification FROM dingtalk_workflow_events WHERE request_id=2 AND event_key='new-paid-fake-source-1'").fetchone()[0] == 'erp_owned'
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': '1'})
    assert response.status_code == 200
    assert any(event['id'] == 'new-paid-fake-source-1' for event in response.json()['items'][0]['events'])


@pytest.mark.parametrize('mutation', [
    "UPDATE payment_records SET payment_date=NULL WHERE id=10",
    "UPDATE payment_requests SET dingding_id='different-approval' WHERE id=2",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.source_currency_raw','USD') WHERE id=2",
    "UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.original_source_amount_raw','100.000000000000001') WHERE id=2",
    "UPDATE dingtalk_workflow_events SET process_instance_id='other-approval' WHERE request_id=2",
    "INSERT INTO payment_requests(id,logical_request_id,batch_id,source_sheet,raw_extra_json,amount,paid_amount,pending_amount,currency,created_at,updated_at) SELECT 4,4,101,'运营',raw_extra_json,100,0,100,'CNY','2026-01-01','2026-01-12' FROM payment_requests WHERE id=2",
])
def test_takeover_requires_complete_exact_identity_and_original_money(takeover_client, mutation):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute(mutation)
    assert preview(client).status_code == 409


@pytest.mark.parametrize('original_amount,allowed', [
    ('200', False), ('NaN', False), ('Infinity', False), ('-Infinity', False),
    ('not-an-amount', False), ('100', True), ('100.00', True), (None, True),
])
def test_takeover_requires_finite_matching_original_amount_when_present(takeover_client, original_amount, allowed):
    client, path = takeover_client
    prior = preview_item(client)
    with sqlite3.connect(path) as conn:
        if original_amount is None:
            conn.execute("UPDATE payment_requests SET raw_extra_json=json_remove(raw_extra_json,'$.external_source.source_amount') WHERE id=2")
        else:
            conn.execute("UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.original_source_amount_raw',?) WHERE id=2", (original_amount,))
    before = path.read_bytes()
    exported = client.get(PREFIX, headers=HEADERS, params={'source_id': '1'})
    assert exported.status_code == 200, exported.text
    assert exported.json()['items'][0]['original_source_amount'] == original_amount
    response = preview(client)
    assert path.read_bytes() == before
    assert response.status_code == (200 if allowed else 409), response.text
    if allowed:
        assert claim(client, response.json()['items'][0]).status_code == 200
    else:
        rejected = claim(client, prior)
        assert rejected.status_code == 409, rejected.text
        assert 'original' in rejected.json()['detail'].lower()
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM erp_operating_expense_ownership').fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='erp.operating_expense.takeover_claim'").fetchone()[0] == 0


@pytest.mark.parametrize('payment_date,allowed', [
    (None, False), ('', False), ('   ', False), ('not-a-date', False),
    ('2026-02-30', False), ('2026/01/10', False), ('2026-01-10T08:00:00', False),
    ('2026-01-10', True),
])
def test_takeover_requires_valid_dates_even_when_all_payment_copies_agree(takeover_client, payment_date, allowed):
    client, path = takeover_client
    prior = preview_item(client)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE payment_records SET payment_date=?', (payment_date,))
    before = path.read_bytes()
    exported = client.get(PREFIX, headers=HEADERS, params={'source_id': '1'})
    assert exported.status_code == 200, exported.text
    assert exported.json()['items'][0]['payments'][0]['payment_date'] == payment_date
    response = preview(client)
    assert path.read_bytes() == before
    assert response.status_code == (200 if allowed else 409), response.text
    if allowed:
        assert claim(client, response.json()['items'][0]).status_code == 200
    else:
        rejected = claim(client, prior)
        assert rejected.status_code == 409, rejected.text
        assert 'date' in rejected.json()['detail'].lower()
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM erp_operating_expense_ownership').fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE action='erp.operating_expense.takeover_claim'").fetchone()[0] == 0


def test_workflow_preserves_pending_current_event_without_approved_fabrication(takeover_client):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE dingtalk_workflow_events SET stage_name='待审批节点',result=NULL,comment=NULL,is_current=1 WHERE request_id=2")
    event = client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': '1'}).json()['items'][0]['events'][0]
    assert event['stage'] == '待审批节点' and event['result'] is None and event['comment'] is None and event['current']


def test_changed_integration_actor_cannot_reuse_another_actors_claim(takeover_client, monkeypatch):
    client, _ = takeover_client
    item = preview_item(client)
    assert claim(client, item).status_code == 200
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN', 'different-fake-token')
    response = client.post(PREFIX + '/takeover-claim', headers={'Authorization': 'Bearer different-fake-token'},
                           json={'source_id': '1', 'expected_version': item['version'], 'request_id': 'fake-request-1'})
    assert response.status_code == 409


@pytest.mark.parametrize('lookup_status', ['unmatched', 'conflict'])
def test_workflow_transport_discloses_cached_lookup_status_for_exact_source(takeover_client, lookup_status):
    client, path = takeover_client
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.lookup_status',?) WHERE id=2", (lookup_status,))
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params={'source_id': '1'})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['lookup_status'] == lookup_status
    if lookup_status == 'conflict':
        assert response.json()['items'][0]['events'] == []


def test_restoring_unclaimed_shared_legacy_file_cannot_overwrite_claimed_proof(takeover_client, cashier_client, tmp_path, monkeypatch):
    from backend.app import file_storage
    from backend.app.snapshots import create_batch_snapshot
    client, path = takeover_client
    monkeypatch.setattr(file_storage, 'DATA_DIR', tmp_path)
    proof = tmp_path / 'shared-legacy.pdf'
    proof.write_bytes(b'old baseline proof')
    with db.connect() as conn:
        for rid in (2, 3):
            conn.execute("INSERT INTO attachment_links(request_id,url_path,file_path,created_at) VALUES(?,'shared','shared-legacy.pdf','2026-01-10')", (rid,))
        create_batch_snapshot(conn, 101, 'baseline', 1)
    proof.write_bytes(b'proof actually frozen by ERP')
    assert claim(client, preview_item(client)).status_code == 200
    response = cashier_client.post('/api/batches/101/restore-baseline?expected_batch_version=1')
    assert response.status_code == 200, response.text
    assert proof.read_bytes() == b'proof actually frozen by ERP'
    with db.connect() as conn:
        unclaimed_attachment = conn.execute('SELECT * FROM attachment_links WHERE request_id=3').fetchone()
        restored_path, _ = file_storage.resolve_attachment_path(unclaimed_attachment, conn)
        assert restored_path.read_bytes() == b'old baseline proof'


def test_claimed_historical_files_remain_readable_after_approval_lookup_conflict(takeover_client, tmp_path, monkeypatch):
    from backend.app import file_storage
    client, path = takeover_client
    monkeypatch.setattr(file_storage, 'DATA_DIR', tmp_path)
    (tmp_path / 'old-proof.pdf').write_bytes(b'fake immutable historical proof')
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO attachment_links(id,request_id,url_path,file_path,created_at) VALUES(91,2,'old-proof','old-proof.pdf','2026-01-10')")
        conn.execute("INSERT INTO attachment_links(id,request_id,url_path,file_path,created_at) VALUES(93,3,'old-proof','old-proof.pdf','2026-01-10')")
    assert claim(client, preview_item(client)).status_code == 200
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE payment_requests SET raw_extra_json=json_set(raw_extra_json,'$.external_source.lookup_status','conflict') WHERE id IN(2,3)")
    monkeypatch.delenv('PAYMENT_ERP_TAKEOVER_ENABLED')
    response = client.get('/api/integrations/erp/attachments/91', headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.content == b'fake immutable historical proof'
    assert client.get('/api/integrations/erp/attachments/93', headers=HEADERS).status_code == 404
