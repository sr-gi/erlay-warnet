#!/usr/bin/env python3
"""
Run against the networks produced by utils/create_eclipse_network.py:
`networks/eclipse-{control,relay,recon}`. The victim's whole baseline outbound
is blackholes (captured), the arm decides its extra (lifeline) connections:

  control : no extras              -> victim is fully eclipsed
  relay   : N honest full-relay    -> victim survives via fanout
  recon   : N honest full-recon    -> victim survives via reconciliation 
"""

import time
from collections import Counter

from commander import Commander


class CheckEclipse(Commander):
    def set_test_params(self):
        super().set_test_params()
        # Overridden by Commander.setup() to the real tank count; required here.
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Single-node eclipse: does the victim survive via its extra connections?"
        parser.usage = "warnet run /path/to/check_eclipse.py"
        parser.add_argument("--tx_count", dest="tx_count", default=20, type=int,
                            help="Transactions to broadcast into the honest mesh (default: 20)")
        parser.add_argument("--timeout", dest="timeout", default=180, type=int,
                            help="Seconds to wait for honest-network propagation and for the victim (default: 180)")
        parser.add_argument("--sync_timeout", dest="sync_timeout", default=60, type=int,
                            help="Seconds to wait for the victim to block-sync before treating it as eclipsed (default: 60)")

    # Poll a predicate up to `timeout`, returning True/False instead of raising.
    def poll(self, predicate, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(1)
        return predicate()

    # Aggregate a node's received bytes-per-message-type across all its peers.
    def recv_bytes(self, node):
        c = Counter()
        for peer in node.getpeerinfo():
            for msgtype, nbytes in peer.get("bytesrecv_per_msg", {}).items():
                c[msgtype] += nbytes
        return c

    def run_test(self):
        victim = self.tanks["victim"]
        # The honest network is every tank-XXXX node created by create_network.py.
        honest = [self.tanks[k] for k in sorted(self.tanks) if k.startswith("tank")]
        miner_node = honest[0]

        self.wait_for_tanks_connected()
        # The victim's baseline (addnode -> blackholes) is built of manual connections, its honest
        # lifeline is the addconnection extras (outbound-full-{relay, recon}).
        outbound = [p for p in victim.getpeerinfo() if not p["inbound"]]
        lifeline = [p for p in outbound if p["connection_type"] != "manual"]
        baseline_peers = len(victim.getpeerinfo())
        self.log.info(f"Eclipse setup: {len(honest)}-node honest mesh; victim has {len(outbound)} outbound "
                      f"({len(lifeline)} honest lifeline / {len(outbound) - len(lifeline)} blackhole baseline)")

        miner = Commander.ensure_miner(miner_node)
        addr = miner.getnewaddress()
        miner_p2p = f"{miner_node.rpchost}:18444"

        self.log.info("Temporarily linking the victim to the miner to feed it the chain")
        victim.addnode(miner_p2p, "onetry")
        self.log.info("Mining 120 blocks and syncing the whole network")
        self.generatetoaddress(miner_node, 120, addr)
        height = miner_node.getblockcount()

        self.log.info("Dropping the temporary link to seal the eclipse")
        for p in victim.getpeerinfo():
            if miner_node.rpchost in p["addr"]:
                victim.disconnectnode("", p["id"])
        self.wait_until(lambda: len(victim.getpeerinfo()) == baseline_peers, timeout=self.options.timeout)
        self.log.info(f"Victim pre-synced to height {height}, eclipse sealed ({baseline_peers} peers)")

        before = self.recv_bytes(victim)

        # Make sure the victim is actually eclipsed for blocks by generating an extra block
        # dropping the victim's lifeline.
        self.log.info("Mining 1 post-eclipse block")
        self.generatetoaddress(miner_node, 1, addr, sync_fun=self.no_op)
        tip = miner_node.getblockcount()
        for node in honest:
            self.wait_until(lambda n=node: n.getblockcount() >= tip, timeout=self.options.timeout)
        victim_synced = self.poll(lambda: victim.getblockcount() >= tip, self.options.sync_timeout)
        self.log.info(f"Victim block sync: {'SYNCED' if victim_synced else 'STUCK (eclipsed)'} "
                      f"at height {victim.getblockcount()}/{tip}")

        # Transaction eclipse test: broadcast transactions into the honest mesh.
        self.log.info(f"Broadcasting {self.options.tx_count} transactions into the honest mesh")
        txids = [miner.sendtoaddress(miner.getnewaddress(), 0.001) for _ in range(self.options.tx_count)]

        # They must reach the whole honest mesh first (sanity that broadcast worked).
        for node in honest:
            self.wait_until(lambda n=node: all(t in n.getrawmempool() for t in txids),
                            timeout=self.options.timeout)
        self.log.info("Honest mesh received all transactions")

        # Now measure the victim: how many it gets, and how long it takes.
        got_all = self.poll(lambda: all(t in victim.getrawmempool() for t in txids), self.options.timeout)
        vmempool = set(victim.getrawmempool())
        received = sum(1 for t in txids if t in vmempool)
        diff = self.recv_bytes(victim) - before  # bytes the victim received during the broadcast phase

        # Check propagation as the time between the first received inv (by any node) to the last received
        # transaction time. We exclude the source when computing, as it has no first_inv_time nor recv_time
        marker_tx = txids[-1]
        inv_times, recv_times = [], []
        for n in honest[1:] + [victim]:
            try:
                e = n.getmempoolentry(marker_tx)
            except Exception:
                continue
            iv, rv = e.get("first_inv_time"), e.get("recv_time")
            if iv is not None and rv is not None:
                inv_times.append(iv)
                recv_times.append(rv)
        elapsed = (max(recv_times) - min(inv_times)) / 1_000_000.0 if inv_times else None

        self.log.info("================= ECLIPSE RESULT (victim) =================")
        self.log.info(f"transactions received : {received}/{self.options.tx_count}")
        if got_all and elapsed is not None:
            self.log.info(f"time to receive all   : {elapsed:.1f}s")
        else:
            self.log.info(f"time to receive all   : NOT all within {self.options.timeout}s")
        self.log.info(f"block height          : {victim.getblockcount()}/{tip} "
                      f"({'synced' if victim_synced else 'eclipsed'}); pre-synced to {height}")
        self.log.info(f"bytes received / msg  : {dict(diff)}")
        self.log.info("===========================================================")


def main():
    CheckEclipse().main()


if __name__ == "__main__":
    main()
