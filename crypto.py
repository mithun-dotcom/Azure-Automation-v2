"""Encrypt per-tenant refresh tokens at rest using Fernet."""
from cryptography.fernet import Fernet
from .config import get_settings


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY not set")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def generate_key() -> str:
    """Run once to mint a key for TOKEN_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()
