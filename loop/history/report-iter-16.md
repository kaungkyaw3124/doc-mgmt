# report.md — Iteration 16

## 1. Summary
- **Status**: DONE
- **Goal**: Backup & Restore: (1) a server-side backup/restore engine + nightly job, (2) **Download backup** button in the web UI, (3) **Restore from file** in the web UI
- **Ponytail skill loaded**: yes (loaded by the session-start hook and followed for the whole iteration)
- **Branch / commit**: `dev` @ (this commit, see `git log`)
- **One-paragraph summary**: A new small container, **`backup-service`**, holds the one engine (`services/backup-service/app/engine.py`). The web API, `scripts/backup.sh`, `restore.sh` and `deploy.sh`, and the nightly cron all call it.
  - **What a backup holds.** A backup is one `.tar.gz` containing `manifest.json`, a `pg_dump -Fc` of each of the 3 databases, and every MinIO object.
  - **Restore.** The engine validates the archive (readable, manifest, checksums, safe paths, Alembic revision known), then:
    1. turns on maintenance mode (nginx answers 503);
    2. takes a pre-restore backup;
    3. restores each database in one transaction;
    4. replaces the MinIO objects;
    5. runs migrations to head and rebuilds both search indexes;
    6. turns maintenance off.
  - **Web UI.** A new superuser-only **Backup & Restore** page has Download (optional password: openssl AES-256), the server-side backup list, and Restore. Restore means: upload the file, see the summary, type RESTORE, watch progress, then see the result with the iteration 15 tick.
  - **Scripts.** Nightly cron installer, retention of 14, and `deploy.sh` (pull → backup → rebuild → restart nginx).
  - **Testing.** Everything was tested on a throwaway `loop-test` stack. The round trip is lossless (row counts equal, files byte-identical, PDF still has logo + seal, search finds the product).
  - **Your data.** Your stack at :8080 was backed up first, then rebuilt. Its data is intact.

## 2. Baseline (before any change)
On the throwaway stack. The only edits made before this run were the test-stack file and a 1-line `COMPOSE_OVERRIDE` hook in verify.sh; app code was unchanged.
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=<override> GATEWAY=http://localhost:18080 ./scripts/verify.sh --keep
======================== 24 passed, 11 skipped in 5.03s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Round-trip restore is lossless | PASS | `tests/test_backup_restore.py::test_round_trip_restore_is_lossless PASSED`. Steps: seed a category, a product + PDF file, a company + logo + seal, a customer, a project, a quotation (company/customer/project) + document file → back up through the API → download by ticket → **DROP SCHEMA on docmgmt + catalogue and delete every MinIO object** → validate + restore through the API. Checked afterwards: `row_counts` for docmgmt and catalogue equal; the auth users count is equal; the sha256 of every `minio/*` file is equal and the content types are equal; the logo downloaded via its presigned URL matches the backup checksum; the quotation PDF has the same image count (≥2: logo + seal) and the same text; `/search` finds the product (index rebuilt); a `pre-restore-*` file is listed; and **503 was observed** on `/api/products` during the restore |
| A2 | Validation / auth tests pass | PASS | `test_non_superuser_gets_403_everywhere` (list/create/download-ticket/validate/restore → 403 ×5; no token → 401), `test_bad_backups_are_refused[garbage, truncated, checksum, newer-revision, path-traversal, unlisted-file]` → 400 with a clear message, `test_encrypted_backup_needs_the_right_password` (missing → "encrypted", wrong → "Wrong password", right → 200), `test_restore_requires_typing_restore` (""/"restore"/"yes" → 400), `test_maintenance_mode_blocks_api_but_not_status` (503 on the API/login/files, status 200, UI 200), `test_second_backup_or_restore_is_rejected_while_one_runs` (409 on both create and restore while the lock is held), `test_download_is_a_valid_backup_and_listed`, single-use ticket (2nd fetch → 404). Engine unit tests: `services/backup-service/tests/test_engine.py` → `9 passed` |
| A3 | UI flow works end to end | PASS | `tests/test_ui_backup_restore.py` (Playwright image) → `2 passed`. Steps: admin → Backup & Restore → Download (the file arrives through `expect_download`, and it is a valid tar with a manifest) → add a product through the API → upload that file → summary → "restore" keeps the button disabled, "RESTORE" enables it → Restore → "Restore complete … pre-restore-…" + tick → after logging in again, the added product is 404. A normal Editor sees no nav entry and no page. Screenshots: `loop/evidence/iter-16-backup-page.png`, `iter-16-restore-confirm.png`, `iter-16-restore-done.png` |
| A4 | Nightly cron + retention + pre-update backup | PASS | `tests/test_backup_scripts.py` → `3 passed`. (1) The cron installer prints its line and installs exactly one `30 2 * * *` line (run twice; other crontab lines are kept; a fake `crontab` is used, the real one is never touched). (2) `deploy.sh` order is git pull < backup.sh < `up -d --build`, with no `down`/`volume rm`/`prune`. (3) 20 fake `backup-2000-01-*` files + `scripts/backup.sh` → exactly the newest 14 are kept, including the new one. Unit test: 20 fakes → 14 kept, and `pre-restore-*` is never pruned. `deploy.sh` is quoted below |
| A5 | No regressions | PASS | `verify.sh` on loop-test → `42 passed, 12 skipped`. The skips are the Playwright UI files, as in the baseline. All Playwright UI tests: `29 passed, 6 skipped` (the same 6 short-session skips as iteration 15). Service unit tests: document-service `239 passed`, catalogue `80 passed`, auth `93 passed` |
| A6 | Data safety | PASS | See "Data safety" below |

**Data safety**: every test (verify.sh, Playwright, unit, script, and the manual CLI tries) ran on the **throwaway `loop-test` stack**: `infra/docker-compose.loop-test.yml`, its own data under `infra/data-loop-test/`, ports 18080/18443. Afterwards I tore it down with `down -v` and deleted its data folder. **Your stack was never tested against.** Its volumes were never removed.
- Before rebuilding your stack, I took a backup of it: `backups/backup-2026-11-17-1032.tar.gz` (293 KB).
- Counts before and after the rebuild are identical: documents 1, companies 1, customers 1, projects 1, products 100, users 20.
- One deviation: the pre-backup `up -d --build backup-service` also recreated document-service and catalogue-service with the new code, because they are its dependencies. This happened before the backup, and only the code changed; the data did not. `deploy.sh` and LOOP.md now use `--no-deps`.

`scripts/deploy.sh` (comments trimmed):
```bash
git pull --ff-only
export APP_COMMIT="$(git rev-parse --short HEAD)"
"${COMPOSE[@]}" up -d --build --no-deps backup-service      # only the backup container, so the first deploy can back up too
for _ in $(seq 30); do "${COMPOSE[@]}" exec -T backup-service true 2>/dev/null && break; sleep 2; done
./scripts/backup.sh                                          # set -e: a failed backup stops the deploy
"${COMPOSE[@]}" up -d --build
"${COMPOSE[@]}" restart nginx
# wait up to 2 min for http://localhost:8080/ → 200
```

### Task 1 — what I found in the stack
- **Databases**: one Postgres 16 server with **3 databases**: `docmgmt` (document-service), `catalogue`, `auth`. They are created by `infra/init-db/*.sql`, and all are owned by `docmgmt` (POSTGRES_USER, a superuser).
- **MinIO buckets**: `documents` (company logos/seals, MD seals, document files) and `products` (catalogue files). Each service creates its bucket at startup. **There is no letterhead upload** (already noted in iteration 15).
- **Migrations**: only **document-service** uses Alembic. It runs `alembic upgrade head` in `on_startup` (`docs/decisions.md` §4, which says migrations are not run, is outdated). catalogue-service and auth-service use `create_all()` only. auth also runs `ensure_session_columns`.
- **Search indexing**: there was **no re-index code**. Rows are indexed one at a time on create/update (`index_document`, `index_product`). I added `rebuild_index()` next to each, behind a new internal `/internal/after-restore` route.
- **Data storage**: all data lives in **bind mounts** (`infra/data/...`), not named volumes. So a different compose project name alone would share your data folders. The throwaway stack file therefore re-points every data path.

### Design choices (the plan asked me to say why)
- **pg_dump custom format (-Fc)**: it is compressed, readable by pg_restore (which is how validation checks it), and restores with `--no-owner --no-privileges`. The restore turns it into SQL and runs `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` + the dump in **one `psql --single-transaction`**. A failure part-way through a database rolls back to how that database was. A plain-SQL dump would work too, but is larger and harder to check.
- **backup-service built `FROM postgres:16`**: pg_dump refuses newer servers, and Debian's own client is version 15 (bookworm). This image has exactly 16.x. It has no CA bundle, so the Dockerfile borrows one from `python:3.12-slim-bookworm` (already a base for the other services), which keeps apt on https. There is no Docker socket. The service reaches the others only through the compose network: postgres, minio, and document-/catalogue-service `/internal/*` with the internal secret.
- **Encryption**: `openssl enc -aes-256-cbc -pbkdf2 -iter 200000`, streamed on download. Manual decrypt (verified against a server-produced `.enc`: the decrypted file equals the original byte for byte):
  `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in X.tar.gz.enc -out X.tar.gz`
- **Download**: the page asks for a **single-use, 60-second ticket**, then opens `/api/backup/fetch/<ticket>` as a plain link. The browser streams it to disk with its own progress bar. Neither the server nor the page holds the file in memory, and the password never appears in a URL.
- **"Newer revision" check**: the backup's Alembic revision must be one that this app's document-service knows (`/internal/alembic-revisions`). Unknown → refused: "This backup is from a newer version of the app (database revision X). Update the app first, then restore."
- **Maintenance mode**: backup-service creates `/maintenance/on`, a shared bind mount that nginx mounts read-only. nginx then returns 503 for `/api/*`, `/documents/*` and `/products/*`, except `= /api/backup/status`. The UI itself keeps loading. backup-service clears the flag in `finally` and on every startup, so a crash can't leave the app stuck at 503.
- **Order change versus the plan**: maintenance mode starts **before** the pre-restore backup, not after. That way no write can land between the rollback snapshot and the restore and then be lost.
- **Sessions**: `refresh_tokens` **data** is excluded from backups (they are live credentials). A restore therefore signs everyone out, which the UI says. `/api/backup/status` is public for that reason: the admin's own session ends during the restore. It returns only state/step/message/maintenance.
- **Lock**: a `flock` on `backups/.lock` shared by the API and CLI execs. The kernel releases it if the holder dies.
- **Rollback**: not automatic. On failure, the message names the step and the pre-restore file, and docs/BACKUP_RESTORE.md gives the command. Databases already restored by then are committed, so automatic rollback would be another full restore. I didn't build it because it isn't simple, and the plan allowed skipping it.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `services/backup-service/` (Dockerfile, .dockerignore, requirements*.txt, `app/engine.py`, `app/main.py`, `tests/test_engine.py`) | added | engine + CLI + API; unit tests |
| `services/document-service/app/main.py` | modified | `GET /internal/alembic-revisions`, `POST /internal/after-restore` (re-runs the idempotent `on_startup` = migrations to head, then rebuilds the index); shared `_ALEMBIC_INI` |
| `services/catalogue-service/app/main.py` | modified | `POST /internal/after-restore` (create_all + index rebuild) |
| `services/{document,catalogue}-service/app/core/search_client.py` | modified | `rebuild_index()` (clear, then re-add every row; fails open like the other index calls) |
| `services/auth-service/app/routers/auth.py` | modified | `/verify` refuses `X-Service: backup` to non-superusers (explicit; not a grantable role service) |
| `infra/docker-compose.yml` | modified | `backup-service` (compose-level secrets, `../backups`, `./data/maintenance`); nginx mounts the maintenance folder read-only and depends on backup-service |
| `infra/nginx/nginx.conf.template` | modified | maintenance 503 block; `/api/backup/status` (public), `/api/backup/fetch/<ticket>` (no buffering, 3600 s), `/api/backup/` (superuser auth_request, `client_max_body_size 20g`, 3600 s timeouts, request buffering off). Those three locations only; nothing global |
| `infra/docker-compose.loop-test.yml` | added | throwaway test stack (own data paths and ports) |
| `scripts/verify.sh` | modified | optional `COMPOSE_OVERRIDE` (1 line + comment) to run on the throwaway stack |
| `scripts/backup.sh`, `restore.sh`, `install-backup-cron.sh`, `deploy.sh` | added | CLI / nightly / pre-update backup |
| `web/index.html` | modified | "Backup & Restore" nav item (hidden) + view |
| `web/js/app.js` | modified | superuser-only nav; list (loadSeq guard), download by ticket, check/confirm/restore + status polling, result + tick |
| `tests/conftest.py` | modified | `destructive` guard (flag **and** a non-`infra` project name), `restores_data` marker run last, `relogin()` |
| `tests/test_backup_restore.py`, `test_backup_scripts.py`, `test_ui_backup_restore.py` | added | API/engine/scripts/UI tests |
| `docs/BACKUP_RESTORE.md` | added | back up, restore, decrypt by hand, fresh server, off-site note |
| `docs/operations.md`, `loop/LOOP.md` | modified | pre-update backup step / deploy.sh; how to use the throwaway stack |
| `.gitignore` | modified | `backups/`, `infra/data-loop-test/` |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | build backup-service | apt: `certificate verify failed` | the postgres:16 image has no CA bundle (and the keyring is `.pgp` on trixie) | COPY the CA bundle from python:3.12-slim-bookworm; sed only http→https in the image's own sources file |
| 2 | A1 round trip | `409 Another backup or restore is already running` | the lock test's `holder.kill()` stopped only the local docker client; the process in the container kept the lock | wait for the holder to finish (3 s sleep) instead of killing it |
| 3 | A4 cron installer | the other crontab line was lost | `crontab -l | … | crontab -` read and wrote the table in one pipeline | read it fully into a variable first, then install (a real script bug) |
| 4 | manual `restore.sh` | typed RESTORE ignored, exit 1 | `docker compose exec -T` (verify step) consumed stdin | `</dev/null` on that exec and on `cp` |
| 5 | A3 (full UI run) | strict mode: 2 `.tick-toast.tick-ok` | the "Backup ready" toast was still visible when the restore finished fast | locator narrowed to `has_text="Restore complete"` (test-only flake) |
| 6 | Playwright environment | `Executable doesn't exist … 1.65.2` / module missing | I had pip-installed `pytest-playwright`, which upgraded playwright | install `playwright==1.65.1` to match the image |

## 6. Final verification output
```
$ COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml \
  GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
tests/test_backup_restore.py::test_non_superuser_gets_403_everywhere PASSED
tests/test_backup_restore.py::test_bad_backups_are_refused[checksum] PASSED
tests/test_backup_restore.py::test_bad_backups_are_refused[newer-revision] PASSED
tests/test_backup_restore.py::test_encrypted_backup_needs_the_right_password PASSED
tests/test_backup_restore.py::test_maintenance_mode_blocks_api_but_not_status PASSED
tests/test_backup_restore.py::test_second_backup_or_restore_is_rejected_while_one_runs PASSED
tests/test_backup_scripts.py::test_backup_script_keeps_newest_14 PASSED
...
tests/test_backup_restore.py::test_round_trip_restore_is_lossless PASSED [100%]
======================= 42 passed, 12 skipped in 16.18s ========================
verify.sh: PASSED (stack left running: --keep)

$ pytest tests/test_ui_*.py      (Playwright image, loop-test stack)
29 passed, 6 skipped in 61.30s

$ backup-service:   python -m pytest tests  → 9 passed
$ document-service: python -m pytest tests  → 239 passed
$ catalogue-service: python -m pytest tests → 80 passed
$ auth-service:     python -m pytest tests  → 93 passed
```
Not rerun after the last two small edits: `--no-deps` in deploy.sh (its test reran: `2 passed`) and `.dockerignore` for backup-service.

## 7. Found but not fixed (out of scope)
- **Off-site copy**: not built (out of scope). docs/BACKUP_RESTORE.md says how to put `./backups/` on a mounted disk.
- **Backup files are root-owned and mode 0644** in `./backups/`. Any local account on the VM can read them, and they contain password hashes and all data. The docs suggest `chmod 700 backups`. Making the engine write 0600 would force the user to use sudo to copy the files off the server.
- **A backup is not one atomic snapshot**: the 3 pg_dumps and the MinIO copy run one after another. A write during a nightly backup could be in one part and missing from another. The pre-restore backup doesn't have this problem (it runs in maintenance mode). The fix would be maintenance mode during manual/nightly backups too, which would block users for that time.
- **`restore.sh` copies an outside file into `backups/` and leaves it there.** Encrypted `.enc` copies don't show in the UI list, because the list shows `*.tar.gz` only.
- **Pre-restore backups are never pruned.** Restores are rare, but the files add up.
- **The UI keeps the pre-restore backup in its list only after a reload.** The page signs out after a restore anyway.
- **backup-service has no `ENVIRONMENT=production` secrets check of its own.** It uses the same compose-level secrets that postgres/minio/nginx are started with, and the other services already refuse placeholders.
- **`docs/decisions.md` §4 is outdated**: it says Alembic is not run, but document-service runs `upgrade head` at startup.
- **The single `DELETE /documents/{id}` and `/companies/{id}` still leave their MinIO files behind** (from iteration 15). Backups faithfully include those orphans.
- **Running Playwright with `UI_SCREENSHOT_DIR` rewrites earlier iterations' evidence PNGs.** I restored them with `git checkout` this time. A per-test filter would avoid it.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- On the VM: run `./scripts/install-backup-cron.sh` once, and use `./scripts/deploy.sh` for updates from now on.
- Decide on an off-site destination (a USB disk, a NAS, or a cloud bucket via rclone) and add the copy step to the cron job.
- Consider maintenance mode (or a short "read-only" mode) during nightly backups if a fully consistent snapshot matters.
- Remove the DEV-ONLY Delete ALL feature before production (iteration 15 §4). Backups now make mistakes recoverable, but that button is still one click with no confirm.

## App
App running at http://localhost:8080 (login: admin / CHANGE_ME, local only). All 9 services are up, including the new `backup-service`; `GET /` → 200, `/api/backup/status` → `idle`, `/api/backup/list` without a token → 401.
