1. Clone and cd to this repo

2. Create virtual environment and install warnet:

`python -m venv .venv && source .venv/bin/activate && pip install warnet`

3. Ensure dependencies

`warnet setup`

4. Deploy small (8-node) erlay image network

`warnet deploy networks/erlay-small`

The relevant images to use in your deployments are:

- `sr-gi/bitcoin:99.0.0-getnetmsgs` For master + [#29418](https://github.com/bitcoin/bitcoin/pull/29418)

    Use this for NO Erlay, if you want to make sure no Erlay code is running (Erlay can also be disabled in the next image)
- `sr-gi/bitcoin:99.0.0-erlay-conf-d693` For [#30277](https://github.com/bitcoin/bitcoin/pull/30277) + [#29418](https://github.com/bitcoin/bitcoin/pull/29418)

    You can configure the amount of fanout using `outfanout` and `infanout` in `nodes-default.yaml`
    ```
    # Example config to fanout to 1 outbound and 20% of inbounds
    outfanout = 1
    infanout = 20
    ```

5. Alternatively, deploy a medium-size (50-node) erlay image network (or even a large one, but you may want a kubernetes cluster for that)

`warnet deploy networks/erlay-medium`

6. Open web UI

`warnet dashboard`

7. Run scenarios

`warnet run scenarios/check_net_bandwidth.py --debug --tx_count=10 --n=10 --admin`

