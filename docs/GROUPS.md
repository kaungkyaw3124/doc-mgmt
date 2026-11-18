# Groups (departments) and data ownership

Since loop iteration 19, every **document, customer, project and product**
belongs to exactly one **group** (a department: Operation, CityMart, …).
A user sees and changes only the records of the groups they are in.
**Companies** (supplier identities, logos, seals, MDs) and **product
categories** are shared by every group.

## Who sees what

| Caller | Sees | Creates |
|---|---|---|
| Superuser | everything, including *unassigned* rows | must pick the group (Group select in the form; `group_id` in the API) |
| User in one group | that group's rows | automatically owned by that group |
| User in several groups | rows of all their groups | must pick one of their groups (Group select / `group_id`) |
| User in no active group | nothing | refused: "You are not in any group — ask an admin" |

- A disabled group counts as "not a member" (its members lose access to its rows).
- Records of another group answer **404**, never 403, so their existence is not revealed.
- A document's saved customer and project must be in the document's own group
  (422 otherwise). The document form's pickers only offer the chosen group's rows.
- Line items keep their own description/price snapshot, so a document still
  renders and exports when its product is deleted or belongs to another group.
- Document numbers (`SC-20261118/001`) and SKUs stay one sequence across all
  groups (they are unique system-wide).
- Search (Meilisearch) filters on the indexed `group_id` server-side.
- The project-access rule (`X-Allowed-Projects`) still applies on top.

## How it works

1. auth-service `/verify` returns `X-Group-Ids` (the user's **active** group
   ids; every active group for a superuser) and `X-Is-Superuser` (`1`/`0`).
2. Nginx copies both onto every `/api/documents|customers|projects|products|search`
   request and **always overwrites** any copy a client sent (blanked on the
   shared `/api/companies` and `/api/categories`).
3. Each service trusts them only with the `X-Internal-Secret` and filters
   every query through one helper: `app/core/group_scope.py` in
   document-service and catalogue-service (`scoped()`, `get_or_404()`,
   `group_for_create()`). Missing headers mean "no groups" — fail closed.
4. The column is `group_id` (UUID, nullable, indexed) on `documents`,
   `customers`, `projects` (document-service migration `0009_group_id`) and
   `products` (catalogue-service migration `0001_product_group_id`, the first
   Alembic revision of that service). No foreign key: the id lives in the auth database.

## Data from before groups existed ("unassigned")

The migrations add the column and assign nothing. Old rows have
`group_id = NULL`: only superusers see them, until you give them to a group:

```bash
./scripts/assign-unassigned.sh "Operation"
```

It assigns every unassigned document, customer, project and product to that
group, rebuilds both search indexes, and prints the counts per entity.
Running it again is harmless (it finds nothing left). It calls the
superuser-only endpoints `POST /documents/assign-unassigned` and
`POST /products/assign-unassigned` (`{"group_id": "<uuid>"}`) from inside
the stack. For another stack set `COMPOSE_PROJECT_NAME` / `COMPOSE_OVERRIDE`
like `backup.sh`.

## Adding a department

1. Admin → Groups → create the group (superuser).
2. Give it roles (e.g. Editor with `documents` + `products` + `search` edit).
3. Create or add its users. From their next request they see only that
   group's records, and everything they create belongs to it.

Moving records between groups and an "Unassigned data" admin card come in
iteration 20.
