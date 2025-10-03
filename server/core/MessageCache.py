from __future__ import annotations

import time
from collections import deque
from typing import Dict, Deque, Optional, Tuple

from shared.envelope import Envelope
from shared.log import get_logger

logger = get_logger(__name__)


class MessageDeduplicationCache:
    """
    Duplicate suppression cache per SOCP §10.
    
    Uses content_sig (the user's end-to-end signature) as the unique identifier
    for SERVER_DELIVER messages. The content_sig is cryptographically unique per
    message and prevents delivery loops in the mesh network.
    """
    
    def __init__(self, ttl: float = 120.0):
        """
        Initialize the deduplication cache.
        
        Args:
            ttl: Time-to-live in seconds for cached message IDs (default 120s)
        """
        self.ttl = ttl
        self._seen_messages: Dict[str, float] = {}
        self._seen_queue: Deque[Tuple[str, float]] = deque()
    
    def get_message_id(self, envelope: Envelope) -> Optional[str]:
        """
        Extract the unique message identifier from the envelope.
        
        For SERVER_DELIVER messages, uses the content_sig from the payload,
        which is the sender's RSASSA-PSS signature over the encrypted content.
        This is already unique per message.
        
        Args:
            envelope: The envelope to extract ID from
            
        Returns:
            The content_sig string if present, None otherwise
        """
        if envelope.type == "SERVER_DELIVER" and isinstance(envelope.payload, dict):
            return envelope.payload.get("content_sig")
        return None
    
    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if message ID is in seen cache (duplicate).
        
        Args:
            message_id: The content_sig to check
            
        Returns:
            True if this message has been seen before, False otherwise
        """
        return message_id in self._seen_messages
    
    def mark_seen(self, message_id: str) -> None:
        """
        Mark message ID as seen with current timestamp.
        
        Args:
            message_id: The content_sig to mark as seen
        """
        now = time.monotonic()
        self._seen_messages[message_id] = now
        self._seen_queue.append((message_id, now))
        logger.debug("Marked message as seen: %s...", message_id[:16])
    
    def prune_expired(self) -> int:
        """
        Remove expired entries from seen cache.
        
        Returns:
            Number of entries removed
        """
        now = time.monotonic()
        cutoff = now - self.ttl
        removed = 0
        
        # Remove old entries from front of queue
        while self._seen_queue and self._seen_queue[0][1] < cutoff:
            msg_id, _ = self._seen_queue.popleft()
            if self._seen_messages.pop(msg_id, None) is not None:
                removed += 1
        
        if removed > 0:
            logger.debug("Pruned %d expired message IDs from cache", removed)
        
        return removed
    
    def check_and_mark(self, envelope: Envelope) -> bool:
        """
        Convenience method: extract content_sig, check if duplicate, and mark if not.
        
        This is the primary interface for deduplication - pass a SERVER_DELIVER
        Envelope and get a boolean indicating whether it should be dropped.
        
        Args:
            envelope: The SERVER_DELIVER envelope to check
            
        Returns:
            True if this is a duplicate (should be dropped), False if new (should process)
        """
        msg_id = self.get_message_id(envelope)
        
        # If no content_sig, can't check for duplicates (shouldn't happen for valid SERVER_DELIVER)
        if msg_id is None:
            logger.warning("No content_sig in %s from %s - cannot check duplicates", 
                         envelope.type, envelope.from_)
            return False  # Don't drop, let it through
        
        if self.is_duplicate(msg_id):
            logger.debug("Duplicate message detected: %s from %s", envelope.type, envelope.from_)
            return True
        
        self.mark_seen(msg_id)
        return False
    
    def clear(self) -> None:
        """Clear all cached message IDs."""
        self._seen_messages.clear()
        self._seen_queue.clear()
        logger.info("Cleared message deduplication cache")
    
    def size(self) -> int:
        """Return the current number of cached message IDs."""
        return len(self._seen_messages)
    
    def stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        return {
            "cached_messages": len(self._seen_messages),
            "queue_length": len(self._seen_queue),
        }

