from __future__ import annotations
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_to_bytes(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode(s + pad)


def load_public_key_from_b64url(b64url_pem: str) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(b64url_to_bytes(b64url_pem))
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError(f"Expected RSA public key, got {type(key).__name__}")
    return key


def load_public_key(pem: bytes) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError(f"Expected RSA public key, got {type(key).__name__}")
    return key


def load_private_key(pem: bytes) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key, got {type(key).__name__}")
    return key


def rsa_oaep_encrypt(public_key_pem_or_b64url: bytes | str, plaintext: bytes) -> str:
    if isinstance(public_key_pem_or_b64url, str):
        pub = load_public_key_from_b64url(public_key_pem_or_b64url)
    else:
        pub = load_public_key(public_key_pem_or_b64url)
    ct = pub.encrypt(
        plaintext,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return _b64url_nopad(ct)


def rsa_oaep_decrypt(private_pem: bytes, ciphertext_b64url: str) -> bytes:
    priv = load_private_key(private_pem)
    ct = b64url_to_bytes(ciphertext_b64url)
    return priv.decrypt(
        ct,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


def rsassa_pss_sign(private_pem: bytes, message: bytes) -> str:
    priv = load_private_key(private_pem)
    sig = priv.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return _b64url_nopad(sig)


def rsassa_pss_verify(public_key_pem_or_b64url: bytes | str, message: bytes, sig_b64url: str) -> bool:
    if isinstance(public_key_pem_or_b64url, str):
        pub = load_public_key_from_b64url(public_key_pem_or_b64url)
    else:
        pub = load_public_key(public_key_pem_or_b64url)
    try:
        pub.verify(
            b64url_to_bytes(sig_b64url),
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False

 
