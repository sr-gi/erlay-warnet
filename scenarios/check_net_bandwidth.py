#!/usr/bin/env python3

from collections import Counter
from decimal import Decimal, ROUND_DOWN
import logging
import threading
import time

from commander import Commander

SATOSHI_PRECISION = Decimal('0.00000001')
MIN_UTXO_VALUE = Decimal(0.0002)

def generate_transaction(wallet_rpc, utxo):
    # Make sure we don't create floating point values with sub-satoshi precision
    output_amount = Decimal(utxo["amount"]/Decimal(4)).quantize(SATOSHI_PRECISION, rounding=ROUND_DOWN)
    fee = Decimal(0.00001)
    output_minus_fee = (output_amount - fee).quantize(SATOSHI_PRECISION, rounding=ROUND_DOWN)

    # Create a 1in-4out transaction, so each run of the test gives us more UTXOs to play with
    # without having to constantly mine many blocks
    inputs = [{"txid": utxo["txid"], "vout": utxo["vout"]}]
    outputs = {wallet_rpc.getnewaddress(): output_amount for _ in range(3)}

    # Add one last output subtracting the fee. If the amount per output is too
    # small, discard this output and send all the remainder to fees
    if output_amount > 2*fee:
        outputs.update({wallet_rpc.getnewaddress(): output_minus_fee})

    rawtx = wallet_rpc.createrawtransaction(inputs, outputs)
    return wallet_rpc.signrawtransactionwithwallet(rawtx)["hex"]


class CheckNetBandwidth(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def wait_for_tanks_connected(self):
        # Increasing the log level momentarily so wait_for_tanks doesn't blow our stdout
        self.log.setLevel(logging.WARN)
        super().wait_for_tanks_connected()
        self.log.setLevel(logging.INFO)

    def add_options(self, parser):
        parser.description = (
            "Creates an network simulation by creating tx_count transaction and sending them from "
            "different nodes in the network, waiting for all nodes to receive them, and then "
            "report on network utilization"
        )
        parser.usage = "warnet run /path/to/check_net_bandwidth.py"
        parser.add_argument(
            "--tx_count",
            dest="tx_count",
            default=80,
            type=int,
            help="Number of transaction to be generated amongst all nodes",
        )
        parser.add_argument(
            "--n",
            dest="n",
            default=1,
            type=int,
            help="Number of times the simulation is repeated",
        )

    """Mines up to n blocks from a target node"""
    def mine_blocks(self, miner, n):
        def check_block_height(n, t):
            while n.getblockcount() < t:
                time.sleep(1)

        wallet_rpc = Commander.ensure_miner(miner)
        utxo_count = len([u for u in wallet_rpc.listunspent(1) if u['spendable']])
        block_count =  miner.getblockcount()

        # Mine at least a single block to clear the mempools
        if utxo_count >= n:
            blocks_to_mine = 1
        else:
            # Mine enough blocks to have n available utxos
            blocks_to_mine = n - utxo_count if block_count > 100 else  100 + (n-utxo_count)

        self.generatetoaddress(miner, blocks_to_mine, wallet_rpc.getnewaddress())
        height = block_count + blocks_to_mine
        self.log.info(f"generated {blocks_to_mine} block(s) from node {miner.index}. New chain height: {height}")

        # Wait until all nodes are at the expected height
        threads = []
        for node in self.nodes:
            # No need to check it on the miner
            if node.index != miner.index:
                thread = threading.Thread(target=lambda h=height, n=node: check_block_height(n, h), daemon=False)
                thread.start()
                threads.append(thread)

        self.log.info(f"waiting for all chains to be on sync")
        all(t.join() is None for t in threads)

        return wallet_rpc

    """Creates n transaction using a provided node and using only spendable utxos over MIN_UTXO_VALUE"""
    def create_txs(self, wallet_rpc, n):
        utxos = [u for u in wallet_rpc.listunspent(1) if u['spendable'] and u["amount"] >= MIN_UTXO_VALUE]
        if len(utxos) >= n:
            utxos = utxos[:n]

        self.log.info(f"creating {n} transactions")
        return [generate_transaction(wallet_rpc, utxo) for utxo in utxos]

    """Waits until all nodes have received all transactions"""
    def monitor_mempool(self, target_count):
        def check_mempool_txs(node):
            while len(node.getrawmempool()) < target_count:
                time.sleep(1)

        threads = []
        for node in self.nodes:
            thread = threading.Thread(target=lambda n=node: check_mempool_txs(n), daemon=False)
            thread.start()
            threads.append(thread)

        self.log.info(f"waiting for all mempools to be on sync")
        all(t.join() is None for t in threads)

    """
    Broadcasts a set of transaction from different nodes in the network.
    Transactions are broadcasts one at a time, picking a different node each time
    in a round robin manner.
    """
    def broadcast_txs(self, txs):
        # Send one transaction from each node until the queue is empty
        target_node_id = 0
        self.log.info(f"broadcasting {len(txs)} transaction from different sources in the network")
        while txs:
            target_node = self.nodes[target_node_id]
            target_node.sendrawtransaction( txs.pop())
            target_node_id = (target_node_id + 1) % len(self.nodes)

    """
    Get the statistics of the whole network by accumulating the result of calling
    getpeerinfo on every node in the network.
    """
    def get_net_stats(self):
        def get_node_stats(node):
            node_stats_count = {"sent": {}, "recv": {}}
            node_stats_bytes = {"sent": {}, "recv": {}}
            info = node.getnetmsgstats(["network", "connection_type"])
            for (k, v) in info["sent"].items():
                node_stats_count["sent"] = Counter(node_stats_count["sent"]) + Counter({k: v["count"]})
                node_stats_bytes["sent"] = Counter(node_stats_bytes["sent"]) + Counter({k: v["bytes"]})
            for (k, v) in info["recv"].items():
                node_stats_count["recv"] = Counter(node_stats_count["recv"]) + Counter({k: v["count"]})
                node_stats_bytes["recv"] = Counter(node_stats_bytes["recv"]) + Counter({k: v["bytes"]})

            return node_stats_count, node_stats_bytes

        net_stats_count = {"sent": {}, "recv": {}}
        net_stats_bytes = {"sent": {}, "recv": {}}
        for node in self.nodes:
            (node_stats_count,  node_stats_bytes) = get_node_stats(node)
            net_stats_count["sent"] = Counter(net_stats_count["sent"]) + Counter(node_stats_count["sent"])
            net_stats_count["recv"] = Counter(net_stats_count["recv"]) + Counter(node_stats_count["recv"])
            net_stats_bytes["sent"] = Counter(net_stats_bytes["sent"]) + Counter(node_stats_bytes["sent"])
            net_stats_bytes["recv"] = Counter(net_stats_bytes["recv"]) + Counter(node_stats_bytes["recv"])

        # It could be the case that we try to collect net stats right when a node sends a message to a peer, but before the peer receives it,
        # so out sent/receive values won't match. In the unlikely case this happens, just get the stats again
        while (net_stats_count["sent"] != net_stats_count["recv"]):
            time.sleep(2)
            (net_stats_count,  net_stats_bytes) = self.get_net_stats()

        return net_stats_count,  net_stats_bytes

    """
    Waits for the first INV message to be sent by any node in the network and records the time.
    This is used to account for the starting time of transactions propagation.
    """
    def timestamp_first_inv(self, init_stats_count):
        while (self.get_net_stats()[0]["sent"] - init_stats_count["sent"])["inv"] < 1:
            time.sleep(0.1)

        self.t0 = time.perf_counter()

    def run_test(self):
        self.orders(self.nodes[0])

    def orders(self, node):
        # Set the initial state
        self.wait_for_tanks_connected()

        # Repeat the experiments n times
        for _ in range(self.options.n):
            wallet_rpc = self.mine_blocks(node, self.options.tx_count)
            # Get a snapshot of the stats before creating any transactions
            # so we can account only for the traffic that derives from this experiment
            (init_stats_count, init_stats_bytes) = self.get_net_stats()

            # Create transactions
            txs = self.create_txs(wallet_rpc, self.options.tx_count)
            # Start a thread to monitor, roughly, when the first INV is received by a node
            threading.Thread(target=lambda: self.timestamp_first_inv(init_stats_count), daemon=False).start()
            # Propagate transactions
            self.broadcast_txs(txs)
            self.monitor_mempool(self.options.tx_count)
            propagation_time = time.perf_counter() - self.t0

            # Report back
            (net_stats_count,  net_stats_bytes) = self.get_net_stats()
            # Get the total number of inv entries. Headers are 21 byte-long, INV contain 1 byte as counter*
            # and 36 (32+4) bytes per entry
            # *counter is actually varsize, but it should not be over 1 byte in our experiments
            diff_count = dict(net_stats_count["sent"] - init_stats_count["sent"])
            diff_bytes = dict(net_stats_bytes["sent"] - init_stats_bytes["sent"])
            inv_entry_count = int((diff_bytes["inv"] - diff_count["inv"] * 22) / 36.0)
            self.log.info(f"reporting netstats:")
            self.log.info(f"message count: {diff_count}")
            self.log.info(f"bytes per message: {diff_bytes}")
            self.log.info(f"INV entry count: {inv_entry_count}")
            self.log.info(f"approx propagation time: {propagation_time}s")


def main():
    CheckNetBandwidth().main()

if __name__ == "__main__":
    main()
