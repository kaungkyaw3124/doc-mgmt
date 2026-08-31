# Glossary

Terms as used in this codebase specifically — not generic definitions.

| Term | Meaning |
|---|---|
| **Ledger** | The frontend application's display name (`web/index.html:6`, `<title>Ledger — Document Management</title>`). Despite the name, the system has no bookkeeping/accounting ledger features — see [overview.md](overview.md). |
| **Document** | The central business object: an invoice, quotation, proposal, or catalogue hand-out (`doc_type`). Owned by document-service. |
| **Doc number / `doc_number`** | A document's human-readable, unique identifier, auto-generated as `{company.short_name or "DOC"}-{YYYYMMDD}/{seq:03d}` per company and calendar day. |
| **Line item** | One row on a Document — either a reference to a catalogue Product (with quantity/price overrides) or a free-text description + price. |
| **Product** | A catalogue entry owned by catalogue-service: SKU, name, category, price, optional attached file, optional Sub-items. |
| **SKU** | A product's unique item code, auto-generated as `{category.short_term or "PRD"}-{seq:04d}` if not supplied manually. |
| **Sub-item** | A component Product referenced by a "bundle" parent Product (e.g. a Desktop Computer's CPU/RAM/GPU), in a fixed display order (`sequence_number`). |
| **Category** | A managed, admin-controlled list of product categories; its `short_term` is the SKU prefix for products in that category. |
| **Project** | Groups Documents by initiative/budget year. Also the unit that project-level access restriction (see below) is scoped to. |
| **Company** | A supplier profile (name, logo, seal, contact info) chosen per-document as the issuing party on exports. Exactly one Company may be `is_primary` at a time. |
| **Customer** | Who a Document is issued to. |
| **Catalogue export** | A zip of every line item's attached catalogue file(s) for one Document, positionally numbered (`GET /documents/{id}/export/catalogue`). Not to be confused with the `catalogue` `doc_type`, or with catalogue-service. |
| **Recycle bin** | The soft-delete UI/pattern: `is_deleted=true` hides a row from normal listing but keeps it recoverable via `/trash` + `/restore`, as opposed to a genuinely destructive `DELETE`. |
| **Group** | An organizational unit in auth-service (e.g. a sales team). Owns Roles and a pool of visible Projects. |
| **Role** | A named bundle of service-access grants, scoped to exactly one Group. |
| **Service** (in the RBAC sense) | One of the five grantable permission domains: `documents`, `products`, `search`, `audit-log`, `categories` (`VALID_SERVICES`). Note `/api/customers`, `/api/companies`, `/api/projects` are all gated under the `documents` service — there is no separate grant for them. |
| **Access level** | `view` or `edit` — only meaningfully distinguished for the `documents` service; every other service is all-or-nothing once granted. |
| **Superuser** | Unrestricted administrator; bypasses every authorization check. |
| **Group admin** | A per-membership flag (`UserGroup.is_group_admin`) granting management rights over one specific Group only. |
| **Project access pool** | The set of Projects a superuser has granted to a whole Group (`GroupProjectAccess`) — the only Projects that Group's admins may, in turn, grant to individual members. |
| **X-Allowed-Projects** | The header Nginx forwards downstream after `/verify`: `"ALL"` (unrestricted), `"NONE"`, or a comma-separated list of allowed project UUIDs. |
| **`/verify`** | auth-service's internal endpoint that Nginx's `auth_request` directive calls on every protected route to check the JWT and service access, returning the `X-*` headers downstream services trust. |
| **Bearer token** | The JWT issued at login, sent as `Authorization: Bearer <token>`, stored client-side in `localStorage` under the key `ledger_token`. |
| **`auth_request`** | The Nginx module/directive (`infra/nginx/nginx.conf`) implementing the gateway's centralized authentication/authorization check — see [architecture.md](architecture.md). |
| **RBAC** | Role-Based Access Control — the Group → Role → Service-grant → User-assignment model implemented in auth-service. |
| **Version (`Document.version`)** | A schema column that exists but is never read or written by any code path — see [known-issues.md](known-issues.md). Not a real versioning feature. |
| **MinIO** | Self-hosted S3-compatible object storage; one bucket per service (`documents`, `products`). |
| **Meilisearch** | Self-hosted search engine; one index per service (`documents`, `products`), queried by search-service. |
| **`gpu-server`** | The historical name of the target deployment host, per `Project status.md` and the seed scripts' default `BASE_URL`. |
