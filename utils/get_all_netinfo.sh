#!/bin/bash

node_count=$(( $1 - 1 ))

for i in $(seq 0 $node_count); do
    node=$(printf "tank-%04d" "$i")

    echo "Copying $node netinfo"
    $(warnet bitcoin rpc $node getpeerinfo > logs/$node-netinfo.log)
done