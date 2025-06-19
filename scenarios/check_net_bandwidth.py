#!/usr/bin/env python3

from collections import Counter
from decimal import Decimal
import logging
import statistics
import threading
import time

from commander import Commander

from test_framework.messages import CTransaction, CTxIn, CTxOut, COutPoint, COIN
from test_framework.address import address_to_scriptpubkey
from test_framework.util import satoshi_round

SATOSHI_PRECISION = Decimal(0.00000001)
MIN_UTXO_VALUE = Decimal(0.0002)
MAX_RETRIES = 10

def generate_transaction(wallet_rpc, utxo):
    # Make sure we don't create floating point values with sub-satoshi precision
    fee = Decimal(0.00001)
    output_amount = satoshi_round(utxo["amount"]/4)
    output_minus_fee = satoshi_round(output_amount - fee)

    # Create a 1in-4out transaction, so each run of the test gives us more UTXOs to play with
    # without having to constantly mine many blocks
    tx = CTransaction()
    tx.vin = [CTxIn(COutPoint(int(utxo["txid"], 16), utxo['vout']))]
    tx.vout = [CTxOut(int(output_amount * COIN), bytearray(address_to_scriptpubkey(wallet_rpc.getnewaddress()))) for _ in range(3)]

    # Add one last output subtracting the fee. If the amount per output is too
    # small, discard this output and send all the remainder to fees
    if output_amount > 2*fee:
        tx.vout.append(CTxOut(int(output_minus_fee * COIN), bytearray(address_to_scriptpubkey(wallet_rpc.getnewaddress()))))

    signed_tx = wallet_rpc.signrawtransactionwithwallet(tx.serialize().hex())

    return signed_tx["hex"]

class CheckNetBandwidth(Commander):
    def set_test_params(self):
        super().set_test_params()
        self.num_nodes = 1
        self.inv_timestamps = []
        self.tx_timestamps = []

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
        utxo_count = len([u for u in wallet_rpc.listunspent(1) if u['spendable'] and u["amount"] >= MIN_UTXO_VALUE])
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

        self.log.info("waiting for all chains to be on sync")
        all(t.join() is None for t in threads)

        return wallet_rpc

    """Creates n transaction using a provided node and using only spendable utxos over MIN_UTXO_VALUE"""
    def create_txs(self, wallet_rpc, n):
        utxos = [u for u in wallet_rpc.listunspent(1) if u['spendable'] and u["amount"] >= MIN_UTXO_VALUE]
        if len(utxos) >= n:
            utxos = utxos[:n]
        else:
            raise ValueError("Not enough UTXOs")

        self.log.info(f"creating {n} transactions")
        return [generate_transaction(wallet_rpc, utxo) for utxo in utxos]

    """Waits until all nodes have received all transactions"""
    def monitor_mempool(self, target_count):
        def check_mempool_txs(node):
            retries = 0
            while len(raw_mempool := node.getrawmempool()) < target_count:
                # In some unlikely cases, a transaction can hit a false positive in the
                # RecentConfirmedTransactionsFilter or the one of the RecentRejectsFilter.
                # Check if we have been 1 transaction away from making it to the target for
                # about 10 seconds, and return if so, flagging this as a false positive.
                # (This happens 1/1M, which is ~every 125 iters in a network of 200 nodes
                # sending 400 transaction per iteration).
                if raw_mempool and len(raw_mempool) == target_count - 1:
                    retries+=1
                    if retries >= MAX_RETRIES:
                        self.log.info(f"false positive in RollingBloomFilter detected")
                        return

                time.sleep(1)

        threads = []
        for node in self.nodes:
            thread = threading.Thread(target=lambda n=node: check_mempool_txs(n), daemon=False)
            thread.start()
            threads.append(thread)

        self.log.info("waiting for all mempools to be on sync")
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

        return net_stats_count,  net_stats_bytes

    def check_propagation_time(self, node_rpc, txid):
        mempool_entry = node_rpc.getmempoolentry(txid)
        self.inv_timestamps.append(mempool_entry.get("first_inv_time"))
        self.tx_timestamps.append(mempool_entry.get("recv_time"))

    def run_test(self):
        self.orders(self.nodes[0])

    def orders(self, node):
        # Set the initial state
        self.log.info("waiting for all nodes to be connected")
        self.wait_for_tanks_connected()

        # Structures to store the partial results of each iteration
        diff_count = Counter()
        diff_bytes = Counter()
        inv_entry_count = []
        propagation_time = []

        # Repeat the experiments n times
        for i in range(self.options.n):
            if self.options.n > 1:
                self.log.info(f"Iter {i+1}")
            wallet_rpc = self.mine_blocks(node, self.options.tx_count)
            # Get a snapshot of the stats before creating any transactions
            # so we can account only for the traffic that derives from this experiment
            (init_stats_count, init_stats_bytes) = self.get_net_stats()

            # Create transactions
            txs = self.create_txs(wallet_rpc, self.options.tx_count)
            # Pick a single transaction to check its propagation time.
            decoded_target_tx = wallet_rpc.decoderawtransaction(txs[-1])

            # Propagate all transactions
            self.broadcast_txs(txs)

            # Wait until all mempools are the same to conclude the experiment, so we can check the exchanged messages
            self.monitor_mempool(self.options.tx_count)
            self.log.info("all transaction were received by all nodes")

            # Report back
            (net_stats_count,  net_stats_bytes) = self.get_net_stats()

            # Query all nodes to get the times where the target transaction was first heard of and received
            # so we can compute its propagation time over the network.
            threads = []
            for (i, node_rpc) in enumerate(self.nodes[1:]):
                thread = threading.Thread(target=lambda : self.check_propagation_time(node_rpc, decoded_target_tx["txid"]), daemon=False)
                thread.start()
                threads.append(thread)
            all(t.join() is None for t in threads)

            propagation_time.append(max(self.tx_timestamps) - min(self.inv_timestamps))
            if not (len(self.inv_timestamps) == len(self.tx_timestamps) == (len(self.nodes) - 1)):
                self.log.warning(f"Some timestamp data is missing. Results may be imprecise. \
                                 inv count: {len(self.inv_timestamps)}, \
                                 tx count: {len(self.tx_timestamps)}, \
                                 expected: {len(self.nodes)-1}")
            self.inv_timestamps.clear()
            self.tx_timestamps.clear()

            # Get the total number of inv entries. Headers are 21 byte-long, INV contain 1 byte as counter*
            # and 36 (32+4) bytes per entry
            # *counter is actually varsize, but it should not be over 1 byte in our experiments
            dc = (net_stats_count["sent"] - init_stats_count["sent"])
            db = (net_stats_bytes["sent"] - init_stats_bytes["sent"])
            diff_count += dc
            diff_bytes += db
            inv_entry_count.append(int((db["inv"] - dc["inv"] * 22) / 36.0))

        avg_diff_count = {k: v / float(self.options.n) for k, v in diff_count.items()}
        avg_diff_bytes = {k: v / float(self.options.n) for k, v in diff_bytes.items()}
        self.log.info("reporting netstats:")
        self.log.info(f"message count: {dict(avg_diff_count)}")
        self.log.info(f"bytes per message: {dict(avg_diff_bytes)}")
        self.log.info(f"INV entry count: {statistics.mean(inv_entry_count)}")
        self.log.info(f"approx propagation time: {statistics.mean(propagation_time) / 1000000.0}s")


def main():
    CheckNetBandwidth().main()

if __name__ == "__main__":
    main()
