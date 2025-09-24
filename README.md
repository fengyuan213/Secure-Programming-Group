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
python -m client.client
```
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

