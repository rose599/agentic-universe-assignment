"""PII protection.

Two independent keys are derived from one master secret:

* an encryption key (Fernet: AES-128-CBC + HMAC) for values we may need to read
  back later (customer name, full phone number);
* an HMAC key used to build a *blind index* of the canonical phone number.

The blind index (`phone_hash`) is what lets us match an event to a lead by phone
without storing the phone in plaintext. It is a keyed HMAC, not a plain hash, so
the 10-digit Indian mobile space (~4e9 numbers) cannot be brute-forced offline
without the key.

The master secret comes from the AU_PII_SECRET env var. If it is absent we create
a random one in `<db>.secret` next to the database (dev convenience only; in
production the secret belongs in a KMS / secret manager, never next to the data).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from cryptography.fernet import Fernet

ENV_VAR = "AU_PII_SECRET"


def _derive(secret: bytes, label: bytes) -> bytes:
    return hmac.new(secret, label, hashlib.sha256).digest()


class Vault:
    def __init__(self, secret: bytes):
        if len(secret) < 16:
            raise ValueError("PII secret is too short")
        self._fernet = Fernet(base64.urlsafe_b64encode(_derive(secret, b"au-ledger/enc/v1")))
        self._mac_key = _derive(secret, b"au-ledger/phone-index/v1")

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

    def phone_hash(self, canonical_phone: str) -> str:
        """Deterministic blind index for a canonical '+91XXXXXXXXXX' number."""
        return hmac.new(self._mac_key, canonical_phone.encode("ascii"), hashlib.sha256).hexdigest()


def load_vault(db_path: str | os.PathLike) -> Vault:
    env = os.environ.get(ENV_VAR)
    if env:
        return Vault(env.encode("utf-8"))
    key_file = Path(str(db_path) + ".secret")
    if key_file.exists():
        return Vault(bytes.fromhex(key_file.read_text().strip()))
    secret = os.urandom(32)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secret.hex())
    return Vault(secret)
