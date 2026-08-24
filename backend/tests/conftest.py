import os
import shutil
import tempfile
from pathlib import Path


# Pytest imports conftest.py before collecting test modules. Database paths must
# be set here, before any test can import backend.app.db and freeze DB_PATH.
TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="cashier-payment-pytest-"))
os.environ["PAYMENT_APP_DATA_DIR"] = str(TEST_RUNTIME_DIR / "data")
os.environ["PAYMENT_APP_DB"] = str(TEST_RUNTIME_DIR / "app.db")
os.environ["PAYMENT_ATTACHMENT_STORAGE_DIR"] = str(TEST_RUNTIME_DIR / "attachment-storage")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_RUNTIME_DIR, ignore_errors=True)
