from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable, Dict, Set, Any, Optional

from server.core.MessageTypes import MessageType
from shared.envelope import Envelope, create_envelope
from shared.utils import is_uuid_v4
from shared.log import get_logger

if TYPE_CHECKING:
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink

logger = get_logger(__name__)

# Type alias for handler functions
MessageHandler = Callable[["SOCPServer", "ConnectionLink", Envelope], Awaitable[None]]

class DMRecipientRouter:
    def __init__(self, server: "SOCPServer", connection: "ConnectionLink", sender_id: str, recipient_id: str):
        self.server = server
        self.connection = connection
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        self.envelope_delivered_local:Optional[Envelope] = None
        self.envelope_delivered_remote:Optional[Envelope] = None
    def envelope_to_local(self, env:Envelope):
        """Directly supply prebuilt local envelope"""
        self.envelope_delivered_local = env
        return self

    def envelope_to_another_server(self, env:Envelope):
        """Directly supply prebuilt remote envelope"""
        self.envelope_delivered_remote = env
        return self

    async def run(self) -> bool:
        rec = self.server.users.get(self.recipient_id)
        location = getattr(rec, "location", None)

        # Case 1: Local
        if location == "local":
            local_link = getattr(rec, "link", None)
            if not local_link:
                await self.server.send_error(
                    self.connection,
                    "NO_ROUTE",
                    f"Recipient {self.recipient_id} not connected",
                    to_id=self.sender_id,
                )
                return False

            if not self.envelope_delivered_local:
                raise RuntimeError("No local_variant supplied")

            await local_link.send_message(self.envelope_delivered_local)
            logger.info("Delivered %s from %s to local user %s",self.envelope_delivered_local.type, self.sender_id, self.recipient_id)
            
            return True

        # Case 2: Remote
        if isinstance(location, str) and is_uuid_v4(location):
            server_link = self.server._resolve_server_link(location)
            if not server_link:
                await self.server.send_error(
                    self.connection,
                    "NO_ROUTE",
                    f"No server link for {location}",
                    to_id=self.sender_id,
                )
                return False

            if not self.envelope_delivered_remote:
                raise RuntimeError("No remote_variant supplied")

            await server_link.send_message(self.envelope_delivered_remote)
            logger.info("Forwarded %s from %s to server %s for user %s", self.envelope_delivered_remote.type, self.sender_id, location, self.recipient_id)
          
            return True

        # Case 3: No route
        await self.server.send_error(
            self.connection,
            "NO_ROUTE",
            f"No route to recipient {self.recipient_id}",
            to_id=self.sender_id,
        )
        return False
class GenericValidators:
    """
    Generic reusable validators for message validation (SOCP compliance).
    Can be used across different message types.
    """
    
    @staticmethod
    async def validate_sender(
        server: "SOCPServer",
        connection: "ConnectionLink",
        envelope: Envelope,
    ) -> Optional[str]:
        """Validate that envelope sender matches the connected user."""
        sender_id = connection.user_id
        if sender_id is None or sender_id != envelope.from_:
            await server.send_error(
                connection,
                "BAD_SENDER",
                "Envelope sender mismatch",
                to_id=envelope.from_,
            )
            return None
        return sender_id
    
    @staticmethod
    async def validate_uuid_field(
        server: "SOCPServer",
        connection: "ConnectionLink",
        field_value: Any,
        field_name: str,
        sender_id: str,
    ) -> bool:
        """Validate that a field is a valid UUIDv4."""
        if not isinstance(field_value, str) or not is_uuid_v4(field_value):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"{field_name} must be valid UUIDv4",
                to_id=sender_id,
            )
            return False
        return True
    
    @staticmethod
    async def validate_required_fields(
        server: "SOCPServer",
        connection: "ConnectionLink",
        payload: Dict,
        required_fields: Set[str],
        sender_id: str,
    ) -> bool:
        """Validate that payload contains all required fields."""
        if not required_fields.issubset(payload.keys()):
            missing = required_fields - set(payload.keys())
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"Missing required fields: {sorted(missing)}",
                to_id=sender_id,
            )
            return False
        return True
    
    @staticmethod
    async def validate_enum_field(
        server: "SOCPServer",
        connection: "ConnectionLink",
        field_value: Any,
        field_name: str,
        allowed_values: Set[str],
        sender_id: str,
    ) -> bool:
        """Validate that a field has one of the allowed enum values."""
        if field_value not in allowed_values:
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"{field_name} must be one of {sorted(allowed_values)}",
                to_id=sender_id,
            )
            return False
        return True


class GenericRouters:
    """
    Generic reusable routing functions for DM and public channel messages.
    Used by multiple message types (MSG_DIRECT, MSG_PUBLIC_CHANNEL, FILE_*, etc.).
    
    Note: DM routing now uses DMRecipientRouter builder pattern for better flexibility.
    """
    
    @staticmethod
    async def route_to_public_channel(
        server: "SOCPServer",
        connection: "ConnectionLink",
        sender_id: str,
        payload: Dict[str, Any],
        message_type: str,
        exclude_sender: bool = True,
    ) -> bool:
        """
        Generic routing for public channel messages to all members.
        
        Handles:
        - Authorization check (sender must be member)
        - Local delivery to all local members
        - Broadcasting to other servers
        
        Args:
            exclude_sender: If True, don't echo back to sender (default for chat)
        
        Returns True if successfully routed, False otherwise.
        """
        # Check authorization
        if not hasattr(server, 'public_channel') or sender_id not in server.public_channel.get_members():
            await server.send_error(
                connection,
                "UNAUTHORIZED",
                "Not a member of public channel",
                to_id=sender_id,
            )
            return False
        
        # Get all channel members
        members = server.public_channel.get_members()
        
        # Deliver to local members
        for member_id in members:
            if exclude_sender and member_id == sender_id:
                continue  # Don't echo back to sender
            
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    deliver_env = create_envelope(
                        message_type,
                        sender_id,
                        "public",
                        payload,
                    )
                    await local_link.send_message(deliver_env)
                    logger.debug("Delivered %s to local member %s", message_type, member_id)
        
        # Broadcast to all other servers
        broadcast_env = create_envelope(
            message_type,
            sender_id,
            "public",
            payload,
        )
        for link in server.all_server_connection_links:
            await link.send_message(broadcast_env)
        
        logger.info("Broadcasted %s from %s to public channel (%d members)", message_type, sender_id, len(members))
        return True
    
    @staticmethod
    async def relay_server_to_dm_recipient(
        server: "SOCPServer",
        envelope: Envelope,
        message_type: str,
    ) -> None:
        """
        Generic relay for server-forwarded DM messages.
        No error handling - pure relay from server to local user or next hop.
        """
        recipient_id = envelope.to
        payload = envelope.payload
        sender_id = envelope.from_
        
        # Extract user_id if wrapped in SERVER_DELIVER
        if "user_id" in payload:
            recipient_id = payload["user_id"]
        
        # Check routing table
        rec = server.users.get(recipient_id)
        location = getattr(rec, "location", None)
        
        # Local delivery
        if location == "local":
            local_link = getattr(rec, "link", None)
            if local_link:
                deliver_env = create_envelope(
                    message_type,
                    sender_id,
                    recipient_id,
                    payload,
                )
                await local_link.send_message(deliver_env)
                logger.debug("Relayed %s to local user %s", message_type, recipient_id)
            return
        
        # Forward to next hop
        if isinstance(location, str) and is_uuid_v4(location):
            server_link = server._resolve_server_link(location)
            if server_link:
                await server_link.send_message(envelope)
                logger.debug("Forwarded %s to server %s", message_type, location)
    
    @staticmethod
    async def relay_server_to_public_channel(
        server: "SOCPServer",
        envelope: Envelope,
        message_type: str,
    ) -> None:
        """
        Generic relay for server-forwarded public channel messages.
        Delivers to local members only - no re-broadcasting.
        """
        if not hasattr(server, 'public_channel'):
            return
        
        members = server.public_channel.get_members()
        payload = envelope.payload
        sender_id = envelope.from_
        
        # Deliver to all local members
        for member_id in members:
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    deliver_env = create_envelope(
                        message_type,
                        sender_id,
                        "public",
                        payload,
                    )
                    await local_link.send_message(deliver_env)
        
        logger.debug("Relayed %s to local public channel members", message_type)


class ServerMessageHandlers:
    """
    Registry of server message handlers.
    
    Each handler is a static method that processes one message type.
    This provides clean separation of concerns and makes testing easier.
    """
    
    @staticmethod
    async def handle_heartbeat(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle HEARTBEAT messages - update last_seen timestamp."""
        import time
        connection.last_seen = time.monotonic()
        logger.debug("Heartbeat received from %s", envelope.from_)
    
    @staticmethod
    async def handle_server_announce(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle SERVER_ANNOUNCE - pin/update server endpoint and pubkey."""
        from server.core.MemoryTable import ServerEndpoint
        
        payload = envelope.payload
        host = payload.get("host")
        port = payload.get("port")
        pubkey = payload.get("pubkey", "")
        origin_server_id = envelope.from_
        
        if not (isinstance(host, str) and isinstance(port, int)):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                "Malformed SERVER_ANNOUNCE payload",
                to_id=origin_server_id,
            )
            return
        
        # Preserve existing link if any, update endpoint and pubkey
        existing = server.servers.get(origin_server_id)
        from server.core.ConnectionLink import ConnectionLink as CL
        link = existing if isinstance(existing, CL) else getattr(existing, "link", None)
        server.add_update_server(origin_server_id, ServerEndpoint(host=host, port=port), link, pubkey)
        logger.info("Updated server %s from SERVER_ANNOUNCE to %s:%s", origin_server_id, host, port)
    
    @staticmethod
    async def handle_presence_gossip(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle USER_ADVERTISE and USER_REMOVE - maintain user presence."""
        msg_type = envelope.type
        origin_server_id = envelope.from_
        payload = envelope.payload
        user_id = payload.get("user_id")
        server_id = payload.get("server_id")
        
        if not (isinstance(user_id, str) and isinstance(server_id, str)):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"Malformed {msg_type} payload",
                to_id=origin_server_id,
            )
            return
        
        # Verify signature before mutating shared state
        if not server._verify_server_signature(origin_server_id, envelope):
            logger.warning(
                "Rejected %s from %s due to signature mismatch",
                msg_type,
                origin_server_id,
            )
            return
        
        if server_id != origin_server_id:
            logger.debug(
                "Forwarded %s claims host %s while signed by %s",
                msg_type,
                server_id,
                origin_server_id,
            )
        
        # Update user presence
        if msg_type == MessageType.USER_ADVERTISE.value:
            server.add_update_user(user_id, None, server_id)
        elif msg_type == MessageType.USER_REMOVE.value: # USER_REMOVE
            rec = server.users.get(user_id)
            if getattr(rec, "location", None) == server_id:
                del server.users[user_id]
        
        # Gossip to other connected servers
        for link in server.all_server_connection_links:
            if link is connection or (link.server_id and link.server_id == origin_server_id):
                continue
            logger.debug(
                "Forwarding %s for %s to server %s",
                msg_type,
                user_id,
                link.server_id,
            )
            await link.send_message(envelope)
    
    @staticmethod
    async def handle_server_deliver(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle SERVER_DELIVER - route message to local or remote user."""
        origin_server_id = envelope.from_
        
        # Duplicate suppression per SOCP §10
        if server.message_cache.check_and_mark(envelope):
            logger.debug("Dropping duplicate SERVER_DELIVER from %s", origin_server_id)
            return
        
        # Verify signature
        if not server._verify_server_signature(origin_server_id, envelope):
            logger.warning("SERVER_DELIVER failed signature from %s", origin_server_id)
            return
        
        payload = envelope.payload
        user_id = payload.get("user_id")
        
        if not await GenericValidators.validate_uuid_field(server, connection, user_id, "user_id", origin_server_id):
            return
        
        # After validation, user_id is guaranteed to be a valid UUID string
        assert isinstance(user_id, str)
        
        # Validate required fields per SOCP §8.3
        required_fields = {
            "ciphertext",
            "sender", "sender_pub", "content_sig",
        }
        if not await GenericValidators.validate_required_fields(server, connection, payload, required_fields, origin_server_id):
            return
        
        # Check for routing loop
        rec = server.users.get(user_id)
        target_location = getattr(rec, "location", None)
        if isinstance(target_location, str) and is_uuid_v4(target_location):
            if target_location == origin_server_id:
                logger.debug("SERVER_DELIVER already at destination %s; dropping", target_location)
                return
        
        # Prepare envelopes for routing
        deliver_payload = {
            "ciphertext": payload["ciphertext"],
            "sender": payload["sender"],
            "sender_pub": payload["sender_pub"],
            "content_sig": payload["content_sig"],
        }
        
        local_env = create_envelope(
            MessageType.USER_DELIVER.value,
            server.local_server.id,
            user_id,
            deliver_payload,
        )
        
        # For remote, forward the original SERVER_DELIVER envelope as-is
        remote_env = envelope
        
        # Use builder pattern for routing (sender_id is the original message sender for error context)
        original_sender = payload["sender"]
        result = await DMRecipientRouter(server, connection, original_sender, user_id) \
            .envelope_to_local(local_env) \
            .envelope_to_another_server(remote_env) \
            .run()
        
        if not result:
            logger.warning("Failed to route SERVER_DELIVER for user %s", user_id)


    @staticmethod
    async def handle_public_channel_add(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle PUBLIC_CHANNEL_ADD - notification that users joined the channel."""
        origin_server_id = envelope.from_
        payload = envelope.payload
        
        # Verify signature
        if not server._verify_server_signature(origin_server_id, envelope):
            logger.warning("PUBLIC_CHANNEL_ADD failed signature from %s", origin_server_id)
            return
        
        # Extract added users
        added_users = payload.get("add", [])
        if_version = payload.get("if_version")
        
        if not isinstance(added_users, list):
            logger.warning("Malformed PUBLIC_CHANNEL_ADD from %s", origin_server_id)
            return
        
        logger.info(
            "PUBLIC_CHANNEL_ADD from %s: %d users added (if_version=%s)",
            origin_server_id,
            len(added_users),
            if_version,
        )
        
        # Forward to other servers
        for link in server.all_server_connection_links:
            if link is connection or (link.server_id and link.server_id == origin_server_id):
                continue
            await link.send_message(envelope)
    
    @staticmethod
    async def handle_public_channel_updated(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle PUBLIC_CHANNEL_UPDATED - channel version and key wraps update."""
        origin_server_id = envelope.from_
        payload = envelope.payload
        
        # Verify signature
        if not server._verify_server_signature(origin_server_id, envelope):
            logger.warning("PUBLIC_CHANNEL_UPDATED failed signature from %s", origin_server_id)
            return
        
        # Extract version and wraps
        version = payload.get("version")
        wraps = payload.get("wraps", [])
        
        if not isinstance(version, int) or not isinstance(wraps, list):
            logger.warning("Malformed PUBLIC_CHANNEL_UPDATED from %s", origin_server_id)
            return
        
        logger.info(
            "PUBLIC_CHANNEL_UPDATED from %s: version=%d, %d wraps",
            origin_server_id,
            version,
            len(wraps),
        )
        
        # Distribute wraps to local users
        for wrap in wraps:
            if not isinstance(wrap, dict):
                continue
            
            member_id = wrap.get("member_id")
            wrapped_key = wrap.get("wrapped_key")
            
            if not (isinstance(member_id, str) and isinstance(wrapped_key, str)):
                continue
            
            # Check if this member is local
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    # Send updated wrap to local user
                    update_env = create_envelope(
                        "PUBLIC_CHANNEL_UPDATED",
                        server.local_server.id,
                        member_id,
                        {
                            "version": version,
                            "wrapped_key": wrapped_key,
                        },
                    )
                    await local_link.send_message(update_env)
                    logger.debug("Sent channel update to local user %s", member_id)
        
        # Forward to other servers
        for link in server.all_server_connection_links:
            if link is connection or (link.server_id and link.server_id == origin_server_id):
                continue
            await link.send_message(envelope)
    
    @staticmethod
    async def handle_public_channel_key_share(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle PUBLIC_CHANNEL_KEY_SHARE - distribute wrapped channel keys to members."""
        origin_server_id = envelope.from_
        payload = envelope.payload
        
        # Verify signature
        if not server._verify_server_signature(origin_server_id, envelope):
            logger.warning("PUBLIC_CHANNEL_KEY_SHARE failed signature from %s", origin_server_id)
            return
        
        # Extract shares
        shares = payload.get("shares", [])
        if not isinstance(shares, list):
            logger.warning("Malformed PUBLIC_CHANNEL_KEY_SHARE from %s", origin_server_id)
            return
        
        # Distribute shares to local users
        for share in shares:
            if not isinstance(share, dict):
                continue
            
            member_id = share.get("member")
            wrapped_key = share.get("wrapped_public_channel_key")
            
            if not (isinstance(member_id, str) and isinstance(wrapped_key, str)):
                continue
            
            # Check if this member is local
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    # Send key to local user
                    key_share_env = create_envelope(
                        "PUBLIC_CHANNEL_KEY_SHARE",
                        server.local_server.id,
                        member_id,
                        {
                            "wrapped_public_channel_key": wrapped_key,
                            "creator_pub": payload.get("creator_pub", ""),
                            "content_sig": payload.get("content_sig", ""),
                        },
                    )
                    await local_link.send_message(key_share_env)
                    logger.debug("Sent channel key share to local user %s", member_id)
        
        # Forward to other servers for their local users
        for link in server.all_server_connection_links:
            if link is connection or (link.server_id and link.server_id == origin_server_id):
                continue
            await link.send_message(envelope)
    
    @staticmethod
    async def handle_msg_public_channel_server(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle MSG_PUBLIC_CHANNEL from another server - deliver to local members."""
        sender_id = envelope.from_
        payload = envelope.payload
        
        # Validate payload
        required_fields = {"ciphertext", "iv", "tag", "sender_pub", "content_sig"}
        if not required_fields.issubset(payload.keys()):
            logger.warning("Malformed MSG_PUBLIC_CHANNEL from %s", sender_id)
            return
        
        # Get local channel members
        if not hasattr(server, 'public_channel'):
            return
        
        members = server.public_channel.get_members()
        
        # Deliver to local members only
        for member_id in members:
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    deliver_env = create_envelope(
                        MessageType.MSG_PUBLIC_CHANNEL.value,
                        sender_id,
                        "public",
                        payload,
                    )
                    await local_link.send_message(deliver_env)
                    logger.debug("Delivered public channel message from %s to local user %s", sender_id, member_id)


class UserMessageHandlers:
    """
    Registry of user message handlers.
    
    Handles messages originating from connected users.
    """
    
    @staticmethod
    async def handle_msg_direct(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle MSG_DIRECT - route direct message to recipient."""
        # Validate sender
        sender_id = await GenericValidators.validate_sender(server, connection, envelope)
        if not sender_id:
            return
        
        # Validate recipient
        recipient_id = envelope.to
        if not await GenericValidators.validate_uuid_field(
            server, connection, recipient_id, "recipient (to field)", sender_id
        ):
            return
        
        # Validate payload
        payload = envelope.payload
        required_fields = {
            "ciphertext",
            "sender", "sender_pub", "content_sig",
        }
        if not await GenericValidators.validate_required_fields(
            server, connection, payload, required_fields, sender_id
        ):
            return
        
        # Prepare payload for routing
        # Per SOCP §9.2, MSG_DIRECT does NOT have "sender" in payload
        # Server must add it for SERVER_DELIVER and USER_DELIVER
        base_payload = {
            "ciphertext": payload["ciphertext"],
            "sender": payload["sender"],
            "sender_pub": payload["sender_pub"],
            "content_sig": payload["content_sig"],
            }
        
        # Prepare envelopes for local and remote delivery
        local_env = create_envelope(
            MessageType.USER_DELIVER.value,
            server.local_server.id,
            recipient_id,
            base_payload,
        )
        
        # For remote delivery, get recipient's server location
        rec = server.users.get(recipient_id)
        remote_server_id = getattr(rec, "location", None)
        if not isinstance(remote_server_id, str):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"Recipient {recipient_id} has no location found",
                to_id=sender_id,
            )
            return
        remote_env = create_envelope(
            MessageType.SERVER_DELIVER.value,
            server.local_server.id,
            remote_server_id,
            {"user_id": recipient_id, **base_payload},
        )
        
        # Use builder pattern for DM routing
        await DMRecipientRouter(server, connection, sender_id, recipient_id) \
            .envelope_to_local(local_env) \
            .envelope_to_another_server(remote_env) \
            .run()
    
    @staticmethod
    async def handle_msg_public_channel(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle MSG_PUBLIC_CHANNEL - broadcast message to all channel members."""
        # Validate sender
        sender_id = await GenericValidators.validate_sender(server, connection, envelope)
        if not sender_id:
            return
        
        # Validate payload
        payload = envelope.payload
        required_fields = {"ciphertext", "iv", "tag", "sender_pub", "content_sig"}
        if not await GenericValidators.validate_required_fields(
            server, connection, payload, required_fields, sender_id
        ):
            return
        
        # Prepare broadcast payload
        broadcast_payload = {
            "ciphertext": payload["ciphertext"],
            "iv": payload["iv"],
            "tag": payload["tag"],
            "sender_pub": payload["sender_pub"],
            "content_sig": payload["content_sig"],
        }
        
        # Use generic router for public channel broadcast
        await GenericRouters.route_to_public_channel(
            server,
            connection,
            sender_id,
            broadcast_payload,
            MessageType.MSG_PUBLIC_CHANNEL.value,
            exclude_sender=True,  # Don't echo back to sender
        )


class UserFileTransferHandlers:
    """
    User-initiated file transfer handlers per SOCP §9.4.
    Supports both DM and public channel modes.
    """
    
    @staticmethod
    async def handle_file_start(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """
        Handle FILE_START - initiate file transfer with manifest.
        
        Payload: {file_id (UUID), name, size, sha256, mode ("dm"|"public")}
        """
        # Validate sender
        sender_id = await GenericValidators.validate_sender(server, connection, envelope)
        if not sender_id:
            return
        
        payload = envelope.payload
        
        # Validate required fields
        required_fields = {"file_id", "name", "size", "sha256", "mode"}
        if not await GenericValidators.validate_required_fields(
            server, connection, payload, required_fields, sender_id
        ):
            return
        
        # Validate file_id is UUID
        file_id = payload["file_id"]
        if not await GenericValidators.validate_uuid_field(
            server, connection, file_id, "file_id", sender_id
        ):
            return
        
        # Validate mode
        mode = payload["mode"]
        if not await GenericValidators.validate_enum_field(
            server, connection, mode, "mode", {"dm", "public"}, sender_id
        ):
            return
        
        # Validate name and size
        if not isinstance(payload["name"], str) or not isinstance(payload["size"], int):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                "name must be string and size must be integer",
                to_id=sender_id,
            )
            return
        
        # Route based on mode
        if mode == "dm":
            recipient_id = envelope.to
            if not await GenericValidators.validate_uuid_field(
                server, connection, recipient_id, "recipient (to field)", sender_id
            ):
                return
            
            # FILE_START is forwarded as-is (same envelope for both local and remote)
            await DMRecipientRouter(server, connection, sender_id, recipient_id) \
                .envelope_to_local(envelope) \
                .envelope_to_another_server(envelope) \
                .run()
            logger.info("FILE_START from %s to %s: %s (%d bytes)", sender_id, recipient_id, payload["name"], payload["size"])
        
        elif mode == "public":
            await GenericRouters.route_to_public_channel(
                server,
                connection,
                sender_id,
                payload,
                MessageType.FILE_START.value,
                exclude_sender=True,  # Don't echo back
            )
            logger.info("FILE_START from %s to public: %s (%d bytes)", sender_id, payload["name"], payload["size"])
    
    @staticmethod
    async def handle_file_chunk(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """
        Handle FILE_CHUNK - transfer encrypted file chunk.
        
        Payload: {file_id, index, ciphertext, iv, tag, [wrapped_key]}
        Note: wrapped_key is REQUIRED for DM, omitted for public channel.
        """
        # Validate sender
        sender_id = await GenericValidators.validate_sender(server, connection, envelope)
        if not sender_id:
            return
        
        payload = envelope.payload
        
        # Validate base required fields
        required_fields = {"file_id", "index", "ciphertext", "iv", "tag"}
        if not await GenericValidators.validate_required_fields(
            server, connection, payload, required_fields, sender_id
        ):
            return
        
        # Validate file_id
        file_id = payload["file_id"]
        if not await GenericValidators.validate_uuid_field(
            server, connection, file_id, "file_id", sender_id
        ):
            return
        
        # Validate index is integer
        if not isinstance(payload["index"], int):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                "index must be integer",
                to_id=sender_id,
            )
            return
        
        # Determine mode from recipient
        recipient_id = envelope.to
        
        if is_uuid_v4(recipient_id):
            # DM mode - wrapped_key REQUIRED per SOCP §9.4
            if "wrapped_key" not in payload:
                await server.send_error(
                    connection,
                    "BAD_PAYLOAD",
                    "wrapped_key required for DM file transfer",
                    to_id=sender_id,
                )
                return
            
            # FILE_CHUNK is forwarded as-is (same envelope for both local and remote)
            await DMRecipientRouter(server, connection, sender_id, recipient_id) \
                .envelope_to_local(envelope) \
                .envelope_to_another_server(envelope) \
                .run()
            logger.debug("FILE_CHUNK from %s to %s: chunk %d", sender_id, recipient_id, payload["index"])
        
        elif recipient_id == "public":
            # Public channel mode - no wrapped_key (uses public channel key)
            await GenericRouters.route_to_public_channel(
                server,
                connection,
                sender_id,
                payload,
                MessageType.FILE_CHUNK.value,
                exclude_sender=True,
            )
            logger.debug("FILE_CHUNK from %s to public: chunk %d", sender_id, payload["index"])
        
        else:
            await server.send_error(
                connection,
                "BAD_RECIPIENT",
                f"Invalid recipient: {recipient_id}",
                to_id=sender_id,
            )
    
    @staticmethod
    async def handle_file_end(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """
        Handle FILE_END - signal completion of file transfer.
        
        Payload: {file_id}
        """
        # Validate sender
        sender_id = await GenericValidators.validate_sender(server, connection, envelope)
        if not sender_id:
            return
        
        payload = envelope.payload
        
        # Validate file_id
        file_id = payload.get("file_id")
        if not await GenericValidators.validate_uuid_field(
            server, connection, file_id, "file_id", sender_id
        ):
            return
        
        # Determine mode from recipient
        recipient_id = envelope.to
        
        if is_uuid_v4(recipient_id):
            # DM mode - FILE_END is forwarded as-is (same envelope for both local and remote)
            await DMRecipientRouter(server, connection, sender_id, recipient_id) \
                .envelope_to_local(envelope) \
                .envelope_to_another_server(envelope) \
                .run()
            logger.info("FILE_END from %s to %s: file_id=%s", sender_id, recipient_id, file_id)
        
        elif recipient_id == "public":
            # Public channel mode
            await GenericRouters.route_to_public_channel(
                server,
                connection,
                sender_id,
                payload,
                MessageType.FILE_END.value,
                exclude_sender=True,
            )
            logger.info("FILE_END from %s to public: file_id=%s", sender_id, file_id)
        
        else:
            await server.send_error(
                connection,
                "BAD_RECIPIENT",
                f"Invalid recipient: {recipient_id}",
                to_id=sender_id,
            )


class ServerFileTransferHandlers:
    """
    Server-relayed file transfer handlers per SOCP §9.4.
    Pure relay - no authorization or validation, just routing.
    """
    @staticmethod
    async def handle_file_generic_relay(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope, message_type: MessageType) -> None:
        """Handle generic relay - initiate file transfer with manifest."""
        recipient_id = envelope.to
        
        if is_uuid_v4(recipient_id):
            # DM mode
            await GenericRouters.relay_server_to_dm_recipient(
                server,
                envelope,
                message_type,
            )
        elif recipient_id == "public" or recipient_id == "*":
            # Public channel mode
            await GenericRouters.relay_server_to_public_channel(
                server,
                envelope,
                message_type,
            )
        else:
            logger.warning("Invalid recipient %s in %s from %s", recipient_id, message_type, envelope.from_)
            # DM mode
    @staticmethod
    async def handle_file_start_relay(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Relay FILE_START from another server to local users or next hop."""
        await ServerFileTransferHandlers.handle_file_generic_relay(server, connection, envelope, MessageType.FILE_START)
    
    @staticmethod
    async def handle_file_chunk_relay(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Relay FILE_CHUNK from another server to local users or next hop."""
        await ServerFileTransferHandlers.handle_file_generic_relay(server, connection, envelope, MessageType.FILE_CHUNK)
    @staticmethod
    async def handle_file_end_relay(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Relay FILE_END from another server to local users or next hop."""
        await ServerFileTransferHandlers.handle_file_generic_relay(server, connection, envelope, MessageType.FILE_END)


# Handler registry mapping message types to their handlers
SERVER_HANDLER_REGISTRY: Dict[MessageType, MessageHandler] = {
    MessageType.HEARTBEAT: ServerMessageHandlers.handle_heartbeat,
    MessageType.SERVER_ANNOUNCE: ServerMessageHandlers.handle_server_announce,
    MessageType.USER_ADVERTISE: ServerMessageHandlers.handle_presence_gossip,
    MessageType.USER_REMOVE: ServerMessageHandlers.handle_presence_gossip,
    MessageType.SERVER_DELIVER: ServerMessageHandlers.handle_server_deliver,
    MessageType.PUBLIC_CHANNEL_ADD: ServerMessageHandlers.handle_public_channel_add,
    MessageType.PUBLIC_CHANNEL_UPDATED: ServerMessageHandlers.handle_public_channel_updated,
    MessageType.PUBLIC_CHANNEL_KEY_SHARE: ServerMessageHandlers.handle_public_channel_key_share,
    MessageType.MSG_PUBLIC_CHANNEL: ServerMessageHandlers.handle_msg_public_channel_server,
    # File transfer (server-relayed)
    MessageType.FILE_START: ServerFileTransferHandlers.handle_file_start_relay,
    MessageType.FILE_CHUNK: ServerFileTransferHandlers.handle_file_chunk_relay,
    MessageType.FILE_END: ServerFileTransferHandlers.handle_file_end_relay,
}

USER_HANDLER_REGISTRY: Dict[MessageType, MessageHandler] = {
    MessageType.MSG_DIRECT: UserMessageHandlers.handle_msg_direct,
    MessageType.MSG_PUBLIC_CHANNEL: UserMessageHandlers.handle_msg_public_channel,
    # File transfer (user-initiated)
    MessageType.FILE_START: UserFileTransferHandlers.handle_file_start,
    MessageType.FILE_CHUNK: UserFileTransferHandlers.handle_file_chunk,
    MessageType.FILE_END: UserFileTransferHandlers.handle_file_end,
}

