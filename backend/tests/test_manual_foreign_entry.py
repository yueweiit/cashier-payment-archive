from __future__ import annotations
import io
import uuid
from datetime import date
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest
import backend.app.main as main
from backend.app.db import connect
from backend.app.excel_io import parse_weekly_excel
from backend.app.daily_payables import daily_snapshot

SHEET = 'YW MOLDES MX模具'

def workbook_bytes(prefix='USD'):
    wb = Workbook()
    ws = wb.active
    ws.title = '明细'
    ws.append(['钉钉申请单号','应付款公司','账户性质','支出性质','摘要','应付金额','已支付金额','货币类型','收款人名称','账号','收款行（具体到分行）','需求付款日期','是否开具发票'])
    ws.append([prefix+'-1','YUEWEI MX','美金','材料款-美金','磁铁\n含空运费用\t明细',45567.18,0,'USD','Yuyang','769911864332088','招商银行','2026-02-28','无票'])
    for installment, due in [(11,'2026-09-11'),(12,'2026-10-16')]:
        ws.append([prefix+'-2','YW MOLDES','美金','辅助材料',f'海天注塑机尾款第{installment}期',3600,0,'USD','HAITIAN','260-323258-179','HSBC',due,None])
    result = io.BytesIO(); wb.save(result)
    return result.getvalue()

@pytest.fixture
def client(monkeypatch):
    def rates(day, currencies):
        # All external I/O must happen before the SQLite write lock is held.
        with connect() as conn:
            conn.execute('PRAGMA busy_timeout = 20')
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        return {c: {'cny_per_unit':7.2,'requested_date':day.isoformat(),'actual_date':day.isoformat(),'fallback':False} for c in currencies}
    monkeypatch.setattr(main,'fetch_rates',rates)
    with TestClient(main.app) as c:
        assert c.post('/api/auth/login',json={'username':'admin','password':'admin123'}).status_code == 200
        name = 'finance-'+uuid.uuid4().hex
        assert c.post('/api/admin/users',json={'username':name,'password':'finance123','display_name':'财务测试','role':'finance','active':True}).status_code == 200
        c.post('/api/auth/logout')
        assert c.post('/api/auth/login',json={'username':name,'password':'finance123'}).status_code == 200
        yield c

def batch(client):
    return client.post('/api/batches',json={'name':uuid.uuid4().hex,'start_date':'2026-09-09','end_date':'2026-09-15'}).json()['batch']['id']

def test_manual_foreign_single_and_bulk_stay_visible(client):
    bid = batch(client)
    url = f'/api/batches/{bid}/requests'
    payload = {'source_sheet':SHEET,'summary':'美元手动新增','amount':45567.18,'currency':'USD'}
    response = client.post(url,json=payload)
    assert response.status_code == 200, response.text
    row = response.json()['request']
    assert row['amount'] == 45567.18
    assert row['base_amount_cny'] == 328083.70
    assert row['fx_rate_cny_per_unit'] == 7.2
    response = client.patch(url+'/bulk',json={'creates':[{**payload,'summary':'第11期','amount':3600}], 'updates':[], 'deletes':[]})
    assert response.status_code == 200, response.text
    assert len(client.get(url).json()['requests']) == 2
    assert client.get(f'/api/batches/{bid}').json()['batch']['request_count'] == 2

def test_missing_fx_is_atomic_and_actionable(client,monkeypatch):
    bid = batch(client)
    def fail(*args): raise main.FxRateError('未找到 USD 汇率')
    monkeypatch.setattr(main,'fetch_rates',fail)
    r = client.patch(f'/api/batches/{bid}/requests/bulk',json={'creates':[{'source_sheet':SHEET,'amount':1},{'source_sheet':SHEET,'amount':3600,'currency':'USD'}]})
    assert r.status_code == 400
    assert '汇率' in r.text
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM payment_requests WHERE batch_id=?',(bid,)).fetchone()[0] == 0

def test_finance_workbook_aliases(tmp_path):
    path = tmp_path/'美元.xlsx'; path.write_bytes(workbook_bytes())
    rows, _ = parse_weekly_excel(path)
    assert len(rows) == 3
    assert rows[0]['payee_name'] == 'Yuyang'
    assert rows[0]['bank_name'] == '招商银行'
    assert rows[0]['payment_account'] == '美金'
    assert rows[0]['expense_type'] == '材料款-美金'
    assert rows[0]['invoice_status'] == '无票'
    assert rows[0]['payee_account'] == '769911864332088'
    assert '\n' in rows[0]['summary'] and '\t' in rows[0]['summary']

@pytest.mark.parametrize('merge',[False,True])
def test_three_foreign_rows_import_and_repeat_merge_preserve_installments(client,merge):
    bid = batch(client)
    files = {'file':('美元.xlsx',workbook_bytes(uuid.uuid4().hex),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    data = {'batch_id':str(bid),'target_sheet':SHEET}
    if merge:
        preview = client.post('/api/import/weekly-excel/merge-preview',files=files,data=data)
        assert preview.status_code == 200, preview.text
        r = client.post(f"/api/import-jobs/{preview.json()['job_id']}/apply-merge",json={'resolutions':[],'payment_dates':{}})
    else:
        r = client.post('/api/import/weekly-excel',files=files,data=data)
    assert r.status_code == 200, r.text
    rows = client.get(f'/api/batches/{bid}/requests').json()['requests']
    assert len(rows) == 3
    assert {row['source_sheet'] for row in rows} == {SHEET}
    assert sum(row['amount'] for row in rows) == 52767.18
    assert {row['currency'] for row in rows} == {'USD'}
    ids = {row['id'] for row in rows}
    with connect() as conn:
        states = daily_snapshot(conn,date(2026,10,17),allowed_sheets={SHEET},include_details=True)
    assert len([x for x in states['items'] if x['source_request_id'] in ids]) == 3
    preview = client.post('/api/import/weekly-excel/merge-preview',files=files,data=data)
    assert preview.status_code == 200, preview.text
    r = client.post(f"/api/import-jobs/{preview.json()['job_id']}/apply-merge",json={'resolutions':[],'payment_dates':{}})
    assert r.status_code == 200, r.text
    rows_after = client.get(f'/api/batches/{bid}/requests').json()['requests']
    assert len(rows_after) == 3
    assert {r['id'] for r in rows_after} == ids
    assert {r['summary'] for r in rows_after} == {r['summary'] for r in rows}

def test_installment_identity_survives_edits_and_rollover(client):
    bid = batch(client)
    approval = uuid.uuid4().hex
    url = f'/api/batches/{bid}/requests'
    rows=[]
    for n in (11,12):
        r=client.post(url,json={'source_sheet':SHEET,'dingding_id':approval,'summary':f'尾款第{n}期','amount':3600,'currency':'USD','needed_payment_date':'2026-09-11'})
        assert r.status_code==200,r.text
        rows.append(r.json()['request'])
    keys={r['payable_item_key'] for r in rows}
    assert len(keys)==2
    r=client.patch(f"{url}/{rows[0]['id']}",json={'summary':'第11期摘要补充','needed_payment_date':'2026-09-18','expected_version':rows[0]['version']})
    assert r.status_code==200,r.text
    assert r.json()['request']['payable_item_key']==rows[0]['payable_item_key']
    copied=client.post(f'/api/batches/{bid}/rollover',json={'name':'分期结转','copy_mode':'all'})
    assert copied.status_code==200,copied.text
    target=client.get(f"/api/batches/{copied.json()['batch']['id']}/requests").json()['requests']
    assert {r['payable_item_key'] for r in target}==keys
    assert len(target)==2
    with connect() as conn:
        result=daily_snapshot(conn,date(2026,10,17),allowed_sheets={SHEET},include_details=True)
    assert len([r for r in result['items'] if r['dingding_id']==approval])==2


def test_merge_never_overwrites_a_different_installment(client):
    bid=batch(client)
    response=client.post(f'/api/batches/{bid}/requests',json={'source_sheet':SHEET,'dingding_id':'USD-2','summary':'海天注塑机尾款第10期','amount':3600,'needed_payment_date':'2026-08-11','currency':'USD'})
    assert response.status_code==200,response.text
    files={'file':('美元.xlsx',workbook_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    preview=client.post('/api/import/weekly-excel/merge-preview',files=files,data={'batch_id':str(bid),'target_sheet':SHEET})
    assert preview.status_code==200,preview.text
    assert preview.json()['summary']['conflict']==2
    result=client.post(f"/api/import-jobs/{preview.json()['job_id']}/apply-merge",json={'resolutions':[],'payment_dates':{}})
    assert result.status_code==400,result.text
    rows=client.get(f'/api/batches/{bid}/requests').json()['requests']
    assert len(rows)==1 and rows[0]['summary']=='海天注塑机尾款第10期'

@pytest.mark.parametrize('route',['single','bulk','import','merge'])
def test_explicit_sheet_wins_over_applicant_department(client,route):
    name='映射申请人-'+uuid.uuid4().hex
    with connect() as conn:
        conn.execute("INSERT INTO employee_department_mappings (user_id,employee_name,second_level_department,source_file,imported_by,imported_at) VALUES (?,?,?,'test',1,?)",(name,name,'国内制造',main.now_iso()))
    bid=batch(client)
    data={'source_sheet':SHEET,'applicant':name,'summary':'明确选择模具Sheet','amount':1,'currency':'USD'}
    if route=='single':
        result=client.post(f'/api/batches/{bid}/requests',json=data)
    elif route=='bulk':
        result=client.patch(f'/api/batches/{bid}/requests/bulk',json={'creates':[data]})
    else:
        wb=Workbook(); ws=wb.active; ws.title='明细'; ws.append(['钉钉申请单号','申请人','摘要','应付金额','货币类型']); ws.append([uuid.uuid4().hex,name,data['summary'],1,'USD']); output=io.BytesIO(); wb.save(output)
        files={'file':('输入.xlsx',output.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        if route=='import':
            result=client.post('/api/import/weekly-excel',files=files,data={'batch_id':str(bid),'target_sheet':SHEET})
        else:
            preview=client.post('/api/import/weekly-excel/merge-preview',files=files,data={'batch_id':str(bid),'target_sheet':SHEET})
            assert preview.status_code==200,preview.text
            result=client.post(f"/api/import-jobs/{preview.json()['job_id']}/apply-merge",json={'resolutions':[],'payment_dates':{}})
    assert result.status_code==200,result.text
    rows=client.get(f'/api/batches/{bid}/requests').json()['requests']
    assert len(rows)==1 and rows[0]['source_sheet']==SHEET
    updated=client.patch(f"/api/batches/{bid}/requests/{rows[0]['id']}",json={'applicant':name,'source_sheet':SHEET,'remark':'保留手选Sheet','expected_version':rows[0]['version']})
    assert updated.status_code==200,updated.text
    assert updated.json()['request']['source_sheet']==SHEET

def test_manual_installments_can_receive_approval_number_after_creation(client):
    bid=batch(client); approval=uuid.uuid4().hex; ids=[]
    for n in (11,12):
        r=client.post(f'/api/batches/{bid}/requests',json={'source_sheet':SHEET,'summary':f'后补单号第{n}期','amount':3600,'currency':'USD','needed_payment_date':'2026-09-11'})
        assert r.status_code==200,r.text
        row=r.json()['request']; ids.append(row['id'])
        updated=client.patch(f"/api/batches/{bid}/requests/{row['id']}",json={'dingding_id':approval,'expected_version':row['version']})
        assert updated.status_code==200,updated.text
    with connect() as conn:
        result=daily_snapshot(conn,date(2026,10,17),allowed_sheets={SHEET},include_details=True)
    matches=[r for r in result['items'] if r['source_request_id'] in ids]
    assert len(matches)==2
    assert sum(r['pending_amount'] for r in matches)==7200
