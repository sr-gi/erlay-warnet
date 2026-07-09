1. Clone and cd to this repo

2. Create virtual environment and install warnet:

    `python -m venv .venv && source .venv/bin/activate && pip install warnet`

3. Ensure dependencies

    `warnet setup`

4. Deploy a test network

    `warnet deploy networks/erlay-testnet`

    The relevant image to use in your deployments is `sr-gi99.0.0-erlay-full-recon-d2f8`.

    This is equivalent to run my [erlay-full-recon-getnetmsgstats](https://github.com/sr-gi/bitcoin/tree/erlay-full-recon-getnetmsgstats) branch, which includes vasild's new `getnetmsgstats` RPC plus an (insecure, test-only) patch to check the reception time of INVs and transactions in the mempool, via `getmempoolentry`.

5. Alternatively, you can deploy other networks. You can choose from the pre-defined ones in `networks/`.

    For any network over 50-100 nodes (depending on your local setup), you may want to use a kubernetes cluster.

    You can also create your own random networks using the script at `utils/create_network.py`.

    `warnet deploy networks/<your_network>`

6. Run scenarios

    `warnet run scenarios/check_net_bandwidth.py --debug --tx_count=10 --n=100`

