#!/usr/bin/env python3

import argparse
import random
import yaml

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--reachable', type=int, required=True, help='Number of reachable peers')
    parser.add_argument('-s', '--size', type=int, required=True, help='Total size of the network')
    parser.add_argument('-o', '--outbound', type=int, default=8, help='Number of outbound connections per node')

    args = parser.parse_args()
    net_size = args.size
    reachable = args.reachable
    outbound_count = args.outbound

    yaml_network = {"caddy": {"enabled": False}, "fork_observer": {"configQueryInterval": 5, "enabled": False}}

    # Init the network
    network = {format(f"tank-{node_id:04d}"): [] for node_id in range(0, net_size)}

    # Create outbound connections. Nodes need to connect only to reachable. A node can only connect to another
    # once, independently of the direction. This means two connections A->B would be wrong, but also A->B + B->A.
    # A node cannot connect to itself either.
    for node_id in range(0, net_size):
        tank_id = format(f"tank-{node_id:04d}")
        while len(network[tank_id]) < outbound_count:
            out_tank_id = format(f"tank-{random.randrange(0, reachable):04d}")
            # Not connect to itself AND no duplicates AND no mirrored duplicated
            if out_tank_id != tank_id and out_tank_id not in network[tank_id] and tank_id not in network[out_tank_id]:
                network[tank_id].append(out_tank_id)

    # Convert the dictionary to the desired structure
    yaml_network["nodes"] = [{"addnode": peers, "name": node} for node, peers in network.items()]

    # Store file
    with open('network.yaml', 'w') as file:
        file.write(yaml.dump(yaml_network, sort_keys=False))

if __name__ == "__main__":
    main()
