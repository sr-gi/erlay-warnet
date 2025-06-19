1. Clone and cd to this repo

2. Create virtual environment and install warnet:

    `python -m venv .venv && source .venv/bin/activate && pip install warnet`

3. Ensure dependencies

    `warnet setup`

4. Deploy small (8-node) erlay image network

    `warnet deploy networks/erlay-small`

    The relevant images to use in your deployments are:

- `sr-gi/bitcoin:99.0.0-erlay-draft-2ab8` For [#30277](https://github.com/bitcoin/bitcoin/pull/30277) + [#29418](https://github.com/bitcoin/bitcoin/pull/29418) + [commit 0cfd425](https://github.com/sr-gi/bitcoin/commit/0cfd425a159e5fbe687bb9466ac3a4602097603c)

    This is basically equivalent to run my [erlay-full-draft-warnet](https://github.com/sr-gi/bitcoin/tree/202406-erlay-full-draft-warnet) branch, which includes vasild's new `getnetmsgstats` RPC plus a patch to check the reception time of INVs and transactions
    in the mempool, via `getmempoolentry`.

    You can configure the amount of fanout using `outfanout` and `infanout` in `nodes-default.yaml`
    ```
    # Example config to fanout to 1 outbound and 20% of inbounds
    outfanout = 1
    infanout = 20
    ```

5. Alternatively, you can deploy bigger networks. You can choose from the pre-defined ones in `networks/`:  medium size (50-node), big (200-node) and huge (500-node).

    For any network over 50-100 nodes, you may want to use a kubernetes cluster.

    You can also create your own random networks using the script at `utils/create_network.py`.

    `warnet deploy networks/erlay-{medium, biug, huge, your-own}`

6. Run scenarios

    `warnet run scenarios/check_net_bandwidth.py --debug --tx_count=10 --n=100`

