#!/bin/bash

expected=$1

for i in $(seq 0 199); do
    node=$(printf "tank-%04d" "$i")

    output=$(( $(warnet bitcoin rpc "$node" getrawmempool | wc -l | xargs) - 3 )) # There are three extra lines in the return

    if [[ "$output" == "$expected" ]]; then
        echo "[✓] $node: OK"
    else
        echo "[✗] $node: Got '$output', expected '$expected'"
    fi
done