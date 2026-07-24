import base64
import io
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

TEST_DIR = Path(tempfile.mkdtemp(prefix="cashier-payment-tests-"))
os.environ["PAYMENT_APP_DATA_DIR"] = str(TEST_DIR / "data")
os.environ["PAYMENT_APP_DB"] = str(TEST_DIR / "app.db")

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from backend.app.db import connect, now_iso
from backend.app.external_expenses import ExternalExpenseError, _preview_conditions, applicant_name_from_title, map_external_expense, valid_applicant_name
from backend.app.excel_io import export_workbook
from backend.app import main as main_module
from backend.app.main import app
from backend.app.normalize_payment_data import normalize_payment_data
from backend.app.rebuild_weekly_data import rebuild_weekly_data


SAMPLE = Path("/Users/smk/Downloads/20260626~20260707请款明细.xlsx")


def login(client: TestClient, username: str = "admin", password: str = "admin123") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def test_sheet_order_is_saved_and_inherited_by_rollover():
    with TestClient(app) as client:
        login(client)
        source = client.post(
            "/api/batches",
            json={"name": "Sheet 排序来源", "start_date": "2026-07-16", "end_date": "2026-07-21"},
        ).json()["batch"]
        for sheet_name in ("采购中心", "财务中心", "供应商"):
            created = client.post(
                f"/api/batches/{source['id']}/requests",
                json={"source_sheet": sheet_name, "payment_account": "私户", "amount": 100},
            )
            assert created.status_code == 200

        order = ["供应商", "财务中心", "采购中心"]
        saved = client.put(f"/api/batches/{source['id']}/sheet-order", json={"sheet_order": order})
        assert saved.status_code == 200
        assert saved.json()["batch"]["sheet_order"] == order
        assert client.get(f"/api/batches/{source['id']}").json()["batch"]["sheet_order"] == order

        exported = client.get(f"/api/batches/{source['id']}/export.xlsx")
        assert exported.status_code == 200
        workbook = load_workbook(io.BytesIO(exported.content))
        assert workbook.sheetnames[:3] == order

        rollover = client.post(
            f"/api/batches/{source['id']}/rollover",
            json={"name": "Sheet 排序结转", "start_date": "2026-07-22", "end_date": "2026-07-28", "copy_mode": "all"},
        )
        assert rollover.status_code == 200
        assert rollover.json()["batch"]["sheet_order"] == order

        archived = client.post(f"/api/batches/{source['id']}/archive")
        assert archived.status_code == 200
        archived_order = list(reversed(order))
        reordered = client.put(f"/api/batches/{source['id']}/sheet-order", json={"sheet_order": archived_order})
        assert reordered.status_code == 200
        assert reordered.json()["batch"]["sheet_order"] == archived_order


def external_expense_test_row(
    approval_no: str,
    source_id: str,
    *,
    source_type: str = "operation",
    status: str = "RUNNING",
    amount: float = 123.45,
    beneficiary: str = "测试收款信息",
    warnings=None,
) -> dict:
    source_label = "运营支出" if source_type == "operation" else "采购支出"
    request_data = {
        "dingding_id": approval_no,
        "expense_type": "测试支出",
        "summary": "中间表测试",
        "amount": amount,
        "currency": "CNY",
        "payee_account": beneficiary or None,
        "source_sheet": "测试部门",
        "raw_extra": {
            "external_source": {
                "system": "dingtalk_expense_database",
                "table": f"approval_expense_{source_type}",
                "record_id": source_id,
                "approval_no": approval_no,
                "approval_status": status,
                "applicant_id": "test-user-id",
                "applicant": "测试申请人",
                "applicant_department": "测试部门",
                "application_date": "2026-07-15",
            }
        },
    }
    return {
        "source_type": source_type,
        "source_label": source_label,
        "source_id": source_id,
        "application_date": "2026-07-15",
        "approval_no": approval_no,
        "applicant_id": "test-user-id",
        "applicant": "测试申请人",
        "applicant_department": "测试部门",
        "approval_status": status,
        "approval_result": "agree",
        "summary": "中间表测试",
        "amount": amount,
        "beneficiary": beneficiary,
        "needed_payment_date": "2026-07-20",
        "warnings": warnings or [],
        "errors": [],
        "source_conflict": False,
        "request_data": request_data,
    }


def test_rollover_copies_only_unfinished_rows():
    if not SAMPLE.exists():
        return
    with TestClient(app) as client:
        login(client)
        with SAMPLE.open("rb") as handle:
            imported = client.post(
                "/api/import/weekly-excel",
                files={"file": (SAMPLE.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        assert imported.status_code == 200
        imported_payload = imported.json()
        source_batch_id = imported_payload["batch_id"]
        assert imported_payload["meta"]["images"]["saved"] == 9
        listed_attachments = client.get(f"/api/batches/{source_batch_id}/attachments")
        assert listed_attachments.status_code == 200
        assert len(listed_attachments.json()["attachments"]) == 9
        exported_source = client.get(f"/api/batches/{source_batch_id}/export.xlsx")
        assert exported_source.status_code == 200
        exported_workbook = load_workbook(io.BytesIO(exported_source.content))
        exported_headers = [
            cell.value
            for worksheet in exported_workbook.worksheets
            for row in worksheet.iter_rows(max_row=2)
            for cell in row
        ]
        assert "图片附件" in exported_headers
        assert "付款情况" not in exported_headers
        assert sum(len(getattr(worksheet, "_images", [])) for worksheet in exported_workbook.worksheets) == 9
        source_requests = client.get(f"/api/batches/{source_batch_id}/requests").json()["requests"]
        partial_source = next(row for row in source_requests if row["finance_review"] == "未付款" and float(row.get("amount") or 0) > 1)
        partial_source_id = partial_source["id"]
        partial_update = client.post(
            f"/api/batches/{source_batch_id}/requests/{partial_source_id}/payments",
            json={"amount": round(float(partial_source["amount"]) / 2, 2), "payment_date": "2026-07-10"},
        )
        assert partial_update.status_code == 200
        assert partial_update.json()["request"]["finance_review"] == "部分付款"
        archived = client.post(f"/api/batches/{source_batch_id}/archive")
        assert archived.status_code == 200
        rollover = client.post(
            f"/api/batches/{source_batch_id}/rollover",
            json={"name": "20260708~20260714请款明细", "start_date": "2026-07-08", "end_date": "2026-07-14"},
        )
        assert rollover.status_code == 200
        copied_count = rollover.json()["copied_count"]
        assert copied_count > 0
        target_batch_id = rollover.json()["batch"]["id"]
        requests = client.get(f"/api/batches/{target_batch_id}/requests").json()["requests"]
        assert len(requests) == copied_count
        assert all(row.get("finance_review") != "已付款" for row in requests)
        assert any(row.get("copied_from_request_id") == partial_source_id and row.get("finance_review") == "部分付款" for row in requests)
        assert all(not row.get("actual_payment_date") or row.get("finance_review") == "部分付款" for row in requests)
        assert all(row.get("copied_from_request_id") for row in requests)

        rollover_all = client.post(
            f"/api/batches/{source_batch_id}/rollover",
            json={
                "name": "20260715~20260721请款明细",
                "start_date": "2026-07-15",
                "end_date": "2026-07-21",
                "copy_mode": "all",
            },
        )
        assert rollover_all.status_code == 200
        assert rollover_all.json()["copied_count"] == 161
        assert rollover_all.json()["copy_mode"] == "all"
        target_all_batch_id = rollover_all.json()["batch"]["id"]
        all_requests = client.get(f"/api/batches/{target_all_batch_id}/requests").json()["requests"]
        assert len(all_requests) == 161
        assert any(row.get("finance_review") == "已付款" for row in all_requests)
        assert all(row.get("copied_from_request_id") for row in all_requests)
        all_attachments = client.get(f"/api/batches/{target_all_batch_id}/attachments")
        assert all_attachments.status_code == 200
        assert len(all_attachments.json()["attachments"]) == 9


def test_rollback_latest_import_restores_previous_batch_state():
    if not SAMPLE.exists():
        return
    with TestClient(app) as client:
        login(client)
        with SAMPLE.open("rb") as handle:
            first = client.post(
                "/api/import/weekly-excel",
                files={"file": (SAMPLE.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        assert first.status_code == 200
        batch_id = first.json()["batch_id"]
        with SAMPLE.open("rb") as handle:
            second = client.post(
                "/api/import/weekly-excel",
                data={"batch_id": str(batch_id)},
                files={"file": (SAMPLE.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        assert second.status_code == 200
        assert client.get(f"/api/batches/{batch_id}/requests").json()["totals"]["count"] == 322
        assert len(client.get(f"/api/batches/{batch_id}/attachments").json()["attachments"]) == 18

        rolled_back = client.post(f"/api/batches/{batch_id}/imports/latest/rollback")
        assert rolled_back.status_code == 200
        payload = rolled_back.json()
        assert payload["deleted_requests"] == 161
        assert payload["deleted_attachments"] == 9
        assert client.get(f"/api/batches/{batch_id}/requests").json()["totals"]["count"] == 161
        assert len(client.get(f"/api/batches/{batch_id}/attachments").json()["attachments"]) == 9
        with connect() as conn:
            row = conn.execute("SELECT status, imported_rows FROM import_jobs WHERE id = ?", (payload["job_id"],)).fetchone()
            assert row["status"] == "rolled_back"
            assert row["imported_rows"] == 0


def test_bulk_save_create_update_delete_and_rollback():
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "bulk-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = batch["id"]
        created = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={
                "creates": [
                    {
                        "dingding_id": "D-1",
                        "payment_account": "私户",
                        "expense_type": "材料款",
                        "summary": "测试请款",
                        "amount": 123.45,
                        "general_manager_approval": "存在争议",
                        "general_manager_approval_date": "2026-07-10",
                    }
                ],
                "updates": [],
                "deletes": [],
            },
        )
        assert created.status_code == 200
        request_id = created.json()["created"][0]
        blocked_summary_update = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={"creates": [], "updates": [{"id": request_id, "finance_review": "已付款"}], "deletes": []},
        )
        assert blocked_summary_update.status_code == 400
        paid = client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/payments",
            json={"amount": 123.45, "payment_date": "2026-07-09"},
        )
        assert paid.status_code == 200
        row = client.get(f"/api/batches/{batch_id}/requests").json()["requests"][0]
        assert row["finance_review"] == "已付款"
        assert row["payment_status"] == "已付款"
        assert row["actual_payment_date"] == "2026-07-09"
        assert row["general_manager_approval"] == "存在争议"
        assert row["general_manager_approval_date"] == "2026-07-10"
        split_manager_opinion = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={
                "creates": [],
                "updates": [{"id": request_id, "general_manager_approval": "存在争议，供应商还在谈解决方案。"}],
                "deletes": [],
            },
        )
        assert split_manager_opinion.status_code == 200
        row = client.get(f"/api/batches/{batch_id}/requests").json()["requests"][0]
        assert row["general_manager_approval"] == "存在争议"
        assert "供应商还在谈解决方案" in row["general_manager_opinion"]
        failed = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={
                "creates": [{"dingding_id": "D-rollback", "summary": "不应保存", "amount": 1}],
                "updates": [{"id": 999999, "finance_review": "已付款"}],
                "deletes": [],
            },
        )
        assert failed.status_code == 404
        rows_after_failure = client.get(f"/api/batches/{batch_id}/requests").json()["requests"]
        assert all(row.get("dingding_id") != "D-rollback" for row in rows_after_failure)
        deleted = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={"creates": [], "updates": [], "deletes": [request_id]},
        )
        assert deleted.status_code == 200
        assert client.get(f"/api/batches/{batch_id}/requests").json()["totals"]["count"] == 0


def test_partial_payment_amounts_are_calculated_and_summarized():
    with TestClient(app) as client:
        login(client)
        batch = client.post(
            "/api/batches",
            json={"name": "partial-payment-test", "start_date": "2026-07-01", "end_date": "2026-07-07"},
        ).json()["batch"]
        batch_id = batch["id"]
        created = client.post(
            f"/api/batches/{batch_id}/requests",
            json={"summary": "分三次付款", "amount": 100},
        )
        assert created.status_code == 200
        row = created.json()["request"]
        assert row["paid_amount"] == 0
        assert row["pending_amount"] == 100
        assert row["finance_review"] == "未付款"

        direct_update = client.patch(
            f"/api/batches/{batch_id}/requests/{row['id']}",
            json={"paid_amount": 30},
        )
        assert direct_update.status_code == 400

        for index, amount in enumerate((30, 20, 50), start=1):
            payment = client.post(
                f"/api/batches/{batch_id}/requests/{row['id']}/payments",
                json={"amount": amount, "payment_date": f"2026-07-{index + 9:02d}", "payer": f"付款人{index}"},
            )
            assert payment.status_code == 200
            updated_row = payment.json()["request"]
            expected_paid = sum((30, 20, 50)[:index])
            assert updated_row["paid_amount"] == expected_paid
            assert updated_row["pending_amount"] == 100 - expected_paid
            assert updated_row["finance_review"] == ("已付款" if index == 3 else "部分付款")
            assert updated_row["general_manager_approval"] == ("同意付款" if index == 3 else None)

        detail = client.get(f"/api/batches/{batch_id}/requests/{row['id']}/payments").json()
        assert detail["summary"]["payment_count"] == 3
        assert len(detail["payments"]) == 3

        overpaid = client.post(
            f"/api/batches/{batch_id}/requests/{row['id']}/payments",
            json={"amount": 0.01, "payment_date": "2026-07-13"},
        )
        assert overpaid.status_code == 400

        totals = client.get(f"/api/batches/{batch_id}/requests").json()["totals"]
        assert totals == {"count": 1, "amount": 100.0, "paid_amount": 100.0, "pending_amount": 0.0}
        batch_totals = next(item for item in client.get("/api/batches").json()["batches"] if item["id"] == batch_id)
        assert batch_totals["total_amount"] == 100
        assert batch_totals["total_paid_amount"] == 100
        assert batch_totals["total_pending_amount"] == 0

        increased = client.patch(
            f"/api/batches/{batch_id}/requests/{row['id']}",
            json={"amount": 120},
        )
        assert increased.status_code == 200
        assert increased.json()["request"]["paid_amount"] == 100
        assert increased.json()["request"]["pending_amount"] == 20
        assert increased.json()["request"]["finance_review"] == "部分付款"


def test_concurrent_payments_cannot_exceed_request_amount():
    with TestClient(app) as setup_client:
        login(setup_client)
        batch = setup_client.post(
            "/api/batches",
            json={"name": "concurrent-payment-test", "start_date": "2026-07-01", "end_date": "2026-07-07"},
        ).json()["batch"]
        request = setup_client.post(
            f"/api/batches/{batch['id']}/requests",
            json={"summary": "并发付款", "amount": 100},
        ).json()["request"]

    def submit_payment() -> int:
        with TestClient(app) as client:
            login(client)
            response = client.post(
                f"/api/batches/{batch['id']}/requests/{request['id']}/payments",
                json={"amount": 80, "payment_date": "2026-07-10"},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: submit_payment(), range(2)))
    assert statuses == [200, 400]
    with TestClient(app) as client:
        login(client)
        detail = client.get(f"/api/batches/{batch['id']}/requests/{request['id']}/payments").json()
        assert detail["summary"]["paid_amount"] == 80
        assert detail["summary"]["pending_amount"] == 20
        assert detail["summary"]["payment_count"] == 1


def test_admin_can_restore_archived_batch_to_draft():
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "status-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = batch["id"]
        archived = client.post(f"/api/batches/{batch_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["batch"]["status"] == "archived"
        restored = client.post(f"/api/batches/{batch_id}/unarchive")
        assert restored.status_code == 200
        assert restored.json()["batch"]["status"] == "draft"
        logs = client.get(f"/api/batches/{batch_id}/audit")
        assert logs.status_code == 200
        actions = [log["action"] for log in logs.json()["logs"]]
        assert "batch.archive" in actions
        assert "batch.unarchive" in actions


def test_all_roles_can_change_own_password_and_other_sessions_are_revoked():
    accounts = [
        ("self-password-business", "业务改密", "business", "business-old", "business-new"),
        ("self-password-finance", "财务改密", "finance", "finance-old", "finance-new"),
        ("self-password-manager", "总经理改密", "general_manager", "manager-old", "manager-new"),
    ]
    with TestClient(app) as admin_client:
        login(admin_client)
        created_user_ids = []
        for username, display_name, role, old_password, _ in accounts:
            created = admin_client.post(
                "/api/admin/users",
                json={"username": username, "password": old_password, "display_name": display_name, "role": role, "active": True},
            )
            assert created.status_code == 200
            created_user_ids.append(created.json()["user"]["id"])

        business = accounts[0]
        with TestClient(app) as current_client, TestClient(app) as other_client:
            login(current_client, business[0], business[3])
            login(other_client, business[0], business[3])

            assert current_client.post(
                "/api/auth/change-password",
                json={"current_password": "", "new_password": business[4], "confirm_password": business[4]},
            ).status_code == 400
            assert current_client.post(
                "/api/auth/change-password",
                json={"current_password": business[3], "new_password": "12345", "confirm_password": "12345"},
            ).status_code == 400
            assert current_client.post(
                "/api/auth/change-password",
                json={"current_password": business[3], "new_password": business[4], "confirm_password": "different"},
            ).status_code == 400
            assert current_client.post(
                "/api/auth/change-password",
                json={"current_password": "wrong-password", "new_password": business[4], "confirm_password": business[4]},
            ).status_code == 400
            assert current_client.post(
                "/api/auth/change-password",
                json={"current_password": business[3], "new_password": business[3], "confirm_password": business[3]},
            ).status_code == 400

            changed = current_client.post(
                "/api/auth/change-password",
                json={"current_password": business[3], "new_password": business[4], "confirm_password": business[4]},
            )
            assert changed.status_code == 200
            assert changed.json() == {"status": "ok", "signed_out_sessions": 1}
            assert current_client.get("/api/me").status_code == 200
            assert other_client.get("/api/me").status_code == 401

        old_login = admin_client.post("/api/auth/login", json={"username": business[0], "password": business[3]})
        assert old_login.status_code == 401
        with TestClient(app) as new_login_client:
            login(new_login_client, business[0], business[4])

        for username, _, _, old_password, new_password in accounts[1:]:
            with TestClient(app) as role_client:
                login(role_client, username, old_password)
                changed = role_client.post(
                    "/api/auth/change-password",
                    json={"current_password": old_password, "new_password": new_password, "confirm_password": new_password},
                )
                assert changed.status_code == 200
                assert role_client.get("/api/me").status_code == 200
            with TestClient(app) as new_login_client:
                login(new_login_client, username, new_password)

        admin_changed = admin_client.post(
            "/api/auth/change-password",
            json={"current_password": "admin123", "new_password": "admin456", "confirm_password": "admin456"},
        )
        assert admin_changed.status_code == 200
        assert admin_client.get("/api/me").status_code == 200
        admin_restored = admin_client.post(
            "/api/auth/change-password",
            json={"current_password": "admin456", "new_password": "admin123", "confirm_password": "admin123"},
        )
        assert admin_restored.status_code == 200

        with connect() as conn:
            audit_rows = conn.execute(
                "SELECT old_value_json, new_value_json FROM audit_logs WHERE action = 'user.change_password'"
            ).fetchall()
        assert len(audit_rows) >= 5
        audit_payload = json.dumps([dict(row) for row in audit_rows], ensure_ascii=False)
        assert "password_hash" not in audit_payload
        for password in [value for account in accounts for value in account[3:]] + ["admin123", "admin456"]:
            assert password not in audit_payload
        for user_id in created_user_ids:
            assert admin_client.delete(f"/api/admin/users/{user_id}").status_code == 200


def test_role_permissions_user_crud_and_audit_logs():
    with TestClient(app) as admin_client, TestClient(app) as business_client, TestClient(app) as finance_client, TestClient(app) as manager_client:
        login(admin_client)
        assert admin_client.get("/api/me").json()["user"]["role"] == "admin"
        with connect() as conn:
            active_roles = {
                row["value"]
                for row in conn.execute("SELECT value FROM dictionaries WHERE kind = 'role' AND active = 1").fetchall()
            }
        assert active_roles == {"business", "finance", "general_manager", "admin"}
        admin_users = admin_client.get("/api/admin/users")
        assert admin_users.status_code == 200

        business_user = admin_client.post(
            "/api/admin/users",
            json={"username": "biz-user", "password": "biz123", "display_name": "业务", "role": "business", "active": True},
        )
        assert business_user.status_code == 200
        finance_user = admin_client.post(
            "/api/admin/users",
            json={"username": "finance-user", "password": "fin123", "display_name": "财务", "role": "finance", "active": True},
        )
        assert finance_user.status_code == 200
        finance_user_id = finance_user.json()["user"]["id"]
        manager_user = admin_client.post(
            "/api/admin/users",
            json={"username": "gm-user", "password": "gm123", "display_name": "总经理", "role": "general_manager", "active": True},
        )
        assert manager_user.status_code == 200
        manager_user_id = manager_user.json()["user"]["id"]

        batch = admin_client.post("/api/batches", json={"name": "permission-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = batch["id"]
        created = admin_client.post(
            f"/api/batches/{batch_id}/requests",
            json={"summary": "权限测试", "amount": 10, "general_manager_approval": "同意付款"},
        )
        assert created.status_code == 200
        request_id = created.json()["request"]["id"]

        login(business_client, "biz-user", "biz123")
        blocked_business = business_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"summary": "业务改摘要", "finance_review": "已付款"},
        )
        assert blocked_business.status_code == 400
        allowed_business = business_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"summary": "业务改摘要"},
        )
        assert allowed_business.status_code == 200
        assert allowed_business.json()["request"]["summary"] == "业务改摘要"
        blocked_business_create = business_client.post(
            f"/api/batches/{batch_id}/requests",
            json={
                "summary": "业务新增",
                "amount": 20,
                "finance_review": "已付款",
                "actual_payment_date": "2026-07-10",
                "general_manager_opinion": "不应保存",
            },
        )
        assert blocked_business_create.status_code == 400
        business_create = business_client.post(
            f"/api/batches/{batch_id}/requests",
            json={"summary": "业务新增", "amount": 20},
        )
        assert business_create.status_code == 200
        business_row = business_create.json()["request"]
        assert business_row["finance_review"] == "未付款"
        assert business_row["payment_status"] == "未付款"
        assert business_row["actual_payment_date"] is None
        assert business_row["general_manager_opinion"] is None

        login(finance_client, "finance-user", "fin123")
        blocked_payment_api = business_client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/payments",
            json={"amount": 1, "payment_date": "2026-07-10"},
        )
        assert blocked_payment_api.status_code == 403
        partial_finance_update = finance_client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/payments",
            json={"amount": 4, "payment_date": "2026-07-10"},
        )
        assert partial_finance_update.status_code == 200
        assert partial_finance_update.json()["request"]["finance_review"] == "部分付款"
        finance_update = finance_client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/payments",
            json={"amount": 6, "payment_date": "2026-07-11"},
        )
        assert finance_update.status_code == 200
        assert finance_update.json()["request"]["finance_review"] == "已付款"
        blocked_finance = finance_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"general_manager_opinion": "财务不能改总经理意见"},
        )
        assert blocked_finance.status_code == 403

        login(manager_client, "gm-user", "gm123")
        manager_users = manager_client.get("/api/admin/users")
        assert manager_users.status_code == 200
        manager_update = manager_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"general_manager_opinion": "总经理可改"},
        )
        assert manager_update.status_code == 200
        assert manager_update.json()["request"]["general_manager_opinion"] == "总经理可改"

        reset = admin_client.patch(f"/api/admin/users/{finance_user_id}", json={"password": "fin456", "display_name": "财务改"})
        assert reset.status_code == 200
        assert reset.json()["user"]["display_name"] == "财务改"
        relogin = TestClient(app)
        with relogin as client:
            login(client, "finance-user", "fin456")
        default_reset = admin_client.post(f"/api/admin/users/{finance_user_id}/reset-password")
        assert default_reset.status_code == 200
        assert default_reset.json()["password"] == "123456"
        reset_login = TestClient(app)
        with reset_login as client:
            login(client, "finance-user", "123456")

        blocked_self_delete = admin_client.delete("/api/admin/users/1")
        assert blocked_self_delete.status_code == 400
        business_user_id = business_user.json()["user"]["id"]
        deactivated = admin_client.patch(f"/api/admin/users/{business_user_id}", json={"active": False})
        assert deactivated.status_code == 200
        listed_after_deactivate = admin_client.get("/api/admin/users").json()["users"]
        inactive_business = next(user for user in listed_after_deactivate if user["id"] == business_user_id)
        assert inactive_business["active"] is False
        inactive_login = business_client.post("/api/auth/login", json={"username": "biz-user", "password": "biz123"})
        assert inactive_login.status_code == 401
        deleted_business = admin_client.delete(f"/api/admin/users/{business_user_id}")
        assert deleted_business.status_code == 200
        listed_after_delete = admin_client.get("/api/admin/users").json()["users"]
        assert all(user["id"] != business_user_id for user in listed_after_delete)
        manager_deactivated = admin_client.delete(f"/api/admin/users/{manager_user_id}")
        assert manager_deactivated.status_code == 200
        blocked_last_privileged = admin_client.patch("/api/admin/users/1", json={"role": "finance"})
        assert blocked_last_privileged.status_code == 400

        logs = admin_client.get(f"/api/batches/{batch_id}/audit").json()["logs"]
        actions = [log["action"] for log in logs]
        assert "request.create" in actions
        assert "request.update" in actions
        assert "payment.create" in actions
        with connect() as conn:
            user_actions = {
                row["action"]
                for row in conn.execute("SELECT action FROM audit_logs WHERE entity_type = 'user'").fetchall()
            }
        assert {"user.create", "user.update", "user.deactivate", "user.delete", "user.reset_password"} <= user_actions


def test_image_attachment_upload_and_export():
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "image-export-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = batch["id"]
        created = client.post(
            f"/api/batches/{batch_id}/requests",
            json={
                "dingding_id": "IMG-1",
                "summary": "带图片附件",
                "amount": 100,
                "source_sheet": "图片测试",
                "general_manager_approval_date": "2026-07-10",
            },
        )
        assert created.status_code == 200
        request_id = created.json()["request"]["id"]
        uploaded = client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/attachments/image",
            data={"label": "付款截图"},
            files={"file": ("proof.png", io.BytesIO(png_bytes), "image/png")},
        )
        assert uploaded.status_code == 200
        attachment = uploaded.json()["attachment"]
        assert attachment["attachment_type"] == "image"
        assert attachment["file_url"].endswith(f"/api/attachments/{attachment['id']}/file")
        listed = client.get(f"/api/batches/{batch_id}/attachments")
        assert listed.status_code == 200
        assert len(listed.json()["attachments"]) == 1
        downloaded = client.get(attachment["file_url"])
        assert downloaded.status_code == 200
        assert "inline" in downloaded.headers["content-disposition"].lower()
        assert downloaded.content == png_bytes
        exported = client.get(f"/api/batches/{batch_id}/export.xlsx")
        assert exported.status_code == 200
        workbook = load_workbook(io.BytesIO(exported.content))
        worksheet = workbook[workbook.sheetnames[0]]
        headers = [cell.value for cell in next(worksheet.iter_rows(max_row=1))]
        assert "图片附件" in headers
        assert "财务付款时间" in headers
        assert "总经理审批时间" in headers
        assert "总经理意见" in headers
        assert worksheet._images


def test_restore_draft_baseline_restores_saved_rows_and_attachments():
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    second_png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8BQz0AEYBxVSFIBADuVBAXnTKiNAAAAAElFTkSuQmCC"
    )
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "restore-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = batch["id"]
        created = client.post(
            f"/api/batches/{batch_id}/requests",
            json={"dingding_id": "RESTORE-1", "summary": "基线记录", "amount": 100, "source_sheet": "还原测试"},
        )
        assert created.status_code == 200
        request_id = created.json()["request"]["id"]
        uploaded = client.post(
            f"/api/batches/{batch_id}/requests/{request_id}/attachments/image",
            files={"file": ("baseline.png", io.BytesIO(png_bytes), "image/png")},
        )
        assert uploaded.status_code == 200
        baseline_attachment = uploaded.json()["attachment"]
        baseline_file_path = TEST_DIR / "data" / baseline_attachment["file_path"]
        assert baseline_file_path.exists()
        baseline = client.post(f"/api/batches/{batch_id}/snapshots/baseline")
        assert baseline.status_code == 200
        assert baseline.json()["snapshot"]["request_count"] == 1
        assert baseline.json()["snapshot"]["attachment_count"] == 1

        updated = client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"summary": "已经保存的错误修改", "amount": 999},
        )
        assert updated.status_code == 200
        extra = client.post(
            f"/api/batches/{batch_id}/requests",
            json={"dingding_id": "RESTORE-EXTRA", "summary": "应被还原删除", "amount": 1},
        )
        assert extra.status_code == 200
        extra_id = extra.json()["request"]["id"]
        second_attachment = client.post(
            f"/api/batches/{batch_id}/requests/{extra_id}/attachments/image",
            files={"file": ("extra.png", io.BytesIO(second_png_bytes), "image/png")},
        )
        assert second_attachment.status_code == 200
        extra_file_path = TEST_DIR / "data" / second_attachment.json()["attachment"]["file_path"]
        assert extra_file_path.exists()
        deleted_attachment = client.delete(f"/api/batches/{batch_id}/requests/{request_id}/attachments/{baseline_attachment['id']}")
        assert deleted_attachment.status_code == 200
        assert not baseline_file_path.exists()

        restored = client.post(f"/api/batches/{batch_id}/restore-baseline")
        assert restored.status_code == 200
        payload = restored.json()
        assert payload["before"]["requests"] == 2
        assert payload["after"]["requests"] == 1
        rows = client.get(f"/api/batches/{batch_id}/requests").json()["requests"]
        assert len(rows) == 1
        assert rows[0]["dingding_id"] == "RESTORE-1"
        assert rows[0]["summary"] == "基线记录"
        assert rows[0]["amount"] == 100
        attachments = client.get(f"/api/batches/{batch_id}/attachments").json()["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["id"] == baseline_attachment["id"]
        assert baseline_file_path.exists()
        assert not extra_file_path.exists()
        downloaded = client.get(attachments[0]["file_url"])
        assert downloaded.status_code == 200
        assert downloaded.content == png_bytes
        with connect() as conn:
            pre_restore_count = conn.execute(
                "SELECT COUNT(*) AS count FROM batch_snapshots WHERE batch_id = ? AND snapshot_type = 'pre_restore'",
                (batch_id,),
            ).fetchone()["count"]
            assert pre_restore_count == 1
        actions = [log["action"] for log in client.get(f"/api/batches/{batch_id}/audit").json()["logs"]]
        assert "batch.snapshot.baseline" in actions
        assert "batch.restore_baseline" in actions


def test_payment_vouchers_snapshot_rollover_and_archived_corrections():
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    with TestClient(app) as client:
        login(client)
        batch = client.post(
            "/api/batches",
            json={"name": "payment-snapshot-test", "start_date": "2026-07-01", "end_date": "2026-07-07"},
        ).json()["batch"]
        batch_id = batch["id"]
        request = client.post(
            f"/api/batches/{batch_id}/requests",
            json={"dingding_id": "PAY-SNAPSHOT-1", "summary": "付款快照", "amount": 100, "source_sheet": "付款测试"},
        ).json()["request"]
        payment = client.post(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments",
            json={"amount": 40, "payment_date": "2026-07-10", "payer": "出纳A", "bank_reference": "FLOW-1"},
        ).json()["payment"]
        image_voucher = client.post(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}/vouchers",
            files={"file": ("proof.png", io.BytesIO(png_bytes), "image/png")},
        )
        assert image_voucher.status_code == 200
        assert image_voucher.json()["voucher"]["voucher_type"] == "image"
        pdf_voucher = client.post(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}/vouchers",
            files={"file": ("proof.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        )
        assert pdf_voucher.status_code == 200
        assert pdf_voucher.json()["voucher"]["voucher_type"] == "pdf"
        baseline = client.post(f"/api/batches/{batch_id}/snapshots/baseline")
        assert baseline.status_code == 200
        assert baseline.json()["snapshot"]["payment_count"] == 1
        assert baseline.json()["snapshot"]["payment_voucher_count"] == 2

        changed = client.patch(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}",
            json={"amount": 60, "payment_date": "2026-07-11"},
        )
        assert changed.status_code == 200
        deleted_voucher = client.delete(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}/vouchers/{image_voucher.json()['voucher']['id']}"
        )
        assert deleted_voucher.status_code == 200
        restored = client.post(f"/api/batches/{batch_id}/restore-baseline")
        assert restored.status_code == 200
        restored_detail = client.get(f"/api/batches/{batch_id}/requests/{request['id']}/payments").json()
        assert restored_detail["summary"]["paid_amount"] == 40
        assert restored_detail["summary"]["pending_amount"] == 60
        assert len(restored_detail["payments"]) == 1
        assert len(restored_detail["payments"][0]["vouchers"]) == 2

        rollover = client.post(
            f"/api/batches/{batch_id}/rollover",
            json={"name": "payment-rollover", "start_date": "2026-07-08", "end_date": "2026-07-14", "copy_mode": "all"},
        )
        assert rollover.status_code == 200
        target_batch_id = rollover.json()["batch"]["id"]
        target_request = client.get(f"/api/batches/{target_batch_id}/requests").json()["requests"][0]
        inherited = client.get(f"/api/batches/{target_batch_id}/requests/{target_request['id']}/payments").json()["payments"][0]
        assert inherited["inherited"] is True
        assert inherited["amount"] == 40
        assert len(inherited["vouchers"]) == 2
        blocked_inherited = client.patch(
            f"/api/batches/{target_batch_id}/requests/{target_request['id']}/payments/{inherited['id']}",
            json={"amount": 30, "payment_date": "2026-07-12"},
        )
        assert blocked_inherited.status_code == 400

        source_changed = client.patch(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}",
            json={"amount": 50, "payment_date": "2026-07-12"},
        )
        assert source_changed.status_code == 200
        target_after_source_change = client.get(
            f"/api/batches/{target_batch_id}/requests/{target_request['id']}/payments"
        ).json()["payments"][0]
        assert target_after_source_change["amount"] == 40

        assert client.post(f"/api/batches/{batch_id}/archive").status_code == 200
        missing_reason = client.patch(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}",
            json={"amount": 45, "payment_date": "2026-07-12"},
        )
        assert missing_reason.status_code == 400
        corrected = client.patch(
            f"/api/batches/{batch_id}/requests/{request['id']}/payments/{payment['id']}",
            json={"amount": 45, "payment_date": "2026-07-12", "reason": "银行回单金额更正"},
        )
        assert corrected.status_code == 200
        logs = client.get(f"/api/batches/{batch_id}/audit").json()["logs"]
        assert any(log["action"] == "payment.correction" and log["reason"] == "银行回单金额更正" for log in logs)


def test_excel_payment_detail_import_export_duplicate_and_rollback(tmp_path):
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    voucher_path = tmp_path / "excel-proof.png"
    voucher_path.write_bytes(png_bytes)
    record = {
        "id": 999999,
        "dingding_id": "EXCEL-PAY-1",
        "summary": "Excel 分次付款",
        "amount": 100,
        "paid_amount": 30,
        "pending_amount": 70,
        "finance_review": "部分付款",
        "source_sheet": "Excel付款测试",
    }
    detail = {
        "request_id": 999999,
        "dingding_id": "EXCEL-PAY-1",
        "request_source_sheet": "Excel付款测试",
        "payment_date": "2026-07-10",
        "amount": 30,
        "payer": "Excel出纳",
        "payment_account": "公户",
        "bank_reference": "EXCEL-FLOW-1",
        "remark": "首笔",
        "source_type": "excel_detail",
        "vouchers": [
            {
                "id": 1,
                "payment_id": 1,
                "original_filename": "excel-proof.png",
                "mime_type": "image/png",
                "file_url": "/api/payment-vouchers/1/file",
                "absolute_path": str(voucher_path),
            }
        ],
    }
    workbook_bytes = export_workbook(
        {"name": "Excel付款导入", "end_date": "2026-07-07"},
        [record],
        {},
        [detail, {**detail, "vouchers": []}],
    )
    with TestClient(app) as client:
        login(client)
        imported = client.post(
            "/api/import/weekly-excel",
            files={
                "file": (
                    "excel-payment-detail.xlsx",
                    io.BytesIO(workbook_bytes),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200
        payload = imported.json()
        batch_id = payload["batch_id"]
        assert payload["meta"]["payment_details"]["imported"] == 1
        assert payload["meta"]["payment_details"]["duplicates"] == 1
        assert payload["meta"]["payment_details"]["saved_vouchers"] == 1
        request = client.get(f"/api/batches/{batch_id}/requests").json()["requests"][0]
        assert request["paid_amount"] == 30
        assert request["pending_amount"] == 70
        assert request["payment_count"] == 1
        payments = client.get(f"/api/batches/{batch_id}/requests/{request['id']}/payments").json()["payments"]
        assert len(payments) == 1
        assert len(payments[0]["vouchers"]) == 1

        exported = client.get(f"/api/batches/{batch_id}/export.xlsx")
        assert exported.status_code == 200
        exported_workbook = load_workbook(io.BytesIO(exported.content))
        payment_sheet = exported_workbook["付款明细"]
        headers = [cell.value for cell in payment_sheet[1]]
        assert headers[:12] == ["付款标识", "请款标识", "钉钉单号", "来源 Sheet", "付款日期", "本次金额", "付款人", "付款账户", "流水号", "备注", "来源标记", "凭证信息"]
        assert payment_sheet.cell(2, 6).value == 30
        assert len(payment_sheet._images) == 1

        rolled_back = client.post(f"/api/batches/{batch_id}/imports/latest/rollback")
        assert rolled_back.status_code == 200
        assert rolled_back.json()["deleted_payments"] == 1
        assert rolled_back.json()["deleted_payment_vouchers"] == 1
        assert client.get(f"/api/batches/{batch_id}/requests").json()["totals"]["count"] == 0


def test_restore_and_delete_draft_require_privileged_role():
    with TestClient(app) as admin_client, TestClient(app) as business_client:
        login(admin_client)
        business_user = admin_client.post(
            "/api/admin/users",
            json={"username": "restore-biz", "password": "biz123", "display_name": "业务还原", "role": "business", "active": True},
        )
        assert business_user.status_code == 200
        batch = admin_client.post("/api/batches", json={"name": "restore-permission", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        login(business_client, "restore-biz", "biz123")
        blocked_restore = business_client.post(f"/api/batches/{batch['id']}/restore-baseline")
        assert blocked_restore.status_code == 403
        blocked_delete = business_client.delete(f"/api/batches/{batch['id']}")
        assert blocked_delete.status_code == 403


def test_delete_draft_batch_and_keep_archived_batches():
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    with TestClient(app) as client:
        login(client)
        draft = client.post("/api/batches", json={"name": "delete-me", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        batch_id = draft["id"]
        request = client.post(
            f"/api/batches/{batch_id}/requests",
            json={"dingding_id": "DEL-1", "summary": "删除草稿", "amount": 12},
        ).json()["request"]
        uploaded = client.post(
            f"/api/batches/{batch_id}/requests/{request['id']}/attachments/image",
            files={"file": ("proof.png", io.BytesIO(png_bytes), "image/png")},
        )
        assert uploaded.status_code == 200
        file_path = TEST_DIR / "data" / uploaded.json()["attachment"]["file_path"]
        assert file_path.exists()
        deleted = client.delete(f"/api/batches/{batch_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/batches/{batch_id}").status_code == 404
        assert not file_path.exists()

        archived = client.post("/api/batches", json={"name": "keep-me", "start_date": "2026-07-08", "end_date": "2026-07-14"}).json()["batch"]
        archived_id = archived["id"]
        assert client.post(f"/api/batches/{archived_id}/archive").status_code == 200
        blocked = client.delete(f"/api/batches/{archived_id}")
        assert blocked.status_code == 400
        assert client.get(f"/api/batches/{archived_id}").status_code == 200


def test_rebuild_weekly_data_resets_business_data():
    if not SAMPLE.exists():
        return
    with TestClient(app) as client:
        login(client)
        old_batch = client.post("/api/batches", json={"name": "old-data", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        created = client.post(
            f"/api/batches/{old_batch['id']}/requests",
            json={"dingding_id": "OLD-1", "summary": "旧数据", "amount": 1},
        )
        assert created.status_code == 200
        result = rebuild_weekly_data(SAMPLE)
        assert result["requests"] == 161
        assert result["attachments"] == 9
        assert result["batch"]["status"] == "draft"
        batches = client.get("/api/batches").json()["batches"]
        assert len(batches) == 1
        batch = batches[0]
        assert batch["id"] == 1
        assert batch["name"] == "20260626~20260707请款明细"
        assert batch["status"] == "draft"
        assert batch["request_count"] == 161
        attachments = client.get(f"/api/batches/{batch['id']}/attachments").json()["attachments"]
        assert len(attachments) == 9
        assert all(attachment["attachment_type"] == "image" for attachment in attachments)
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"] > 0
            assert conn.execute("SELECT COUNT(*) AS count FROM dictionaries").fetchone()["count"] > 0
            assert conn.execute("SELECT COUNT(*) AS count FROM import_jobs").fetchone()["count"] == 1
            assert conn.execute("SELECT COUNT(*) AS count FROM audit_logs").fetchone()["count"] == 1


def test_normalize_payment_data_repairs_saved_rows_and_dictionary():
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "normalize-test", "start_date": "2026-07-01", "end_date": "2026-07-07"}).json()["batch"]
        timestamp = now_iso()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, amount, finance_review, general_manager_approval,
                    payment_status, remark, content_hash, raw_extra_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    "FIX-1",
                    14500,
                    "已经支付14500元",
                    "我要审核一下\n审核意见反馈给小吴了。",
                    "",
                    "",
                    "bad-hash-1",
                    '{"财务审核": null, "财务审核#2": "已经支付14500元", "总经理批复": "我要审核一下\\n审核意见反馈给小吴了。"}',
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, amount, finance_review, general_manager_approval,
                    payment_status, remark, content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    "FIX-2",
                    50440,
                    "无票",
                    "2026.06.30 Tiffany垫付了50440元",
                    "同意支付",
                    "赵高雅",
                    "bad-hash-2",
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, amount, payment_status, remark, content_hash,
                    raw_extra_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    "FIX-3",
                    178750,
                    "已支付",
                    "已支付10万\nTiffany垫付的178750元",
                    "bad-hash-3",
                    '{"付款情况": null, "备注": "已支付10万", "备注#2": "Tiffany垫付的178750元"}',
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, amount, general_manager_approval,
                    payment_status, remark, content_hash, raw_extra_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    "FIX-4",
                    25000,
                    "存在争议，CMA船司在问YUEWEI索要巨款",
                    "未支付",
                    "广州思辉新\n存在争议，CMA船司在问YUEWEI索要巨款",
                    "bad-hash-4",
                    '{"备注": "广州思辉新", "总经理批复": "存在争议，CMA船司在问YUEWEI索要巨款"}',
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, amount, finance_review, actual_payment_date,
                    payment_status, remark, content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    "FIX-5",
                    8800,
                    "部分付款",
                    "2026-07-10",
                    "已支付",
                    "",
                    "bad-hash-5",
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO dictionaries (kind, value, active, created_at) VALUES ('payment_status', '待付款', 1, ?)",
                (timestamp,),
            )

        result = normalize_payment_data()
        assert result["changed_rows"] >= 3
        with connect() as conn:
            paid_row = conn.execute("SELECT * FROM payment_requests WHERE dingding_id = 'FIX-1'").fetchone()
            advance_row = conn.execute("SELECT * FROM payment_requests WHERE dingding_id = 'FIX-2'").fetchone()
            raw_extra_row = conn.execute("SELECT * FROM payment_requests WHERE dingding_id = 'FIX-3'").fetchone()
            dispute_row = conn.execute("SELECT * FROM payment_requests WHERE dingding_id = 'FIX-4'").fetchone()
            partial_row = conn.execute("SELECT * FROM payment_requests WHERE dingding_id = 'FIX-5'").fetchone()
            assert paid_row["finance_review"] == "已付款"
            assert paid_row["general_manager_approval"] == "同意付款"
            assert paid_row["payment_status"] == "已付款"
            assert conn.execute("SELECT COUNT(*) AS count FROM payment_records WHERE request_id = ?", (paid_row["id"],)).fetchone()["count"] == 1
            assert "已经支付14500元" in paid_row["remark"]
            assert "我要审核一下" in paid_row["general_manager_opinion"]
            assert not paid_row["remark"] or "我要审核一下" not in paid_row["remark"]
            assert advance_row["finance_review"] == "未付款"
            assert advance_row["general_manager_approval"] is None
            assert advance_row["payment_status"] == "未付款"
            assert "无票" in advance_row["remark"]
            assert "Tiffany垫付" in advance_row["general_manager_opinion"]
            assert "Tiffany垫付" not in advance_row["remark"]
            assert "原付款情况：同意支付" in advance_row["remark"]
            assert raw_extra_row["finance_review"] == "未付款"
            assert raw_extra_row["payment_status"] == "未付款"
            assert "Tiffany垫付" in raw_extra_row["remark"]
            assert dispute_row["general_manager_approval"] == "存在争议"
            assert "CMA船司" in dispute_row["general_manager_opinion"]
            assert dispute_row["remark"] == "广州思辉新"
            assert partial_row["finance_review"] == "未付款"
            assert partial_row["payment_status"] == "未付款"
            assert conn.execute("SELECT active FROM dictionaries WHERE kind = 'payment_status' AND value = '待付款'").fetchone()["active"] == 0
            active_payment_status_values = {
                row["value"]
                for row in conn.execute("SELECT value FROM dictionaries WHERE kind = 'payment_status' AND active = 1").fetchall()
            }
            assert active_payment_status_values == set()
            active_finance_values = {
                row["value"]
                for row in conn.execute("SELECT value FROM dictionaries WHERE kind = 'finance_review' AND active = 1").fetchall()
            }
            assert active_finance_values == {"未付款", "部分付款", "已付款"}


def test_external_expense_mapping_uses_base_currency_and_purchase_form_values():
    purchase = map_external_expense(
        {
            "source_type": "purchase",
            "source_id": "501",
            "effective_date": date(2026, 7, 15),
            "approval_no": "PURCHASE-501",
            "creator_name": "user-purchase-501",
            "applicant_department": "采购部",
            "approval_title": "备用姓名提交的采购支出",
            "approval_status": "RUNNING",
            "approval_result": "agree",
            "execution_region": "中国China",
            "beneficiary": None,
            "expense_type": "订单采购",
            "summary": None,
            "project": "项目A",
            "needed_payment_date": None,
            "source_currency": None,
            "source_amount": Decimal("90"),
            "base_currency_amount": Decimal("100.25"),
            "order_name": "订单A",
            "product_name": "产品A",
            "source_created_at": datetime(2026, 7, 15, 9, 10),
            "source_updated_at": datetime(2026, 7, 15, 10, 10),
            "raw_data": {
                "formComponentValues": [
                    {"name": "收款人beneficiario", "value": "供应商A 账号1"},
                    {"name": "收款人beneficiario", "value": "供应商B 账号2"},
                    {"name": "规格明细需求说明Descripción", "value": "采购测试摘要"},
                    {"name": "付款日期Fecha de pago", "value": "2026-07-20"},
                ]
            },
        },
        {"user-purchase-501": "快照表姓名"},
    )
    assert purchase["amount"] == 100.25
    assert purchase["beneficiary"] == "供应商A 账号1 / 供应商B 账号2"
    assert purchase["summary"] == "采购测试摘要"
    assert purchase["needed_payment_date"] == "2026-07-20"
    assert "存在多个收款人，请确认" in purchase["warnings"]
    assert purchase["request_data"]["currency"] == "CNY"
    assert purchase["request_data"]["payee_account"] == purchase["beneficiary"]
    assert purchase["request_data"]["raw_extra"]["external_source"]["approval_status"] == "RUNNING"
    assert purchase["applicant"] == "快照表姓名"
    assert purchase["applicant_id"] == "user-purchase-501"
    assert purchase["request_data"]["source_sheet"] == "采购部"
    assert purchase["request_data"]["raw_extra"]["external_source"]["applicant_name_source"] == "ding_user_snapshot"

    refused = map_external_expense(
        {
            "source_type": "operation",
            "source_id": "601",
            "effective_date": date(2026, 7, 15),
            "approval_no": "OP-601",
            "creator_name": "运营申请人",
            "applicant_department": "运营部",
            "approval_title": "Title enviado por Operador Uno",
            "approval_status": "COMPLETED",
            "approval_result": "refuse",
            "execution_region": "中国China",
            "beneficiary": "工资卡",
            "expense_type": "管理费用",
            "summary": "运营测试摘要",
            "project": None,
            "needed_payment_date": "2026-07-21",
            "source_currency": "美元",
            "source_amount": Decimal("10"),
            "base_currency_amount": Decimal("72.3"),
            "source_created_at": datetime(2026, 7, 15, 9, 10),
            "source_updated_at": datetime(2026, 7, 15, 10, 10),
            "raw_data": {},
        }
    )
    assert refused["amount"] == 72.3
    assert "审批结果为拒绝" in refused["errors"]
    assert refused["applicant"] == "Operador Uno"


def test_external_expense_applicant_title_patterns():
    assert applicant_name_from_title("张三提交的运营支出") == "张三"
    assert applicant_name_from_title("Solicitud enviado por Zhang San") == "Zhang San"
    assert applicant_name_from_title("Expense submitted by John Smith") == "John Smith"
    assert applicant_name_from_title("Jane Doe's Purchase Expense") == "Jane Doe"
    assert applicant_name_from_title("无法识别的标题") == ""
    assert not valid_applicant_name("unknown")
    assert not valid_applicant_name("未识别人员")
    assert valid_applicant_name("王道伦")

    fallback = map_external_expense(
        {
            "source_type": "operation",
            "source_id": "title-fallback",
            "effective_date": date(2026, 7, 15),
            "approval_no": "TITLE-FALLBACK",
            "creator_name": "user-with-placeholder",
            "applicant_department": "财务部",
            "approval_title": "王道伦提交的运营支出",
            "approval_status": "RUNNING",
            "approval_result": "agree",
            "execution_region": "中国China",
            "beneficiary": "测试收款人",
            "expense_type": "管理费用",
            "summary": "测试摘要",
            "base_currency_amount": Decimal("100"),
            "raw_data": {},
        },
        {"user-with-placeholder": "unknown"},
    )
    assert fallback["applicant"] == "王道伦"
    assert fallback["request_data"]["raw_extra"]["external_source"]["applicant_name_source"] == "approval_title"


def test_external_expense_exact_approval_number_ignores_dates():
    exact_sql, exact_params = _preview_conditions(None, None, ["operation", "purchase"], " 202607071704000140246 ", [])
    assert "effective_date" not in exact_sql
    assert "BTRIM(approval_no) = %s" in exact_sql
    assert "base_currency_amount <> 0" in exact_sql
    assert exact_params[-1] == "202607071704000140246"

    dated_sql, dated_params = _preview_conditions(date(2026, 7, 1), date(2026, 7, 15), ["operation"], "", [])
    assert "effective_date BETWEEN %s AND %s" in dated_sql
    assert dated_params[:2] == [date(2026, 7, 1), date(2026, 7, 15)]


def test_external_expense_zero_amount_is_not_importable():
    zero_amount = map_external_expense(
        {
            "source_type": "operation",
            "source_id": "zero-amount",
            "effective_date": date(2026, 7, 15),
            "approval_no": "ZERO-AMOUNT",
            "creator_name": "zero-user",
            "approval_title": "零金额申请人提交的运营支出",
            "applicant_department": "测试部门",
            "approval_status": "RUNNING",
            "approval_result": "agree",
            "execution_region": "中国China",
            "beneficiary": "测试收款人",
            "base_currency_amount": Decimal("0"),
            "raw_data": {},
        }
    )
    assert "应付金额为 0，暂不导入" in zero_amount["errors"]
    assert "应付金额为 0" not in zero_amount["warnings"]


def test_external_expense_preview_import_global_dedupe_and_rollback(monkeypatch):
    duplicate_row = external_expense_test_row("EXT-DUPLICATE", "701")
    new_row = external_expense_test_row("EXT-NEW", "702", status="COMPLETED", warnings=["缺少收款信息"], beneficiary="")
    invalid_row = external_expense_test_row("EXT-INVALID", "703")
    invalid_row["errors"] = ["金额缺失"]

    def fake_preview(**kwargs):
        if kwargs["approval_no"] == "EXT-NEW":
            assert kwargs["date_from"] is None
            assert kwargs["date_to"] is None
        else:
            assert kwargs["date_from"] == date(2026, 7, 1)
            assert kwargs["date_to"] == date(2026, 7, 15)
        assert kwargs["source_types"] == ["operation", "purchase"]
        assert kwargs["applicant_ids"] == []
        return {
            "rows": [duplicate_row, new_row, invalid_row],
            "applicant_options": [{"id": "test-user-id", "name": "测试申请人", "department": "测试部门", "count": 3}],
        }

    def fake_fetch(items):
        by_id = {"701": duplicate_row, "702": new_row, "703": invalid_row}
        return [by_id[item["source_id"]] for item in items if item["source_id"] in by_id]

    monkeypatch.setattr(main_module, "preview_external_expenses", fake_preview)
    monkeypatch.setattr(main_module, "fetch_external_expenses", fake_fetch)

    with TestClient(app) as client:
        login(client)
        old_batch = client.post("/api/batches", json={"name": "external-old", "start_date": "2026-06-01", "end_date": "2026-06-07"}).json()["batch"]
        existing = client.post(
            f"/api/batches/{old_batch['id']}/requests",
            json={"dingding_id": " EXT-DUPLICATE ", "summary": "历史记录", "amount": 1},
        )
        assert existing.status_code == 200
        target_batch = client.post("/api/batches", json={"name": "external-target", "start_date": "2026-07-01", "end_date": "2026-07-15"}).json()["batch"]
        target_id = target_batch["id"]

        preview_filter = {
            "batch_id": target_id,
            "date_from": "2026-07-01",
            "date_to": "2026-07-15",
            "source_types": ["operation", "purchase"],
            "approval_no": "",
            "applicant_ids": [],
            "page": 1,
            "page_size": 50,
        }
        preview = client.post("/api/external-expenses/preview", json=preview_filter)
        assert preview.status_code == 200
        payload = preview.json()
        expected_summary = {"matched": 3, "importable": 1, "duplicates": 1, "warnings": 1, "invalid": 1}
        assert payload["summary"] == expected_summary
        assert payload["pagination"]["total"] == 3
        assert [row["approval_no"] for row in payload["all_rows"]] == ["EXT-DUPLICATE", "EXT-NEW", "EXT-INVALID"]
        duplicate_preview = next(row for row in payload["rows"] if row["approval_no"] == "EXT-DUPLICATE")
        new_preview = next(row for row in payload["rows"] if row["approval_no"] == "EXT-NEW")
        assert duplicate_preview["importable"] is False
        assert duplicate_preview["duplicate"]["batch_name"] == "external-old"
        assert new_preview["importable"] is True

        exact_number_without_dates = client.post(
            "/api/external-expenses/preview",
            json={**preview_filter, "date_from": "", "date_to": "not-a-date", "approval_no": "EXT-NEW"},
        )
        assert exact_number_without_dates.status_code == 200

        expected_filtered_rows = {
            "importable": ["EXT-NEW"],
            "duplicates": ["EXT-DUPLICATE"],
            "warnings": ["EXT-NEW"],
            "invalid": ["EXT-INVALID"],
        }
        for result_filter, expected_approval_nos in expected_filtered_rows.items():
            filtered = client.post(
                "/api/external-expenses/preview",
                json={**preview_filter, "result_filter": result_filter},
            )
            assert filtered.status_code == 200
            filtered_payload = filtered.json()
            assert filtered_payload["summary"] == expected_summary
            assert [row["approval_no"] for row in filtered_payload["rows"]] == expected_approval_nos
            assert [row["approval_no"] for row in filtered_payload["all_rows"]] == ["EXT-DUPLICATE", "EXT-NEW", "EXT-INVALID"]
            assert filtered_payload["pagination"]["total"] == len(expected_approval_nos)

        invalid_result_filter = client.post(
            "/api/external-expenses/preview",
            json={**preview_filter, "result_filter": "unknown"},
        )
        assert invalid_result_filter.status_code == 400

        invalid_range = client.post(
            "/api/external-expenses/preview",
            json={"batch_id": target_id, "date_from": "2026-01-01", "date_to": "2026-03-01", "source_types": ["operation"]},
        )
        assert invalid_range.status_code == 400

        imported = client.post(
            f"/api/batches/{target_id}/imports/external-expenses",
            json={"items": [{"source_type": "operation", "source_id": "702"}]},
        )
        assert imported.status_code == 200
        assert imported.json()["imported_rows"] == 1
        assert imported.json()["warnings"] == 1
        requests = client.get(f"/api/batches/{target_id}/requests").json()["requests"]
        assert len(requests) == 1
        request = requests[0]
        assert request["dingding_id"] == "EXT-NEW"
        assert request["paid_amount"] == 0
        assert request["pending_amount"] == new_row["amount"]
        assert request["finance_review"] == "未付款"
        assert request["raw_extra"]["external_source"]["approval_status"] == "COMPLETED"
        with connect() as conn:
            job = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (imported.json()["job_id"],)).fetchone()
            assert job["kind"] == "external-expenses"
            audit = conn.execute("SELECT 1 FROM audit_logs WHERE action = 'import.external_expenses' AND batch_id = ?", (target_id,)).fetchone()
            assert audit

        repeated = client.post(
            f"/api/batches/{target_id}/imports/external-expenses",
            json={"items": [{"source_type": "operation", "source_id": "702"}]},
        )
        assert repeated.status_code == 200
        assert repeated.json()["imported_rows"] == 0
        assert repeated.json()["duplicate_rows"] == 1
        assert repeated.json()["job_id"] is None

        rolled_back = client.post(f"/api/batches/{target_id}/imports/latest/rollback")
        assert rolled_back.status_code == 200
        assert rolled_back.json()["deleted_requests"] == 1
        assert client.get(f"/api/batches/{target_id}/requests").json()["totals"]["count"] == 0


def test_external_expense_concurrent_dedupe_and_source_failure_are_atomic(monkeypatch):
    concurrent_row = external_expense_test_row("EXT-CONCURRENT", "801")
    monkeypatch.setattr(main_module, "fetch_external_expenses", lambda items: [concurrent_row])

    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "external-concurrent", "start_date": "2026-07-15", "end_date": "2026-07-15"}).json()["batch"]

    def submit_import():
        with TestClient(app) as thread_client:
            login(thread_client)
            response = thread_client.post(
                f"/api/batches/{batch['id']}/imports/external-expenses",
                json={"items": [{"source_type": "operation", "source_id": "801"}]},
            )
            assert response.status_code == 200
            return response.json()["imported_rows"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        imported_counts = sorted(executor.map(lambda _: submit_import(), range(2)))
    assert imported_counts == [0, 1]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM payment_requests WHERE TRIM(dingding_id) = 'EXT-CONCURRENT'").fetchone()["count"] == 1

    def fail_source(items):
        raise ExternalExpenseError("来源数据库暂时不可用")

    monkeypatch.setattr(main_module, "fetch_external_expenses", fail_source)
    with TestClient(app) as client:
        login(client)
        failed_batch = client.post("/api/batches", json={"name": "external-failure", "start_date": "2026-07-15", "end_date": "2026-07-15"}).json()["batch"]
        failed = client.post(
            f"/api/batches/{failed_batch['id']}/imports/external-expenses",
            json={"items": [{"source_type": "operation", "source_id": "999"}]},
        )
        assert failed.status_code == 502
        assert client.get(f"/api/batches/{failed_batch['id']}/requests").json()["totals"]["count"] == 0
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) AS count FROM import_jobs WHERE batch_id = ?", (failed_batch["id"],)).fetchone()["count"] == 0


def test_external_expense_metadata_sync_statuses_conflicts_and_atomic_failure(monkeypatch):
    metadata = [
        {
            "approval_no": "SYNC-MATCH",
            "source_type": "operation",
            "source_label": "运营支出",
            "source_id": "901",
            "table": "approval_expense_operation",
            "record_id": "901",
            "approval_status": "RUNNING",
            "approval_result": "agree",
            "applicant_id": "user-901",
            "applicant": "同步申请人",
            "applicant_name_source": "ding_user_snapshot",
            "applicant_department": "产品与采购部",
        },
        {
            "approval_no": "SYNC-CONFLICT",
            "source_type": "operation",
            "source_label": "运营支出",
            "source_id": "902",
            "approval_status": "COMPLETED",
        },
        {
            "approval_no": "SYNC-CONFLICT",
            "source_type": "purchase",
            "source_label": "采购支出",
            "source_id": "903",
            "approval_status": "TERMINATED",
        },
        {
            "approval_no": "SYNC-TERMINATED",
            "source_type": "purchase",
            "source_label": "采购支出",
            "source_id": "904",
            "approval_status": "TERMINATED",
        },
    ]
    monkeypatch.setattr(main_module, "fetch_external_expense_metadata", lambda approval_nos: metadata)

    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "metadata-sync", "start_date": "2026-07-15", "end_date": "2026-07-15"}).json()["batch"]
        batch_id = batch["id"]
        request_ids = []
        for dingding_id, amount, source_sheet in (
            ("SYNC-MATCH", 100, "用户手动 Sheet"),
            ("SYNC-MATCH", 101, "用户手动 Sheet"),
            ("SYNC-CONFLICT", 200, "冲突 Sheet"),
            ("SYNC-MISSING", 300, "未匹配 Sheet"),
            ("SYNC-TERMINATED", 400, "终止 Sheet"),
        ):
            response = client.post(
                f"/api/batches/{batch_id}/requests",
                json={"dingding_id": dingding_id, "amount": amount, "source_sheet": source_sheet},
            )
            assert response.status_code == 200
            request_ids.append(response.json()["request"]["id"])

        manual_applicant = client.patch(
            f"/api/batches/{batch_id}/requests/{request_ids[0]}",
            json={"applicant": "外部供应商申请人"},
        )
        assert manual_applicant.status_code == 200
        assert manual_applicant.json()["request"]["applicant"] == "外部供应商申请人"

        synced = client.post(f"/api/batches/{batch_id}/external-expenses/sync-metadata")
        assert synced.status_code == 200
        assert synced.json() == {
            "status": "synced",
            "batch_id": batch_id,
            "unique_approval_nos": 4,
            "matched": 2,
            "unmatched": 1,
            "conflicts": 1,
            "updated_requests": 5,
        }
        rows = client.get(f"/api/batches/{batch_id}/requests").json()["requests"]
        by_id = {row["id"]: row for row in rows}
        matched_source = by_id[request_ids[0]]["raw_extra"]["external_source"]
        assert matched_source["lookup_status"] == "matched"
        assert matched_source["approval_status"] == "RUNNING"
        assert matched_source["applicant"] == "同步申请人"
        assert matched_source["applicant_department"] == "产品与采购部"
        assert by_id[request_ids[0]]["applicant"] == "外部供应商申请人"
        assert by_id[request_ids[0]]["amount"] == 100
        assert by_id[request_ids[0]]["source_sheet"] == "用户手动 Sheet"
        assert by_id[request_ids[2]]["raw_extra"]["external_source"]["lookup_status"] == "conflict"
        assert by_id[request_ids[3]]["raw_extra"]["external_source"]["lookup_status"] == "unmatched"
        assert by_id[request_ids[4]]["raw_extra"]["external_source"]["approval_status"] == "TERMINATED"
        assert by_id[request_ids[4]]["general_manager_approval"] == "无需审批"
        with connect() as conn:
            audit_row = conn.execute(
                "SELECT new_value_json FROM audit_logs WHERE batch_id = ? AND action = 'external_expenses.metadata_sync'",
                (batch_id,),
            ).fetchone()
            assert audit_row is not None
            assert json.loads(audit_row["new_value_json"]) == {
                "unique_approval_nos": 4,
                "matched": 2,
                "unmatched": 1,
                "conflicts": 1,
                "updated_requests": 5,
            }

        refreshed_metadata = [
            {**item, "approval_status": "RUNNING"}
            if item["approval_no"] == "SYNC-TERMINATED"
            else item
            for item in metadata
        ]
        monkeypatch.setattr(main_module, "fetch_external_expense_metadata", lambda approval_nos: refreshed_metadata)
        resynced = client.post(f"/api/batches/{batch_id}/external-expenses/sync-metadata")
        assert resynced.status_code == 200
        rows = client.get(f"/api/batches/{batch_id}/requests").json()["requests"]
        by_id = {row["id"]: row for row in rows}
        assert by_id[request_ids[4]]["raw_extra"]["external_source"]["approval_status"] == "RUNNING"
        assert by_id[request_ids[4]]["general_manager_approval"] is None

        archived = client.post(f"/api/batches/{batch_id}/archive")
        assert archived.status_code == 200
        rejected = client.post(f"/api/batches/{batch_id}/external-expenses/sync-metadata")
        assert rejected.status_code == 400

        failure_batch = client.post("/api/batches", json={"name": "metadata-sync-failure"}).json()["batch"]
        created = client.post(
            f"/api/batches/{failure_batch['id']}/requests",
            json={"dingding_id": "SYNC-FAIL", "amount": 50, "raw_extra": {"kept": True}},
        ).json()["request"]

        def fail_metadata(approval_nos):
            raise ExternalExpenseError("来源数据库暂时不可用")

        monkeypatch.setattr(main_module, "fetch_external_expense_metadata", fail_metadata)
        failed = client.post(f"/api/batches/{failure_batch['id']}/external-expenses/sync-metadata")
        assert failed.status_code == 502
        unchanged = client.get(f"/api/batches/{failure_batch['id']}/requests").json()["requests"][0]
        assert unchanged["id"] == created["id"]
        assert unchanged["raw_extra"] == {"kept": True}


def visible_data_sheet(workbook):
    return next(
        sheet
        for sheet in workbook.worksheets
        if sheet.title not in {"付款明细", "_系统信息"}
    )


def header_lookup(sheet):
    header_row = 2 if sheet.cell(2, 1).value == "序号" else 1
    return header_row, {
        str(sheet.cell(header_row, column).value): column
        for column in range(1, sheet.max_column + 1)
        if sheet.cell(header_row, column).value
    }


def test_weekly_merge_roundtrip_update_add_payment_and_rollback():
    with TestClient(app) as client:
        login(client)
        batch = client.post(
            "/api/batches",
            json={"name": "merge-roundtrip", "start_date": "2026-07-16", "end_date": "2026-07-23"},
        ).json()["batch"]
        created = client.post(
            f"/api/batches/{batch['id']}/requests",
            json={
                "dingding_id": "MERGE-001",
                "applicant": "原申请人",
                "payment_account": "私户",
                "summary": "原摘要",
                "amount": 100,
                "source_sheet": "原部门",
            },
        ).json()["request"]
        exported = client.get(f"/api/batches/{batch['id']}/export.xlsx")
        assert exported.status_code == 200
        workbook = load_workbook(io.BytesIO(exported.content))
        assert workbook["_系统信息"].sheet_state == "veryHidden"
        sheet = visible_data_sheet(workbook)
        header_row, headers = header_lookup(sheet)
        assert sheet.column_dimensions[sheet.cell(header_row, headers["请款标识"]).column_letter].hidden
        assert sheet.cell(header_row + 1, headers["请款标识"]).value == created["id"]
        payment_sheet = workbook["付款明细"]
        assert payment_sheet.column_dimensions["A"].hidden
        assert payment_sheet.cell(1, 1).value == "付款标识"

        unchanged_bytes = io.BytesIO()
        workbook.save(unchanged_bytes)
        unchanged_bytes.seek(0)
        unchanged_preview = client.post(
            "/api/import/weekly-excel/merge-preview",
            data={"batch_id": str(batch["id"])},
            files={"file": ("roundtrip.xlsx", unchanged_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert unchanged_preview.status_code == 200
        assert unchanged_preview.json()["summary"]["unchanged"] == 1
        assert unchanged_preview.json()["summary"]["conflict"] == 0

        sheet.title = "新部门"
        header_row, headers = header_lookup(sheet)
        sheet.cell(header_row + 1, headers["摘要"], "更新后的摘要")
        sheet.cell(header_row + 1, headers["已支付金额"], 20)
        new_row = header_row + 2
        sheet.cell(new_row, headers["请款标识"], None)
        sheet.cell(new_row, headers["钉钉申请单号"], "MERGE-002")
        sheet.cell(new_row, headers["申请人"], "新增申请人")
        sheet.cell(new_row, headers["付款账户"], "公户")
        sheet.cell(new_row, headers["摘要"], "新增摘要")
        sheet.cell(new_row, headers["应付金额"], 50)
        sheet.cell(new_row, headers["已支付金额"], 0)
        sheet.cell(new_row, headers["待付款金额"], 50)
        modified = io.BytesIO()
        workbook.save(modified)
        modified.seek(0)

        preview_response = client.post(
            "/api/import/weekly-excel/merge-preview",
            data={"batch_id": str(batch["id"])},
            files={"file": ("modified.xlsx", modified, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["summary"]["create"] == 1
        assert preview["summary"]["update"] == 1
        assert preview["summary"]["payment"] == 1
        payment_key = next(
            key
            for row in preview["rows"]
            if row["dingding_id"] == "MERGE-001"
            for key in row["payment_date_keys"]
        )
        applied = client.post(
            f"/api/import-jobs/{preview['job_id']}/apply-merge",
            json={
                "resolutions": [],
                "payment_dates": {payment_key: "2026-07-23"},
            },
        )
        assert applied.status_code == 200, applied.text
        rows = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"]
        assert len(rows) == 2
        by_ding = {row["dingding_id"]: row for row in rows}
        assert by_ding["MERGE-001"]["summary"] == "更新后的摘要"
        assert by_ding["MERGE-001"]["source_sheet"] == "新部门"
        assert by_ding["MERGE-001"]["paid_amount"] == 20
        assert by_ding["MERGE-002"]["summary"] == "新增摘要"
        assert client.get(f"/api/batches/{batch['id']}").json()["batch"]["sheet_order"][0] == "新部门"

        rolled_back = client.post(f"/api/batches/{batch['id']}/imports/latest/rollback")
        assert rolled_back.status_code == 200, rolled_back.text
        assert rolled_back.json()["restored_requests"] == 1
        restored_rows = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"]
        assert len(restored_rows) == 1
        assert restored_rows[0]["summary"] == "原摘要"
        assert restored_rows[0]["source_sheet"] == "原部门"
        assert restored_rows[0]["paid_amount"] == 0


def test_weekly_merge_legacy_ambiguity_and_concurrent_change():
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "merge-legacy"}).json()["batch"]
        existing_ids = []
        for amount in (100, 200):
            response = client.post(
                f"/api/batches/{batch['id']}/requests",
                json={
                    "dingding_id": "LEGACY-DUP",
                    "payment_account": "私户",
                    "summary": f"原记录 {amount}",
                    "amount": amount,
                    "source_sheet": "旧版 Sheet",
                },
            )
            existing_ids.append(response.json()["request"]["id"])

        legacy = Workbook()
        sheet = legacy.active
        sheet.title = "旧版 Sheet"
        sheet.append(["钉钉申请单号", "申请人", "付款账户", "摘要", "应付金额", "已支付金额", "待付款金额"])
        sheet.append(["LEGACY-DUP", "旧版申请人", "私户", "旧版修改", 180, 0, 180])
        payload = io.BytesIO()
        legacy.save(payload)
        payload.seek(0)
        preview_response = client.post(
            "/api/import/weekly-excel/merge-preview",
            data={"batch_id": str(batch["id"])},
            files={"file": ("legacy.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        row = preview["rows"][0]
        assert row["action"] == "conflict"
        assert {candidate["id"] for candidate in row["candidates"]} == set(existing_ids)

        applied = client.post(
            f"/api/import-jobs/{preview['job_id']}/apply-merge",
            json={
                "resolutions": [{"row_id": row["row_id"], "action": "update", "request_id": existing_ids[0]}],
                "payment_dates": {},
            },
        )
        assert applied.status_code == 200, applied.text
        selected = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"]
        assert next(item for item in selected if item["id"] == existing_ids[0])["summary"] == "旧版修改"

        exported = client.get(f"/api/batches/{batch['id']}/export.xlsx")
        concurrent_preview = client.post(
            "/api/import/weekly-excel/merge-preview",
            data={"batch_id": str(batch["id"])},
            files={"file": ("concurrent.xlsx", io.BytesIO(exported.content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        ).json()
        changed = client.patch(
            f"/api/batches/{batch['id']}/requests/{existing_ids[1]}",
            json={"remark": "预览后修改"},
        )
        assert changed.status_code == 200
        rejected = client.post(
            f"/api/import-jobs/{concurrent_preview['job_id']}/apply-merge",
            json={"resolutions": [], "payment_dates": {}},
        )
        assert rejected.status_code == 409


def test_weekly_merge_payment_detail_is_authoritative_and_missing_main_row_is_preserved():
    with TestClient(app) as client:
        login(client)
        batch = client.post("/api/batches", json={"name": "merge-payment-detail"}).json()["batch"]
        request = client.post(
            f"/api/batches/{batch['id']}/requests",
            json={"dingding_id": "MERGE-PAY", "payment_account": "公户", "amount": 100, "source_sheet": "付款测试"},
        ).json()["request"]
        payment = client.post(
            f"/api/batches/{batch['id']}/requests/{request['id']}/payments",
            json={"amount": 30, "payment_date": "2026-07-20", "payer": "测试出纳"},
        ).json()["payment"]
        exported = client.get(f"/api/batches/{batch['id']}/export.xlsx")
        workbook = load_workbook(io.BytesIO(exported.content))
        main_sheet = visible_data_sheet(workbook)
        main_header_row, _ = header_lookup(main_sheet)
        main_sheet.delete_rows(main_header_row + 1, 1)
        payment_sheet = workbook["付款明细"]
        payment_headers = {cell.value: cell.column for cell in payment_sheet[1] if cell.value}
        assert payment_sheet.cell(2, payment_headers["付款标识"]).value == payment["id"]
        payment_sheet.cell(2, payment_headers["本次金额"], 40)
        modified = io.BytesIO()
        workbook.save(modified)
        modified.seek(0)
        preview_response = client.post(
            "/api/import/weekly-excel/merge-preview",
            data={"batch_id": str(batch["id"])},
            files={"file": ("payment-update.xlsx", modified, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["summary"]["conflict"] == 0
        assert preview["summary"]["payment"] == 1
        preserved = next(row for row in preview["rows"] if row["request_id"] == request["id"])
        assert preserved["payment_change"]
        assert any("系统记录将保留" in warning for warning in preserved["warnings"])
        applied = client.post(
            f"/api/import-jobs/{preview['job_id']}/apply-merge",
            json={"resolutions": [], "payment_dates": {}},
        )
        assert applied.status_code == 200, applied.text
        updated = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"][0]
        assert updated["id"] == request["id"]
        assert updated["paid_amount"] == 40

        rolled_back = client.post(f"/api/batches/{batch['id']}/imports/latest/rollback")
        assert rolled_back.status_code == 200, rolled_back.text
        restored = client.get(f"/api/batches/{batch['id']}/requests").json()["requests"][0]
        assert restored["paid_amount"] == 30
        restored_payment = client.get(
            f"/api/batches/{batch['id']}/requests/{request['id']}/payments"
        ).json()["payments"][0]
        assert restored_payment["amount"] == 30


def teardown_module():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
