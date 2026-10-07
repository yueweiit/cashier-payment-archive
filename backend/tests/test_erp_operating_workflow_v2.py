"""Exact OA workflow reads and source-side payment permission, without live writes."""
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import db, erp_export, external_expenses


HEADERS = {'Authorization': 'Bearer fake-v2-token'}
PREFIX = '/api/integrations/erp/operating-expenses'
IDENTITY = {'corp_id': 'fake-corp', 'process_instance_id': 'fake-instance',
            'source_id': 'oa:' + erp_export.digest(['fake-corp', 'fake-instance'])}
SCOPE = {'corp_id': 'fake-corp', 'process_code': 'fake-process', 'execution_region': '中国',
         'year': 2026, 'source_sheet': '运营'}
POLICY = {'policy_version': 'fake-policy-1', 'process_code': 'fake-process', 'template_version': 'fake-template-1',
          'required_approval_activity_ids': ['manager', 'finance'], 'cashier_activity_ids': ['cashier']}


def workflow_raw(status='RUNNING', result=''):
    return {'process_instance_id': 'fake-instance', 'corp_id': 'fake-corp', 'process_code': 'fake-process',
            'template_version': 'fake-template-1', 'originator_user_id': 'raw-originator',
            'approval_no': 'fake-approval', 'status': status, 'result': result,
            'updated_at': '2026-09-01T04:00:00Z',
            'operation_records': [
                {'activityId': node, 'showName': node, 'type': 'EXECUTE_TASK_NORMAL',
                 'userId': user, 'result': 'AGREE', 'date': '2026-09-01T01:00:00Z'}
                for node, user in [('manager', 'manager-user'), ('finance', 'finance-user')]],
            'tasks': [{'taskId': 'task-1', 'activityId': 'cashier', 'taskGroupName': '出纳执行',
                       'userIds': ['cashier-a', 'cashier-b'], 'status': 'RUNNING',
                       'pcUrl': 'https://aflow.dingtalk.com/a?procInstId=fake-instance&token=drop'}]}


@pytest.fixture
def v2_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'v2.db')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_TOKEN', 'fake-v2-token')
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', '["运营"]')
    monkeypatch.setenv('PAYMENT_ERP_OPERATING_OA_SCOPE', json.dumps(SCOPE))
    monkeypatch.setenv('PAYMENT_ERP_OPERATING_PAYMENT_POLICY', json.dumps(POLICY))
    db.init_db()
    data = {'instances': [{**workflow_raw(), 'id': 'fake-source',
                         'execution_region': '中国', 'effective_date': '2026-09-01',
                         'source_updated_at': '2026-09-01T04:00:00Z', 'source_amount': '100',
                         'source_currency': 'CNY', 'beneficiary': '测试收款人', 'summary': '测试房租',
                         'approval_no': 'fake-approval', 'application_type_raw': '付款',
                         'source_company_raw': '测试公司'}],
            'user_names': {'raw-originator': '真实申请人', 'manager-user': '经理', 'finance-user': '财务',
                           'cashier-a': '出纳甲', 'cashier-b': '出纳乙'}}
    monkeypatch.setattr(erp_export, 'fetch_operating_workflow_sources', lambda identities, scope: data, raising=False)
    app = FastAPI()
    app.include_router(erp_export.router)
    return TestClient(app), data, db.DB_PATH


def test_parser_uses_task_group_name_and_preserves_multiple_assignees():
    parsed = external_expenses.parse_dingtalk_workflow_instance(workflow_raw(), {'cashier-a': '甲', 'cashier-b': '乙'})
    assert parsed['current_node_name'] == '出纳执行'
    assert sorted(task['approver_id'] for task in parsed['current_tasks']) == ['cashier-a', 'cashier-b']
    assert {task['status'] for task in parsed['current_tasks']} == {'RUNNING'}
    assert parsed['originator_user_id'] == 'raw-originator'


def test_exact_oa_workflow_without_cashier_request_is_read_only_and_batched(v2_client):
    client, _, path = v2_client
    before = path.read_bytes()
    response = client.post(PREFIX + '/workflow', headers=HEADERS, json={'identities': [IDENTITY]})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['schema_version'] == 2
    item = payload['items'][0]
    assert item['source_id'] == IDENTITY['source_id'] and item['lookup_status'] == 'found'
    assert item['originator'] == {'id': 'raw-originator', 'name': '真实申请人'}
    assert item['approval_status'] == 'RUNNING' and item['approval_result'] == ''
    assert item['current_tasks'][0]['stage'] == '出纳执行'
    assert sorted(item['current_tasks'][0]['assignees'], key=lambda person: person['id']) == [{'id': 'cashier-a', 'name': '出纳甲'}, {'id': 'cashier-b', 'name': '出纳乙'}]
    assert item['events'][0]['operator_id'] == 'manager-user'
    assert item['payment_eligibility']['can_register_payment'] is True
    assert 'token' not in item['original_url']
    assert client.get(PREFIX + '/workflow', headers=HEADERS, params=IDENTITY).json() == payload
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['required_missing', 'finance_running', 'task_unknown', 'version_unknown', 'refused', 'terminated', 'withdrawn'])
def test_running_payment_permission_fails_closed(v2_client, change):
    client, data, _ = v2_client
    instance = data['instances'][0]
    if change == 'required_missing':
        instance['operation_records'].pop()
    elif change == 'finance_running':
        instance['tasks'][0]['activityId'] = 'finance'
        instance['tasks'][0]['taskGroupName'] = '财务审批'
    elif change == 'task_unknown':
        instance['tasks'][0].pop('activityId')
    elif change == 'version_unknown':
        instance.pop('template_version')
    elif change == 'refused':
        instance['operation_records'][0]['result'] = 'REFUSE'
    elif change == 'terminated':
        instance['status'] = 'TERMINATED'
    else:
        instance['operation_records'].append({'type': 'TERMINATE_PROCESS_INSTANCE', 'result': 'NONE'})
    response = client.post(PREFIX + '/workflow', headers=HEADERS, json={'identities': [IDENTITY]})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['payment_eligibility']['can_register_payment'] is False


@pytest.mark.parametrize('status,result,allowed', [('COMPLETED', 'agree', True), ('COMPLETED', 'refuse', False), ('COMPLETED', '', False)])
def test_completed_permission_requires_explicit_agreement(v2_client, status, result, allowed):
    client, data, _ = v2_client
    data['instances'][0].update(status=status, result=result, tasks=[])
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params=IDENTITY)
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['payment_eligibility']['can_register_payment'] is allowed


@pytest.mark.parametrize('change', ['corp', 'source', 'duplicate', 'region', 'year', 'process'])
def test_exact_identity_conflict_and_scope_never_disclose_history(v2_client, change):
    client, data, _ = v2_client
    if change == 'corp':
        data['instances'][0]['corp_id'] = 'other-corp'
    elif change == 'duplicate':
        data['instances'].append(dict(data['instances'][0], id='other-source'))
    elif change == 'source':
        data['instances'][0]['raw_corp_id'] = 'other-corp'
    else:
        data['instances'][0][{'region': 'execution_region', 'year': 'effective_date', 'process': 'process_code'}[change]] = {
            'region': '中国 / 墨西哥', 'year': '2025-09-01', 'process': 'other-process'}[change]
    response = client.get(PREFIX + '/workflow', headers=HEADERS, params=IDENTITY)
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['lookup_status'] in {'conflict', 'missing'}
    assert item['events'] == [] and item['current_tasks'] == [] and item['originator']['id'] is None
    assert item['payment_eligibility']['can_register_payment'] is False


def test_v2_identity_scope_and_batch_are_strict(v2_client, monkeypatch):
    client, _, _ = v2_client
    assert client.post(PREFIX + '/workflow', json={'identities': [IDENTITY]}).status_code == 401
    assert client.post(PREFIX + '/workflow', headers=HEADERS, json={'identities': [IDENTITY] * 501}).status_code == 422
    assert client.get(PREFIX + '/workflow', headers=HEADERS, params={'corp_id': 'fake-corp'}).status_code == 422
    monkeypatch.delenv('PAYMENT_ERP_OPERATING_OA_SCOPE')
    assert client.get(PREFIX + '/workflow', headers=HEADERS, params=IDENTITY).status_code == 503


def test_oa_only_takeover_is_versioned_requires_human_zero_and_blocks_late_imports(v2_client, monkeypatch):
    client, data, path = v2_client
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    confirmation = {'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'}
    assert client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=IDENTITY).status_code == 409
    preview = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json={**IDENTITY, **confirmation})
    assert preview.status_code == 200, preview.text
    item = preview.json()['items'][0]
    assert item['source_id'] == IDENTITY['source_id'] and item['paid_amount'] == '0' and item['pending_amount'] == '100'
    body = {**IDENTITY, **confirmation, 'expected_version': item['version'], 'request_id': 'fake-oa-claim',
            'expected_eligibility_fingerprint': item['payment_eligibility']['evidence_fingerprint']}
    response = client.post(PREFIX + '/takeover-claim', headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['schema_version'] == 2
    assert response.json()['items'][0]['oa_identity'] == {key: IDENTITY[key] for key in ('corp_id', 'process_instance_id')}
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS, json=body).json() == response.json()
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS, json={**body, 'request_id': 'other-request'}).status_code == 409
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM payment_requests').fetchone()[0] == 0
        owner = conn.execute('SELECT logical_request_id,corp_id,process_instance_id FROM erp_operating_expense_ownership').fetchone()
        assert owner == (None, 'fake-corp', 'fake-instance')
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(1,'fake-batch','2026-01-01','2026-01-01')")
        raw = json.dumps({'external_source': {'system': 'dingtalk_expense_database', 'source_type': 'operation',
            'record_id': 'later-expense-id', 'corp_id': 'fake-corp', 'process_instance_id': 'fake-instance'}})
        with pytest.raises(sqlite3.IntegrityError, match='ERP'):
            conn.execute('INSERT INTO payment_requests(batch_id,raw_extra_json,created_at,updated_at) VALUES(1,?,?,?)',
                         (raw, '2026-01-01', '2026-01-01'))
        with pytest.raises(sqlite3.IntegrityError, match='ERP'):
            conn.execute("INSERT INTO payment_requests(batch_id,dingding_id,created_at,updated_at) VALUES(1,'fake-approval','2026-01-01','2026-01-01')")


def test_oa_takeover_cannot_attest_zero_over_existing_cashier_history(v2_client, monkeypatch):
    client, data, path = v2_client
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO request_batches(id,name,created_at,updated_at) VALUES(1,'fake-batch','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO payment_requests(id,logical_request_id,batch_id,dingding_id,amount,paid_amount,pending_amount,currency,created_at,updated_at) VALUES(1,1,1,'fake-approval',100,20,80,'CNY','2026-01-01','2026-01-01')")
    response = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json={**IDENTITY,
        'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'})
    assert response.status_code == 409
    assert 'history' in response.json()['detail'].lower()


def test_oa_takeover_version_covers_source_permission_and_confirmation(v2_client):
    client, data, _ = v2_client
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    base = {**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'}
    preview = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=base)
    assert preview.status_code == 200, preview.text
    version = preview.json()['items'][0]['version']
    data['instances'][0]['source_amount'] = '101'
    assert client.post(PREFIX + '/takeover-preview', headers=HEADERS, json={**base, 'expected_version': version}).status_code == 409


def test_operating_source_reader_filters_corp_directory_and_uses_normalized_task_time(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timezone
    calls = []
    class FakeConnection:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            self.sql = sql
            return self
        def fetchall(self):
            if 'ding_approval_instance' in self.sql:
                return [{'id': 1, 'corp_id': 'fake-corp', 'process_instance_id': 'fake-instance',
                    'process_code': 'fake-process', 'create_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
                    'status': 'RUNNING', 'result': '', 'updated_at': datetime(2026, 9, 1, 2, tzinfo=timezone.utc),
                    'originator_user_id': 'wrong-structured', 'raw_payload': {'originatorUserId': 'raw-originator',
                    'tasks': [{'taskId': 'task-1', 'createTime': '2026-09-01T09:00Z', 'pcUrl': 'https://aflow.dingtalk.com/a?procInstId=fake-instance'},
                              {'taskId': 'raw-only-task', 'status': 'PENDING', 'taskGroupName': '财务审批', 'userId': 'finance-user'}]},
                    'form_component_values': [{'name': '执行地区', 'value': '中国'}]}]
            if 'ding_approval_task' in self.sql:
                return [{'process_instance_id': 'fake-instance', 'task_id': 'task-1', 'activity_id': 'cashier',
                    'node_name': '出纳执行', 'status': 'RUNNING', 'result': '', 'approver_user_id': 'cashier-a',
                    'start_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc), 'raw_payload': {}}]
            return [{'user_id': 'raw-originator', 'name': '真实申请人'}]
    @contextmanager
    def fake_connection(dbname=None):
        yield FakeConnection()
    monkeypatch.setattr(external_expenses, 'source_connection', fake_connection)
    monkeypatch.setattr(external_expenses, 'source_database_config', lambda: type('Config', (), {'user_dbname': 'fake-db'})())
    fetched = external_expenses.fetch_operating_workflow_sources([IDENTITY], [{**SCOPE, 'process_codes': ['fake-process']}])
    parsed = external_expenses.parse_dingtalk_workflow_instance(fetched['instances'][0], {})
    assert parsed['originator_user_id'] == 'raw-originator'
    assert parsed['current_node_entered_at'] == '2026-09-01T09:00:00+08:00'
    assert len(parsed['current_tasks']) == 2
    assert parsed['workflow_url'] == 'https://aflow.dingtalk.com/a?procInstId=fake-instance'
    assert fetched['user_names'][('fake-corp', 'raw-originator')] == '真实申请人'
    directory = [call for call in calls if 'ding_user_snapshot' in call[0]][0]
    assert 'corp_id=%s' in directory[0] and directory[1][0] == 'fake-corp'


def test_ownership_schema_upgrade_preserves_frozen_legacy_claims_and_guards(v2_client):
    _, _, path = v2_client
    columns = 'logical_request_id,external_source_id,source_sheet,owner,actor_fingerprint,takeover_request_id,claim_token,claimed_at,history_fingerprint,snapshot_json'
    snapshot = json.dumps({'source_id': '7', 'corp_id': 'old-corp', 'process_instance_id': 'old-instance', 'approval_no': 'old-approval'})
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name GLOB 'erp_guard_*'").fetchall():
            conn.execute(f'DROP TRIGGER "{row["name"]}"')
        conn.execute('DROP TABLE erp_operating_expense_ownership')
        conn.execute('''CREATE TABLE erp_operating_expense_ownership(logical_request_id INTEGER PRIMARY KEY CHECK(logical_request_id>0),
            external_source_id TEXT NOT NULL UNIQUE,source_sheet TEXT NOT NULL,owner TEXT NOT NULL,
            actor_fingerprint TEXT NOT NULL,takeover_request_id TEXT NOT NULL,claim_token TEXT NOT NULL UNIQUE,
            claimed_at TEXT NOT NULL,history_fingerprint TEXT NOT NULL,snapshot_json TEXT NOT NULL)''')
        conn.execute(f'INSERT INTO erp_operating_expense_ownership({columns}) VALUES(7,?,?,?,?,?,?,?,?,?)',
                     ('old-source', '运营', 'deeplinkerp', 'old-actor', 'old-request', 'old-token', '2026-01-01', 'old-history', snapshot))
        before = tuple(conn.execute(f'SELECT {columns} FROM erp_operating_expense_ownership').fetchone())
        db.ensure_erp_operating_ownership(conn)
        after = tuple(conn.execute(f'SELECT {columns} FROM erp_operating_expense_ownership').fetchone())
        assert before == after
        assert tuple(conn.execute('SELECT corp_id,process_instance_id,approval_no FROM erp_operating_expense_ownership').fetchone()) == ('old-corp', 'old-instance', 'old-approval')
        with pytest.raises(sqlite3.IntegrityError, match='ERP'):
            conn.execute("UPDATE erp_operating_expense_ownership SET owner='other'")


def test_oa_takeover_uses_one_source_snapshot_and_rejects_wrong_stored_alias(v2_client, monkeypatch):
    client, data, _ = v2_client
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    calls = []
    def fetch(identities, scopes):
        calls.append(identities)
        return data
    monkeypatch.setattr(erp_export, 'fetch_operating_workflow_sources', fetch)
    base = {**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'}
    preview = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=base)
    assert preview.status_code == 200, preview.text
    assert len(calls) == 1
    item = preview.json()['items'][0]
    body = {**base, 'expected_version': item['version'], 'request_id': 'fake-claim',
            'expected_eligibility_fingerprint': item['payment_eligibility']['evidence_fingerprint']}
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS, json=body).status_code == 200
    assert client.post(PREFIX + '/takeover-preview', headers=HEADERS, json={**base, 'source_id': 'oa:wrong'}).status_code in {404, 409}


@pytest.mark.parametrize('raw_hour,expected_comment', [(9, '2026-09-01T10:15:00+08:00'), (1, '2026-09-01T10:15:00+08:00'), (3, None)])
def test_oa_operation_time_uses_unique_task_end_and_proven_instance_encoding(monkeypatch, raw_hour, expected_comment):
    from contextlib import contextmanager
    from datetime import datetime, timezone
    raw_start = f'2026-09-01T{raw_hour:02d}:00Z'
    raw_end = f'2026-09-01T{raw_hour + 1:02d}:00Z'
    raw_comment = f'2026-09-01T{raw_hour + 1:02d}:15Z'
    class FakeConnection:
        def execute(self, sql, params=None):
            self.sql = sql
            return self
        def fetchall(self):
            if 'ding_approval_instance' in self.sql:
                return [{'id': 1, 'corp_id': 'fake-corp', 'process_instance_id': 'fake-instance', 'process_code': 'fake-process',
                    'create_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc), 'status': 'RUNNING', 'result': '',
                    'raw_payload': {'createTime': raw_start, 'operationRecords': [
                        {'activityId': 'finance', 'userId': 'finance-user', 'showName': '财务', 'result': 'AGREE', 'type': 'EXECUTE_TASK_NORMAL', 'date': raw_end},
                        {'userId': 'finance-user', 'showName': '评论', 'type': 'ADD_REMARK', 'date': raw_comment}]},
                    'form_component_values': [{'name': '执行地区', 'value': '中国'}]}]
            if 'ding_approval_task' in self.sql:
                return [{'process_instance_id': 'fake-instance', 'task_id': 'finished', 'activity_id': 'finance',
                    'node_name': '财务', 'status': 'COMPLETED', 'result': 'AGREE', 'approver_user_id': 'finance-user',
                    'start_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc), 'end_time': datetime(2026, 9, 1, 2, tzinfo=timezone.utc),
                    'raw_payload': {'createTime': raw_start, 'finishTime': raw_end}}]
            return []
    @contextmanager
    def fake_connection(dbname=None):
        yield FakeConnection()
    monkeypatch.setattr(external_expenses, 'source_connection', fake_connection)
    monkeypatch.setattr(external_expenses, 'source_database_config', lambda: type('Config', (), {'user_dbname': 'fake-db'})())
    fetched = external_expenses.fetch_operating_workflow_sources([IDENTITY], [{**SCOPE, 'process_codes': ['fake-process']}])
    events = external_expenses.parse_dingtalk_workflow_instance(fetched['instances'][0], {})['events']
    assert events[0]['event_time'] == '2026-09-01T10:00:00+08:00'
    assert events[1]['event_time'] == expected_comment
    assert events[0]['time_provenance'] == 'normalized_task_end_time'


def test_oa_takeover_resolves_company_from_raw_originator_and_existing_department_map(v2_client, monkeypatch):
    client, data, path = v2_client
    label = '悦为智能 YW Tech_Ai'
    monkeypatch.setenv('PAYMENT_ERP_EXPORT_ALLOWED_SHEETS', json.dumps([label]))
    scope = {key: value for key, value in SCOPE.items() if key != 'source_sheet'}
    monkeypatch.setenv('PAYMENT_ERP_OPERATING_OA_SCOPE', json.dumps([scope]))
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[], source_company_raw=None, source_currency='人民币CNY')
    with sqlite3.connect(path) as conn:
        conn.execute('''INSERT INTO employee_department_mappings(user_id,employee_name,second_level_department,third_level_department,imported_at)
            VALUES('raw-originator','真实申请人',?,'','2026-09-01')''', (label,))
    response = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json={**IDENTITY,
        'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'})
    assert response.status_code == 200, response.text
    item = response.json()['items'][0]
    assert item['source_sheet'] == label and item['source_company'] == '悦为智能技术（东莞）有限公司'
    assert item['currency'] == 'CNY'


def test_oa_claim_requires_matching_eligibility_fingerprint(v2_client, monkeypatch):
    client, data, _ = v2_client
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    base = {**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'}
    item = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=base).json()['items'][0]
    claim = {**base, 'expected_version': item['version'], 'request_id': 'fake-claim'}
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS, json=claim).status_code in {409, 422}
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS,
        json={**claim, 'expected_eligibility_fingerprint': '0' * 64}).status_code == 409


@pytest.mark.parametrize('policies', ['missing', 'duplicate'])
def test_running_permission_rejects_absent_or_conflicting_node_policy(v2_client, monkeypatch, policies):
    client, _, _ = v2_client
    if policies == 'missing':
        monkeypatch.delenv('PAYMENT_ERP_OPERATING_PAYMENT_POLICY')
    else:
        monkeypatch.setenv('PAYMENT_ERP_OPERATING_PAYMENT_POLICY', json.dumps([POLICY, {**POLICY, 'required_approval_activity_ids': ['different-required']}]))
    item = client.get(PREFIX + '/workflow', headers=HEADERS, params=IDENTITY).json()['items'][0]
    assert item['payment_eligibility']['can_register_payment'] is False


def test_stored_oa_takeover_still_requires_configured_oa_scope(v2_client, monkeypatch):
    client, data, _ = v2_client
    monkeypatch.setenv('PAYMENT_ERP_TAKEOVER_ENABLED', 'true')
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    base = {**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'}
    item = client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=base).json()['items'][0]
    body = {**base, 'expected_version': item['version'], 'request_id': 'fake-claim',
            'expected_eligibility_fingerprint': item['payment_eligibility']['evidence_fingerprint']}
    assert client.post(PREFIX + '/takeover-claim', headers=HEADERS, json=body).status_code == 200
    monkeypatch.delenv('PAYMENT_ERP_OPERATING_OA_SCOPE')
    assert client.post(PREFIX + '/takeover-preview', headers=HEADERS, json=base).status_code == 503


@pytest.mark.parametrize('amount', ['NaN', '-1', '0', '1.005'])
def test_oa_takeover_rejects_nonpayable_original_money(v2_client, amount):
    client, data, _ = v2_client
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[], source_amount=amount)
    response = client.post(PREFIX + '/takeover-preview', headers=HEADERS,
        json={**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'})
    assert response.status_code == 409


def test_source_form_reader_rejects_duplicate_evidence_without_changing_legacy_callers():
    components = [{'name': '金额', 'value': '100'}, {'name': '金额Amount', 'value': '200'}]
    assert external_expenses._form_component_value(components, '金额') == '100'
    assert external_expenses._form_component_value(components, '金额', require_unique=True) is None


def test_oa_zero_attestation_cannot_override_unreconciled_workflow_payment_evidence(v2_client):
    client, data, _ = v2_client
    data['instances'][0].update(status='COMPLETED', result='agree', tasks=[])
    data['instances'][0]['operation_records'][-1]['remark'] = '已支付100元'
    response = client.post(PREFIX + '/takeover-preview', headers=HEADERS,
        json={**IDENTITY, 'zero_history_confirmed': True, 'confirmed_by': 'fake-finance'})
    assert response.status_code == 409
    assert 'payment evidence' in response.json()['detail'].lower()


def test_repeated_operation_identity_does_not_reuse_a_single_task_end_time():
    from datetime import datetime, timezone
    raw = {'createTime': '2026-09-01T01:00Z', 'operationRecords': [
        {'activityId': 'finance', 'userId': 'same-user', 'result': 'AGREE', 'type': 'EXECUTE_TASK_NORMAL', 'date': time}
        for time in ['2026-09-01T02:00Z', '2026-09-01T03:00Z']]}
    instance = {'create_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc), 'raw_payload': raw}
    task = {'activity_id': 'finance', 'approver_user_id': 'same-user', 'result': 'AGREE',
        'start_time': datetime(2026, 9, 1, 1, tzinfo=timezone.utc), 'end_time': datetime(2026, 9, 1, 2, tzinfo=timezone.utc),
        'raw_payload': {'createTime': '2026-09-01T01:00Z', 'finishTime': '2026-09-01T02:00Z'}}
    operations = external_expenses._oa_operation_times(instance, [task])
    assert external_expenses._workflow_event_time(operations[1]['source_event_time']) == '2026-09-01T11:00:00+08:00'
    assert operations[1]['source_event_time_provenance'] == 'instance_and_task_verified_encoding'
