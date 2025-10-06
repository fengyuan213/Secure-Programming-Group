# SOCP v1.3 Implementation

This is a Python implementation of the Secure Overlay Chat Protocol (SOCP) version 1.3.

Group 56
- Fengyuan Liu a1835007 (.fenguan on discord)
- Jamie Siggurs a1886269
- Vu Nguyen a1848491
- Dineth Wickramasekara a1894205

Feel free to contact any of the members to discuss any part of the application.

## Project Structure

```
├── client/                     # Command-line SOCP client
│   ├── socp_cli.py             # Click entry point and command loop
│   ├── ws_client.py            # WebSocket session helpers
│   ├── state.py                # Local keystore and presence cache
│   ├── pubdir.py               # Public key directory management
│   ├── client.py               # Legacy demo client wrapper
│   └── test_golden.py          # Golden Bob→Alice messaging test
├── server/                     # Federation-ready SOCP server
│   ├── server.py               # Async WebSocket server entry point
│   ├── bootstrap.yaml          # Default peer bootstrap list
│   ├── storage.py              # Persistent server identity helpers
│   ├── identity/               # UUID utilities and validation
│   └── core/                   # Message handlers and in-memory tables
├── shared/                     # Client/server shared logic
│   ├── envelope.py             # Frame envelope codec & validation
│   ├── utils.py                # Common validation helpers
│   ├── log.py                  # Structured logging helpers
│   └── crypto/                 # RSA/OAEP/PSS crypto abstractions
│ 
├── run_2nd_server.py           # Helper for local multi-server testing
├── requirements.txt            # Python dependencies
└── setup.py                    # Editable-install configuration
```

### Option 1: Install as Editable Package (Recommended)

```bash
# Create and activate virtual environment
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On Linux/Mac:
source .venv/bin/activate

# Install the package in editable mode
pip install -r requirements.txt
pip install -e .
```


### RUNNING THE CODE 

```bash
# Start the server (from project root directory)
python -m server.server

# Run the test client (from project root directory, in another terminal)

python -m client.socp_cli run [OPTIONS]

Options:
  --server TEXT       WebSocket URL of local server [default: ws://localhost:8765]
  --user-id TEXT      UUIDv4 user id; generated if omitted
  --server-id TEXT    UUIDv4 of local server if known
  --help             Show this message and exit

# run the client and connect to default server
python -m client.socp_cli run 

# 1. Quick test with auto-generated user ID
python -m client.socp_cli run --server ws://localhost:8767

# 2. Persistent identity (same user ID across sessions)
python -m client.socp_cli run --server ws://localhost:8767 --user-id alice-uuid-here


# 4. Local development with multiple clients
# Terminal 1 (Alice on Server 1):
python -m client.socp_cli run --server ws://localhost:8765 --user-id alice-id

# Terminal 2 (Bob on Server 2):
python -m client.socp_cli run --server ws://localhost:8767 --user-id bob-id
```

### Client Commands

The interactive shell accepts the following commands:

- `/list` – Display currently known users from the presence cache.
- `/pubkey set <user-id> <b64url>` – Record or update a recipient public key.
- `/pubkey list` – Show stored public keys.
- `/tell <user-id> <message>` – Send an end-to-end encrypted direct message.
- `/all <message>` – Broadcast to the shared channel.
- `/file <user-id> <path>` – Initiate an encrypted, chunked file transfer.
- `/help` – Print command help.
- `/quit` – Exit the client.

On server startup, you may see 'bootstrap failed' messages. This is normal, it is attempting to connect to the servers listed in the bootstrap.yaml file, and since those servers are not running, bootstrap will fail.

For multiple servers, you need to edit the bootstrap.yml in the `/server` folder and then configure and `run_2nd_server.py` inside that directory. For example, 
- `python -m server.server` for first server 
- and `python run_2nd_server.py` for another server. 
- first server data directory is `.server` second data directory is `.server2`


#### Production using Installed Package (if you used pip install -e .)
You can run in production executable after running `pip install -e`
```bash
# Start the server data directory is `.server` in current running directory 
socp-server 

# Run the test client (in another terminal) data stored in `.socp` in current running directory
socp-client
```



