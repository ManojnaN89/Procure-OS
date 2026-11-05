from cryptography.fernet import Fernet

from p2p.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().tenant_secret_key
    if not key:
        raise RuntimeError("TENANT_SECRET_KEY is not set (python scripts/make_key.py makes one)")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
