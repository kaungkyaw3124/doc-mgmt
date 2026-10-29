# report.md — Iteration <N>

## 1. Summary
- **Status**: DONE | BLOCKED | NEEDS_DECISION
- **Goal**: <copied from PLAN.md>
- **Ponytail plugin active**: yes / no (check `/plugin` or whether `/ponytail-help` exists in this session)
- **Branch / commit**: `loop/iter-<N>-...` @ `<short sha>`
- **One-paragraph summary**: what was built/changed and whether it works.

## 2. Baseline (before any change)
`./scripts/verify.sh` result before work started (trimmed output):
```
<output>
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | ... | PASS / FAIL | command + key output line |

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `path/to/file` | added / modified / deleted | ... |

## 5. Test → fix log
One row per attempt.
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | ... | ... | ... | ... |

## 6. Final verification output
```
<trimmed output of the final ./scripts/verify.sh run — pytest summary line must be visible>
```

## 7. Found but not fixed (out of scope)
- <bug / risk / smell> — where (`file:line`), how found, suggested fix.

## 8. Blockers / decisions needed
(Only for BLOCKED or NEEDS_DECISION.)
- **Question**: ...
- **Options**: A) ... B) ...
- **My recommendation**: ...
- **What I tried** (for BLOCKED): ...

## 9. Suggestions for the next iteration
- ...
