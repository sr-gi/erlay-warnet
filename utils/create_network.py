#!/usr/bin/env python3
"""Generate a random network.yaml to be run on warnet.

Connection model (mirrors real Bitcoin reachability):
  * Nodes ``0 .. reachable-1`` are *reachable* and accept inbound connections.
  * Every node opens ``outbound`` connections, always toward reachable nodes.
  * The union of all connections is treated as a simple undirected graph:
    no self-loops, no duplicate edges, and no mirrored edges (A->B && B->A)
"""

import argparse
import math
import sys
from random import Random

import networkx as nx
import yaml

# Default connection type for addconnection edges, matching warnet's
# addconnection_init scenario. warnet applies this default when `type` is omitted
# from an entry, so we only omit `type` when it equals this value.
DEFAULT_CONNECTION_TYPE = "outbound-full-relay"


class InfeasibleNetwork(Exception):
    """Raised when the requested parameters cannot form a valid graph."""


def validate_args(size, reachable, outbound, recon_outbound, max_inbound):
    """Reject parameter combinations that can never produce a valid graph."""
    if size <= 0:
        raise InfeasibleNetwork("size must be > 0")
    if outbound <= 0:
        raise InfeasibleNetwork("outbound must be > 0")
    if recon_outbound < 0:
        raise InfeasibleNetwork("recon-outbound must be >= 0")
    if not (0 < reachable <= size):
        raise InfeasibleNetwork(
            f"reachable must satisfy 0 < reachable <= size (got reachable={reachable}, size={size})"
        )

    # addnode and addconnection edges share a single simple undirected graph among the
    # reachable nodes, so each reachable node initiates `outbound + recon_outbound` edges.
    # Capacity cannot exceed one edge per pair:
    # reachable * total <= reachable * (reachable − 1) / 2 => reachable >= 2 * total + 1
    total_outbound = outbound + recon_outbound
    min_reachable = 2 * total_outbound + 1
    if reachable < min_reachable:
        raise InfeasibleNetwork(
            f"reachable={reachable} is too small for outbound+recon-outbound={total_outbound}; "
            f"need reachable >= 2*(outbound+recon-outbound) + 1 = {min_reachable}"
        )

    # Every outbound and recon-outbound connection counts as an inbound slot of a reachable
    # peer, therefore, the total inbound capacity must cover all edges.
    total_edges = size * total_outbound
    if reachable * max_inbound < total_edges:
        min_cap = math.ceil(total_edges / reachable)
        raise InfeasibleNetwork(
            f"max_inbound={max_inbound} too small: {size} nodes * {total_outbound} (outbound+recon) = "
            f"{total_edges} edges must fit in {reachable} reachable nodes; "
            f"need max_inbound >= {min_cap}"
        )


def try_build_graph(size, reachable, outbound, recon_outbound, max_inbound, rng):
    """Attempt to build one valid graph. Returns a DiGraph or None on deadlock.

    This creates both manual outbounds (build at deployment type), and optionally
    more specific types of connections under the addconnection tag, that will be
    created after the network deployment."""
    graph = nx.DiGraph()
    # First add nodes, as some of them may end up being isolated
    graph.add_nodes_from(range(size))

    for degree, manual in ((outbound, True), (recon_outbound, False)):
        for node_id in range(size):
            # Now define candidates: reachable peers that we are not connected to yet.
            # Don't connect to ourselves, and make sure the node has inbound slots left.
            candidates = [
                r
                for r in range(reachable)
                if r != node_id
                and not graph.has_edge(node_id, r)
                and not graph.has_edge(r, node_id)
                and graph.in_degree(r) < max_inbound
            ]

            if len(candidates) < degree:
                # Backed into a corner. Give up (we may retry if we haven't exhausted our attempts)
                return None

            for target in rng.sample(candidates, degree):
                graph.add_edge(node_id, target, manual=manual)

    return graph


def validate_graph(graph, size, reachable, outbound, recon_outbound, max_inbound):
    """Assert every invariant on the produced graph; raises on violation."""
    assert nx.number_of_selfloops(graph) == 0, "graph contains a self-loop"
    for node_id in range(size):
        targets = list(graph.successors(node_id))
        recon = [
            t for _, t, manual in graph.out_edges(node_id, data="manual") if not manual
        ]
        assert (
            len(targets) == outbound + recon_outbound
        ), f"node {node_id} has {len(targets)} outbound != {outbound + recon_outbound}"
        assert (
            len(recon) == recon_outbound
        ), f"node {node_id} has {len(recon)} addconnection != {recon_outbound}"
        for t in targets:
            assert 0 <= t < reachable, f"node {node_id} connects to non-reachable {t}"
            assert not graph.has_edge(
                t, node_id
            ), f"mirrored edge between {node_id} and {t}"
        assert (
            graph.in_degree(node_id) <= max_inbound
        ), f"node {node_id} exceeds max_inbound ({graph.in_degree(node_id)} > {max_inbound})"
    assert nx.is_connected(
        graph.to_undirected()
    ), "graph is not a single connected component"


def build_network(
    size, reachable, outbound, recon_outbound, max_inbound, max_attempts, rng
):
    """Build a valid, connected graph, retrying on deadlock or disconnection."""
    for attempt in range(1, max_attempts + 1):
        graph = try_build_graph(
            size, reachable, outbound, recon_outbound, max_inbound, rng
        )
        # Confirm the graph is a single connected component ignoring direction
        if graph is not None and nx.is_connected(graph.to_undirected()):
            return graph, attempt
    raise InfeasibleNetwork(
        f"failed to build a connected network after {max_attempts} attempts; "
        f"try increasing --reachable, lowering --outbound, or raising --max-attempts"
    )


def to_network_yaml(graph, size, connection_type, v2=True):
    """Render the graph as the warnet network.yaml structure.

    `addnode` tags only need the tank name, and create manual connections at node deployment time.
    `addconnection` tags can create other types of connections (e.g. blocks-only, reconciliation),
    and are specified as objects {to: tank-name, type: type}. type is omitted when it equals
    `DEFAULT_CONNECTION_TYPE`."""
    nodes = []
    for node_id in range(size):
        addnode, addconnection = [], []
        for _, target, is_manual in graph.out_edges(node_id, data="manual"):
            tank_name = f"tank-{target:04d}"
            if is_manual:
                addnode.append(tank_name)
            else:
                entry = {"to": tank_name}
                if connection_type != DEFAULT_CONNECTION_TYPE:
                    entry["type"] = connection_type
                if not v2:
                    entry["v2"] = False
                addconnection.append(entry)
        node = {"addnode": addnode}
        if addconnection:
            node["addconnection"] = addconnection
        node["name"] = f"tank-{node_id:04d}"
        nodes.append(node)
    return {
        "caddy": {"enabled": False},
        "fork_observer": {"configQueryInterval": 5, "enabled": False},
        "nodes": nodes,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-s", "--size", type=int, required=True, help="Total size of the network"
    )
    parser.add_argument(
        "-r",
        "--reachable",
        type=int,
        required=True,
        help="Number of reachable peers (accept inbound)",
    )
    parser.add_argument(
        "-o",
        "--outbound",
        type=int,
        default=8,
        help="Outbound connections per node (default: 8)",
    )
    parser.add_argument(
        "-c",
        "--recon-outbound",
        type=int,
        default=4,
        help="Reconciliation outbound connections per node, stored under "
        '"addconnection" (default: 4)',
    )
    parser.add_argument(
        "-m",
        "--max-inbound",
        type=int,
        default=125,
        help="Max inbound connections per reachable node (default: 125, as in Bitcoin Core)",
    )
    parser.add_argument(
        "--connection-type",
        default=DEFAULT_CONNECTION_TYPE,
        help="Connection type for the addconnection (reconciliation) edges. "
        "When left at the default, the per-entry `type` is omitted "
        f"(default: {DEFAULT_CONNECTION_TYPE})",
    )
    parser.add_argument(
        "--v2",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Use v2 (BIP324) transport for addconnection edges; --no-v2 emits v2: false "
        "to force v1 (default: v2)",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible topologies"
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=500,
        help="Max build attempts before giving up (default: 100)",
    )
    parser.add_argument(
        "-O",
        "--output",
        default="network.yaml",
        help="Output file path (default: network.yaml)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    try:
        validate_args(
            args.size,
            args.reachable,
            args.outbound,
            args.recon_outbound,
            args.max_inbound,
        )
        rng = Random(args.seed)  # Defaults to Random(None), which seeds from OS entropy
        graph, attempts = build_network(
            args.size,
            args.reachable,
            args.outbound,
            args.recon_outbound,
            args.max_inbound,
            args.max_attempts,
            rng,
        )
    except InfeasibleNetwork as e:
        sys.exit(f"error: {e}")

    # Validate the produced graph before writing it out.
    validate_graph(
        graph,
        args.size,
        args.reachable,
        args.outbound,
        args.recon_outbound,
        args.max_inbound,
    )

    with open(args.output, "w") as file:
        file.write(
            yaml.dump(
                to_network_yaml(graph, args.size, args.connection_type, args.v2), sort_keys=False
            )
        )

    inbound_counts = [graph.in_degree(r) for r in range(args.reachable)]
    total_edges = args.size * (args.outbound + args.recon_outbound)
    print(
        f"wrote {args.output}: {args.size} nodes, {args.reachable} reachable, {total_edges} connections "
        f"({args.outbound} addnode + {args.recon_outbound} addconnection per node) "
        f"(attempt {attempts}/{args.max_attempts})\n"
        f"inbound per reachable node: min={min(inbound_counts)}, "
        f"max={max(inbound_counts)}, avg={total_edges / args.reachable:.1f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
