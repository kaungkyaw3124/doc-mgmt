# Backup & Restore

One engine (`services/backup-service/app/engine.py`) does all of it. The
web page, the nightly cron and the `scripts/*.sh` helpers all call it.

## What is in a backup

`backup-YYYY-MM-DD-HHMM.tar.gz`:

| Path | Content |
|---|---|
| `manifest.json` | created-at, app commit, Alembic revision, row counts per table, object count, sha256 of every file |
| `db/docmgmt.dump`, `db/catalogue.dump`, `db/auth.dump` | `pg_dump -Fc` of the three databases |
| `minio/<bucket>/<key>` | every stored file (logos, seals, MD seals, product files, document files), byte for byte |

**Not included:**
- `.env` files and secrets. Keep those separately.
- The Meilisearch index. It is rebuilt from the database after a restore.
- Login sessions (`refresh_tokens`). A restore signs everyone out.

Backups live in `./backups/` on the server (git-ignored). Retention keeps the
newest **14** `backup-*` files. `pre-restore-*` files are never deleted
automatically.

## Back up

- **Web:** Admin → **Backup & Restore** → **Download backup** (superusers only).
  - If you type a password, the download is encrypted (`.tar.gz.enc`).
  - The server keeps a plain copy in `./backups/`.
  - The list on that page lets you download any server-side backup.
- **Command line:** `./scripts/backup.sh`
- **Nightly:** run `./scripts/install-backup-cron.sh` once on the server.
  - It adds a cron line that runs every night at 02:30. Set `AT="0 3"` for 03:00.
  - The output goes to `backups/cron.log`.
  - To remove it: `crontab -l | grep -v doc-mgmt-nightly-backup | crontab -`
- **Updates:** `./scripts/deploy.sh` runs these steps in order:
  1. `git pull`
  2. **Backup.** The deploy stops if the backup fails.
  3. `up -d --build`
  4. Restart nginx.
  5. Wait for http://localhost:8080.

  It never removes volumes.

## Restore

The restore replaces **all** data.

- **Web:** Admin → Backup & Restore → **Restore from a file**.
  1. Choose the file, and enter the password if it is encrypted.
  2. Click **Check file**.
  3. Read the summary, type `RESTORE`, then click **Restore**.
- **Command line:** `./scripts/restore.sh path/to/backup.tar.gz[.enc]`. It asks for the password and for `RESTORE`.

The server checks the backup before it restores anything. It refuses the backup if any of these is true:
- The archive can't be read.
- `manifest.json` is missing.
- A checksum doesn't match.
- A file is missing from the manifest, or is not listed in it.
- A path would escape the archive.
- The password is wrong.
- The backup's Alembic revision is unknown to this app, which means the backup came from a newer version. Update the app first.
- There is clearly not enough free disk space.

The restore then runs these steps:
1. Turn on maintenance mode. The API and file downloads return 503, except `/api/backup/status`. The UI pages still load.
2. Save a **pre-restore backup** (`pre-restore-*.tar.gz`). This runs inside maintenance mode, so no change can slip in between this copy and the restore.
3. Restore each database. Each one runs in **one transaction** together with the `DROP SCHEMA` that empties it first. A failure rolls that database back to how it was.
4. Restore the MinIO buckets. It uploads first, then deletes objects that are not in the backup.
5. Run migrations to head (document-service) and rebuild both search indexes.
6. Turn off maintenance mode. This always happens, including after a failure.

**If a restore fails:** the message names the step that failed and the pre-restore backup. To roll back, restore that file with the same page or with `./scripts/restore.sh backups/pre-restore-....tar.gz`. Nothing rolls back automatically, because the databases that were already restored have already been committed.

Only one backup or restore can run at a time. A second one gets 409.

## Decrypt by hand

The tool is `openssl enc`, AES-256-CBC, PBKDF2 with 200000 iterations and a salt:

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in backup-2026-11-17-0230.tar.gz.enc -out backup.tar.gz
tar tzf backup.tar.gz | head      # manifest.json, db/..., minio/...
```

## Restore on a fresh server

1. Clone the repo and create the `.env` files. They are not in the backup, so use your saved copies.
2. `docker compose -f infra/docker-compose.yml up -d --build`, then `restart nginx`.
3. Sign in as the seeded admin. Open Admin → Backup & Restore and restore the file. You can also use `./scripts/restore.sh <file>`.
4. Sign in again with the users and passwords **from the backup**.

## Off-site copies

This is not built in yet. Mount another disk (or a network share) on `./backups/`, or point a symlink there, and the nightly files land on it. Copy them off the server with rsync/rclone from cron if you need to.

Backups contain everything, including user password hashes. Keep `./backups/` readable only by the people who administer the server: `chmod 700 backups`.
