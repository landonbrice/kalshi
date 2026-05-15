import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_ws.api.auth import sign_request, signed_headers


@pytest.fixture(scope="module")
def rsa_keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("keys") / "test_key.pem"
    path.write_bytes(pem)
    return path, key.public_key()


def test_sign_request_produces_verifiable_signature(
    rsa_keypair: tuple[Path, rsa.RSAPublicKey],
) -> None:
    key_path, public_key = rsa_keypair
    timestamp_ms = 1_700_000_000_000
    method = "GET"
    path = "/trade-api/v2/markets/KXNBA-25MAR03-NBA"

    signature_b64 = sign_request(key_path, timestamp_ms, method, path)

    message = f"{timestamp_ms}{method}{path}".encode()
    public_key.verify(
        base64.b64decode(signature_b64),
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )  # raises if invalid


def test_signed_headers_shape(rsa_keypair: tuple[Path, rsa.RSAPublicKey]) -> None:
    key_path, _ = rsa_keypair
    headers = signed_headers(
        api_key_id="abc-123",
        private_key_path=key_path,
        method="GET",
        path="/trade-api/v2/markets/X",
    )
    assert headers["KALSHI-ACCESS-KEY"] == "abc-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 0
