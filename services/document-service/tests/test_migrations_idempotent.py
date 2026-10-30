"""
Regression test for the "Documents API 500s" incident: migration
0001_initial used to call op.create_table/op.create_index
unconditionally, which meant `alembic upgrade head` could never safely
run against an already-existing deployment (one whose tables were only
ever created by create_all(), never stamped) — it would fail on the
very first statement with "relation already exists", so a later,
legitimate migration (0003, adding Document.deleted_by/deleted_at)
never got a chance to run at all. That's what caused GET /documents and
GET /documents/trash to start 500ing. See
docs/SECURITY_HARDENING_LOG.md's "Documents API 500s" entry.

No live Postgres needed — this is a source-level guard against ever
reintroducing an unconditional op.create_table/op.create_index call in
this migration, the same way the file's own helpers
(_create_table_if_missing/_create_index_if_missing) are meant to be
used everywhere.
"""
import ast
import pathlib

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _upgrade_function_source(filename: str) -> str:
    path = _MIGRATIONS_DIR / filename
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return ast.unparse(node)
    raise AssertionError(f"no upgrade() function found in {filename}")


def test_0001_initial_never_calls_create_table_unconditionally():
    source = _upgrade_function_source("0001_initial.py")
    assert "op.create_table(" not in source, (
        "0001_initial.upgrade() calls op.create_table directly — it must go "
        "through _create_table_if_missing so alembic upgrade head stays safe "
        "to run against an already-existing (but never-stamped) deployment"
    )


def test_0001_initial_never_calls_create_index_unconditionally():
    source = _upgrade_function_source("0001_initial.py")
    assert "op.create_index(" not in source, (
        "0001_initial.upgrade() calls op.create_index directly — it must go "
        "through _create_index_if_missing for the same reason as create_table"
    )


def test_0001_initial_upgrade_uses_the_guard_helpers():
    source = _upgrade_function_source("0001_initial.py")
    assert "_create_table_if_missing(" in source
    assert "_create_index_if_missing(" in source
