# SOCP Signature implementation per §12

from __future__ import annotations
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from shared.crypto.crypto import rsassa_pss_sign
from shared.crypto.keys import RSAKeypair

class Signer(ABC):
    """Abstract base class for message signers"""
    @abstractmethod
    def sign(self, data: Dict[str, Any]) -> Optional[str]:
        """Sign data and return signature"""
        ...
    
    @abstractmethod
    def get_public_key(self) -> str:
        """Get public key for verification"""
        ...
class RSAClientContentSigner(Signer):
    """
    RSA-based signer implementing SOCP client signature requirements per §12.
    
    Handles two types of signatures:
    1. Content signatures (content_sig): End-to-end message authentication
       - For DM: SHA256(ciphertext || from || to || ts)
       - For Public Channel: SHA256(ciphertext || from || ts)
    
    2. Transport signatures (sig): Envelope authentication
       - Canonicalized JSON payload with sorted keys
    """
    
    def __init__(self, keypair: RSAKeypair):
        """
        Initialize client signer with an RSAKeypair.
        
        Args:
            keypair: RSAKeypair object containing both private and public keys
        """
        self.keypair = keypair
    
    def sign(self, data: Dict[str, Any]) -> Optional[str]:
        """
        Sign transport envelope payload (canonicalized JSON) per SOCP §12.
        
        This is the transport signature that goes in the envelope's 'sig' field.
        
        Args:
            data: Payload dictionary to sign
            
        Returns:
            Base64url encoded signature
        """
        # Canonicalize JSON payload (sorted keys, no whitespace)
        canonical_json = json.dumps(data, separators=(',', ':'), sort_keys=True)
        
        # Sign with RSASSA-PSS
        signature = rsassa_pss_sign(self.keypair.private_pem, canonical_json.encode())
        
        return signature
    
    def sign_dm_content(self, ciphertext: str, from_id: str, to_id: str, ts: int) -> str:
        """
        Sign direct message content per SOCP §12.
        
        This is the content_sig field for MSG_DIRECT messages.
        Canonical form: SHA256(ciphertext || from || to || ts)
        
        Args:
            ciphertext: Base64url encoded RSA-OAEP ciphertext
            from_id: Sender's UUIDv4
            to_id: Recipient's UUIDv4
            ts: Unix timestamp in milliseconds
            
        Returns:
            Base64url encoded content signature
        """
        canonical = (ciphertext + from_id + to_id + str(ts)).encode()
        return rsassa_pss_sign(self.keypair.private_pem, canonical)
    
    def sign_public_channel_content(self, ciphertext: str, from_id: str, ts: int) -> str:
        """
        Sign public channel message content per SOCP §12.
        
        This is the content_sig field for MSG_PUBLIC_CHANNEL messages.
        Canonical form: SHA256(ciphertext || from || ts)
        
        Args:
            ciphertext: Base64url encoded message content
            from_id: Sender's UUIDv4
            ts: Unix timestamp in milliseconds
            
        Returns:
            Base64url encoded content signature
        """
        canonical = (ciphertext + from_id + str(ts)).encode()
        return rsassa_pss_sign(self.keypair.private_pem, canonical)
    
    def sign_file_chunk_content(self, ciphertext: str, from_id: str, to_id: str, ts: int) -> str:
        """
        Sign file chunk content (same as DM content signature).
        
        Args:
            ciphertext: Base64url encoded RSA-OAEP ciphertext
            from_id: Sender's UUIDv4
            to_id: Recipient's UUIDv4
            ts: Unix timestamp in milliseconds
            
        Returns:
            Base64url encoded content signature
        """
        # File chunks use same signing as DM per SOCP §9.4
        return self.sign_dm_content(ciphertext, from_id, to_id, ts)
    
    @staticmethod
    def verify_dm_content(sender_pub: str, ciphertext: str, from_id: str, to_id: str, ts: int, signature: str) -> bool:
        """
        Verify direct message content signature per SOCP §12.
        
        Args:
            sender_pub: Sender's base64url RSA public key
            ciphertext: Base64url encoded RSA-OAEP ciphertext
            from_id: Sender's UUIDv4
            to_id: Recipient's UUIDv4
            ts: Unix timestamp in milliseconds
            signature: Base64url encoded content signature
            
        Returns:
            True if signature is valid, False otherwise
        """
        from shared.crypto.crypto import rsassa_pss_verify
        canonical = (ciphertext + from_id + to_id + str(ts)).encode()
        return rsassa_pss_verify(sender_pub, canonical, signature)
    
    @staticmethod
    def verify_public_channel_content(sender_pub: str, ciphertext: str, from_id: str, ts: int, signature: str) -> bool:
        """
        Verify public channel message content signature per SOCP §12.
        
        Args:
            sender_pub: Sender's base64url RSA public key
            ciphertext: Base64url encoded message content
            from_id: Sender's UUIDv4
            ts: Unix timestamp in milliseconds
            signature: Base64url encoded content signature
            
        Returns:
            True if signature is valid, False otherwise
        """
        from shared.crypto.crypto import rsassa_pss_verify
        canonical = (ciphertext + from_id + str(ts)).encode()
        return rsassa_pss_verify(sender_pub, canonical, signature)
    
    @staticmethod
    def verify_file_chunk_content(sender_pub: str, ciphertext: str, from_id: str, to_id: str, ts: int, signature: str) -> bool:
        """
        Verify file chunk content signature (same as DM content signature).
        
        Args:
            sender_pub: Sender's base64url RSA public key
            ciphertext: Base64url encoded RSA-OAEP ciphertext
            from_id: Sender's UUIDv4
            to_id: Recipient's UUIDv4
            ts: Unix timestamp in milliseconds
            signature: Base64url encoded content signature
            
        Returns:
            True if signature is valid, False otherwise
        """
        # File chunks use same verification as DM per SOCP §9.4
        return RSAClientContentSigner.verify_dm_content(sender_pub, ciphertext, from_id, to_id, ts, signature)
    
    def get_public_key(self) -> str:
        """
        Get public key in base64url format for sender_pub field.
        
        Returns:
            Base64url encoded RSA-4096 public key
        """
        return self.keypair.public_b64url()
        
        
class RSATransportSigner(Signer):
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


