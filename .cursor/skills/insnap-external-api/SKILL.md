---
name: insnap-external-api
description: Design, extend, and debug the external /v1/ API system in this project, including API Key management, permissions, cursor-based pagination, sub-resource routing, and the discovery endpoint. Use when adding new /v1/ endpoints, new permissions, or fixing issues with API key authentication and audit logging.
---

# InSnap External API System

## API Discovery — Start Here

**`GET /v1/`** is the discovery endpoint. It returns the full catalogue of endpoints the caller's API key can access, filtered by the key's permissions. **Always point an AI agent at this endpoint first** before building any integration.

Response structure (abridged):
```json
{
  "api_version": "v1",
  "active_permissions": ["api:kol:read", "api:post:read"],
  "pagination_note": "...",
  "endpoints": [
    {
      "method": "GET",
      "path": "/v1/kols/",
      "summary": "List KOLs",
      "description": "...",
      "permission": "api:kol:read",
      "additional_permissions": [],
      "params": [
        {"name": "page_size", "type": "int", "required": false, "default": 20, "description": "..."},
        {"name": "cursor",    "type": "str", "required": false, "default": null, "description": "..."}
      ],
      "response_type": "cursor_page",
      "response_fields": [
        {"name": "items[].profile_id", "type": "int", "description": "..."},
        ...
      ]
    }
  ]
}
```

Endpoints whose required permission the key does not hold are omitted entirely.

When **adding a new endpoint**, you MUST also add a matching `EndpointSpec` entry to `ALL_ENDPOINTS` in `backend/routers/v1/discovery.py`. This keeps the discovery response accurate.

---

## Architecture Overview

Two separate API layers:

| Layer | Prefix | Auth | Purpose |
|-------|--------|------|---------|
| Internal | `/api/` | HttpOnly cookie + JWT | Frontend ↔ Backend |
| External | `/v1/` | `Authorization: Bearer insnap_xxx` | Third-party clients |

The Next.js middleware proxies both layers to the FastAPI backend. The `/v1/` proxy strips the internal cookie and forwards the `Authorization` header as-is (`frontend/src/middleware.ts` → `forwardV1ToBackend`).

---

## Key Files

```
backend/
  models/api_key_models.py          # UserApiKey, ApiAuditLog, API_PERMISSIONS dict
  services/api_key_service.py       # Key generation, hashing, TTLCache verification, audit log
  middleware/api_key_auth.py        # Starlette middleware: validates Bearer on /v1/* routes
  routers/api_keys.py               # /api/auth/api-keys — CRUD for logged-in users
  routers/v1/
    deps.py                         # V1AuthInfo + get_v1_auth dependency
    pagination.py                   # CursorPage, encode/decode cursor, cursor filter helpers
    kols.py                         # /v1/kols + sub-resources
    effects.py                      # /v1/effects
    effect_results.py               # /v1/effect-results
    posts.py                        # /v1/posts
  services/auth_service.py          # initialize_default_permissions (platform perms)

frontend/
  src/components/profile/api-keys-section.tsx     # UI: list, create, revoke keys
  src/components/profile/create-api-key-dialog.tsx
  src/lib/api/api-keys-api.ts                     # Frontend API client
```

---

## Adding a New /v1/ Resource

1. **Create `backend/routers/v1/<resource>.py`**

   ```python
   from routers.v1.deps import V1AuthInfo, get_v1_auth
   from routers.v1.pagination import CursorPage, datetime_cursor_filter, int_cursor_filter, paginate, encode_cursor

   router = APIRouter(prefix="/v1/<resource>", tags=["v1-<resource>"])
   PERM = "api:<resource>:read"

   @router.get("/", response_model=CursorPage[V1Item])
   async def list_items(page_size: int = 20, cursor: Optional[str] = None, auth: V1AuthInfo = Depends(get_v1_auth)):
       if not auth.has_permission(PERM):
           raise HTTPException(403, f"Permission required: {PERM}")
       cursor_filter = datetime_cursor_filter(cursor)   # or int_cursor_filter for epoch ints
       docs = await col.find({**base_filter, **cursor_filter}).sort(...).limit(page_size + 1).to_list(None)
       page_docs, next_cursor = paginate(docs, page_size, make_cursor_fn)
       return CursorPage(items=[...], next_cursor=next_cursor, has_more=next_cursor is not None)
   ```

2. **Register in `backend/models/api_key_models.py` → `API_PERMISSIONS`**

   ```python
   API_PERMISSIONS = {
       ...
       "api:<resource>:read": "Description shown to users when creating keys",
   }
   ```

3. **Register router in `backend/app_factory.py`**

   ```python
   from routers.v1.<resource> import router as v1_<resource>_router
   app.include_router(v1_<resource>_router)
   ```

   `initialize_api_permissions()` in `auth_service.py` auto-syncs `API_PERMISSIONS` to MongoDB on startup — no manual DB step needed.

---

## Adding a New Platform Permission

Platform permissions (not API-specific) live in `auth_service.initialize_default_permissions()`:

```python
{"name": "my_permission", "description": "Human-readable description"},
```

They are auto-upserted at startup. Front-end permission check: `hasPermission('my_permission')` from `useAuth()`. Back-end check: `auth_service.has_permission(current_user.permissions, "my_permission")`. `admin` always passes every check.

---

## Cursor-Based Pagination

All `/v1/` list endpoints use opaque cursor tokens (no `total`, no `offset`).

**Cursor helpers** (`routers/v1/pagination.py`):

| Helper | Sort fields | Use when |
|--------|-------------|----------|
| `datetime_cursor_filter(cursor)` | `created_at DESC, _id DESC` | datetime primary sort |
| `int_cursor_filter(cursor, sort_field, pk_field)` | `int DESC, int DESC` | epoch int sort (e.g. KOLs) |

**Always fetch `page_size + 1`**, then call `paginate(docs, page_size, make_cursor_fn)`.

`make_cursor_fn` receives a raw MongoDB doc and returns `encode_cursor(sort_value, id_value)`.

---

## Authentication Flow

1. User holds platform permission `api_access` → Profile page shows API Keys section.
2. User creates a key (frontend `POST /api/auth/api-keys/`) → backend stores `SHA256(key)`, returns plaintext once.
3. Client sends `Authorization: Bearer insnap_<hex>` to `/v1/*`.
4. `ApiKeyAuthMiddleware` hashes the token, looks it up (TTLCache 5 min), injects `request.state.v1_user_id / v1_permissions / v1_key_id`.
5. Route uses `get_v1_auth` dependency → `V1AuthInfo.has_permission(perm)`.
6. Middleware asynchronously writes to `api_audit_logs` collection after each request.

---

## Sub-Resources Pattern (KOL example)

`GET /v1/kols/{id}/outfits` — parallel fetch from `live_outfit` + `live_kol_config.preset_res`.
`GET /v1/kols/{id}/settings` — requires both `api:kol:read` AND `api:kol:settings:read`.
`GET /v1/kols/{id}/posts` — requires both `api:kol:read` AND `api:post:read`.

Use double permission checks for sensitive sub-resources:
```python
if not auth.has_permission(PARENT_PERM): raise HTTPException(403, ...)
if not auth.has_permission(SENSITIVE_PERM): raise HTTPException(403, ...)
```
