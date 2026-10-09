import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_ROOT = Path(tempfile.mkdtemp(prefix="ems-educativo-tests-"))

os.environ["EMS_EXECUTION_MODE"] = "simulated"
os.environ["EMS_MULTI_UPF_ENABLED"] = "false"
os.environ["EMS_ENABLE_REAL_CAPTURES"] = "false"
os.environ["EMS_DATABASE_PATH"] = str(TEST_ROOT / "test-ems.db")
os.environ["EMS_CAPTURE_DIR"] = str(TEST_ROOT / "captures")
os.environ["EMS_BACKUP_DIR"] = str(TEST_ROOT / "backups")
os.environ["EMS_ALLOWED_CONFIG_ROOTS"] = f'["{(TEST_ROOT / "config").as_posix()}"]'
os.environ["EMS_ALLOWED_INTERFACES"] = '["lo","ogstun","any"]'

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def teacher_headers(client):
    response = client.post("/api/v1/auth/login", json={"username": "docente", "password": "teacher-change-me"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def student_headers(client):
    response = client.post("/api/v1/auth/login", json={"username": "grupo1", "password": "grupo1-pass-2026"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
