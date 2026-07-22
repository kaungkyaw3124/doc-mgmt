# Document Management Platform — Project Status

_Last updated: July 13, 2026_
_Environment: local deployment, Ubuntu server (`gpu-server`), Docker Compose_

---

## 1. What's Done

### 1.1 Infrastructure running
- **Docker & Docker Compose** on `gpu-server`
- **Postgres 16** — one container, three databases: `docmgmt` (document-service), `catalogue` (catalogue-service), `auth` (auth-service)
- **MinIO** — file storage
- **Meilisearch** — search engine
- **document-service**, **catalogue-service**, **search-service**, **auth-service** — all working
- **Nginx** — the only externally-reachable entry point (`:8080`), enforcing both authentication and RBAC

### 1.2 document-service / catalogue-service / search-service
Unchanged from before — CRUD for documents/products, product-linking on line items, customer endpoints, file upload/download via MinIO, full-text search across both indexes. See earlier sections of git history for detail; nothing changed here in this round.

### 1.3 auth-service — now full RBAC (new)

**Data model:**
- `users` — username, hashed password (bcrypt), `is_superuser` flag
- `groups` — named groups (e.g. "Sales", "Engineering")
- `user_groups` — many-to-many membership, with a per-membership `is_group_admin` flag
- `roles` — belong to exactly one group
- `role_access` — which whole services (`documents`, `products`, `search`) a role grants
- `user_roles` — which roles a user has been assigned

**Permission model, as built:**
- **Superuser**: create any user, create any group, manage any group's roles/members/access — unrestricted
- **Group admin** (per-group flag, a user can be admin of some groups and a plain member of others): can create/add/remove users within *their own* group, create roles in their group, grant/revoke service access on those roles, assign/unassign users to those roles — all scoped to that one group, confirmed blocked (`403`) when attempting the same on a different group
- **Regular user**: gets whatever whole-service access (`documents`/`products`/`search`) comes from the roles they've been assigned — nothing else

**Enforcement:** happens centrally in `auth-service`, called by Nginx's `auth_request` on every protected route (same mechanism as the original JWT check, extended). Nginx now tags each route with a `service_name` (`documents`, `products`, or `search`) via `set $service_name` in `nginx.conf`, forwards it to `auth-service`'s `/verify` endpoint alongside the token. `/verify` now returns `401` for a missing/invalid/expired token, and `403` for a valid token whose user lacks access to that specific service.

**New endpoints** (all under `/api/admin/...`):
| Endpoint | Who |
|---|---|
| `POST /admin/groups` | superuser only |
| `GET /admin/groups` | anyone (scoped to their own groups unless superuser) |
| `POST /admin/groups/{id}/users` (create + add in one step) | superuser or that group's admin |
| `POST /admin/groups/{id}/members` (add existing user) | same |
| `DELETE /admin/groups/{id}/members/{username}` (remove from group) | same |
| `POST /admin/groups/{id}/roles` | same |
| `POST /admin/roles/{id}/access` (grant a service) | superuser or the role's group admin |
| `DELETE /admin/roles/{id}/access/{service}` | same |
| `POST /admin/roles/{id}/assign` | same |
| `DELETE /admin/roles/{id}/assign/{username}` | same |
| `GET /admin/me` | anyone — see your own groups/roles/access |
| `POST /api/auth/users` (bare account, no group) | superuser only |

**Fully tested end-to-end, live on the running system:**
1. Superuser created a group ("Sales") and a group-admin user (`sales_admin`) inside it — confirmed `is_superuser: false`, `is_group_admin: true` via `/admin/me`
2. `sales_admin` created a role ("Sales Rep") and granted it `documents` access — succeeded (own group)
3. Superuser created a *second* group ("Engineering"); `sales_admin` attempted to create a role in it — **correctly blocked with `403`**
4. `sales_admin` created a plain member (`sales_rep1`) and assigned them the "Sales Rep" role
5. `sales_rep1` logged in and hit `/api/documents` → **`200`** (has access); hit `/api/products` → **`403`** (role never granted product access)

This confirms the full chain — group membership → role → service grant → gateway enforcement — works against real API calls, not just database bookkeeping.

### 1.4 Known limitation, flagged deliberately (not a bug)
**Access controls whole services, not individual records.** A user granted "documents" access via one group's role can see *all* documents in the system — the `documents`/`products` tables have no `group_id` column, so there's no per-record filtering by group. Two users with "documents" access via different groups currently see the same shared data. If you want Group A's documents genuinely invisible to Group B, that's a separate, larger change (adding `group_id` to the documents/products schema and filtering every query) — not yet built. Flagging this now so it's a known, deliberate scope boundary rather than a surprise later.

### 1.5 Design choice worth remembering
**Group-admin "delete user" was implemented as "remove from group,"** not full account deletion — since a user might belong to groups other admins control, only a superuser can delete an account outright (via direct DB access for now; no dedicated endpoint yet). Removing someone from a group also revokes any roles they held via that group.

### 1.6 Bugs fixed this session
- **Nginx doesn't hot-reload `nginx.conf`** — changes require `docker compose restart nginx` explicitly; `up -d --build` alone won't pick up config edits if the nginx service definition itself didn't change. This bit us twice; worth remembering going forward.
- (Carried over from earlier sessions: MinIO presigned URL hostname mismatch, Meilisearch primary-key ambiguity — both already fixed, see git history.)

### 1.7 Repo state
```
document-mgmt-platform/
├── services/
│   ├── document-service/
│   ├── catalogue-service/
│   ├── search-service/
│   └── auth-service/            ← RBAC models, admin.py, RBAC-aware /verify
└── infra/
    ├── docker-compose.yml
    ├── nginx/nginx.conf           ← per-route $service_name tagging
    └── init-db/ (3 database-creation scripts: catalogue, auth)
```
Git initialized, `main` branch, all of the above committed as of this session.

---

## 2. Known Shortcuts (intentional, for now)

| Shortcut | Why | When to fix |
|---|---|---|
| RBAC controls whole services, not per-record/group data isolation | Matches what was asked for this round | Add `group_id` columns + query filtering to document/product tables if true data isolation is needed later |
| No dedicated "delete user account" endpoint | Avoids ambiguity around users in multiple groups | Add one, superuser-only, when actually needed |
| `JWT_SECRET` is a plaintext default in `.env.example` | Fine for local testing | Change to a real random value before this is reachable beyond your own machine |
| No token refresh/revocation | Tokens just expire (60 min) | Add refresh tokens if session length becomes limiting |
| `Base.metadata.create_all()` instead of Alembic migrations | Shelved earlier after migration issues | Revisit with a clean slate before schema changes again |
| Postgres/MinIO/Meilisearch ports still exposed to host | Convenient for debugging | Close off once not actively debugging |
| Search indexing is synchronous, fire-and-forget | Simpler for local dev | Add async worker later if needed |

---

## 3. Where We Are on the Overall Architecture

```
                              ┌─────────────────────────┐
                              │   Nginx Gateway            │  ✅ WORKING — only external entry (:8080)
                              │  auth_request + RBAC        │     tags each route, checks via auth-service
                              └──────────┬────────────────┘
                                         │
        ┌───────────────┬───────────────┼───────────────┬───────────────┐
        │               │               │               │               │
 ┌──────▼─────┐  ┌──────▼──────┐ ┌─────▼──────┐  ┌──────▼──────┐ ┌──────▼──────┐
 │ Auth+RBAC   │  │ Document    │ │ Catalogue  │  │ Search      │ │ Notification│
 │ WORKING     │  │ WORKING     │ │ WORKING    │  │ WORKING     │ │ not built   │
 └─────────────┘  └──────┬──────┘ └─────┬──────┘  └──────┬──────┘ └─────────────┘
                         │◄────HTTP─────►│                │
                  ┌──────▼──────┐ ┌──────▼──────┐   ┌──────▼──────┐   ┌────────────────┐
                  │ Postgres     │ │ Postgres     │   │ Meilisearch  │   │ MinIO           │
                  │ (docmgmt)    │ │ (catalogue)  │   │ WORKING      │   │ WORKING         │
                  └──────────────┘ └──────────────┘   └──────────────┘   └─────────────────┘
                  ┌──────────────┐
                  │ Postgres      │ ← auth database: users, groups, roles, access grants
                  │ (auth)        │
                  └──────────────┘
```

**5 of ~8 planned components fully working**, now with a tested, working RBAC layer gating every request through the single gateway. This is the complete architecture from the original design, with real access control on top.

---

## 4. What's Left To Do

### If needed — data-level group isolation
Add `group_id` to documents/products, filter queries — only if "Group A can't see Group B's documents" becomes an actual requirement (currently: whole-service access only, as discussed).

### Then — revisit Alembic migrations
Clean-slate attempt, schema is now more complex (RBAC tables included).

### Then — general hardening
Real `JWT_SECRET`, close remaining exposed ports, CI/CD, backups, token refresh/revocation.

---

## 5. Quick Reference

```bash
# Start everything
cd ~/document-mgmt-platform/infra
docker compose up -d

# nginx.conf changes need an explicit restart
docker compose restart nginx

# Log in
curl -X POST http://localhost:8080/api/auth/login -H "Content-Type: application/json" -d '{"username": "...", "password": "..."}'

# Check your own permissions
curl http://localhost:8080/api/admin/me -H "Authorization: Bearer <token>"

# Create a group (superuser only)
curl -X POST http://localhost:8080/api/admin/groups -H "Authorization: Bearer <token>" -d '{"name": "..."}'

# Create a user in a group (superuser or that group's admin)
curl -X POST http://localhost:8080/api/admin/groups/<group_id>/users -H "Authorization: Bearer <token>" -d '{"username": "...", "password": "...", "is_group_admin": false}'

# Create a role, grant it service access, assign a user
curl -X POST http://localhost:8080/api/admin/groups/<group_id>/roles -H "Authorization: Bearer <token>" -d '{"name": "..."}'
curl -X POST http://localhost:8080/api/admin/roles/<role_id>/access -H "Authorization: Bearer <token>" -d '{"service_name": "documents"}'
curl -X POST http://localhost:8080/api/admin/roles/<role_id>/assign -H "Authorization: Bearer <token>" -d '{"username": "..."}'
```

**Note on wiping Postgres data:** `docker compose down -v` does not clear `./data/postgres` (bind mount). Full reset: `docker compose down && sudo rm -rf ./data/postgres && docker compose up -d`.

---

## 6. Suggested Next Session

Either add data-level group isolation if it turns out to be needed, or move on to hardening (real JWT secret, closing remaining ports) and finally revisiting Alembic migrations with the now-larger schema.