import asyncio
import uuid

import pytest
import websockets

from shared.envelope import Envelope, create_envelope


def _assign_new_id(server):
    from server.core.ConnectionLink import ConnectionLink

    old_id = server.current_server_id
    new_id = str(uuid.uuid4())
    server.current_server_id = new_id
    record = server.servers.pop(old_id, None)
    if record is not None:
        if isinstance(record, ConnectionLink):
            record.server_id = new_id
            server.servers[new_id] = record
        else:
            record.id = new_id  # type: ignore[attr-defined]
            server.servers[new_id] = record
    server.server_addrs[new_id] = (server.host, server.port)
    server.server_pubkeys[new_id] = server.local_pubkey
    if old_id in server.server_addrs:
        server.server_addrs.pop(old_id, None)
    if old_id in server.server_pubkeys:
        server.server_pubkeys.pop(old_id, None)
    return new_id


@pytest.mark.asyncio
async def test_forward_remote_delivery_to_local_user():
    from server.server import SOCPServer

    host = "127.0.0.1"
    port_a = 9050
    port_b = 9051

    server_a = SOCPServer(host=host, port=port_a)
    server_b = SOCPServer(host=host, port=port_b)

    server_a_id = _assign_new_id(server_a)
    server_b_id = _assign_new_id(server_b)

    task_a = asyncio.create_task(server_a.start_server())
    task_b = asyncio.create_task(server_b.start_server())

    await asyncio.sleep(0.2)

    server_a.server_addrs[server_b_id] = (host, port_b)
    server_a.server_pubkeys[server_b_id] = server_b.local_pubkey
    server_b.server_pubkeys[server_a_id] = server_a.local_pubkey

    await server_a.connect_to_known_servers()

    async def wait_for(predicate, timeout=3.0):
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return False

    assert await wait_for(
        lambda: server_b_id in server_a.servers
        and server_a.servers.get(server_b_id) is not None
    )

    recipient_id = str(uuid.uuid4())
    sender_id = str(uuid.uuid4())

    ws_recipient = await websockets.connect(f"ws://{host}:{port_b}")
    hello_recipient = create_envelope(
        "USER_HELLO",
        recipient_id,
        server_b_id,
        {"client": "cli-v1", "pubkey": "AA", "enc_pubkey": "AA"},
        signature="AA",
    )
    await ws_recipient.send(hello_recipient.to_json())

    assert await wait_for(lambda: server_b.local_users.get(recipient_id) is not None)

    await asyncio.sleep(0.1)

    assert await wait_for(lambda: server_a.user_locations.get(recipient_id) == server_b_id)

    ws_sender = await websockets.connect(f"ws://{host}:{port_a}")
    hello_sender = create_envelope(
        "USER_HELLO",
        sender_id,
        server_a_id,
        {"client": "cli-v1", "pubkey": "AA", "enc_pubkey": "AA"},
        signature="AA",
    )
    await ws_sender.send(hello_sender.to_json())

    assert await wait_for(lambda: server_a.local_users.get(sender_id) is not None)

    msg_payload = {
        "ciphertext": "cipher",
        "iv": "iv",
        "tag": "tag",
        "wrapped_key": "wrapped",
        "sender_pub": "pub",
        "content_sig": "sig",
    }
    msg = create_envelope("MSG_DIRECT", sender_id, recipient_id, msg_payload, signature="AA")
    await ws_sender.send(msg.to_json())

    raw = await asyncio.wait_for(ws_recipient.recv(), timeout=3.0)
    deliver_env = Envelope.from_json(raw)

    assert deliver_env.type == "USER_DELIVER"
    assert deliver_env.from_ == server_b_id
    assert deliver_env.to == recipient_id
    assert deliver_env.payload["ciphertext"] == "cipher"
    assert deliver_env.payload["sender"] == sender_id

    await ws_sender.close()
    await ws_recipient.close()

    task_a.cancel()
    task_b.cancel()
    for task in (task_a, task_b):
        with pytest.raises(Exception):
            await task
