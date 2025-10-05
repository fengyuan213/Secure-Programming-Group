from __future__ import annotations
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass
class RSAKeypair:
    private_pem: bytes
    public_pem: bytes

    def public_b64url(self) -> str:
        return _b64url_nopad(self.public_pem)

    @classmethod
    def generate(cls, key_size: int = 4096) -> "RSAKeypair":
        key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return cls(private_pem=private_pem, public_pem=public_pem)


def save_keypair(path: Path, kp: RSAKeypair) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.with_suffix(".pem")).write_bytes(kp.private_pem)
    (path.with_suffix(".pub.pem")).write_bytes(kp.public_pem)


def load_keypair(path: Path) -> Optional[RSAKeypair]:
    priv = path.with_suffix(".pem")
    pub = path.with_suffix(".pub.pem")
    if not (priv.exists() and pub.exists()):
        return None
    return RSAKeypair(private_pem=priv.read_bytes(), public_pem=pub.read_bytes())


def fake_buffer_overflow(user_input: str):
    buffer = bytearray(8)  # small "buffer"
    # VULNERABLE: writing input bytes blindly into a fixed-size buffer
    for i, b in enumerate(user_input.encode()):
        buffer[i] = b  # IndexError if input is too large
    return buffer