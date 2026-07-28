#!/usr/bin/env python3
"""Verify the -blackhole functionality on a live warnet deployment.

Intended for the `networks/blackhole-test` topology:

                        +--------------------------+
                        |    tank-0000 (source)    |
                        +------------+-------------+
                                     |
                    +----------------+----------------+
                    |                                 |
        +-----------v-----------+         +-----------v-----------+
        | tank-0001 (blackhole) |         |       tank-0003       |
        +-----------+-----------+         +-----------+-----------+
                    |                                 |
                    |                                 |
                    v                                 v
        +-----------------------+         +-----------------------+
        |  tank-0002 (victim)   |         |  tank-0004 (control)  |
        +-----------------------+         +-----------------------+
"""

import time

from commander import Commander


class CheckBlackhole(Commander):
    def set_test_params(self):
        super().set_test_params()
        # Overridden by Commander.setup() to the real tank count; required to be set here.
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Check that a -blackhole node receives but never forwards blocks or transactions"
        parser.usage = "warnet run /path/to/check_blackhole.py"
        parser.add_argument("--timeout", dest="timeout", default=120, type=int,
                            help="Seconds to wait for propagation (default: 120)")
        parser.add_argument("--settle", dest="settle", default=10, type=int,
                            help="Extra seconds before asserting the victim did NOT receive something "
                            "(default: 10)")

    def run_test(self):
        source = self.tanks["tank-0000"]
        blackhole = self.tanks["tank-0001"]
        victim = self.tanks["tank-0002"]
        relay = self.tanks["tank-0003"]
        control = self.tanks["tank-0004"]

        self.log.info("Waiting for all tanks to be connected")
        self.wait_for_tanks_connected()

        miner = Commander.ensure_miner(source)
        addr = miner.getnewaddress()
        source_p2p = f"{source.rpchost}:18444"

        # Temporarily connect the victim to the source, so it takes part
        # in the initial sync just like everyone else. Drop the connection after.
        self.log.info("Temporarily connecting the isolated victim to the source")
        victim.addnode(source_p2p, "onetry")
        self.log.info("Mining 101 blocks and syncing the whole network")
        self.generatetoaddress(source, 101, addr)
        height = source.getblockcount()

        # Drop the temporary link: the victim is now fully synced but its only
        # remaining peer is the blackhole.
        self.log.info("Dropping the temporary link to isolate the victim")
        for p in victim.getpeerinfo():
            if source.rpchost in p["addr"]:
                victim.disconnectnode("", p["id"])
        self.wait_until(lambda: len(victim.getpeerinfo()) == 1, timeout=self.options.timeout)
        self.log.info(f"Victim isolated")

        # Test block propagation.
        # A block mined after isolation must reach the control path but not the
        # isolated victim (the blackhole forwards no blocks).
        self.generatetoaddress(source, 1, addr, sync_fun=self.no_op)
        new_height = source.getblockcount()
        self.wait_until(lambda: control.getblockcount() >= new_height, timeout=self.options.timeout)
        self.log.info(f"Control node received the block")

        # Test transaction propagation.
        # Spends a mature coinbase the victim already knows (from the synced chain),
        # so it can validate it: an empty mempool means the blackhole did not
        # forward it.
        txid = miner.sendtoaddress(miner.getnewaddress(), 1)
        self.log.info(f"Broadcast tx {txid} from the source")
        for node, label in (
            (blackhole, "Blackhole (tank-0001)"),
            (relay, "Relay node(tank-0003)"),
            (control, "Control node (tank-0004)"),
        ):
            self.wait_until(lambda n=node: txid in n.getrawmempool(), timeout=self.options.timeout)
            self.log.info(f"{label} received the tx")

        # The control path has it, so propagation has had time to run. Give a
        # further grace period, then confirm the isolated victim got neither.
        self.log.info(f"Settling {self.options.settle}s before checking the isolated victim")
        time.sleep(self.options.settle)

        if victim.getblockcount() != height:
            raise AssertionError(
                f"Test failed. The isolated victim advanced to {victim.getblockcount()} (expected {height}). "
                "The blackhole forwarded a block"
            )
        self.log.info(f"Isolated victim stuck at height {height} (blackhole forwarded no block)")

        if txid in victim.getrawmempool():
            raise AssertionError("Test failed. The isolated victim received the tx. The blackhole forwarded it")
        self.log.info("Isolated victim did not receive the tx")

        if txid not in blackhole.getrawmempool():
            raise AssertionError("Test failed. The blackhole does not have the tx; it should receive and keep it")
        self.log.info("The blackhole still holds the tx (received and kept)")


def main():
    CheckBlackhole().main()


if __name__ == "__main__":
    main()
