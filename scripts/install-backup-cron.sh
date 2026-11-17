#!/usr/bin/env bash
# install-backup-cron.sh — add (or replace) the nightly backup in the current
# user's crontab: every night at $AT (default "30 2" = 02:30) it runs
# scripts/backup.sh, which keeps the newest 14 backups in ./backups/.
# Prints the line it installs. Run it once on the server; it does nothing
# until called. Remove with: crontab -l | grep -v doc-mgmt-nightly-backup | crontab -
# The user needs to be able to run docker (docker group).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="# doc-mgmt-nightly-backup"
LINE="${AT:-30 2} * * * cd $REPO_ROOT && ./scripts/backup.sh >> $REPO_ROOT/backups/cron.log 2>&1 $MARK"
mkdir -p "$REPO_ROOT/backups"
echo "Adding to $(id -un)'s crontab:"
echo "  $LINE"
# Read the table completely before writing it back (not one pipeline).
current="$(crontab -l 2>/dev/null | grep -vF "$MARK" || true)"
printf '%s\n%s\n' "$current" "$LINE" | sed '/^$/d' | crontab -
echo "Installed. Check with: crontab -l"
