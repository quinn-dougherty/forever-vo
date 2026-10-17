#!/usr/bin/env bash
# Runs the bulk backlog as several parallel generate.py shards.
#
# One autoregressive stream leaves the GPU about 60% idle: measured on the
# RTX 3080, one process does 1.68x realtime, two together 2.28x, three no better
# than two (and three copies of the model no longer fit comfortably in 16 GB).
# Each worker takes every Nth file of the same priority-ordered todo list, so
# stopping early still leaves the low-level zones done.
#
# Started by forever-vo-bulk.service. The default is one stream: the Classic
# backlog finished on 2026-09-23, so what remains is a trickle of captured and
# regenerated lines, and a second worker would only take the GPU from the game
# while the owner plays. Set FOREVER_VO_WORKERS=2 for a big run (a new
# narrator voice, a bitrate change) when the client is closed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKERS="${FOREVER_VO_WORKERS:-1}"
cd "$ROOT"

pids=()
for ((i = 0; i < WORKERS; i++)); do
    ./tools/run.sh tools/generate.py --shard "$i/$WORKERS" "$@" &
    pids+=("$!")
done

# Pass a stop on to the workers rather than orphaning them on the GPU
terminate() {
    kill -TERM "${pids[@]}" 2>/dev/null
}
trap terminate TERM INT

status=0
for pid in "${pids[@]}"; do
    # `wait` returns early when the trap fires; wait again for the worker's own
    # exit, which saves the seeds of the files it drew (generate.py's finally)
    wait "$pid" || wait "$pid" || status=$?
done
exit "$status"
