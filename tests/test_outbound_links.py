import asyncio
import pytest
import uuid


@pytest.mark.asyncio
async def test_outbound_links_process_incoming_frames():
    from server.server import SOCPServer
    from server.core.ConnectionLink import ConnectionLink
    from shared.envelope import create_envelope

    host = "127.0.0.1"
    port_a = 9030
    port_b = 9031

    server_a = SOCPServer(host=host, port=port_a)
    server_b = SOCPServer(host=host, port=port_b)

    def assign_new_id(server: SOCPServer) -> str:
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
        if old_id in server.server_addrs:
            server.server_addrs[new_id] = server.server_addrs.pop(old_id)
        else:
            server.server_addrs[new_id] = (server.host, server.port)
        server.server_pubkeys[new_id] = server.local_pubkey
        return new_id

    server_a_id = assign_new_id(server_a)
    server_b_id = assign_new_id(server_b)

    task_a = asyncio.create_task(server_a.start_server())
    task_b = asyncio.create_task(server_b.start_server())
    await asyncio.sleep(0.2)

    # Register server B in A's directory so it dials out
    server_a.server_addrs[server_b_id] = (host, port_b)
    server_a.server_pubkeys[server_b_id] = server_b.local_pubkey

    await server_a.connect_to_known_servers()

    async def wait_for(predicate, timeout=3.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return False

    assert await wait_for(lambda: server_b_id in server_a.servers and isinstance(server_a.servers[server_b_id], ConnectionLink))

    link_record = server_b.servers.get(server_a_id)
    if isinstance(link_record, ConnectionLink):
        link_from_b = link_record
    else:
        link_from_b = getattr(link_record, "link", None)
    assert link_from_b is not None

    user_id = str(uuid.uuid4())
    payload = {"user_id": user_id, "server_id": server_b_id, "meta": {}}
    envelope = create_envelope(
        "USER_ADVERTISE",
        server_b_id,
        "*",
        payload,
        signature=server_b.local_pubkey,
    )

    await link_from_b.send_message(envelope)

    assert await wait_for(lambda: server_a.user_locations.get(user_id) == server_b_id)

    task_a.cancel()
    task_b.cancel()
    for task in (task_a, task_b):
        with pytest.raises(Exception):
            await task
