# Erlay simulations in warnet

Bandwidth, latency, and eclipse-resistance experiments for Erlay (BIP-330 tx
reconciliation) on Bitcoin Core, run with
[warnet](https://github.com/bitcoin-dev-project/warnet).

## Setup

1. Clone and `cd` into this repo.
2. `python -m venv .venv && source .venv/bin/activate && pip install warnet`
3. `warnet setup`
4. Deploy a network and run a scenario:

   ```
   warnet deploy networks/erlay-testnet
   warnet run scenarios/check_net_bandwidth.py --debug --tx_count=10 --n=5
   ```

Networks use the image `srgi/bitcoin:99.0.0-erlay-full-recon-blackholes-5ea9` — my
[erlay-full-recon-getnetmsgstats](https://github.com/sr-gi/bitcoin/tree/erlay-full-recon-getnetmsgstats)
branch (Erlay + vasild's `getnetmsgstats` RPC + an insecure, test-only patch exposing
INV/tx reception times via `getmempoolentry`), plus a test-only `-blackhole` flag: a
node that receives and keeps everything but forwards nothing.

Networks over ~50–100 nodes may need a real Kubernetes cluster.

## Experiments

Each experiment is a generator (`utils/`) + scenario (`scenarios/`) + results file:

| experiment | networks | generator | scenario | results |
|---|---|---|---|---|
| Connection redundancy — extra relay vs recon links | `conn-redundancy-*` | `create_network.py` | `check_net_bandwidth.py` | `results-conn-redundancy.txt` |
| Single-node eclipse — can extra honest connections save a captured node | `eclipse-*` | `create_eclipse_network.py` | `check_eclipse.py` | `results-eclipse.txt` |

`scenarios/check_blackhole.py` (+ `networks/blackhole-test`) verifies the `-blackhole` flag.

## Custom networks

```
python utils/create_network.py --size 50 --reachable 30 --outbound 8 -O networks/mynet/network.yaml
```

Add a `node-defaults.yaml` (use the pre-existing as example), then `warnet deploy networks/mynet`.
