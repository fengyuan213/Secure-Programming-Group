import asyncio, json, time, uuid, websockets

# REPLACE  current_server_id WITH THE SERVER ID OF THE SERVER YOU WANT TO TEST AGAINST

def ms(): return int(time.time()*1000)

async def user_hello(server_ws="ws://127.0.0.1:8765", current_server_id="4071c714-bd14-4ac5-bc75-b2212669881b"):
    env = {
        "type": "USER_HELLO",
        "from": str(uuid.uuid4()),
        "to": current_server_id,
        "ts": ms(),
        "payload": {
            "client": "cli-v1",
            "pubkey": "AA",      # placeholder base64url
            "enc_pubkey": "AA",  # placeholder base64url
        },
        "sig": "AA"               # allowed to omit for USER_HELLO
    }
    async with websockets.connect(server_ws) as ws:
        await ws.send(json.dumps(env, separators=(",", ":"), sort_keys=True))
        await asyncio.sleep(0.5)  # let server handle/log

async def server_hello_join(server_ws="ws://127.0.0.1:8765"):
    other_server_id = str(uuid.uuid4())
    # For bootstrap, 'to' is host:port per spec
    # Adjust host/port to match your server listener if different
    env = {
        "type": "SERVER_HELLO_JOIN",
        "from": other_server_id,
        "to": "127.0.0.1:8765",
        "ts": ms(),
        "payload": {
            "host": "127.0.0.1",
            "port": 7777,
            "pubkey": "AA"
        },
        "sig": "AA"
    }
    async with websockets.connect(server_ws) as ws:
        await ws.send(json.dumps(env, separators=(",", ":"), sort_keys=True))
        reply = await ws.recv()  # expect SERVER_WELCOME
        print("Got reply:", reply)


# change which test functon you want to run
asyncio.run(user_hello())