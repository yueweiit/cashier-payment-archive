from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from backend.app.db import connect, now_iso
from backend.app.main import app
from backend.app.payable_history import payment_effective_at, record_request_state


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200


def create_request(client: TestClient, *, amount: float = 20000) -> dict:
    batch = client.post(
        "/api/batches",
        json={"name": f"每日应付-{uuid.uuid4().hex}", "start_date": "2026-08-21", "end_date": "2026-08-28"},
    ).json()["batch"]
    return client.post(
        f"/api/batches/{batch['id']}/requests",
        json={
            "source_sheet": "每日应付测试公司",
            "summary": "历史状态测试",
            "applicant": "测试申请人",
            "amount": amount,
            "currency": "CNY",
            "needed_payment_date": "2026-08-21",
        },
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
