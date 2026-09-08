# Security Hardening Project Report

Branch: `security/auth-hardening`. This file is the current-status
summary of the overall security-hardening project; `docs/
SECURITY_HARDENING_LOG.md` is the detailed, chronological development
log (full goals, acceptance criteria, bugs found/fixed, and evidence
for every task). This file is updated as each task completes; it does
not replace the log.

| Task | Goal | Status | Commit |
|---|---|---|---|
| 1 | Login rate limiting | PASS | `bc3610f` |
| 2 | Remove insecure default secrets | PASS | `59d09ce` |
| 3 | Remove host exposure of internal datastores | PASS | `02957ee` (fix: `db0c219`) |
| 4 | Authenticate gateway trust headers | PASS | `3d3f014` (fix: `12b1da0`) |
| 5 | Secure uploaded Content-Type | PASS | `c21f72c` (this pass adds content-sniffing + an Nginx Host-header fix — see below) |
| 6 | JWT storage and rotation | implemented, CI not yet re-verified | `72a9af5` |
| 7 | CORS policy | not started | - |
| 8 | Security headers / Nginx hardening | not started | - |

## Task 5 — Secure Uploaded Content-Type

**Status: PASS**

### Original problem

Two distinct, related risks:

1. **Content-Type spoofing on serve**: if the client-supplied
   `Content-Type` (or the raw filename extension, unchecked) were ever
   trusted and stored, a presigned download URL could later serve
   attacker-controlled content as e.g. `text/html`, letting a browser
   execute embedded `<script>` from what looks like a normal document
   link (`docs/known-issues.md` finding #5).
2. **Content/extension mismatch on upload** (the gap this pass closes):
   even with Content-Type correctly derived from the filename
   server-side, nothing was reading the file's actual bytes — so real
   HTML, a script, or an executable could be uploaded under any
   allowed extension and stored/served as if it were that type. Not
   executable-as-HTML (risk 1 already prevented that), but still not
   what "prevent attacker-controlled type mismatches" requires.

### Files changed (this pass)

- `services/document-service/app/core/upload_safety.py`
- `services/catalogue-service/app/core/upload_safety.py`
- `services/document-service/app/routers/documents.py`
- `services/catalogue-service/app/routers/products.py`
- `services/document-service/tests/test_upload_safety.py`
- `services/catalogue-service/tests/test_upload_safety.py`
- `infra/nginx/nginx.conf.template`
- `.github/workflows/tests.yml`
- `docs/SECURITY_HARDENING_LOG.md`
- `docs/SECURITY_PROJECT_REPORT.md` (this file)

### Upload paths inspected (entire repository)

- `document-service`: `POST /documents/{id}/file` (`documents.py`) —
  only file-upload endpoint in the service. `GET /{id}/file-url` is the
  only download/serve path.
- `catalogue-service`: `POST /products/{id}/file` (`products.py`) —
  the interactively-uploaded product image/spec-sheet path.
  `POST /products/bulk-import` also writes files extracted from an
  uploaded `.zip`/`.rar` archive (`upload_file(..., content_type=
  "application/octet-stream")`, `products.py` line ~320) — inspected
  and confirmed already safe on its own terms: it always stores
  extracted entries as `application/octet-stream` regardless of
  filename, so it was never subject to the label-trusting bug this
  task targets, and is intentionally out of scope for further changes.
  `GET /{id}/file-url` is the only download/serve path.
- `services/*/app/core/storage.py` (both services): `upload_file()`
  and `get_presigned_url()` — the only two functions that talk to
  MinIO for these paths. No other upload/download code exists in
  either service.
- `infra/nginx/nginx.conf.template`: `/documents/` and `/products/`
  locations proxy presigned download URLs straight through to MinIO —
  inspected and found to have a real, pre-existing bug (see "Bugs
  found" below), fixed in this pass.

### Security design implemented

1. **Content-Type derivation (pre-existing, re-verified)**: never the
   client's header — always `safe_content_type(filename)`, a fixed
   extension allowlist; unrecognized extensions get
   `application/octet-stream`.
2. **Force-download (pre-existing, re-verified)**: a fixed set of
   browser-active-content extensions (`svg`, `html`, `xml`, `js`, ...)
   always get `Content-Disposition: attachment` on download, regardless
   of their derived Content-Type.
3. **Content/extension mismatch rejection (new this pass)**:
   `content_matches_extension(filename, head_bytes)` sniffs the file's
   leading bytes against known binary signatures (PDF, PNG, JPEG, GIF,
   BMP, WEBP, ZIP/Office-OOXML, legacy OLE Office, RAR, 7z) and against
   executable signatures (Windows PE, ELF, Mach-O, `#!` shebang
   scripts). An executable signature is rejected unconditionally. For
   extensions this app accepts with a well-defined binary format, the
   detected content kind must match what the extension claims, or the
   upload is rejected with `400` **before** anything is written to
   storage. Extensions with no reliable signature to check (`txt`,
   `csv`, unrecognized extensions, and the already-force-downloaded
   active-content extensions) are accepted without a content check —
   there is nothing meaningful to sniff, and they're never served as
   anything renderable/executable regardless.
4. **Filename handling (pre-existing, re-verified)**:
   `_sanitize_filename()` strips directory components
   (`os.path.basename`) and collapses everything outside
   `[A-Za-z0-9._-]` to `_` — defeats path traversal and neutralizes
   Unicode/control-character filenames without rejecting them outright.

### Supported file types

`pdf`, `doc`/`docx`, `xls`/`xlsx`, `ppt`/`pptx`, `png`, `jpg`/`jpeg`,
`gif`, `bmp`, `webp`, `txt`, `csv`, `zip`, `rar`, `7z` — all get a
specific derived Content-Type. `svg`, `svgz`, `html`, `htm`, `xhtml`,
`shtml`, `mhtml`, `xml`, `js`, `mjs` are accepted but always
force-downloaded, never rendered inline, regardless of content or
declared type. Any other extension is accepted and stored as an opaque
`application/octet-stream` download.

### Tests executed and exact results

- **Unit tests** (`test_upload_safety.py`, identical structure per
  service, 21 tests each): pure-logic tests of `safe_content_type`,
  `should_force_download`, `detect_content_kind`, and
  `content_matches_extension`. `pytest` cannot be installed in this
  sandbox (no package-registry network access), so each module was
  imported directly and every `test_*` function actually executed
  against the real code (a small local shim reproduced
  `pytest.mark.parametrize`'s expansion — the assertions themselves ran
  unmodified). **Result: 21/21 pass, both services.**
- **`python3 -m py_compile`** on every changed `.py` file: no syntax
  errors.
- **YAML validation** (`yaml.safe_load`) on the rewritten
  `.github/workflows/tests.yml`: valid.
- **CI end-to-end** (GitHub Actions — the real, only place this
  environment can boot the actual Docker/MinIO/Nginx stack): the
  `infra-integration` job's "Acceptance — uploaded file Content-Type is
  derived server-side, not trusted from the client" step now runs 9
  distinct real-HTTP scenarios against a live stack — see the log entry
  in `SECURITY_HARDENING_LOG.md` for the full list. Verified via
  `mcp__github__get_job_logs` with `return_content: true` (raw log
  content, never the `conclusion` field alone).

### Bugs found and fixed

1. **Nginx presigned-URL signature bug (pre-existing, not introduced by
   this task)**: `MINIO_PUBLIC_ENDPOINT=localhost:8080` includes a
   port, which boto3 signs into the presigned URL's SigV4 signature via
   the `Host` header. Nginx's `$host` variable strips the port before
   forwarding, so MinIO recomputed a different signature and rejected
   every real signed download with `403`. No earlier CI check ever
   attempted a genuinely signed round trip (Task 3's existing presigned-
   URL check deliberately uses an *unsigned* URL, only proving
   connectivity). Fixed by forwarding `$http_host` instead of `$host`
   in the `/documents/` and `/products/` Nginx locations.
2. No bugs found in the new content-sniffing logic itself — validated
   against local unit tests before wiring into the routers, and the
   CI scenarios built to exercise it all pass.

### Security verification

- Real HTML content named `evil.pdf` (with a spoofed
  `Content-Type: text/html` on top) → **rejected**, `400`, never
  stored (`file-url` for that product returns `404` afterward).
- Non-image garbage named `evil.png` → **rejected**, `400`.
- A real JPEG renamed `.png` → **rejected**, `400` (cross-format
  mismatch, not just "not an image at all").
- An ELF binary named `evil.jpg` → **rejected**, `400` (executable
  disguised as an image).
- Path-traversal filename (`../../../etc/passwd.pdf`) with legitimate
  PDF content → accepted, object key sanitized, no `..` present.
- Unicode filename with legitimate PDF content → accepted.
- Double extension (`invoice.pdf.exe`) with legitimate PDF content →
  accepted (final extension `.exe` governs — unrecognized, so served
  as opaque `application/octet-stream`, never `application/pdf`).
- SVG with an embedded `<script>` → accepted (no binary signature to
  check), served with `Content-Disposition: attachment`.
- A genuinely legitimate PDF → accepted, and — for the first time in
  this project's CI — actually downloaded back successfully through a
  real, correctly-verified presigned MinIO signature (`200`,
  `Content-Type: application/pdf`), not just checked for a non-502
  connectivity response.

### Regression results

- Tasks 1–4: no code in their scope was touched this pass except the
  Nginx `Host` header fix, which is isolated to the `/documents/` and
  `/products/` MinIO-proxy locations — unrelated to Task 4's
  `auth_request`/trust-header locations. Their own CI jobs are
  unaffected by this change.
- Existing upload functionality: legitimate uploads of every supported
  type continue to work (see "Tests executed" and "Security
  verification" above).
- MinIO integration: now verified more strongly than before this pass
  — a real signed download succeeding end-to-end was not previously
  exercised anywhere in this project's CI.

### Documentation updated

- `docs/SECURITY_HARDENING_LOG.md`: full Task 5 entry added (goal,
  acceptance criteria, critical re-evaluation, what changed, bugs
  found/fixed, testing performed, verification, scope notes), and the
  status table updated from `NOT STARTED` to `PASS`.
- `docs/SECURITY_PROJECT_REPORT.md`: this file, newly created.

### Commit / push / branch status

Committed on `security/auth-hardening` as a single dedicated commit for
this Task 5 pass (code + tests + CI workflow + both docs together), and
pushed to `origin/security/auth-hardening`. No PR opened, no merge to
`main` — per this project's standing branch policy. The exact commit
SHA and the CI run/job IDs pulled as evidence are in the chat FINAL
REPORT for this task (not duplicated here to avoid this file going
stale the moment a later commit lands on the branch).

### Next task

Per the current scope instruction, Task 5 was the only task worked on
this pass. Task 6 (JWT storage and rotation) is implemented
(`72a9af5`) but its CI result has not yet been re-verified in this
pass — that is the next task once directed to resume the broader
chain, followed by Task 7 (CORS policy), Task 8 (security headers /
Nginx hardening), and the final full security audit.
