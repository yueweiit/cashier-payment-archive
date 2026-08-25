from pathlib import Path


RUNBOOK = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "mexico-approver-access-release.md"
)


def test_mexico_release_runbook_reuses_the_live_service_database_path() -> None:
    content = RUNBOOK.read_text(encoding="utf-8")

    assert "/proc/{service_pid}/environ" in content
    assert 'PAYMENT_APP_DB="$PAYMENT_APP_DB"' in content
    assert content.count('PAYMENT_APP_DB="$PAYMENT_APP_DB"') >= 3
    assert 'cp data/app.db' not in content
    assert 'cp "$PAYMENT_APP_DB"' in content
    assert '"${PAYMENT_APP_DB}-wal"' in content
    assert '"${PAYMENT_APP_DB}-shm"' in content
    ownership_handoff = 'sudo chown "$(id -u):$(id -g)" "$RELEASE_ENV_FILE"'
    assert ownership_handoff in content
    assert content.index(ownership_handoff) < content.index('. "$RELEASE_ENV_FILE"')
