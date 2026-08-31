# Coding Standards

These are patterns *observed consistently* across the codebase, not a
written style guide (none exists in the repo — no `CONTRIBUTING.md`,
`.editorconfig`, linter config, or formatter config was found). Follow them
when adding to this codebase so new code doesn't stand out.

## Backend (Python / FastAPI)

- **Python 3.12**, FastAPI 0.115, SQLAlchemy 2.0 in **synchronous** mode —
  every route handler is `def`, never `async def`, confirmed by a
  repository-wide search (zero `async def` in any router). FastAPI runs
  sync handlers in a threadpool; do not mix in `async def` handlers that
  call the sync ORM session directly, and do not assume request handling
  is non-blocking.
- **Routers call the ORM directly** — there is no repository or service
  class layer. A router function does validation, the `db.query(...)`
  calls, and response shaping all in one place. Cross-cutting logic (auth
  checks, search indexing, storage, cross-service HTTP calls) is factored
  into `app/core/*.py` modules and called from routers, not wrapped in a
  class.
- **Dependency injection via FastAPI `Depends`** for everything
  request-scoped: `db: Session = Depends(get_db)`, `current_user:
  models.User = Depends(get_current_user)`, `_current_user: models.User =
  Depends(require_superuser)`. Follow this pattern rather than reading
  headers/globals ad hoc.
- **UUID primary keys everywhere** (`Column(UUID(as_uuid=True),
  primary_key=True, default=uuid.uuid4)`) — never an auto-increment
  integer PK.
- **Soft delete is a first-class, repeated pattern**: an `is_deleted`
  boolean column (indexed), paired with `PATCH .../{id}/trash`, `PATCH
  .../{id}/restore`, and a genuinely destructive `DELETE .../{id}` used
  only from a "recycle bin" UI. New deletable resources should follow
  this exact shape rather than inventing a new one.
- **Unique-constraint races are handled by catching `IntegrityError`**,
  not by a pre-check-only approach: check-then-insert, but always wrap the
  `db.commit()` in `try/except IntegrityError: db.rollback(); raise
  HTTPException(409, ...)`. This is applied consistently after commit
  `bdf21bf` closed several gaps where it was missing — replicate it on any
  new unique field.
- **Cross-service references are plain UUIDs/strings, never foreign
  keys** — `project_id` on auth-service's access-grant tables, `product_id`
  on `line_items`, `category` on `products`. If you add a new cross-service
  reference, follow this same loosely-coupled convention (see
  [decisions.md](decisions.md)) rather than trying to add a cross-database
  FK (which Postgres cannot do across separate databases anyway).
- **Explicit fail-open vs. fail-closed choices, always commented.** When a
  cross-service HTTP call can't complete: project-visibility resolution
  fails **closed** (denies/returns empty — a dependency hiccup must never
  silently over-expose data); enrichment lookups (e.g. "get this
  product's name for a line item description") fail **open** (return
  `None`/`[]`, don't block the primary operation). Pick deliberately and
  say so in a comment when adding a new cross-service call — don't default
  to one without thinking about which failure mode is safer for that
  specific call.
- **Validation**: Pydantic models in `schemas.py` (document-service,
  catalogue-service) define the wire contract; enum-like fields
  (`doc_type`? — actually *not* validated; `status`, `currency`,
  `service_name`, `access_level`) are checked against a module-level
  `VALID_X = {...}` set/frozenset inside the router, returning `422` with
  the allowed values in the message, rather than a Pydantic `Literal` type
  — follow this existing convention for new constrained-string fields.
- **Filenames from uploads are always sanitized** before use in a storage
  object key (`_sanitize_filename`, strips directory components and
  non-safe characters) — never interpolate a raw client filename into a
  path.
- **User-controlled strings that reach Excel or HTML output are escaped
  defensively**: `_safe_str()` in `export_quotation.py` neutralizes
  spreadsheet formula injection; Jinja2's `autoescape=True` is explicitly
  set in `export_pdf.py` for the same reason on the PDF path. Any new
  export/template code that embeds user data must do the same.
- **Comment style**: comments explain *why*, not *what* — a deliberately
  heavy, narrative style is used throughout this codebase (e.g. explaining
  a security fix's rationale, a past incident, or a non-obvious trade-off)
  rather than restating the code. This is consistent enough across the
  whole repo, including in git commit bodies co-authored by "Claude Sonnet
  5", to be a deliberate convention — match it rather than writing terse
  or purely descriptive comments.
- **Errors**: raise `fastapi.HTTPException` directly in routers with a
  human-readable `detail` string; there is no centralized exception
  handler or custom exception hierarchy mapped to HTTP codes.

## Frontend (`web/index.html`)

- **One file, no build step.** There is no bundler, transpiler, or package
  manager — everything is plain HTML/CSS/JS loaded directly by the
  browser. New frontend work should stay consistent with this (no
  introducing a framework or build pipeline without a deliberate,
  discussed architectural change).
- **No JS modules** — everything is in one global `<script>` block;
  functions and a handful of `let`/`const` state variables
  (`cachedDocuments`, `cachedProjects`, `editingDocumentId`,
  `currentUserIsSuperuser`, etc.) live at top level.
- **`apiFetch()` is the only sanctioned way to call the API** — it
  attaches the bearer token, handles `401` by force-logout, and formats
  FastAPI/Pydantic error bodies into readable text. Don't call `fetch()`
  directly against `/api/...` except for the two pre-login calls
  (`/auth/login`, `/auth/register`, `/auth/groups-public`) that
  legitimately have no token yet.
- **Stale-response guarding**: any list that can be reloaded faster than
  its previous request resolves must use the `loadSeq`/`startLoad`/
  `isStaleLoad` pattern (`web/index.html:1479-1486`) to discard an
  out-of-order response — added after this exact bug was observed and
  fixed in commit `bdf21bf`. Follow the same pattern for any new
  reloadable list.
- **HTML injection**: any value interpolated into `innerHTML` from API
  data must go through `escapeHtml()` first — used consistently
  throughout the render functions.
- **No client-side router/URL state** — navigation is just toggling
  `.active` on `.nav-item`/`.view` pairs; a page refresh always returns to
  the Documents view regardless of what was open.

## Naming

- **snake_case** for Python (variables, functions, DB columns), including
  Pydantic fields (no camelCase aliasing).
- **camelCase** for JS variables/functions (`loadDocuments`,
  `renderProductsTable`), matching the JS payloads' own snake_case field
  names being read directly (e.g. `doc.doc_number`) — no case translation
  layer between the API and the frontend.
- Router files are named after the resource they own
  (`documents.py`, `products.py`); `APIRouter(prefix="/resource",
  tags=["resource"])` set once at the top of the file.
