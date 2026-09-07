# Architectural Decisions

This project has no formal ADR (Architecture Decision Record) directory.
This document reconstructs the significant decisions from code comments, a
historical `Project status.md` file (present in the initial commit, removed
by the time of the current `main` — recovered via `git show
3dc26f7:"Project status.md"`), and git commit messages. Each entry states
the decision, the evidence for it, and — where inferable — the reasoning.

## 1. Database-per-service, no cross-database foreign keys

**Decision**: Each service (auth, catalogue, document) owns its own
Postgres database. Any reference to another service's data (a `project_id`
stored in auth-service's access-grant tables, a `product_id` on a document's
line item, a `category` string on a product) is a bare UUID/string with no
foreign-key constraint, resolved at request time via an HTTP call to the
owning service.

**Evidence**: `services/auth-service/app/models.py:88-96` (docstring on
`RoleProjectAccess` — "not an enforced foreign key here, same
loose-coupling pattern as `RoleAccess.service_name` being a plain string");
`services/catalogue-service/app/core/document_client.py`,
`services/document-service/app/core/catalogue_client.py` (the HTTP
client/adapter pattern this requires).

**Why**: keeps each service's schema and deployment independently
manageable — a genuine microservices boundary, not a shared monolith
database split across processes. **Trade-off, explicitly accepted**:
deleting a Project in document-service leaves orphaned access-grant rows in
auth-service, and deleting a Product leaves `line_items.product_id`
pointing at nothing — there is no cross-service cascade or cleanup job (see
[known-issues.md](known-issues.md)).

## 2. Whole-service RBAC first, opt-in per-project visibility added later

**Decision, as originally built** (per `Project status.md`, July 13 snapshot):
access was granted per *whole service* (`documents`/`products`/`search`) —
"Two users with 'documents' access via different groups currently see the
same shared data," flagged explicitly as "a known, deliberate scope
boundary, not a surprise."

**Decision, as it stands now**: a second, additive layer —
`GroupProjectAccess` (a superuser-granted pool per group) and
`UserProjectAccess` (a per-user grant drawn from that pool) — restricts
*document/project/product visibility* by project, **only when at least one
grant exists for that user**; zero grants still means "see everything,"
preserving old behavior for anyone never migrated onto the new model
(`services/auth-service/app/core/authz.py:83-98`, "restrictions are opt-in").

**Why the two-tier group-pool-then-user-grant shape**: lets a superuser
control the outer boundary (which projects exist at all for a given group)
while delegating the day-to-day "who on my team sees what" decision to that
group's own admin — without a group admin ever being able to grant a
project their group doesn't itself have.

## 3. JWT subject is the user's UUID, not their username

**Decision**: `create_access_token(subject=str(user.id))`
(`services/auth-service/app/routers/auth.py:109`), not the username.

**Evidence**: commit `b6687c9` — "JWT subject switched from mutable
username to immutable user id so renaming a user can't let a stale token
resolve to a different account."

**Why**: usernames are mutable (`PATCH /admin/users/{id}`); if a token
encoded the username and User A renamed themselves to what used to be User
B's username, a still-valid old token could resolve to the wrong account.
A UUID never changes for the life of the row.

## 4. Alembic migrations exist but are deliberately not run

**Decision**: `document-service` has a full Alembic setup
(`alembic/`, `alembic.ini`, one migration `0001_initial`), but
`app/main.py:16-20` calls `Base.metadata.create_all(bind=engine)` on
startup instead of running migrations, with the comment: "back to
`create_all()` for now. Alembic migrations are set up... but shelved until
a later session." `catalogue-service` carries the identical pattern and
comment. `auth-service` never had Alembic set up at all.

**Evidence for this being a *repeated*, still-current decision, not an
oversight**: the historical `Project status.md`'s section 4 ("What's Left
To Do") already listed "revisit Alembic migrations" as a deferred item on
2026-07-13; commit `b6687c9` (2026-07-27) *rewrote* the Alembic migration
to fix a schema mismatch, and the `main.py` comment referencing this
decision is still present in the current code — meaning the migration
tooling was maintained/fixed but its *use* was re-deferred again.

**Why**: **not verified** beyond what the comments say ("shelved... to a
later session"). The practical implication is real regardless of intent:
schema changes to any model after first boot will not be applied to an
existing database automatically — see [known-issues.md](known-issues.md).

## 5. Fail-open vs. fail-closed is chosen per call site, not globally

**Decision**: when a cross-service call fails, the response differs by what
kind of data is at stake:

- **Fails closed** (denies access / returns empty) when the call resolves
  *project-visibility* for a restricted caller —
  `catalogue-service/app/core/document_client.py:6-33`,
  `search-service/app/core/document_client.py:6-33`: "a dependency hiccup
  shouldn't silently drop the caller's project restriction and expose
  every product['s search results]."
- **Fails open** (returns `None`/`[]`, continues) when the call is
  *enrichment*, not access control — e.g.
  `document-service/app/core/catalogue_client.py:40-51`
  (`get_product_sub_items`): "fails open rather than blocking the whole
  catalogue export over one product's sub-item lookup."

**Why**: a uniform policy in either direction would be wrong somewhere —
always failing closed would make routine catalogue exports fragile against
any transient hiccup; always failing open would risk exposing data a
project restriction was supposed to hide. Each call site's comment states
which regime it uses and why — preserve this when adding new cross-service
calls (see [coding-standards.md](coding-standards.md)).

## 6. Group-admin "remove user" is scoped removal, not account deletion

**Decision**: `DELETE /admin/groups/{id}/members/{username}` removes a
membership (and any roles held via that group) but never deletes the
account; only a superuser (or, for non-superuser accounts, a group admin of
a group the target actually belongs to) can call `DELETE
/admin/users/{id}` to remove the account outright, and never for another
superuser's account.

**Evidence**: `Project status.md` section 1.5 ("Design choice worth
remembering") — "since a user might belong to groups other admins control,
only a superuser can delete an account outright... Removing someone from a
group also revokes any roles they held via that group." Confirmed still
true in current `services/auth-service/app/routers/admin.py:299-331,
596-629`.

**Why**: a user can belong to multiple groups; one group admin unilaterally
deleting the whole account would destroy access another group's admin
still legitimately granted.

## 7. Single gateway, unconditionally trusted headers downstream

**Decision**: Nginx's `auth_request` is the **only** enforcement point for
token validity and whole-service access; `document-service`,
`catalogue-service`, and `search-service` read `X-Allowed-Projects`,
`X-Access-Level`, `X-Has-Audit-Log`, `X-Has-Category-Access`, `X-Username`
directly off the request with no independent re-verification.

**Evidence**: `infra/nginx/nginx.conf:20-31` (`/_verify` internal location);
comment in `catalogue-service/app/routers/products.py:447-456` on
`get_product_file_content` explicitly noting it's "reachable directly
through nginx's `/api/products` routing, which DOES forward the header, so
it's still project-gated for that path" — i.e. the design assumes the
*only* path to these services is through Nginx.

**Why**: avoids re-implementing/duplicating the RBAC logic in every
service — a single, well-tested policy-decision point
(`auth-service/app/core/authz.py`) rather than four. **Trade-off**: any
direct network access to a service's internal port (bypassing Nginx)
bypasses all authorization entirely — acceptable inside a trusted Docker
Compose network with no host port published for those services, but a
hard requirement to preserve if the network topology ever changes (see
[known-issues.md](known-issues.md)).

**Update (`docs/SECURITY_HARDENING_LOG.md` Task 4)**: the "hard
requirement to preserve" above is no longer just a topology assumption —
it's now enforced in code. Each of `document-service`,
`catalogue-service`, `search-service` runs a middleware
(`app/core/gateway_auth.py`) that rejects any request lacking a shared
`X-Internal-Secret`, which only Nginx and these services' own direct
inter-service calls are configured with. The RBAC *decision* is still
made once, in auth-service, exactly as decided here — this only closes
the gap where a caller with any other path to a service's port could
skip that decision-point entirely by setting the trust headers itself.

## 8. Iterative delivery: ship fast, then dedicated hardening passes

**Decision**: functionality was built first; two large, dedicated commits
retroactively closed authorization and injection gaps: `b6687c9` ("Fix
authorization gaps, injection risks, and data-integrity bugs across
services") and `bdf21bf` ("Fix Excel formula injection, admin.py races, and
multi-group access edge cases"), both about a week apart from the features
they were hardening.

**Evidence**: commit history (`git log`) — feature-adding commits (RBAC,
project access, categories, sub-items, bulk import) followed by these two
security/correctness-focused commits, each fixing multiple issues found by
review rather than by an incident.

**Why**: **not verified** as an explicit methodology, but the pattern is
consistent enough (and the commit messages detailed/retrospective enough)
to note for future contributors — a new feature area in this codebase
should be expected to need a follow-up authorization/injection review pass,
not just a first pass focused on making the happy path work.

## 9. The frontend is one static file with no build step

**Decision**: `web/index.html` (~4,880 lines) is the entire frontend —
inline `<style>`, one `<script>` block, no framework, no bundler, no npm
`package.json` anywhere in the repo.

**Why**: **not verified** explicitly, but consistent with this being an
internal tool for a small team (see [overview.md](overview.md)) — a build
pipeline adds operational overhead (Node toolchain, build step in the
Dockerfile/CI) that this project has avoided everywhere else too (no CI at
all). The trade-off is a large, harder-to-navigate single file as the
application has grown — see [known-issues.md](known-issues.md).
