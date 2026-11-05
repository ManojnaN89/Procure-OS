"""File storage behind a small interface (put, get, delete). Local disk now; cloud object storage replaces it in P6.

Every tenant has its own folder. Keys are made here, never taken from a user, and a key can never point outside the
tenant's folder.
"""

import secrets
from pathlib import Path
from typing import Protocol

from p2p.config import REPO_ROOT, get_settings


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put(self, tenant: str, data: bytes) -> str: ...

    def get(self, tenant: str, key: str) -> bytes: ...

    def delete(self, tenant: str, key: str) -> None: ...


class LocalDiskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, tenant: str, key: str) -> Path:
        base = (self.root / tenant).resolve()
        path = (base / key).resolve()
        if not path.is_relative_to(base) or path == base:
            raise StorageError("invalid storage key")
        return path

    def put(self, tenant: str, data: bytes) -> str:
        key = f"{secrets.token_hex(2)}/{secrets.token_hex(16)}"
        path = self._path(tenant, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, tenant: str, key: str) -> bytes:
        path = self._path(tenant, key)
        if not path.is_file():
            raise StorageError("file not found")
        return path.read_bytes()

    def delete(self, tenant: str, key: str) -> None:
        self._path(tenant, key).unlink(missing_ok=True)


def get_storage() -> Storage:
    configured = get_settings().storage_dir
    return LocalDiskStorage(Path(configured) if configured else REPO_ROOT / "storage")
