import json
from contextlib import contextmanager
from datetime import date

import pytest

from backend.app import external_expenses as expenses


CODE = "PROC-8B1B8902-DC25-45AA-B976-2C920F639E77"


def logistics_instance(*, region="中国 China", total="127", amounts=(60, 67)):
    return {
        "source_id": "915412", "process_instance_id": "logistics-instance",
        "process_code": CODE, "create_time": "2026-11-09T09:36:00+00:00",
        "updated_at": "2026-11-09T10:00:00+00:00", "status": "RUNNING", "result": "agree",
        "title": "示例申请人提交的物流费用支出Gastos logísticos",
        "raw_payload": {
            "businessId": "202611091736000049307", "originatorUserId": "logistics-user",
            "originatorDeptName": "拉丁购",
            "formComponentValues": [
                {"name": "执行地区", "value": region},
                {"name": "币种Moneda", "value": "人民币 CNY"},
                {"name": "服务主体Entidad prestadora del servicio", "value": '["拉丁购"]'},
                {"name": "物流服务商Proveedor de servicios logísticos", "value": "示例运输公司"},
                {"name": "结算方式Método de liquidación", "value": "月结"},
                {"name": "物流费用明细", "value": json.dumps([
                    {"rowValue": [
                        {"label": "估算金额", "value": "99999"},
                        {"label": "本次申请付款金额Importe de pago solicitado", "value": str(amount)},
                        {"label": "费用项目Concepto de gasto", "value": "运输费"},
                        {"label": "对应物流单号Número de guía correspondiente", "value": f"DEMO-WB-{i}"},
                        {"label": "费用说明（选填）Descripción del gasto (opcional)", "value": f"本次运费{i}"},
                    ]} for i, amount in enumerate(amounts, 1)
                ])},
                {"name": "本次申请付款总额Importe total del pago solicitado", "value": total},
                {"name": "期望付款日期Fecha prevista de pago", "value": "2026-11-13"},
                {"name": "收款账户Cuenta beneficiaria", "value": "示例运输公司 收款信息"},
                {"name": "账单及费用凭证", "value": json.dumps([{"fileId": "logistics-file", "fileName": "账单.pdf"}])},
            ],
        },
    }


@pytest.mark.parametrize("total", ["127", None])
def test_logistics_uses_current_payment_details_and_preserves_provenance(total):
    mapped = expenses.map_process_expense(logistics_instance(total=total), {"logistics-user": "示例申请人"}, source_type="logistics")
    assert mapped["source_type"] == "logistics"
    assert mapped["source_label"] == "物流费用支出"
    assert mapped["amount"] == 127 and mapped["currency"] == "CNY"
    assert mapped["needed_payment_date"] == "2026-11-13"
    assert mapped["beneficiary"] == "示例运输公司 收款信息"
    assert mapped["expected_payment_account"] == "拉丁购公司账户"
    assert not mapped["errors"]
    source = mapped["request_data"]["raw_extra"]["external_source"]
    assert source["process_code"] == CODE
    assert source["process_instance_id"] == "logistics-instance"
    assert [r["amount"] for r in source["logistics_payment_details"]] == [60, 67]
    assert "DEMO-WB-1" in mapped["summary"] and "DEMO-WB-2" in mapped["summary"]
    attachments = expenses._process_attachments(logistics_instance(), source_type="logistics")
    assert attachments[0]["source_type"] == "logistics"
    assert attachments[0]["attachment_id"].startswith("logistics-")
    assert attachments[0]["file_id"] == "logistics-file"


@pytest.mark.parametrize("total,amounts", [("128", (60, 67)), ("100", (60, "")), ("10", (-50, 60)), (None, ()), ("100", ()), ("0", (0,))])
def test_logistics_invalid_money_is_blocked(total, amounts):
    mapped = expenses.map_process_expense(logistics_instance(total=total, amounts=amounts), source_type="logistics")
    assert mapped["errors"]


def test_logistics_preview_fetch_metadata_and_attachments_use_existing_pipeline(monkeypatch):
    rows = [logistics_instance(), logistics_instance(region="墨西哥 México")]
    rows[1] = {**rows[1], "source_id": "915413", "raw_payload": {**rows[1]["raw_payload"], "businessId": "202611091736000049308"}}
    calls = []

    @contextmanager
    def source_connection(*args, **kwargs):
        class Connection:
            def execute(self, sql, params):
                calls.append((sql, params))
                assert "process_code" in sql and CODE in str(params)
                assert "deleted_at IS NULL" in sql
                return self

            def fetchall(self):
                return rows
        yield Connection()

    monkeypatch.setattr(expenses, "source_connection", source_connection)
    monkeypatch.setattr(expenses, "source_database_config", lambda: type("Config", (), {"user_dbname": "dingtalk_oa"})())
    monkeypatch.setattr(expenses, "fetch_dingtalk_user_names", lambda ids: {"logistics-user": "示例申请人"})
    # No operation/purchase rows in this fixture; keep their SQL source separate.
    original_connection = source_connection
    @contextmanager
    def connection(dbname=None):
        if dbname:
            with original_connection() as conn:
                yield conn
        else:
            class Empty:
                def execute(self, *args): return self
                def fetchall(self): return []
            yield Empty()
    monkeypatch.setattr(expenses, "source_connection", connection)
    preview = expenses.preview_external_expenses(date_from=date(2026, 11, 1), date_to=date(2026, 11, 13), source_types=["logistics"])
    assert [row["source_type"] for row in preview["rows"]] == ["logistics"]
    assert preview["rows"][0]["amount"] == 127
    assert preview["applicant_options"][0]["name"] == "示例申请人"
    fetched = expenses.fetch_external_expenses([{"source_type": "logistics", "source_id": "915412"}])
    assert fetched and fetched[0]["source_type"] == "logistics"
    metadata = expenses.fetch_external_expense_metadata([rows[0]["raw_payload"]["businessId"]])
    assert metadata[0]["source_type"] == "logistics"
    attachments = expenses.fetch_external_expense_attachments([rows[0]["raw_payload"]["businessId"]])
    assert attachments[0]["source_type"] == "logistics"
    assert any("id::text = ANY" in sql for sql, _ in calls)


def test_logistics_discovery_has_independent_cursor_and_running_rechecks():
    class Gateway:
        def fetch_source_changes(self, source_type, cursor, running):
            if source_type != "logistics": return []
            assert cursor == {"updated_at": "2026-11-01T00:00:00+08:00", "source_id": "1"}
            assert running == ("running-approval",)
            return [logistics_instance()]
        def fetch_user_names(self, ids): return {"logistics-user": "示例申请人"}
    result = expenses.discover_expense_workflows({"logistics": {"updated_at": "2026-11-01T00:00:00+08:00", "source_id": "1"}}, ["running-approval"], gateway=Gateway())
    assert len(result.candidates) == 1
    assert result.candidates[0]["process_code"] == CODE
    assert result.next_cursors["logistics"]["source_id"] == "915412"


def test_logistics_queries_pin_process_and_ignore_dates_for_exact_approval(monkeypatch):
    calls = []
    @contextmanager
    def connection(*args):
        class ReadOnly:
            def execute(self, sql, params):
                calls.append((sql, params))
                return self
            def fetchall(self): return []
        yield ReadOnly()
    monkeypatch.setattr(expenses, "source_connection", connection)
    monkeypatch.setattr(expenses, "source_database_config", lambda: type("Config", (), {"user_dbname": "dingtalk_oa"})())
    expenses._process_instance_query(source_type="logistics", approval_no="202611091736000049307")
    sql, params = calls[-1]
    assert params[0] == CODE
    assert "BETWEEN" not in sql
    assert "deleted_at IS NULL" in sql
    assert "ANY(%s)" in sql and "COMPLETED" in str(params) and "RUNNING" in str(params)
    assert "refus" in str(params)
    cursor = {"updated_at": "2026-11-01T00:00:00+08:00", "source_id": "1"}
    expenses.PostgresDiscoveryGateway().fetch_source_changes("logistics", cursor, ["running-no"])
    sql, params = calls[-1]
    assert params[0] == CODE
    assert "COALESCE(updated_at, create_time) >" in sql
    assert ["running-no"] in params
