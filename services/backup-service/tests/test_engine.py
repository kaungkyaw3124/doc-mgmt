"""Pure engine logic, no database or MinIO needed:
docker compose exec backup-service sh -c 'pip install -q -r requirements-dev.txt && python -m pytest tests -q'"""
import os

import pytest

from app import engine


def test_retention_keeps_newest_14_and_never_pre_restore(tmp_path):
    names = [f"backup-2026-10-{d:02d}-0230.tar.gz" for d in range(1, 21)]
    for n in names + ["pre-restore-2026-10-01-0900.tar.gz", "notes.txt"]:
        (tmp_path / n).write_bytes(b"x")
    removed = engine.prune(14, str(tmp_path))
    assert removed == names[:6]
    left = sorted(os.listdir(tmp_path))
    assert [n for n in left if n.startswith("backup-")] == names[6:]
    assert "pre-restore-2026-10-01-0900.tar.gz" in left and "notes.txt" in left


@pytest.mark.parametrize("rel", ["../x", "/etc/passwd", "a/../b", "a//b", "a\\b", "./a"])
def test_unsafe_paths_refused(rel):
    with pytest.raises(engine.BackupError):
        engine.safe_rel(rel)


def test_backup_path_only_accepts_existing_bare_names(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "BACKUP_DIR", str(tmp_path))
    (tmp_path / "backup-1.tar.gz").write_bytes(b"x")
    assert engine.backup_path("backup-1.tar.gz").endswith("backup-1.tar.gz")
    for bad in ("../backup-1.tar.gz", ".lock", "missing.tar.gz", "backup-1.tar"):
        with pytest.raises(engine.BackupError):
            engine.backup_path(bad)


def test_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "BACKUP_DIR", str(tmp_path))
    fd = engine.acquire_lock()
    with pytest.raises(engine.BackupError, match="already running"):
        engine.acquire_lock()
    engine.release_lock(fd)
    engine.release_lock(engine.acquire_lock())


def test_startup_cleanup_leaves_a_running_cli_job_alone(tmp_path, monkeypatch):
    # Regression: backup.sh exec'd right after the container started, and the
    # startup cleanup deleted the running backup's work folder.
    from app import main

    monkeypatch.setattr(engine, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "MAINTENANCE_FLAG", str(tmp_path / "maint" / "on"))
    live = tmp_path / ".tmp" / "job" / "db"
    live.mkdir(parents=True)
    engine.maintenance(True)
    fd = engine.acquire_lock()  # the CLI job
    try:
        main.on_startup()
        assert live.is_dir() and os.path.exists(engine.MAINTENANCE_FLAG)
    finally:
        engine.release_lock(fd)
    main.on_startup()  # nothing running: crash leftovers are cleared
    assert not live.exists() and (tmp_path / ".tmp").is_dir()
    assert not os.path.exists(engine.MAINTENANCE_FLAG)
