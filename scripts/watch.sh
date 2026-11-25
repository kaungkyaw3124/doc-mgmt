#!/usr/bin/env bash
# watch.sh — live, readable view of what Claude Code is doing in run-all.sh.
# Open a SECOND terminal in ~/doc-mgmt and run:  ./scripts/watch.sh
# Follows the newest loop/logs/iter-*.log and switches by itself when the next
# iteration starts. Ctrl+C stops watching; the run keeps going.
cd "$(dirname "$0")/.." || exit 1
newest() { ls -t loop/logs/iter-*.log 2>/dev/null | head -1; }
follow() {
    local f="" tpid=""
    trap '[ -n "$tpid" ] && kill "$tpid" 2>/dev/null; exit 0' INT TERM
    while true; do
        n="$(newest)"
        if [ -n "$n" ] && [ "$n" != "$f" ]; then
            [ -n "$tpid" ] && kill "$tpid" 2>/dev/null
            f="$n"
            echo "{\"type\":\"result\",\"result\":\"--- now watching $f ---\"}"
            tail -n +1 -F "$f" 2>/dev/null &
            tpid=$!
        fi
        sleep 3
    done
}
[ -n "$(newest)" ] || { echo "no log yet — start ./scripts/run-all.sh first"; exit 1; }
follow | python3 -u scripts/watch_fmt.py
