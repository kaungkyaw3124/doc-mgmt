---
description: Run the next loop iteration from loop/PLAN.md
---
Step 0 — sync (do this yourself, never ask the user to run git):
1. `git fetch origin`. If `git status --porcelain` shows uncommitted changes you did not make, stop and report them instead of touching anything.
2. If the current branch is not `dev` or `security/auth-hardening`, run `git checkout dev`.
3. `git pull origin <current branch>`. If `.claude/commands/next.md` changed in that pull, re-read it and follow the new version.

Then load the `ponytail` skill (Skill tool) and follow it for the whole iteration.
Then read `loop/LOOP.md` and follow it exactly. Then read `loop/PLAN.md` and execute the current iteration. Loop implement → test → fix until every acceptance check passes or you hit the attempt limit. When done, write `loop/report.md` using `loop/REPORT_TEMPLATE.md`, commit, and push the current branch. If the plan says "WAITING FOR PLANNER", change nothing and reply "No new plan yet — waiting for the planner."
