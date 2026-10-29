# LOOP.md — Loop Engineering Protocol for Claude Code

You (Claude Code) are the **executor** in a plan → build → test → fix loop.
A separate planner writes `loop/PLAN.md`. You implement it, prove it works
with tests, and hand back `loop/report.md`. The planner reads your report
and writes the next `PLAN.md`.

```
 Planner ──writes──▶ loop/PLAN.md ──▶ Claude Code
    ▲                                     │
    │                          implement → test → fix → test …
    │                                     │   (until ALL acceptance checks pass
    └────────reads──── loop/report.md ◀───┘    or the attempt limit is hit)
```

---

## Kickoff prompt (paste this into Claude Code each iteration)

> Read `loop/LOOP.md` and follow it exactly. Then read `loop/PLAN.md` and
> execute the current iteration. Loop implement → test → fix until every
> acceptance check passes or you hit the attempt limit. When done, write
> `loop/report.md` using `loop/REPORT_TEMPLATE.md`, and commit.

---

## Rules (follow every iteration)

### 1. Before you start
1. Read `loop/PLAN.md` fully. Read the docs it links (in `docs/`) before
   touching code — `docs/coding-standards.md` always applies.
2. If a previous `loop/report.md` exists, move it to
   `loop/history/report-iter-<N>.md` (N = the iteration number inside it).
3. Branch: work on whichever branch the session opened on (`dev` or
   `security/auth-hardening` — they are kept identical). `git pull` first.
   Do not create new branches.
4. Run the full verification once **before changing anything** and record
   the baseline result (what passes/fails already). This stops you from
   "fixing" things that were already broken or blaming yourself for them.

### 2. Implement
- Do only what the iteration's **Tasks** list says. No drive-by refactors,
  no extra features. If you see something worth fixing that is out of
  scope, write it under "Found but not fixed" in the report.
- Keep changes small and in the existing style (FastAPI routers call the
  ORM directly; vanilla JS in `web/index.html`; no new frameworks unless
  the plan says so).
- Schema changes: this project does **not** run Alembic at startup
  (see `docs/decisions.md`). Follow whatever the plan says about migrations;
  if the plan is silent, stop and record it as a blocker rather than guess.

### 3. Test → fix loop
- The single source of truth is: **`./scripts/verify.sh` exits 0** plus
  every acceptance check in PLAN.md marked PASS.
- Loop:
  1. Run verification.
  2. If anything fails: read the actual error/log
     (`docker compose -f infra/docker-compose.yml logs <service> --tail 100`),
     find the root cause, fix it, re-run. One root cause per attempt.
  3. Count attempts. **Attempt limit = 5** full fail→fix cycles on the same
     failing check (unless PLAN.md sets another limit).
- **Never** make a test pass by weakening it, deleting it, skipping it,
  or hard-coding the expected value. If you believe a test itself is wrong,
  leave it failing and explain why in the report.
- Every new behaviour gets at least one automated test in `tests/`.
  Every bug you fix gets a regression test.

### 4. When to stop
Stop and write the report when **one** of these is true:
- ✅ All acceptance checks PASS and `verify.sh` exits 0 → status `DONE`.
- ⛔ Attempt limit hit on any check → status `BLOCKED` (explain what you
  tried, the exact error, and your best hypothesis).
- ❓ The plan is ambiguous or needs a decision that changes behaviour,
  data, or security (e.g. deleting data, changing auth rules) → status
  `NEEDS_DECISION`. Do the safe preparatory work, then stop and ask in
  the report. Do not guess on irreversible things.

### 5. Finish
1. Write `loop/report.md` from `loop/REPORT_TEMPLATE.md`. Be factual:
   paste real command output (trimmed), not summaries like "tests pass".
2. Commit with message `loop iter <N>: <short summary> [<STATUS>]` and push
   the current branch. Never merge into `main` — the user does that.
3. Leave the Docker stack in a clean state
   (`docker compose -f infra/docker-compose.yml down` unless the plan says
   keep it running).

---

## Safety rails
- Never commit real secrets. Use `.env.example` values / test-only values.
- Never run `scripts/clear_data.py` or drop volumes against anything but
  the local test stack.
- Never disable the auth gateway, RBAC checks, or TLS verification to make
  a test pass.
