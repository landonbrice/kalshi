"""Kalshi REST auth: RSA-PSS-SHA256 signing.

Header scheme (verify against current Kalshi API docs before production use):
  KALSHI-ACCESS-KEY:       <api key id>
  KALSHI-ACCESS-TIMESTAMP: <unix ms>
  KALSHI-ACCESS-SIGNATURE: base64(RSA-PSS-SHA256(f"{ts}{method}{path}"))

Callers MUST pass the exact uppercase HTTP method that will be sent on the wire.
This helper does not normalize case — the signature is computed over the literal
method string. A mismatch between signed method and sent method will produce
opaque 401 responses from Kalshi.
"""

from __future__ import annotations

import base64
import time
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# NOTE: cache is keyed by Path object, not file content. If the on-disk PEM is
# rotated while a process is running, the old key stays cached until restart.
# Phase 3's sanctioned auto-quoting loop is the first place this matters.
@lru_cache(maxsize=1)
def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key at {path}, got {type(key).__name__}")
    return key


def sign_request(private_key_path: Path, timestamp_ms: int, method: str, path: str) -> str:
    """Return base64 RSA-PSS-SHA256 signature of f'{ts}{method}{path}'.

    `method` must be uppercase and match the wire method exactly.
    """
    key = _load_private_key(private_key_path)
    message = f"{timestamp_ms}{method}{path}".encode()
    sig = key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def signed_headers(
    *,
    api_key_id: str,
    private_key_path: Path,
    method: str,
    path: str,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    ts = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
        "KALSHI-ACCESS-SIGNATURE": sign_request(private_key_path, ts, method, path),
    }
