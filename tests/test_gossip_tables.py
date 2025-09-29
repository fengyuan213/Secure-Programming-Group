import asyncio, json, time, uuid, websockets, pytest


def ms():
    return int(time.time() * 1000)


@pytest.mark.asyncio
async def test_user_advertise_updates_and_gossips():
    from server.server import SOCPServer

    host, port = "127.0.0.1", 9010
    server = SOCPServer(host=host, port=port)
    task = asyncio.create_task(server.start_server())
    await asyncio.sleep(0.2)

    server_a = str(uuid.uuid4())
    server_b = str(uuid.uuid4())
    user_u = str(uuid.uuid4())

    async with websockets.connect(f"ws://{host}:{port}") as ws_a, \
            websockets.connect(f"ws://{host}:{port}") as ws_b:

        # Identify both connections as servers
        def join(sid: str) -> str:
            return json.dumps(
                {
                    "type": "SERVER_HELLO_JOIN",
                    "from": sid,
                    "to": f"{host}:{port}",
                    "ts": ms(),
                    "payload": {"host": host, "port": 7777, "pubkey": "AA"},
                    "sig": "AA",
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        await ws_a.send(join(server_a))
        _ = await ws_a.recv()
        await ws_b.send(join(server_b))
        _ = await ws_b.recv()

        # A sends USER_ADVERTISE for user_u, should update table and gossip to B
        adv = json.dumps(
            {
                "type": "USER_ADVERTISE",
                "from": server_a,
                "to": "*",
                "ts": ms(),
                "payload": {"user_id": user_u, "server_id": server_a, "meta": {}},
                "sig": "AA",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        await ws_a.send(adv)

        # B should receive the gossip
        msg = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=1.0))
        assert msg["type"] == "USER_ADVERTISE"
        assert msg["payload"]["user_id"] == user_u
        assert server.user_locations.get(user_u) == server_a

    task.cancel()
    with pytest.raises(Exception):
        await task
