"""Production entry point for the cashier payment application."""

import sys
from pathlib import Path

import uvicorn


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8011)
