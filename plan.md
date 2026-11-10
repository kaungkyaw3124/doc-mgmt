# Execution Plan — Claude Code /next Workflow

## Goal

Use this repository as the shared handoff point between ChatGPT (planning/review) and Claude Code on the VPS (implementation/testing).

Claude Code is the executor. It must work only on explicitly defined tasks, verify changes with tests, and record evidence in `report.md`. ChatGPT reviews the report and updates this plan.

## Operating rules

1. Never invent requirements. If a task is ambiguous or blocked, stop and record the exact question/blocker in `report.md`.
2. Execute one task at a time, in the listed order, unless dependencies explicitly allow otherwise.
3. Before editing, inspect the relevant code and existing project conventions.
4. Do not overwrite user changes. Check `git status` before starting and before committing.
5. Run relevant tests, linting, type checks, and build checks available in the repository.
6. A task is **DONE** only when its acceptance criteria pass and test evidence is recorded.
7. If a test fails, diagnose and fix it when within scope. Do not hide, skip, or weaken tests to claim success.
8. Do not perform destructive database operations, production deployments, force pushes, or secret rotations without explicit human approval.
9. Do not commit secrets, credentials, local environment files, generated dependencies, or unrelated changes.
10. Do not run multiple `/next` workers against the same checkout concurrently.

## Task status definitions

- **TODO** — not started.
- **IN_PROGRESS** — currently being worked on.
- **BLOCKED** — cannot safely proceed; explain why in the report.
- **DONE** — acceptance criteria met and evidence recorded.
- **NEEDS_REVIEW** — implementation is ready but requires human/ChatGPT review.

---

## Phase 1 — Prepare the VPS checkout

### TASK-001 — Clone and verify the repository
**Status:** TODO

**Actions**
1. On the VPS, inspect `~/doc-mgmt` before cloning.
2. If it is empty, clone this repository into that directory:
   `git clone https://github.com/kaungkyaw3124/doc-mgmt.git .`
3. If it is not empty or is already a Git checkout, inspect it; do not delete or overwrite anything.
4. Verify `git remote -v`, current branch, and `git status`.
5. Confirm the working tree is clean or document any pre-existing changes.

**Acceptance criteria**
- The VPS checkout points to `https://github.com/kaungkyaw3124/doc-mgmt.git`.
- The expected branch is checked out.
- The working tree state is documented.
- No existing user data was overwritten.

**Human action required:** Run the clone/verification commands on the VPS and confirm the output before proceeding.

### TASK-002 — Verify Git write access from the VPS
**Status:** TODO

**Actions**
1. Check whether Git authentication is already configured.
2. Verify that the VPS can fetch from GitHub.
3. Verify push access without creating a meaningless commit; use a safe test branch only if explicitly approved.
4. Never print or put tokens/private keys in reports.

**Acceptance criteria**
- Fetch works.
- Push authentication method is documented without exposing secrets.
- Any required authentication setup is clearly reported.

---

## Phase 2 — Install the Claude Code command

### TASK-003 — Create the `/next` command
**Status:** TODO

**Actions**
1. Inspect the installed Claude Code version and existing project-level command conventions.
2. Create `.claude/commands/next.md` using the syntax supported by the installed version. If the installed version uses a different supported command location, use that and document the reason.
3. The command must read `plan.md` and `report.md` before acting.
4. Select the first eligible TODO task whose dependencies are DONE.
5. Mark the selected task IN_PROGRESS in the report before implementation.
6. Implement only that task; run the required checks; fix in-scope failures.
7. Update the report with changed files, commands run, test results, commit SHA (if committed), and remaining blockers.
8. Update task status only when the status definitions above are satisfied.

**Acceptance criteria**
- Claude Code recognizes `/next`.
- Running it selects at most one eligible task.
- It does not repeat a DONE task.
- It records task progress and test evidence in `report.md`.
- If no eligible task exists, it reports that clearly and makes no unrelated changes.

### TASK-004 — Define report format
**Status:** TODO

**Actions**
Create `report.md` with a durable execution log containing:
- Task ID and date/time
- Status
- Summary of implementation
- Files changed
- Tests/checks run and exact results
- Commit SHA, if any
- Blockers, risks, and follow-up items

**Acceptance criteria**
- Reports are append-only by execution entry; preserve previous history.
- Claims of success include test evidence.
- Failed checks and unresolved issues remain visible.

---

## Phase 3 — Validate the workflow

### TASK-005 — Run a controlled end-to-end test
**Status:** TODO

**Actions**
1. Add a small, harmless workflow-validation task only after the command and report format are ready.
2. Run `/next` once.
3. Confirm it reads the plan, selects only the validation task, performs the work, runs checks, and writes a report.
4. Review the resulting diff and report before accepting the workflow.

**Acceptance criteria**
- The task lifecycle is visible from TODO to DONE or NEEDS_REVIEW.
- Test evidence is accurate and reproducible.
- No unrelated files are changed.
- The report is readable by ChatGPT through GitHub.

---

## Phase 4 — Normal project operation

### TASK-006 — Establish the recurring planning/review loop
**Status:** TODO

**Actions**
1. ChatGPT adds or refines scoped tasks with dependencies and acceptance criteria.
2. Claude Code executes one task per `/next` invocation.
3. Claude Code records evidence and commits completed work when safe and appropriate.
4. ChatGPT reviews the report and relevant diffs before adding the next batch of tasks.

**Acceptance criteria**
- Every task has a unique ID, scope, dependencies, and acceptance criteria.
- Every execution has a corresponding report entry.
- Human approval gates are respected.

---

## Current next action

Complete **TASK-001** on the VPS first. Do not start implementation tasks until the repository checkout is verified.
