1. Clone and cd to this repo

2. Create virtual environment and install warnet:

`python -m venv .venv && source .venv/bin/activate && pip install warnet`

3. Ensure dependencies

`warnet setup`

4. Deploy small (8-node) erlay image network

`warnet deploy networks/erlay-small`

The relevant images to use in your deployments are:

- `sr-gi/bitcoin:99.0.0-getnetmsgs` For master + [#29418](https://github.com/bitcoin/bitcoin/pull/29418)
- `sr-gi/bitcoin:99.0.0-erlay-full-169d` For [#30277](https://github.com/bitcoin/bitcoin/pull/30277)+ [#29418](https://github.com/bitcoin/bitcoin/pull/29418) with T=1
- `sr-gi/bitcoin:99.0.0-erlay-full-9c7f` [#30277](https://github.com/bitcoin/bitcoin/pull/30277)+ [#29418](https://github.com/bitcoin/bitcoin/pull/29418) with T=4

5. Alternatively, deploy a medium-size (50-node) erlay image network

`warnet deploy networks/erlay-medium`

6. Open web UI

`warnet dashboard`

7. Run scenarios

`warnet run scenarios/check_net_bandwidth.py --debug --tx_count=10`


