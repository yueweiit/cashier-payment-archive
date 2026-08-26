from __future__ import annotations

import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from backend.app.db import connect, get_daily_payables_history_start_date, now_iso
from backend.app.main import app
from backend.app.daily_payables import daily_snapshot, daily_trend
from backend.app.payable_history import payment_effective_at, record_request_state
from backend.app.security import hash_password


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200


def create_request(
    client: TestClient,
    *,
    amount: float = 20000,
    source_sheet: str = "每日应付测试公司",
    needed_payment_date: str | None = "2026-08-21",
    currency: str = "CNY",
    execution_region: str | None = "China",
    dingding_id: str | None = None,
    summary: str = "历史状态测试",
) -> dict:
    batch = client.post(
        "/api/batches",
        json={"name": f"每日应付-{uuid.uuid4().hex}", "start_date": "2026-08-21", "end_date": "2026-08-28"},
    ).json()["batch"]
    payload = {
        "source_sheet": source_sheet,
        "summary": summary,
        "applicant": "测试申请人",
        "amount": amount,
        "currency": currency,
        "needed_payment_date": needed_payment_date,
    }
    if dingding_id is not None:
        payload["dingding_id"] = dingding_id
    if execution_region is not None:
        payload["raw_extra"] = {
            "external_source": {"execution_region": execution_region}
        }
    return client.post(
        f"/api/batches/{batch['id']}/requests",
        json=payload,
    ).json()["request"]


def test_record_request_state_is_idempotent_and_keeps_full_state():
    with TestClient(app) as client:
        login(client)
        request = create_request(client)
        event_key = f"test:{uuid.uuid4().hex}"
        with connect() as conn:
            record_request_state(
                conn,
                request["id"],
                event_type="request.updated",
                event_key=event_key,
                effective_at="2026-08-21T12:00:00.000000",
                actor_id=1,
            )
            record_request_state(
                conn,
                request["id"],
                event_type="request.updated",
                event_key=event_key,
                effective_at="2026-08-21T12:00:00.000000",
                actor_id=1,
            )
            rows = conn.execute(
                "SELECT * FROM payable_history_versions WHERE event_key = ?",
                (event_key,),
            ).fetchall()
            current = conn.execute(
                "SELECT logical_request_id FROM payment_requests WHERE id = ?",
                (request["id"],),
            ).fetchone()
            assert len(rows) == 1
            row = rows[0]
            assert row["logical_request_id"] == current["logical_request_id"]
            assert row["source_batch_id"] == request["batch_id"]
            assert row["dingding_id"] == request.get("dingding_id")
            assert row["amount"] == 20000
            assert row["paid_amount"] == 0
            assert row["pending_amount"] == 20000
            assert row["needed_payment_date"] == "2026-08-21"
            assert row["source_sheet"] == "每日应付测试公司"
            assert row["included"] == 1


def test_record_request_state_tracks_terminated_refused_and_reopened():
    with TestClient(app) as client:
        login(client)
        request = create_request(client)
        with connect() as conn:
            conn.execute(
                "UPDATE payment_requests SET raw_extra_json = ? WHERE id = ?",
                (json.dumps({"external_source": {"approval_status": "TERMINATED"}}), request["id"]),
            )
            record_request_state(
                conn,
                request["id"],
                event_type="dingtalk.terminated",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-21T13:00:00.000000",
                actor_id=1,
            )
            terminated = conn.execute(
                "SELECT * FROM payable_history_versions WHERE source_request_id = ? ORDER BY id DESC LIMIT 1",
                (request["id"],),
            ).fetchone()
            assert terminated["approval_status"] == "TERMINATED"
            assert terminated["included"] == 0

            conn.execute(
                "UPDATE payment_requests SET raw_extra_json = ? WHERE id = ?",
                (json.dumps({"external_source": {"approval_status": "RUNNING", "approval_result": "agree"}}), request["id"]),
            )
            record_request_state(
                conn,
                request["id"],
                event_type="dingtalk.reopened",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-21T14:00:00.000000",
                actor_id=1,
            )
            reopened = conn.execute(
                "SELECT * FROM payable_history_versions WHERE source_request_id = ? ORDER BY id DESC LIMIT 1",
                (request["id"],),
            ).fetchone()
            assert reopened["approval_status"] == "RUNNING"
            assert reopened["approval_result"] == "agree"
            assert reopened["included"] == 1


def test_history_write_rolls_back_with_business_transaction():
    with TestClient(app) as client:
        login(client)
        request = create_request(client)
        event_key = f"rollback:{uuid.uuid4().hex}"
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record_request_state(
                conn,
                request["id"],
                event_type="request.updated",
                event_key=event_key,
                effective_at=now_iso(),
                actor_id=1,
            )
            conn.rollback()
            assert not conn.execute(
                "SELECT 1 FROM payable_history_versions WHERE event_key = ?",
                (event_key,),
            ).fetchone()


def test_payment_effective_at_uses_end_of_backdated_payment_day():
    assert payment_effective_at(
        "2026-08-20",
        recorded_at="2026-08-21T09:00:00.000000",
    ) == "2026-08-20T23:59:59.999999"
    assert payment_effective_at(
        "2026-08-21",
        recorded_at="2026-08-21T09:00:00.000000",
    ) == "2026-08-21T09:00:00.000000"


def test_request_and_payment_endpoints_append_history_versions():
    with TestClient(app) as client:
        login(client)
        request = create_request(client)
        request_id = request["id"]
        with connect() as conn:
            created = conn.execute(
                "SELECT * FROM payable_history_versions WHERE source_request_id = ? ORDER BY id DESC LIMIT 1",
                (request_id,),
            ).fetchone()
            assert created is not None
            assert created["event_type"] == "request.create"
            logical_request_id = created["logical_request_id"]

        updated_response = client.patch(
            f"/api/batches/{request['batch_id']}/requests/{request_id}",
            json={"summary": "更新后的摘要", "expected_version": request["version"]},
        )
        assert updated_response.status_code == 200, updated_response.text
        updated = updated_response.json()["request"]

        payment_response = client.post(
            f"/api/batches/{request['batch_id']}/requests/{request_id}/payments",
            json={
                "amount": 10000,
                "payment_date": "2026-08-21",
                "payer": "测试付款人",
                "expected_request_version": updated["version"],
            },
        )
        assert payment_response.status_code == 200, payment_response.text
        after_payment = payment_response.json()["request"]

        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payable_history_versions WHERE logical_request_id = ? ORDER BY id",
                (logical_request_id,),
            ).fetchall()
            assert [row["event_type"] for row in rows[-3:]] == [
                "request.create",
                "request.update",
                "payment.create",
            ]
            assert rows[-1]["paid_amount"] == 10000
            assert rows[-1]["pending_amount"] == 10000

        delete_response = client.delete(
            f"/api/batches/{request['batch_id']}/requests/{request_id}",
            params={"expected_version": after_payment["version"]},
        )
        assert delete_response.status_code == 200, delete_response.text
        with connect() as conn:
            tombstone = conn.execute(
                "SELECT * FROM payable_history_versions WHERE logical_request_id = ? ORDER BY id DESC LIMIT 1",
                (logical_request_id,),
            ).fetchone()
            assert tombstone["event_type"] == "request.delete"
            assert tombstone["deleted"] == 1
            assert tombstone["included"] == 0


def test_rollover_inherits_logical_request_id_without_duplicate_identity():
    with TestClient(app) as client:
        login(client)
        request = create_request(client)
        source_batch = next(
            batch
            for batch in client.get("/api/batches").json()["batches"]
            if batch["id"] == request["batch_id"]
        )
        response = client.post(
            f"/api/batches/{source_batch['id']}/rollover",
            json={
                "name": f"结转-{uuid.uuid4().hex}",
                "start_date": "2026-08-29",
                "end_date": "2026-09-04",
                "copy_mode": "all",
                "expected_batch_version": source_batch["version"],
            },
        )
        assert response.status_code == 200, response.text
        target_batch = response.json()["batch"]
        target_requests = client.get(f"/api/batches/{target_batch['id']}/requests").json()["requests"]
        copied = next(item for item in target_requests if item["copied_from_request_id"] == request["id"])
        assert copied["logical_request_id"] == request["id"]


def test_daily_payables_deduplicates_reimports_by_dingtalk_business_id():
    with TestClient(app) as client:
        login(client)
        selected = date.today()
        due_date = (selected - timedelta(days=1)).isoformat()
        dingding_id = f"daily-dedup-{uuid.uuid4().hex}"
        source_sheet = f"每日应付去重-{uuid.uuid4().hex}"
        older = create_request(
            client,
            amount=290,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
            dingding_id=dingding_id,
            summary="跨周重复导入",
        )
        newer = create_request(
            client,
            amount=290,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
            dingding_id=dingding_id,
            summary="跨周重复导入",
        )

        with connect() as conn:
            snapshot = daily_snapshot(
                conn,
                selected,
                allowed_sheets={source_sheet},
                include_details=True,
                china_only=True,
            )
            trend = daily_trend(
                conn,
                selected,
                selected,
                allowed_sheets={source_sheet},
                china_only=True,
            )

        matches = [
            item for item in snapshot["items"]
            if item["dingding_id"] == dingding_id
        ]
        assert len(matches) == 1
        assert matches[0]["logical_request_id"] == newer["logical_request_id"]
        assert matches[0]["source_request_id"] == newer["id"]
        assert snapshot["totals_cny"]["end_pending"] == 290
        assert snapshot["counts"]["end_pending"] == 1
        assert trend["points"][0]["totals_cny"]["end_pending"] == 290
        assert trend["points"][0]["counts"]["end_pending"] == 1


def test_daily_trend_does_not_apply_future_dingtalk_identity_to_past():
    with TestClient(app) as client:
        login(client)
        first_day = date.today()
        second_day = first_day + timedelta(days=1)
        due_date = (first_day - timedelta(days=1)).isoformat()
        source_sheet = f"每日应付未来身份-{uuid.uuid4().hex}"
        older = create_request(
            client,
            amount=100,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
        )
        newer = create_request(
            client,
            amount=200,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
        )
        dingding_id = f"daily-future-{uuid.uuid4().hex}"

        with connect() as conn:
            for sequence, request in enumerate((older, newer), start=10):
                conn.execute(
                    "UPDATE payment_requests SET dingding_id = ? WHERE id = ?",
                    (dingding_id, request["id"]),
                )
                record_request_state(
                    conn,
                    request["id"],
                    event_type="dingtalk.linked",
                    event_key=f"test:{uuid.uuid4().hex}",
                    effective_at=f"{second_day.isoformat()}T{sequence}:00:00.000000",
                    actor_id=1,
                )
            trend = daily_trend(
                conn,
                first_day,
                second_day,
                allowed_sheets={source_sheet},
                china_only=True,
            )

        assert trend["points"][0]["totals_cny"]["end_pending"] == 300
        assert trend["points"][0]["counts"]["end_pending"] == 2
        assert trend["points"][1]["totals_cny"]["end_pending"] == 200
        assert trend["points"][1]["counts"]["end_pending"] == 1


def test_daily_payables_treats_cleared_dingtalk_ids_as_independent():
    with TestClient(app) as client:
        login(client)
        selected = date.today()
        due_date = (selected - timedelta(days=1)).isoformat()
        source_sheet = f"每日应付清空身份-{uuid.uuid4().hex}"
        dingding_id = f"daily-cleared-{uuid.uuid4().hex}"
        requests = [
            create_request(
                client,
                amount=amount,
                source_sheet=source_sheet,
                needed_payment_date=due_date,
                dingding_id=dingding_id,
            )
            for amount in (100, 200)
        ]

        with connect() as conn:
            for sequence, request in enumerate(requests, start=20):
                conn.execute(
                    "UPDATE payment_requests SET dingding_id = NULL WHERE id = ?",
                    (request["id"],),
                )
                record_request_state(
                    conn,
                    request["id"],
                    event_type="request.update",
                    event_key=f"test:{uuid.uuid4().hex}",
                    effective_at=f"{selected.isoformat()}T{sequence}:00:00.000000",
                    actor_id=1,
                )
            snapshot = daily_snapshot(
                conn,
                selected,
                allowed_sheets={source_sheet},
                include_details=True,
                china_only=True,
            )

        assert snapshot["totals_cny"]["end_pending"] == 300
        assert snapshot["counts"]["end_pending"] == 2
        assert {item["logical_request_id"] for item in snapshot["items"]} == {
            request["logical_request_id"] for request in requests
        }


def test_daily_payables_uses_only_winning_logical_payment_delta():
    with TestClient(app) as client:
        login(client)
        selected = date.today()
        due_date = (selected - timedelta(days=1)).isoformat()
        source_sheet = f"每日应付付款去重-{uuid.uuid4().hex}"
        dingding_id = f"daily-payment-{uuid.uuid4().hex}"
        older = create_request(
            client,
            amount=100,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
            dingding_id=dingding_id,
        )
        newer = create_request(
            client,
            amount=100,
            source_sheet=source_sheet,
            needed_payment_date=due_date,
            dingding_id=dingding_id,
        )

        with connect() as conn:
            for request, reset_hour, payment_hour in (
                (older, 20, 21),
                (newer, 22, 23),
            ):
                conn.execute(
                    "UPDATE payment_requests SET paid_amount = 0, pending_amount = 100 WHERE id = ?",
                    (request["id"],),
                )
                record_request_state(
                    conn,
                    request["id"],
                    event_type="request.update",
                    event_key=f"test:{uuid.uuid4().hex}",
                    effective_at=f"{selected.isoformat()}T{reset_hour}:00:00.000000",
                    actor_id=1,
                )
                conn.execute(
                    "UPDATE payment_requests SET paid_amount = 50, pending_amount = 50 WHERE id = ?",
                    (request["id"],),
                )
                record_request_state(
                    conn,
                    request["id"],
                    event_type="payment.create",
                    event_key=f"test:{uuid.uuid4().hex}",
                    effective_at=f"{selected.isoformat()}T{payment_hour}:00:00.000000",
                    actor_id=1,
                )
            snapshot = daily_snapshot(
                conn,
                selected,
                allowed_sheets={source_sheet},
                include_details=True,
                china_only=True,
            )

        assert snapshot["counts"]["end_pending"] == 1
        assert snapshot["totals_cny"]["end_pending"] == 50
        assert snapshot["totals_cny"]["paid_today"] == 50
        assert len(snapshot["items"]) == 1
        assert snapshot["items"][0]["logical_request_id"] == newer["logical_request_id"]
        assert snapshot["items"][0]["paid_today"] == 50


def test_daily_payables_summary_details_trend_and_sheet_permissions():
    with TestClient(app) as client:
        login(client)
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES ('daily_payables_history_start_date', '2026-08-21', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (now_iso(),),
            )
        sheet_name = f"每日应付授权-{uuid.uuid4().hex}"
        hidden_sheet = f"每日应付无权-{uuid.uuid4().hex}"
        request = create_request(client, source_sheet=sheet_name)
        hidden_request = create_request(client, amount=99999, source_sheet=hidden_sheet)
        no_due_request = create_request(
            client,
            amount=88888,
            source_sheet=sheet_name,
            needed_payment_date=None,
        )
        usd_request = create_request(
            client,
            amount=100,
            source_sheet=sheet_name,
        )
        reopened_request = create_request(
            client,
            amount=300,
            source_sheet=sheet_name,
        )
        with connect() as conn:
            conn.execute(
                """
                UPDATE payment_requests
                SET currency = 'USD', base_amount_cny = 680, fx_rate_cny_per_unit = 6.8,
                    fx_rate_date = '2026-08-21', fx_rate_actual_date = '2026-08-21'
                WHERE id = ?
                """,
                (usd_request["id"],),
            )
            record_request_state(
                conn,
                usd_request["id"],
                event_type="test.currency",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-21T09:00:00.000000",
                actor_id=1,
            )
            conn.execute(
                "UPDATE payment_requests SET raw_extra_json = ? WHERE id = ?",
                (json.dumps({"external_source": {"approval_status": "TERMINATED"}}), reopened_request["id"]),
            )
            record_request_state(
                conn,
                reopened_request["id"],
                event_type="dingtalk.terminated",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-21T20:00:00.000000",
                actor_id=1,
            )
            conn.execute(
                "UPDATE payment_requests SET raw_extra_json = ? WHERE id = ?",
                (json.dumps({"external_source": {"approval_status": "RUNNING"}}), reopened_request["id"]),
            )
            record_request_state(
                conn,
                reopened_request["id"],
                event_type="dingtalk.reopened",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-22T10:00:00.000000",
                actor_id=1,
            )

        payment_response = client.post(
            f"/api/batches/{request['batch_id']}/requests/{request['id']}/payments",
            json={
                "amount": 10000,
                "payment_date": "2026-08-21",
                "payer": "测试付款人",
                "expected_request_version": request["version"],
            },
        )
        assert payment_response.status_code == 200, payment_response.text

        source_batch = next(
            batch
            for batch in client.get("/api/batches").json()["batches"]
            if batch["id"] == request["batch_id"]
        )
        rollover = client.post(
            f"/api/batches/{request['batch_id']}/rollover",
            json={
                "name": f"每日应付结转-{uuid.uuid4().hex}",
                "start_date": "2026-08-22",
                "end_date": "2026-08-28",
                "copy_mode": "all",
                "expected_batch_version": source_batch["version"],
            },
        )
        assert rollover.status_code == 200, rollover.text

        with connect() as conn:
            conn.execute(
                "UPDATE payment_requests SET paid_amount = 20000, pending_amount = 0 WHERE id = ?",
                (request["id"],),
            )
            record_request_state(
                conn,
                request["id"],
                event_type="payment.create",
                event_key=f"test:{uuid.uuid4().hex}",
                effective_at="2026-08-22T11:00:00.000000",
                actor_id=1,
            )
            conn.execute(
                "UPDATE payment_requests SET paid_amount = 10000, pending_amount = 10000 WHERE id = ?",
                (request["id"],),
            )

        username = f"daily-{uuid.uuid4().hex}"
        with connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, display_name, active, created_at)
                VALUES (?, ?, 'business', ?, 1, ?)
                """,
                (username, hash_password("daily123"), username, now_iso()),
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO user_sheet_permissions (user_id, sheet_name, created_by, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (user_id, sheet_name, now_iso()),
            )
            history_start = get_daily_payables_history_start_date(conn)

        client.post("/api/auth/logout")
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": "daily123"},
        )
        assert response.status_code == 200

        summary_response = client.get("/api/daily-payables/summary", params={"date": "2026-08-21"})
        assert summary_response.status_code == 200, summary_response.text
        summary = summary_response.json()
        assert summary["history_start_date"] == history_start
        assert summary["totals_cny"]["due_today"] == 20680
        assert summary["totals_cny"]["paid_today"] == 10000
        assert summary["totals_cny"]["end_pending"] == 10680
        currencies = {item["currency"]: item for item in summary["currency_totals"]}
        assert currencies["CNY"]["end_pending"] == 10000
        assert currencies["USD"]["end_pending"] == 100

        details_response = client.get("/api/daily-payables/details", params={"date": "2026-08-21"})
        assert details_response.status_code == 200, details_response.text
        details = details_response.json()["items"]
        logical_ids = [item["logical_request_id"] for item in details]
        assert logical_ids.count(request["logical_request_id"]) == 1
        assert hidden_request["logical_request_id"] not in logical_ids
        assert no_due_request["logical_request_id"] not in logical_ids
        assert reopened_request["logical_request_id"] not in logical_ids
        assert next(item for item in details if item["logical_request_id"] == request["logical_request_id"])[
            "pending_amount"
        ] == 10000

        trend_response = client.get(
            "/api/daily-payables/trend",
            params={"start": "2026-08-21", "end": "2026-08-22"},
        )
        assert trend_response.status_code == 200, trend_response.text
        trend = trend_response.json()["points"]
        assert trend[0]["date"] == "2026-08-21"
        assert trend[0]["totals_cny"]["end_pending"] == 10680
        assert trend[1]["totals_cny"]["paid_today"] == 10000
        assert trend[1]["totals_cny"]["overdue_pending"] == 980

        next_day_details = client.get(
            "/api/daily-payables/details", params={"date": "2026-08-22"}
        ).json()["items"]
        assert reopened_request["logical_request_id"] in {
            item["logical_request_id"] for item in next_day_details
        }


def test_daily_payables_rejects_dates_before_baseline_and_long_trends():
    with TestClient(app) as client:
        login(client)
        with connect() as conn:
            history_start = get_daily_payables_history_start_date(conn)
        earlier = (date.fromisoformat(history_start) - timedelta(days=1)).isoformat()
        response = client.get("/api/daily-payables/summary", params={"date": earlier})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "HISTORY_NOT_AVAILABLE"

        response = client.get(
            "/api/daily-payables/trend",
            params={"start": history_start, "end": (date.fromisoformat(history_start) + timedelta(days=93)).isoformat()},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "TREND_RANGE_TOO_LARGE"


def test_daily_payables_can_exclude_mexico_and_region_review_history():
    with TestClient(app) as client:
        login(client)
        selected = date.today()
        selected_iso = selected.isoformat()
        china_sheet = f"中国地区-{uuid.uuid4().hex}"
        mexico_sheet = f"墨西哥地区-{uuid.uuid4().hex}"
        review_sheet = f"地区待核对-{uuid.uuid4().hex}"
        china = create_request(
            client,
            amount=100,
            source_sheet=china_sheet,
            needed_payment_date=selected_iso,
        )
        mexico = create_request(
            client,
            amount=200,
            source_sheet=mexico_sheet,
            needed_payment_date=selected_iso,
            execution_region="Mexico",
        )
        review = create_request(
            client,
            amount=300,
            source_sheet=review_sheet,
            needed_payment_date=selected_iso,
            execution_region=None,
        )

        with connect() as conn:
            all_regions = daily_snapshot(
                conn,
                selected,
                include_details=True,
                china_only=False,
                allowed_sheets={china_sheet, mexico_sheet, review_sheet},
            )
            china_only = daily_snapshot(
                conn,
                selected,
                include_details=True,
                china_only=True,
                allowed_sheets={china_sheet, mexico_sheet, review_sheet},
            )

        assert all_regions["totals_cny"]["due_today"] == 600
        assert china_only["totals_cny"]["due_today"] == 100
        assert [item["logical_request_id"] for item in china_only["items"]] == [
            china["logical_request_id"]
        ]
        assert mexico["logical_request_id"] not in {
            item["logical_request_id"] for item in china_only["items"]
        }
        assert review["logical_request_id"] not in {
            item["logical_request_id"] for item in china_only["items"]
        }
