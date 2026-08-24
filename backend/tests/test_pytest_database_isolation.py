import os
from pathlib import Path

from backend.app import db as db_module


def test_pytest_never_uses_the_repository_local_database():
    repository_database = Path(__file__).resolve().parents[2] / "data" / "app.db"

    assert db_module.DB_PATH.resolve() != repository_database.resolve()
    assert Path(os.environ["PAYMENT_APP_DB"]).resolve() == db_module.DB_PATH.resolve()
