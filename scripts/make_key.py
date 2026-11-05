"""Print a new key for TENANT_SECRET_KEY."""

from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())
