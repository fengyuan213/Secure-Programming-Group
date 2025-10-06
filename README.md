# SOCP v1.3 Implementation

This is a Python implementation of the Secure Overlay Chat Protocol (SOCP) version 1.3.

## Project Structure

```
├── server/                 # SOCP Server implementation
│   ├── server.py          # Main WebSocket server (S0.1 & S0.2)
│   ├── identity/ids.py    # UUID generation and validation
│   └── transport/         # Transport layer components
├── client/                 # SOCP Client implementation  
│   └── client.py          # Test client for first-message identification
├── shared/                 # Shared components
│   ├── envelope.py        # Message envelope validation
│   └── utils.py           # Validation utilities
└── requirements.txt       # Python dependencies
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
# 1. Quick test with auto-generated user ID
python -m client.socp_cli run --server ws://localhost:8767

# 2. Persistent identity (same user ID across sessions)
python -m client.socp_cli run --server ws://localhost:8767 --user-id alice-uuid-here

# 3. Connect to production server
python -m client.socp_cli run --server ws://production.example.com:8765

# 4. Local development with multiple clients
# Terminal 1 (Alice on Server 1):
python -m client.socp_cli run --server ws://localhost:8765 --user-id alice-id

# Terminal 2 (Bob on Server 2):
python -m client.socp_cli run --server ws://localhost:8767 --user-id bob-id
```

on startup, you may see 'bootstrap failed' messages. This is normal, it is attempting to connect to the servers listed in the bootstrap.yaml file, and since those servers are not running, bootstrap will fail.

To test the bootstrap functionality, you need to clone the folder and then run run_2nd_server.py inside that directory. full guide is in tests/bootstrap_guide.md

#### Production using Installed Package (if you used pip install -e .)

```bash
# Start the server
socp-server

# Run the test client (in another terminal)
socp-client
```


## Testing First-Message Identification

The test client will demonstrate both user and server connection flows:

This will:
1. Connect as a **User** with `USER_HELLO` message
2. Connect as a **Server** with `SERVER_HELLO_JOIN` message

### Testing bootstrap (Server ↔ Server Protocol)
full guide is in tests/bootstrap_guide.md
```