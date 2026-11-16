# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **16** |
| Goal | Backup & Restore: (1) a server-side backup/restore engine + nightly job, (2) **Download backup** button in the web UI, (3) **Restore from file** in the web UI |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
The user lost data once (products vanished) and runs the app on a VM with real data. They want
backups they can **download as a file (tar.gz) from the web UI** and **restore from the web UI**,
plus automatic nightly backups on the VM.

## Decisions already made (do not ask the user again)
- **What is in a backup**: the Postgres database(s) (pg_dump, custom or plain SQL — pick what restores most reliably, and say why), all MinIO objects (logos, seals, letterheads, product/catalogue files, document files), and `manifest.json` (app version/git commit, Alembic revision(s) per service, created-at, row counts, object count, file checksums).
  **Not** in a web backup: `.env` files / secrets, the Meilisearch index (rebuild it from the database after restore — reuse the existing re-index code, or add a small one).
- **Engine**: one reusable module/script (e.g. `scripts/backup.sh` + `scripts/restore.sh`, or a small `backup-service` container with `postgresql-client` and MinIO access — choose the smallest design that works inside the existing docker-compose; do not give any container the Docker socket). The web UI and the nightly cron both call the same engine.
- **Nightly job**: cron-installable script (`scripts/install-backup-cron.sh`, prints what it will add, does not run without being called) writing `backup-YYYY-MM-DD-HHMM.tar.gz` into a host folder (default `./backups/`, git-ignored), keep the newest **14**, delete older ones.
- **Pre-update backup**: add to `LOOP.md` finish step and to a `scripts/deploy.sh` (pull → backup → `up -d --build` → restart nginx) a backup taken BEFORE rebuilding. Never `down -v`.
- **Web UI**: new Admin sub-page **"Backup & Restore"**, superuser only (check the RBAC/gateway so other roles get 403 and no menu entry):
  - **Download backup** button → streams a `.tar.gz` (no loading the whole file in memory; progress/“preparing…” state).
  - Optional **password** field: if filled, the download is encrypted (use a standard, well-supported method such as `age`/`openssl enc -aes-256-cbc -pbkdf2`, or AES zip — pick one, document how to decrypt it by hand). Empty = plain tar.gz.
  - List of existing server-side backups (name, size, date) with download buttons; can also be taken from the nightly folder.
  - **Restore**: upload a backup file (+ password if encrypted) → server validates (archive readable, manifest present, checksums OK, Alembic revision not newer than this app's — refuse newer with a clear message) → shows a summary ("backup from DATE: N products, N documents, N files") → user must type **RESTORE** to confirm (this is the real server, unlike the dev delete buttons).
  - Restore steps: take an automatic **pre-restore backup** first (kept in the server list), put the app in maintenance mode ("Restoring… please wait", other API calls return 503 except the status endpoint), replace DB contents and MinIO objects, run migrations to current head, rebuild the search index, leave maintenance mode, report success/failure. If any step fails, say exactly which, keep maintenance mode off, and tell the user the pre-restore backup name for rollback (attempt automatic rollback only if it is simple and tested; otherwise document the manual command).
  - Show the final result with a clear success/failure message (the animated tick from iteration 15 Part C may be reused if it exists; otherwise plain text is fine).
- **Gateway**: raise nginx `client_max_body_size` and proxy timeouts for the restore upload/download routes only (not globally). Check `infra/nginx/nginx.conf.template`.
- **Safety**: never log the password or the archive contents; temp files in a private dir and deleted afterwards; restore refuses if free disk space is clearly insufficient; only one backup/restore may run at a time.

## Notes from the planner
- Iteration 15 is accepted (PASS). Its report is in `loop/history/report-iter-15.md`.
- The user's VM has its own **uncommitted** `infra/backup.sh` / `infra/restore.sh` that are not in git; do not depend on them. Build the engine fresh in this repo; if a file with those names already exists in the working tree when you start, do not overwrite it — use other names (`scripts/backup.sh`, `scripts/restore.sh`).
- Run tests on a throwaway `loop-test` stack only (LOOP.md "Protect the user's data"). The user's stack at :8080 must come out of this iteration with its data intact.
- The DEV-ONLY Delete ALL feature from iteration 15 stays for now; production sets `ENVIRONMENT=production`.

## Tasks
1. Inspect the stack: databases (one or several), what MinIO buckets exist, how migrations run at startup, how search indexing is rebuilt. Write what you found in the report.
2. Build the engine (backup + restore + manifest + verify) and test it from the command line first.
3. Nightly cron installer + retention + `scripts/deploy.sh` with pre-update backup. Update `LOOP.md` accordingly.
4. API endpoints (superuser only) + Admin page UI, per the decisions above.
5. Maintenance mode + rebuild index + migration-to-head after restore.
6. Docs: a short `docs/BACKUP_RESTORE.md` (how to back up, restore, decrypt by hand, restore on a fresh server).

## Tests (run on a THROWAWAY stack, never on the user's data — see LOOP.md "Protect the user's data")
- Engine round trip: seed products, companies (with logo/seal), customers, projects, documents (+ PDF/files) → backup → wipe DB and MinIO → restore → counts equal, files byte-identical (compare checksums), a generated quotation PDF still renders with logo/seal, search finds a product.
- Manifest/validation: corrupt archive → refused; checksum mismatch → refused; newer-revision backup → refused with message; wrong/missing password → refused; unencrypted + encrypted both work.
- API: non-superuser gets 403 on every backup/restore endpoint; download streams a valid tar.gz; restore without typing RESTORE → 400; during restore other endpoints return 503; a second concurrent backup/restore is rejected.
- Retention: 20 fake backups → only newest 14 remain.
- Playwright `tests/test_ui_backup_restore.py`: log in as admin → Admin → Backup & Restore → click Download (file arrives, valid archive) → create extra data → upload that backup → type RESTORE → data returns to the backed-up state, success message shown; a normal user cannot see the page.
- All existing tests pass; `./scripts/verify.sh` passes.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Round-trip restore is lossless | the engine round-trip test output (counts + checksums) |
| A2 | Validation / auth tests pass | listed tests |
| A3 | UI flow works end to end | Playwright test + `loop/evidence/iter-16-backup-page.png`, `iter-16-restore-confirm.png`, `iter-16-restore-done.png` |
| A4 | Nightly cron + retention + pre-update backup | test output, `scripts/deploy.sh` content in the report |
| A5 | No regressions | `./scripts/verify.sh` |
| A6 | Data safety | report states which stack the tests ran on and that the user's volumes were untouched |

## Out of scope — record under "Found but not fixed"
- Off-site copy (rsync/rclone/cloud) — the user has not chosen a destination yet; only document how to point `backups/` at a mounted disk.
- Point-in-time recovery, incremental backups.
