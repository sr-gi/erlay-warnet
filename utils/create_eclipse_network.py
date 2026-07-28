#!/usr/bin/env python3
"""Generate the three network arms for the single-node eclipse experiment.

  control : `--outbound` full-relay                         (all nodes: 8)
  relay   : `--outbound` full-relay + `--extras` full-relay (all nodes: 12)
  recon   : `--outbound` full-relay + `--extras` full-recon (all nodes: 12)

A random honest mesh (create_network.py) with `--reachable` nodes accepting
inbound; overlaid with `--outbound` blackholes and one victim whose whole addnode
baseline is captured by them (plus `--extras` honest lifeline links in relay/recon).
relay and recon share the honest graph and wiring, differing only in link type.
"""

import argparse
import pathlib
from random import Random
import yaml

import create_network


def honest_mesh(size, reachable, outbound, recon_outbound, conn_type, rng):
    create_network.validate_args(size, reachable, outbound, recon_outbound, 125)
    graph, _ = create_network.build_network(size, reachable, outbound, recon_outbound, 125, 500, rng)
    create_network.validate_graph(graph, size, reachable, outbound, recon_outbound, 125)
    return create_network.to_network_yaml(graph, size, conn_type, v2=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-n", "--honest", type=int, default=30, help="honest mesh size (default: 30)")
    p.add_argument("-r", "--reachable", type=int, default=None,
                   help="honest nodes that accept inbound (default: all honest)")
    p.add_argument("-o", "--outbound", type=int, default=8,
                   help="baseline full-relay outbound per node; also #blackholes and the victim's "
                        "captured baseline (default: 8)")
    p.add_argument("-e", "--extras", type=int, default=4,
                   help="extra connections per node in the relay/recon arms (default: 4)")
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--out", default="networks")
    args = p.parse_args()

    honest, outbound, extras = args.honest, args.outbound, args.extras
    reachable = args.reachable if args.reachable is not None else honest
    if outbound > 8:
        p.error("--outbound cannot exceed 8: addnode is capped at MAX_ADDNODE_CONNECTIONS = 8; "
                "extra outbound must be addconnection (--extras)")

    try:
        arms = {
            "control": honest_mesh(honest, reachable, outbound, 0, "outbound-full-relay", Random(args.seed)),
            "relay": honest_mesh(honest, reachable, outbound, extras, "outbound-full-relay", Random(args.seed)),
            "recon": honest_mesh(honest, reachable, outbound, extras, "outbound-full-recon", Random(args.seed)),
        }
    except create_network.InfeasibleNetwork as e:
        p.error(str(e))
        
    honest_names = [n["name"] for n in arms["recon"]["nodes"]]
    reachable_names = honest_names[:reachable]

    rng = Random(args.seed + 1)
    full_deg = outbound + extras
    bh_targets = [rng.sample(reachable_names, full_deg) for _ in range(outbound)]
    victim_extra = rng.sample(reachable_names, extras)

    arm_type = {"control": None, "relay": "outbound-full-relay", "recon": "outbound-full-recon"}
    for arm, ctype in arm_type.items():
        blackholes = []
        for i in range(outbound):
            bh = {"name": f"blackhole-{i + 1:04d}", "addnode": bh_targets[i][:outbound], "config": "blackhole=1"}
            if ctype is not None:
                bh["addconnection"] = [{"to": t, "type": ctype} for t in bh_targets[i][outbound:full_deg]]
            blackholes.append(bh)

        victim = {"name": "victim", "addnode": [b["name"] for b in blackholes]}
        if ctype is not None:
            victim["addconnection"] = [{"to": t, "type": ctype} for t in victim_extra]

        net = dict(arms[arm])
        net["nodes"] = arms[arm]["nodes"] + blackholes + [victim]
        d = pathlib.Path(args.out) / f"eclipse-{arm}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "network.yaml").write_text(yaml.dump(net, sort_keys=False))
        deg = outbound if ctype is None else full_deg
        print(f"wrote {d}/network.yaml  ({len(net['nodes'])} nodes: {honest} honest "
              f"({reachable} reachable) + {outbound} blackholes + victim; degree {deg})")


if __name__ == "__main__":
    main()
