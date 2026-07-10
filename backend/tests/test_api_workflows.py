import base64
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

TEST_DIR = Path(tempfile.mkdtemp(prefix="cashier-payment-tests-"))
os.environ["PAYMENT_APP_DATA_DIR"] = str(TEST_DIR / "data")
os.environ["PAYMENT_APP_DB"] = str(TEST_DIR / "app.db")

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.app.db import connect, now_iso
from backend.app.main import app
from backend.app.normalize_payment_data import normalize_payment_data
from backend.app.rebuild_weekly_data import rebuild_weekly_data


SAMPLE = Path("/Users/smk/Downloads/20260626~20260707请款明细.xlsx")


def login(client: TestClient, username: str = "admin", password: str = "admin123") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


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
        partial_source_id = source_requests[0]["id"]
        partial_update = client.patch(
            f"/api/batches/{source_batch_id}/requests/{partial_source_id}",
            json={"finance_review": "部分付款", "actual_payment_date": "2026-07-10"},
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
                        "payment_status": "待付款",
                        "finance_review": "待批付",
                        "actual_payment_date": "2026-07-09",
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
        updated = client.patch(
            f"/api/batches/{batch_id}/requests/bulk",
            json={"creates": [], "updates": [{"id": request_id, "finance_review": "已付款"}], "deletes": []},
        )
        assert updated.status_code == 200
        row = client.get(f"/api/batches/{batch_id}/requests").json()["requests"][0]
        assert row["finance_review"] == "已付款"
        assert row["payment_status"] is None
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
            json={"summary": "权限测试", "amount": 10, "finance_review": "未付款", "general_manager_approval": "同意付款"},
        )
        assert created.status_code == 200
        request_id = created.json()["request"]["id"]

        login(business_client, "biz-user", "biz123")
        blocked_business = business_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"summary": "业务改摘要", "finance_review": "已付款"},
        )
        assert blocked_business.status_code == 403
        allowed_business = business_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"summary": "业务改摘要", "finance_review": "未付款", "general_manager_approval": "同意付款"},
        )
        assert allowed_business.status_code == 200
        assert allowed_business.json()["request"]["summary"] == "业务改摘要"
        business_create = business_client.post(
            f"/api/batches/{batch_id}/requests",
            json={
                "summary": "业务新增",
                "amount": 20,
                "finance_review": "已付款",
                "actual_payment_date": "2026-07-10",
                "general_manager_opinion": "不应保存",
            },
        )
        assert business_create.status_code == 200
        business_row = business_create.json()["request"]
        assert business_row["finance_review"] == "未付款"
        assert business_row["payment_status"] is None
        assert business_row["actual_payment_date"] is None
        assert business_row["general_manager_opinion"] is None

        login(finance_client, "finance-user", "fin123")
        finance_update = finance_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"finance_review": "已付款", "actual_payment_date": "2026-07-10"},
        )
        assert finance_update.status_code == 200
        assert finance_update.json()["request"]["finance_review"] == "已付款"
        partial_finance_update = finance_client.patch(
            f"/api/batches/{batch_id}/requests/{request_id}",
            json={"finance_review": "部分付款", "actual_payment_date": "2026-07-10"},
        )
        assert partial_finance_update.status_code == 200
        assert partial_finance_update.json()["request"]["finance_review"] == "部分付款"
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
                "actual_payment_date": "2026-07-09",
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
            assert paid_row["general_manager_approval"] is None
            assert paid_row["payment_status"] is None
            assert "已经支付14500元" in paid_row["remark"]
            assert "我要审核一下" in paid_row["general_manager_opinion"]
            assert not paid_row["remark"] or "我要审核一下" not in paid_row["remark"]
            assert advance_row["finance_review"] == "未付款"
            assert advance_row["general_manager_approval"] is None
            assert advance_row["payment_status"] is None
            assert "无票" in advance_row["remark"]
            assert "Tiffany垫付" in advance_row["general_manager_opinion"]
            assert "Tiffany垫付" not in advance_row["remark"]
            assert "原付款情况：同意支付" in advance_row["remark"]
            assert raw_extra_row["finance_review"] == "未付款"
            assert raw_extra_row["payment_status"] is None
            assert "Tiffany垫付" in raw_extra_row["remark"]
            assert dispute_row["general_manager_approval"] == "存在争议"
            assert "CMA船司" in dispute_row["general_manager_opinion"]
            assert dispute_row["remark"] == "广州思辉新"
            assert partial_row["finance_review"] == "部分付款"
            assert partial_row["payment_status"] is None
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


def teardown_module():
    shutil.rmtree(TEST_DIR, ignore_errors=True)
