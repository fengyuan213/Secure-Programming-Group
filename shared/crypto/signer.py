# Add this to shared/envelope.py or create a new file shared/signer.py

from __future__ import annotations
import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import secrets
import base64

from shared.crypto.crypto import rsassa_pss_sign, rsassa_pss_verify
from shared.crypto.keys import RSAKeypair

class Signer(ABC):
    """Abstract base class for message signers"""
    @abstractmethod
    def sign(self,data: Dict[str, Any]) -> Optional[str]:
        """Sign data"""
        pass
    @abstractmethod
    def get_public_key(self) -> str:
        """Get public key for verification"""
        pass
class RSAClientContentSigner(Signer):
    """RSA-based signer implementing SOCP signature requirements"""
    
    def __init__(self, private_key_pem: bytes, public_key_pem: bytes):
        self.private_key = serialization.load_pem_private_key(
            private_key_pem, password=None
        )
        self.public_key = serialization.load_pem_public_key(public_key_pem)
        
        
        
        
class RSAServerTransportSigner(Signer):
    """RSA-based signer implementing SOCP signature requirements"""
    
    def __init__(self, keypair: RSAKeypair):
        self.keypair = keypair
    
    
    def sign(self, data: Dict[str, Any]) -> Optional[str]:
        """Sign transport envelope payload (canonicalized JSON)"""
        # Canonicalize JSON payload (sorted keys, no whitespace)
        canonical_json = json.dumps(data, separators=(',', ':'), sort_keys=True)
        
        # Sign with RSASSA-PSS
        signature = rsassa_pss_sign(self.keypair.private_pem, canonical_json.encode())
    
        # Return base64url encoded signature
        return signature
    def get_public_key(self) -> str:
        """Get public key in base64url format"""
        return self.keypair.public_b64url()


