#!/usr/bin/env bash
# watch.sh — live, readable view of what Claude Code is doing in run-all.sh.
# Open a SECOND terminal in ~/doc-mgmt and run:  ./scripts/watch.sh
# Shows Claude's messages, every file it edits/reads, every command it runs.
cd "$(dirname "$0")/.." || exit 1
f="$(ls -t loop/logs/iter-*.log 2>/dev/null | head -1)"
[ -n "$f" ] || { echo "no log yet — start ./scripts/run-all.sh first"; exit 1; }
echo "watching $f  (Ctrl+C to stop watching; the run keeps going)"
tail -n +1 -F "$f" | python3 -u scripts/watch_fmt.py
