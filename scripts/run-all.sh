#!/usr/bin/env bash
# run-all.sh — run every queued plan, one after another, unattended.
#
# For each iteration it starts a FRESH Claude Code session (`claude -p "/next"`),
# so the context never fills up. When the report says DONE it archives the
# report, promotes the next plan from loop/queue/ into loop/PLAN.md, pushes
# dev and security/auth-hardening, and goes on. It STOPS (and leaves everything
# as it is) when an iteration is BLOCKED / NEEDS_DECISION, fails 3 times, or
# leaves uncommitted/unpushed work. Touch loop/STOP to stop after the current one.
#
# Usage:  ./scripts/run-all.sh          (from the repo root, on branch dev, clean tree)
# Env:    CLAUDE_BIN=claude   MAX_ATTEMPTS=3   (CLAUDE_FLAGS to override the permission flags)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
CLAUDE_FLAGS="${CLAUDE_FLAGS:---permission-mode auto --permission-prompts none}"
LOG_DIR="loop/logs"; mkdir -p "$LOG_DIR"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG_DIR/run-all.log"; }
die() { say "STOPPED: $*"; exit 1; }

plan_iter()   { grep -m1 '^| Iteration' loop/PLAN.md | grep -o '[0-9]\+' | head -1; }
report_iter() { head -1 loop/report.md 2>/dev/null | grep -o 'Iteration [0-9]\+' | grep -o '[0-9]\+'; }
report_status() { grep -m1 '^- \*\*Status\*\*:' loop/report.md 2>/dev/null | sed 's/.*Status\*\*: *//; s/[^A-Z_].*//'; }
next_queued() { ls loop/queue/iter-*.md 2>/dev/null | sort | head -1; }
clean_tree()  { [ -z "$(git status --porcelain)" ]; }
pushed() { git fetch -q origin dev 2>/dev/null; [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/dev)" ]; }
finished() { # the report is for the current plan, says DONE, everything committed and pushed
    [ "$(report_iter)" = "$(plan_iter)" ] && [ "$(report_status)" = "DONE" ] && clean_tree && { pushed || { git push -q origin dev && pushed; }; }
}
push_both() { git push -q origin dev && git push -q origin dev:security/auth-hardening; }

[ "$(git rev-parse --abbrev-ref HEAD)" = "dev" ] || die "not on branch dev (run: git checkout dev)"
command -v "$CLAUDE_BIN" >/dev/null || die "'$CLAUDE_BIN' not found"
clean_tree || die "uncommitted changes — commit or stash them first"
git pull -q --ff-only origin dev || die "git pull failed"

say "=== run-all starting. Plan now: iteration $(plan_iter); queued: $(ls loop/queue/iter-*.md 2>/dev/null | wc -l) ==="
while true; do
    [ -f loop/STOP ] && die "loop/STOP exists"
    N="$(plan_iter)"; [ -n "$N" ] || die "cannot read the iteration number from loop/PLAN.md"
    if grep -qi 'WAITING FOR PLANNER' loop/PLAN.md; then say "Nothing left to do — waiting for the planner."; exit 0; fi

    if finished; then say "iteration $N is already DONE and pushed"; else
        attempt=0
        while [ $attempt -lt "$MAX_ATTEMPTS" ]; do
            attempt=$((attempt+1)); say "iteration $N: attempt $attempt/$MAX_ATTEMPTS"
            if [ $attempt -eq 1 ]; then
                # shellcheck disable=SC2086
                "$CLAUDE_BIN" -p "/next" $CLAUDE_FLAGS >> "$LOG_DIR/iter-$N.log" 2>&1
            else
                # shellcheck disable=SC2086
                "$CLAUDE_BIN" -p "The previous session for iteration $N was interrupted. Continue the current iteration in loop/PLAN.md exactly where it stopped, following loop/LOOP.md: finish the work, run the checks, write loop/report.md, commit and push dev. Do not start over." --continue $CLAUDE_FLAGS >> "$LOG_DIR/iter-$N.log" 2>&1
            fi
            echo "exit code $?" >> "$LOG_DIR/iter-$N.log"
            finished && break
            case "$(report_status)" in BLOCKED|NEEDS_DECISION) [ "$(report_iter)" = "$N" ] && die "iteration $N is $(report_status) — read loop/report.md";; esac
            sleep 20
        done
        finished || die "iteration $N did not finish after $MAX_ATTEMPTS attempts (see $LOG_DIR/iter-$N.log)"
    fi
    say "iteration $N: DONE and pushed"

    # accept it: archive the report, promote the next plan (or leave a 'waiting' stub)
    cp loop/report.md "loop/history/report-iter-$N.md"
    NEXT="$(next_queued)"
    if [ -n "$NEXT" ]; then
        git mv -f "$NEXT" loop/PLAN.md
        python3 - "$NEXT" <<'PY' || die "could not prepare the next plan"
import re, sys
p = "loop/PLAN.md"
s = open(p).read()
s = re.sub(r"^# PLAN\.md — Iteration \d+ \(QUEUED[^\n]*", "# PLAN.md — Current Iteration", s, count=1)
s = s.replace("| Status | QUEUED |", "| Status | READY FOR CLAUDE CODE |", 1)
open(p, "w").write(s)
PY
        M="$(plan_iter)"
        git add -A && git commit -q -m "loop: iteration $N accepted (auto, run-all); promote iteration $M" && push_both || die "could not commit/push the promotion"
        say "promoted iteration $M"
    else
        cat > loop/PLAN.md <<'STUB'
# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Status | **WAITING FOR PLANNER** |

All queued plans are finished. See `loop/history/` for the reports.
STUB
        git add -A && git commit -q -m "loop: iteration $N accepted (auto, run-all); queue empty — waiting for planner" && push_both || die "could not commit/push"
        say "=== ALL PLANS FINISHED. Reports are in loop/history/. App: http://localhost:8080 ==="
        exit 0
    fi
done
