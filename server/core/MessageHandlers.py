from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Awaitable, Dict
import uuid

from server.core.MessageTypes import MessageType
from shared.envelope import Envelope
from shared.utils import is_uuid_v4
from shared.log import get_logger

if TYPE_CHECKING:
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink

logger = get_logger(__name__)

# Type alias for handler functions
MessageHandler = Callable[["SOCPServer", "ConnectionLink", Envelope], Awaitable[None]]


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
        from shared.envelope import create_envelope
        
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
        
        if not isinstance(user_id, str) or not is_uuid_v4(user_id):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                "SERVER_DELIVER missing valid user_id",
                to_id=origin_server_id,
            )
            return
        
        # Validate required fields
        required_fields = {
            "ciphertext", "iv", "tag", "wrapped_key",
            "sender", "sender_pub", "content_sig",
        }
        if not required_fields.issubset(payload.keys()):
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                "SERVER_DELIVER missing ciphertext fields",
                to_id=origin_server_id,
            )
            return
        
        rec = server.users.get(user_id)
        target_location = getattr(rec, "location", None)
        
        # Case 1: Local user delivery
        if target_location == "local" or user_id in server.users:
            local_link = getattr(rec, "link", None)
            if not local_link:
                logger.warning("SERVER_DELIVER for %s but user not connected", user_id)
                return
            
            deliver_payload = {
                "ciphertext": payload["ciphertext"],
                "iv": payload["iv"],
                "tag": payload["tag"],
                "wrapped_key": payload["wrapped_key"],
                "sender": payload["sender"],
                "sender_pub": payload["sender_pub"],
                "content_sig": payload["content_sig"],
            }
            deliver_env = create_envelope(
                MessageType.USER_DELIVER.value,
                server.local_server.id,
                user_id,
                deliver_payload,
            )
            await local_link.send_message(deliver_env)
            logger.info("Delivered SERVER_DELIVER payload to local user %s", user_id)
            return
        
        # Case 2: Forward to remote server
        if isinstance(target_location, str) and is_uuid_v4(target_location):
            if target_location == origin_server_id:
                logger.debug("SERVER_DELIVER already at destination %s; dropping", target_location)
                return
            
            link = server._resolve_server_link(target_location)
            if not link:
                logger.warning(
                    "No link to forward SERVER_DELIVER for %s via %s",
                    user_id,
                    target_location,
                )
                return
            
            await link.send_message(envelope)
            logger.info(
                "Forwarded SERVER_DELIVER for %s toward server %s",
                user_id,
                target_location,
            )
            return
        
        # Case 3: Unknown user
        logger.warning("Dropping SERVER_DELIVER for unknown user %s", user_id)


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
                    from shared.envelope import create_envelope
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
        from shared.envelope import create_envelope
        
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
        from shared.envelope import create_envelope
        
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
        from shared.envelope import create_envelope
        
        sender_id = connection.user_id
        if sender_id is None or sender_id != envelope.from_:
            await server.send_error(
                connection,
                "BAD_SENDER",
                "Envelope sender mismatch",
                to_id=envelope.from_,
            )
            return
        
        recipient_id = envelope.to
        if not is_uuid_v4(recipient_id):
            await server.send_error(
                connection,
                "BAD_RECIPIENT",
                "Recipient must be UUIDv4",
                to_id=sender_id,
            )
            return
        
        payload = envelope.payload
        required_fields = {
            "ciphertext", "iv", "tag", "wrapped_key",
            "sender_pub", "content_sig",
        }
        if not required_fields.issubset(payload.keys()):
            missing = required_fields - set(payload.keys())
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"Missing fields: {sorted(missing)}",
                to_id=sender_id,
            )
            return
        
        base_payload = {
            "ciphertext": payload["ciphertext"],
            "iv": payload["iv"],
            "tag": payload["tag"],
            "wrapped_key": payload["wrapped_key"],
            "sender": payload.get("sender", sender_id),
            "sender_pub": payload["sender_pub"],
            "content_sig": payload["content_sig"],
        }
        
        # Check routing table
        rec = server.users.get(recipient_id)
        location = getattr(rec, "location", None)
        
        # Case 1: Local delivery
        if location == "local" or recipient_id in server.users:
            local_link = getattr(rec, "link", None)
            if not local_link:
                await server.send_error(
                    connection,
                    "NO_ROUTE",
                    f"Recipient {recipient_id} not connected",
                    to_id=sender_id,
                )
                return
            
            deliver_env = create_envelope(
                MessageType.USER_DELIVER.value,
                server.local_server.id,
                recipient_id,
                base_payload,
            )
            await local_link.send_message(deliver_env)
            logger.info("Delivered direct message from %s to local user %s", sender_id, recipient_id)
            return
        
        # Case 2: Forward to remote server
        if isinstance(location, str) and is_uuid_v4(location):
            server_link = server._resolve_server_link(location)
            if not server_link:
                await server.send_error(
                    connection,
                    "NO_ROUTE",
                    f"No server link for {location}",
                    to_id=sender_id,
                )
                return
            
            server_payload = {"user_id": recipient_id, **base_payload}
            server_env = create_envelope(
                MessageType.SERVER_DELIVER.value,
                server.local_server.id,
                location,
                server_payload,
            )
            await server_link.send_message(server_env)
            logger.info(
                "Forwarded message from %s to remote user %s via server %s",
                sender_id,
                recipient_id,
                location,
            )
            return
        
        # Case 3: No route
        await server.send_error(
            connection,
            "NO_ROUTE",
            f"No route to {recipient_id}",
            to_id=sender_id,
        )
    
    @staticmethod
    async def handle_msg_public_channel(server: "SOCPServer", connection: "ConnectionLink", envelope: Envelope) -> None:
        """Handle MSG_PUBLIC_CHANNEL - broadcast message to all channel members."""
        from shared.envelope import create_envelope
        
        sender_id = connection.user_id
        if sender_id is None or sender_id != envelope.from_:
            await server.send_error(
                connection,
                "BAD_SENDER",
                "Envelope sender mismatch",
                to_id=envelope.from_,
            )
            return
        
        # Validate payload
        payload = envelope.payload
        required_fields = {"ciphertext", "iv", "tag", "sender_pub", "content_sig"}
        if not required_fields.issubset(payload.keys()):
            missing = required_fields - set(payload.keys())
            await server.send_error(
                connection,
                "BAD_PAYLOAD",
                f"Missing fields: {sorted(missing)}",
                to_id=sender_id,
            )
            return
        
        # Check if user is a member of public channel
        if not hasattr(server, 'public_channel') or sender_id not in server.public_channel.get_members():
            await server.send_error(
                connection,
                "UNAUTHORIZED",
                "Not a member of public channel",
                to_id=sender_id,
            )
            return
        
        # Prepare broadcast payload
        broadcast_payload = {
            "ciphertext": payload["ciphertext"],
            "iv": payload["iv"],
            "tag": payload["tag"],
            "sender_pub": payload["sender_pub"],
            "content_sig": payload["content_sig"],
        }
        
        # Get all channel members
        members = server.public_channel.get_members()
        
        # Deliver to local members
        for member_id in members:
            if member_id == sender_id:
                continue  # Don't echo back to sender
            
            user_rec = server.users.get(member_id)
            if getattr(user_rec, "location", None) == "local":
                local_link = getattr(user_rec, "link", None)
                if local_link:
                    deliver_env = create_envelope(
                        MessageType.MSG_PUBLIC_CHANNEL.value,
                        sender_id,
                        "public",
                        broadcast_payload,
                    )
                    await local_link.send_message(deliver_env)
        
        # Broadcast to all other servers for their local members
        server_env = create_envelope(
            MessageType.MSG_PUBLIC_CHANNEL.value,
            sender_id,
            "public",
            broadcast_payload,
        )
        server
        for link in server.all_server_connection_links:
            await link.send_message(server_env)
        
        logger.info("Broadcasted public channel message from %s to %d members", sender_id, len(members))


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
}

USER_HANDLER_REGISTRY: Dict[MessageType, MessageHandler] = {
    MessageType.MSG_DIRECT: UserMessageHandlers.handle_msg_direct,
    MessageType.MSG_PUBLIC_CHANNEL: UserMessageHandlers.handle_msg_public_channel,
}

