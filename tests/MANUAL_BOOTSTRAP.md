# Manual Bootstrap Test Guide

This walkthrough shows how to prove that the SOCP bootstrap (Introducer) flow works end-to-end using two local server processes. It recreates Section 8.1 of the SOCP spec: a joining server contacts a trusted introducer, receives its assignment, and announces itself to the network.

> **Why two working copies?**
> The server stores its `server_id` in `server/state.json`. Running two instances from the same folder makes them share the same ID and the introducer will reject the join. Creating a second working copy (or deleting the state file before you start the joining server) guarantees each process has a unique ID.

## 1. Prerequisites

1. **Python:** 3.11+ (matches the version in `requirements.txt`).
2. **Dependencies:** Install once in each working copy (or inside a shared virtual environment):

   ```bash
   cd path/to/chat-app-test
   python -m venv .venv
   source .venv/bin/activate           # PowerShell: .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Two terminals:**
   - **Terminal A** will host the introducer (listens on `localhost:8765`).
   - **Terminal B** will host the joining server (listens on `localhost:8767`).

4. **Second working copy (Terminal B only):**
   - POSIX:

     ```bash
     cd path/to
     cp -R chat-app-test chat-app-test-join
     rm -f chat-app-test-join/server/state.json
     ```

   - Windows PowerShell:

     ```powershell
     Set-Location path\to
     robocopy chat-app-test chat-app-test-join /E
     Remove-Item chat-app-test-join\server\state.json -ErrorAction SilentlyContinue
     ```

   Using a separate copy keeps `server/state.json` from being reused.

## 2. Configure the joining server

Open **Terminal B** in the second working copy (`chat-app-test-join`) and create a bootstrap list that points at your introducer:

```bash
cd path/to/chat-app-test-join
cat <<'YAML' > server/bootstrap.yaml
bootstrap_servers:
  - host: "127.0.0.1"
    port: 8765
    pubkey: "AA"
  - host: "127.0.0.1"
    port: 9001
    pubkey: "AA"
  - host: "127.0.0.1"
    port: 9002
    pubkey: "AA"
YAML
```

PowerShell alternative:

```powershell
@"
bootstrap_servers:
  - host: "127.0.0.1"
    port: 8765
    pubkey: "AA"
  - host: "127.0.0.1"
    port: 9001
    pubkey: "AA"
  - host: "127.0.0.1"
    port: 9002
    pubkey: "AA"
"@ | Set-Content -Encoding UTF8 server/bootstrap.yaml
```

Only the first entry needs to exist for the demo; the extra two satisfy the “at least three introducers” rule in the spec.

## 3. Start the introducer (Terminal A)

From the original working copy:

```bash
cd path/to/chat-app-test
source .venv/bin/activate                         # PowerShell: .\.venv\Scripts\Activate.ps1
python -m server.server
```

Expected log lines:

```
[INFO][__main__]: Initialized SOCP Server with ID: <introducer-uuid>
[INFO][__main__]: Starting SOCP server on localhost:8765
[INFO][__main__]: SOCP server listening on ws://localhost:8765
```

The server will attempt to contact the placeholder public introducers from the default `bootstrap.yaml` and log warnings—this is normal for local testing.

## 4. Start the joining server (Terminal B)

In the second working copy:

```bash
cd path/to/chat-app-test-join
source .venv/bin/activate                         # PowerShell: .\.venv\Scripts\Activate.ps1
python - <<'PY'
import asyncio
from server.server import SOCPServer

async def main():
    server = SOCPServer(host="127.0.0.1", port=8767)
    await server.start_server()

asyncio.run(main())
PY
```

Expected log lines from Terminal B:

```
[INFO][server.server]: Initialized SOCP Server with ID: <joiner-uuid>
[INFO][server.server]: Starting SOCP server on 127.0.0.1:8767
[INFO][server.server]: SOCP server listening on ws://127.0.0.1:8767
[DEBUG][server.core.ConnectionLink]: Sent message SERVER_HELLO_JOIN to server
[INFO][server.server]: Broadcasted SERVER_ANNOUNCE for <joiner-uuid>
[INFO][server.server]: Linked server <introducer-uuid> at localhost:8765
```

## 5. Validate the introducer output

Back in Terminal A you should see the introducer accept the handshake, assign the ID, and connect back to the joiner:

```
[INFO][__main__]: New connection from ('127.0.0.1', <ephemeral-port>)
[INFO][__main__]: First message type: SERVER_HELLO_JOIN from <joiner-uuid>
[INFO][__main__]: Server <joiner-uuid> joined from 127.0.0.1:8767
[DEBUG][server.core.ConnectionLink]: Sent message SERVER_WELCOME to server
[INFO][__main__]: Broadcasted SERVER_ANNOUNCE for <joiner-uuid>
[INFO][__main__]: Connecting to server <joiner-uuid> at 127.0.0.1:8767
[DEBUG][server.core.ConnectionLink]: Sent message SERVER_HELLO_LINK to server
[INFO][__main__]: Connected and linked to server <joiner-uuid> at 127.0.0.1:8767
```

These lines confirm the SOCP bootstrap requirements:

1. The joining server sent `SERVER_HELLO_JOIN` to a trusted introducer.
2. The introducer responded with `SERVER_WELCOME` and registered the joiner.
3. Both sides established a persistent server↔server link and broadcasted `SERVER_ANNOUNCE` to the network.

## 6. Optional: Observe known servers table

You can verify that the joiner learned about the introducer by inspecting its `server_addrs` mapping in a Python REPL while both processes run:

```bash
python - <<'PY'
from server.server import SOCPServer
from pathlib import Path
import json
import os

state = Path('server/state.json')
print('state.json contains:', state.read_text())
PY
```

You should see the UUID issued to that working copy. The runtime logs already prove the linkage, so this step is optional.

## 7. Shutdown and cleanup

1. Press `Ctrl+C` in Terminal B, then Terminal A. Seeing `asyncio.exceptions.CancelledError` during shutdown is expected; the server suppresses it internally.
2. (Optional) Delete the throwaway working copy when finished:

   ```bash
   rm -rf path/to/chat-app-test-join          # PowerShell: Remove-Item chat-app-test-join -Recurse -Force
   ```

3. Restore the original `server/bootstrap.yaml` if you modified it for testing.

Following these steps reproduces the full bootstrap (Introducer) flow locally and validates that the implementation conforms to the SOCP specification.
