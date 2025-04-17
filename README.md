1. Clone and cd to this repo

2. Create virtual environment and install warnet:

`python -m venv .venv && source .venv/bin/activate && pip install warnet`

3. Ensure dependencies

`warnet setup`

4. Deploy 8-node erlay image network

`warnet deploy networks/erlay-small`

5. Open web UI

`warnet dashboard`

6. Run scenarios (example)

- Mine blocks every 30 seconds:

`warnet run scenarios/miner_std.py --allnodes --interval 30 --mature`

- Send random transactions from all nodes

`warnet run scenarios/tx_flood.py --interval=1 --debug`
