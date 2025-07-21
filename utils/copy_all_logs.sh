#!/bin/bash

node_count=$(( $1 - 1 ))

for i in $(seq 0 $node_count); do
    node=$(printf "tank-%04d" "$i")

    echo "Copying $node logs"
    $(kubectl cp $node:root/.bitcoin/regtest/debug.log logs/$node-debug.log)
done