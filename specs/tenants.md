# Tenants — Tenant + Domain Models, Provisioning, Staff Management

<system-reminder>
1. `TenantMainMiddleware` MUST be position 0 in the MIDDLEWARE list — if anything is before it, tenant resolution breaks and every request hits the public schema.
2. Database ENGINE MUST be `django_tenants.postgresql_backend` — NEVER `django.db.backends.postgresql`. The tenant backend intercepts connections to set `search_path`.
3. Migrations MUST use `migrate_schemas --shared` for shared apps and `migrate_schemas --tenant` for tenant apps — NEVER bare `python manage.py migrate`.
4. `DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]` is required in settings — it routes shared models to the public schema and tenant models to the active schema.
5. Tenant and Domain are SHARED apps. They live in the `public` PostgreSQL schema alongside User.
</system-reminder>

## Goal

An authenticated user can create a new clinic tenant, which provisions a new PostgreSQL schema, creates a Domain mapping (subdomain-based routing), and makes the creating user the tenant admin. A superadmin can list all tenants. Tenant admins can invite staff (by email), list staff, and remove staff from their tenant.

## Definitions

- **TenantBase**: Base class from `django-tenant-users` that provides `schema_name`, `auto_create_schema`, and tenant-user membership methods.
- **Tenant**: Our model extending `TenantBase` with `name` (CharField, max 100) and `created_at` (DateTimeField, auto_now_add). Setting `auto_create_schema = True` causes `tenant.save()` to automatically run `CREATE SCHEMA` in PostgreSQL.
- **DomainMixin**: Base class from `django-tenants` that maps a hostname to a tenant. Has `domain` (CharField), `tenant` (ForeignKey to Tenant), and `is_primary` (BooleanField).
- **Domain**: Our model extending `DomainMixin` with no extra fields.
- **Public tenant**: The default tenant with `schema_name = "public"`. Always exists. Hosts the landing page and registration.
- **Tenant provisioning**: The process of creating a Tenant row, a Domain row, running `migrate_schemas --tenant` for the new schema, and adding the creating user as admin.
- **Staff**: A user who has been added to a tenant's membership. Managed via `django-tenant-users` membership methods (`add_user`, `remove_user`).

## Plan

1. Create `apps/tenants/__init__.py` (empty) and `apps/tenants/models.py` with `Tenant(TenantBase)` and `Domain(DomainMixin)`.
2. Add `TENANT_MODEL = "tenants.Tenant"` and `TENANT_DOMAIN_MODEL = "tenants.Domain"` to `config/settings.py`.
3. Verify `apps.tenants` is in `SHARED_APPS` and `django_tenants` is the first entry in `SHARED_APPS`.
4. Create `apps/tenants/schemas.py` with Django Ninja schemas: `TenantCreateIn` (name, subdomain), `TenantOut` (id, name, domain, created_at), `StaffOut` (user_id, name, email, role), `InviteIn` (email).
5. Create `apps/tenants/services.py` with `provision_tenant(name, subdomain, owner_user)` that:
   a. Creates the Tenant with `schema_name = subdomain` (slugified, lowercase, alphanumeric + underscores only).
   b. Creates the Domain with `domain = "{subdomain}.localhost"` (configurable base domain via `TENANT_BASE_DOMAIN` env var).
   c. Adds the owner user to the tenant via `tenant.add_user(user)`.
   d. Sets the owner user's role to `admin` for this tenant.
   e. Returns the Tenant instance.
6. Create `apps/tenants/api.py` with endpoints: create tenant (POST), list tenants (GET, superadmin only), list staff (GET), invite staff (POST), remove staff (DELETE).
7. Wire tenant creation endpoints into `config/urls_public.py` under `/api/tenants/`.
8. Wire staff management endpoints into `config/urls.py` (tenant context) under `/api/staff/`.
9. Create `scripts/create_public_tenant.py` as a Django management command that creates the public tenant with domain `portal.localhost` and a superadmin user.
10. Write tests in `apps/tenants/tests.py`.
11. Run `uv run python manage.py migrate_schemas --shared`.
12. Run `black . && ruff check . --fix`, then `uv run python manage.py test`.

## Source Files

- `apps/tenants/__init__.py` — empty init
- `apps/tenants/models.py` — Tenant and Domain models
- `apps/tenants/schemas.py` — Ninja request/response schemas
- `apps/tenants/services.py` — Tenant provisioning logic
- `apps/tenants/api.py` — Tenant + staff endpoints
- `apps/tenants/tests.py` — Test cases
- `apps/tenants/admin.py` — Register Tenant and Domain in Django admin
- `config/settings.py` — TENANT_MODEL, TENANT_DOMAIN_MODEL, SHARED_APPS, TENANT_APPS, MIDDLEWARE, DATABASES, DATABASE_ROUTERS
- `config/urls_public.py` — Include tenant creation router at `/api/tenants/`
- `config/urls.py` — Include staff management router at `/api/staff/`
- `scripts/create_public_tenant.py` — Management command for public tenant + superadmin

## Test Cases

```
Given an authenticated user and no tenant named "sunrise-clinic"
When POST /api/tenants/ with {"name": "Sunrise Clinic", "subdomain": "sunrise-clinic"}
Then 201 with {"id": <int>, "name": "Sunrise Clinic", "domain": "sunrise-clinic.localhost", "created_at": "..."}
And a new PostgreSQL schema "sunrise_clinic" exists
And the user is a member of the new tenant with role "admin"

Given an authenticated user
When POST /api/tenants/ with {"name": "Dup", "subdomain": "sunrise-clinic"} (subdomain already taken)
Then 409 with {"detail": "Subdomain already exists"}

Given a superadmin user
When GET /api/tenants/
Then 200 with a list of all tenants including public

Given a non-superadmin user
When GET /api/tenants/
Then 403 with {"detail": "Superadmin access required"}

Given an admin user on tenant "sunrise-clinic" and a user "staff@example.com" exists in the system
When POST /api/staff/invite with {"email": "staff@example.com"}
Then 200 with {"user_id": <int>, "name": "...", "email": "staff@example.com", "role": "staff"}
And the user is added to the tenant membership

Given an admin user on tenant "sunrise-clinic"
When POST /api/staff/invite with {"email": "nonexistent@example.com"} (user not registered)
Then 404 with {"detail": "User not found. They must register first."}

Given an admin user on tenant "sunrise-clinic" with 2 staff members
When GET /api/staff/
Then 200 with a list of 2 staff members

Given a staff (non-admin) user on tenant "sunrise-clinic"
When POST /api/staff/invite with {"email": "someone@example.com"}
Then 403 with {"detail": "Admin access required"}

Given an admin user on tenant "sunrise-clinic" and staff member with id 5
When DELETE /api/staff/5
Then 200 with {"success": true} and the user is removed from tenant membership

Given POST /api/tenants/ with {"name": "", "subdomain": "a b c!"}
When the request is processed
Then 422 validation error (name required, subdomain contains invalid characters)
```

## Edge Cases

- Subdomain validation: only lowercase letters, numbers, and hyphens. No spaces, no special characters. Max 63 characters (DNS label limit). Convert to schema name by replacing hyphens with underscores.
- The `public` subdomain/schema is reserved and cannot be created by users.
- Schema names `information_schema`, `pg_catalog`, and any starting with `pg_` are reserved by PostgreSQL and must be rejected.
- An admin cannot remove themselves from the tenant (prevents orphaned tenants with no admin).
- Inviting a user who is already a member of the tenant returns 409, not a duplicate membership.
- When a tenant is created, `auto_create_schema = True` handles the PostgreSQL schema creation, but tenant-app migrations for the new schema must also run. This happens automatically via django-tenants signals.

## Out of Scope

- Tenant deletion or deactivation
- Transferring admin ownership
- Custom domain mapping (only subdomain.localhost)
- Tenant-level settings or configuration
- Billing or subscription tiers per tenant
- Bulk staff import

## Extensions

- Add tenant deactivation (soft delete) that blocks login to that subdomain
- Support custom domains via additional Domain rows with `is_primary = False`
- Add a `tenant_limit` field to User to cap how many tenants one user can create
