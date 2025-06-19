#!/usr/bin/env python3

from collections import Counter
from decimal import Decimal
from datetime import datetime
import logging
import statistics
import threading
import time

from commander import Commander, NAMESPACE, WARNET

from test_framework.messages import CTransaction, CTxIn, CTxOut, COutPoint, COIN
from test_framework.address import address_to_scriptpubkey
from test_framework.util import satoshi_round

SATOSHI_PRECISION = Decimal(0.00000001)
MIN_UTXO_VALUE = Decimal(0.0002)

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
            while len(node.getrawmempool()) < target_count:
                time.sleep(0.5)

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

        # This seemed like a good idea, but in bigger networks in impractical, and may lead to slowdowns in the simulation
        # Since we are only checking the "sent" part, it shouldn't really matter
        #
        # It could be the case that we try to collect net stats right when a node sends a message to a peer, but before the peer receives it,
        # so out sent/receive values won't match. In the unlikely case this happens, just get the stats again
        # while (net_stats_count["sent"] != net_stats_count["recv"]):
        #     time.sleep(2)
        #     (net_stats_count,  net_stats_bytes) = self.get_net_stats()

        return net_stats_count,  net_stats_bytes

    """
    Records the timestamp of the first time a node logs the inv of a certain transaction, and the transaction itself, being received.
    This will be used to check how long transactions take to propagate.
    """
    def timestamp_messages(self, tank_name, log_entries):
        log_stream = self.sclient.read_namespaced_pod_log(tank_name, NAMESPACE, container="bitcoincore",
                                                          follow=True, _preload_content=False, since_seconds=1)

        inv_log_entry, tx_log_entry = log_entries
        inv_found = tx_found = False

        for stream in log_stream.stream():
            chunk = stream.decode("utf-8").rstrip()

            if not any(entry in chunk for entry in log_entries):
                # Check in chunks to save time
                continue

            # If the log_entry can be found check what line contains it
            for line in chunk.splitlines():
                # grep the timestamp (first item in the line)
                if not inv_found and inv_log_entry in line:
                    ts = datetime.strptime(line.split(" ")[0].rstrip(), "%Y-%m-%dT%H:%M:%S.%fZ")
                    self.inv_timestamps.append(ts.timestamp())
                    inv_found = True
                elif not tx_found and tx_log_entry in line:
                    ts = datetime.strptime(line.split(" ")[0].rstrip(), "%Y-%m-%dT%H:%M:%S.%fZ")
                    self.tx_timestamps.append(ts.timestamp())
                    tx_found = True

                if inv_found and tx_found:
                    return

    def run_test(self):
        self.orders(self.nodes[0])

    def orders(self, node):
        # Set the initial state
        self.wait_for_tanks_connected()

        # Repeat the experiments n times
        diff_count = Counter()
        diff_bytes = Counter()
        inv_entry_count = []
        propagation_time = []

        for i in range(self.options.n):
            if self.options.n > 1:
                self.log.info(f"Iter {i+1}")
            wallet_rpc = self.mine_blocks(node, self.options.tx_count)
            # Get a snapshot of the stats before creating any transactions
            # so we can account only for the traffic that derives from this experiment
            (init_stats_count, init_stats_bytes) = self.get_net_stats()

            # Create transactions
            txs = self.create_txs(wallet_rpc, self.options.tx_count)
            # Pick a single transaction to check its propagation time
            decoded_target_tx = wallet_rpc.decoderawtransaction(txs[-1])
            target_wtxid = decoded_target_tx["hash"]

            # Start a thread per node to timestamp the reception of the first inv of the target transaction and the target tx (this excludes the source)
            ts_threads = []
            self.inv_timestamps = []
            self.tx_timestamps = []
            log_entries = [f"got inv: wtx {target_wtxid}", f"accepted {decoded_target_tx["txid"]} (wtxid={target_wtxid})"]
            for tank in WARNET["tanks"][1:]:
                thread = threading.Thread(target=lambda : self.timestamp_messages(tank["tank"], log_entries), daemon=False)
                thread.start()
                ts_threads.append(thread)

            # Propagate all transactions
            self.broadcast_txs(txs)
            # Sync all threads
            all(t.join() is None for t in ts_threads)
            propagation_time.append(max(self.tx_timestamps) - min(self.inv_timestamps))

            # Wait until all mempools are the same to conclude the experiment, so we can check the exchanged messages
            self.monitor_mempool(self.options.tx_count)
            self.log.info("all transaction were received by all nodes")

            # Report back
            (net_stats_count,  net_stats_bytes) = self.get_net_stats()
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
        self.log.info(f"approx propagation time: {statistics.mean(propagation_time)}s")


def main():
    CheckNetBandwidth().main()

if __name__ == "__main__":
    main()
