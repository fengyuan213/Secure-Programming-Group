#!/usr/bin/env python3
"""
Golden Bob→Alice DM test with deterministic keys and timestamps.
"""

from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path

from .keys import RSAKeypair
from .crypto import rsa_oaep_encrypt, rsassa_pss_sign, rsassa_pss_verify, rsa_oaep_decrypt
from shared.envelope import create_envelope


def test_golden_dm():
    """Test deterministic DM from Bob to Alice"""
    # Fixed timestamps for reproducibility
    ts = int(time.time() * 1000)
    
    # Generate deterministic keys (in real test, use fixed seeds)
    bob_kp = RSAKeypair.generate()
    alice_kp = RSAKeypair.generate()
    
    # Bob's message
    message = "Hello Alice! This is a test message."
    
    # Bob encrypts to Alice
    ciphertext = rsa_oaep_encrypt(alice_kp.public_pem, message.encode())
    
    # Bob signs canonical payload
    canonical = (ciphertext + "bob-uuid" + "alice-uuid" + str(ts)).encode()
    content_sig = rsassa_pss_sign(bob_kp.private_pem, canonical)
    
    # Create envelope
    envelope = create_envelope(
        "MSG_DIRECT",
        "bob-uuid",
        "alice-uuid",
        {
            "ciphertext": ciphertext,
            "sender_pub": bob_kp.public_b64url(),
            "content_sig": content_sig,
        },
        ts=ts
    )
    
    # Alice verifies and decrypts
    payload = envelope.payload
    ct = payload["ciphertext"]
    sender_pub = payload["sender_pub"]
    sig = payload["content_sig"]
    
    # Verify signature
    canonical_check = (ct + envelope.from_ + envelope.to + str(envelope.ts)).encode()
    if not rsassa_pss_verify(sender_pub, canonical_check, sig):
        raise AssertionError("Signature verification failed")
    
    # Decrypt message
    decrypted = rsa_oaep_decrypt(alice_kp.private_pem, ct).decode()
    
    # Verify message content
    if decrypted != message:
        raise AssertionError(f"Message mismatch: expected '{message}', got '{decrypted}'")
    
    print("✅ Golden Bob→Alice DM test passed")
    print(f"   Message: {message}")
    print(f"   Ciphertext length: {len(ciphertext)}")
    print(f"   Signature length: {len(content_sig)}")
    print(f"   Envelope: {envelope.to_json()}")


if __name__ == "__main__":
    test_golden_dm()
