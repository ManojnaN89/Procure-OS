import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from p2p.config import get_settings
from p2p.main import app
from p2p.platform import routing, storage, tenants, users
from p2p.platform.files import MAX_BYTES, safe_filename
from p2p.platform.tokens import create_access_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _login(control_db: str) -> tuple[str, int, dict[str, str]]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    uid = users.create_user(slug, f"u{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "U", "Buyer")
    return slug, uid, {"Authorization": f"Bearer {create_access_token(uid, slug, 'Buyer')}"}


def test_a_file_can_be_uploaded_and_downloaded_unchanged(control_db: str) -> None:
    _, _, auth = _login(control_db)
    r = client.post(
        "/api/files", files={"file": ("quote.pdf", b"%PDF-1.4 fake quote", "application/pdf")}, headers=auth
    )

    assert r.status_code == 201
    body = r.json()
    assert body["size"] == 19 and body["filename"] == "quote.pdf" and len(body["sha256"]) == 64
    got = client.get(f"/api/files/{body['id']}", headers=auth)
    assert got.status_code == 200 and got.content == b"%PDF-1.4 fake quote"
    assert got.headers["content-type"] == "application/pdf"
    assert "attachment" in got.headers["content-disposition"]


def test_one_tenant_cannot_fetch_another_tenants_file(control_db: str) -> None:
    _, _, auth_a = _login(control_db)
    _, _, auth_b = _login(control_db)
    file_id = client.post("/api/files", files={"file": ("a.txt", b"secret of A", "text/plain")}, headers=auth_a).json()[
        "id"
    ]

    assert client.get(f"/api/files/{file_id}", headers=auth_a).status_code == 200
    assert client.get(f"/api/files/{file_id}", headers=auth_b).status_code == 404  # B has no such row
    assert client.get(f"/api/files/{file_id}").status_code == 401  # no login, no file


def test_the_storage_layer_itself_keeps_tenants_apart_and_refuses_odd_keys(storage_dir: Path) -> None:
    store = storage.LocalDiskStorage(storage_dir)
    key = store.put("alpha", b"data")
    assert store.get("alpha", key) == b"data"
    with pytest.raises(storage.StorageError):
        store.get("beta", key)  # the same key under another tenant does not exist
    for bad in ("../alpha/" + key, "..", "/etc/passwd", ""):
        with pytest.raises(storage.StorageError):
            store.get("beta", bad)
    store.delete("alpha", key)
    with pytest.raises(storage.StorageError):
        store.get("alpha", key)


def test_every_upload_is_audited_and_filenames_are_made_harmless(control_db: str) -> None:
    slug, uid, auth = _login(control_db)
    r = client.post(
        "/api/files", files={"file": ("..\\..\\evil name?.exe", b"x", "application/octet-stream")}, headers=auth
    )
    assert r.json()["filename"] == "evil name_.exe"
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("???") == "file"

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(routing.engine_for(slug)) as db:
        row = db.execute(text("select actor_id, action from audit_event where action = 'file.upload'")).one()
    assert tuple(row) == (str(uid), "file.upload")


def test_an_oversized_upload_is_refused(control_db: str) -> None:
    _, _, auth = _login(control_db)
    r = client.post(
        "/api/files", files={"file": ("big.bin", b"0" * (MAX_BYTES + 1), "application/octet-stream")}, headers=auth
    )
    assert r.status_code == 413
