"""Nightly cron installer, retention through scripts/backup.sh, and the
pre-update backup in scripts/deploy.sh (loop iteration 16)."""
import os
import re
import subprocess

from conftest import destructive

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")


def test_cron_installer_prints_and_installs_one_idempotent_line(tmp_path):
    # A fake `crontab` that keeps the table in a file: the real one is never touched.
    store = tmp_path / "crontab.txt"
    store.write_text("0 5 * * * /usr/bin/something-else\n")
    fake = tmp_path / "crontab"
    fake.write_text(f'#!/bin/sh\nif [ "$1" = "-l" ]; then cat "{store}"; else cat > "{store}"; fi\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    for _ in range(2):
        out = subprocess.run([os.path.join(SCRIPTS, "install-backup-cron.sh")], env=env,
                             capture_output=True, text=True, check=True).stdout
        assert "scripts/backup.sh" in out
    lines = store.read_text().splitlines()
    assert "0 5 * * * /usr/bin/something-else" in lines
    ours = [line for line in lines if "doc-mgmt-nightly-backup" in line]
    assert len(ours) == 1 and ours[0].startswith("30 2 * * * cd ") and "./scripts/backup.sh" in ours[0]


def test_deploy_backs_up_before_rebuilding_and_never_removes_volumes():
    text = open(os.path.join(SCRIPTS, "deploy.sh")).read()
    code = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    pull = next(i for i, line in enumerate(code) if line.startswith("git pull"))
    backup = code.index("./scripts/backup.sh")
    rebuild = code.index('"${COMPOSE[@]}" up -d --build')
    assert pull < backup < rebuild
    assert not re.search(r"\bdown\b|volume rm|prune", "\n".join(code))


@destructive
def test_backup_script_keeps_newest_14():
    from test_backup_restore import dc  # same compose target as the other stack tests

    fakes = " ".join(f"/backups/backup-2000-01-{d:02d}-0230.tar.gz" for d in range(1, 21))
    assert dc("exec", "-T", "backup-service", "sh", "-c", f"touch {fakes}").returncode == 0
    ls = lambda: dc("exec", "-T", "backup-service", "sh", "-c", "ls /backups").stdout.split()
    before = [n for n in ls() if n.startswith("backup-")]
    run = subprocess.run([os.path.join(SCRIPTS, "backup.sh")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    new = re.search(r"created (\S+?);? ", run.stdout + " ").group(1)
    kept = sorted(n for n in ls() if n.startswith("backup-"))
    assert kept == sorted(before + [new])[-14:], run.stdout
    assert new in kept
