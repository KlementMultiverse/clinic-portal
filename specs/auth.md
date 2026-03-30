# Auth — Login, Register, Logout, Me

<system-reminder>
1. Auth endpoints live on the PUBLIC schema URL config (`config/urls_public.py`) — they must be accessible before any tenant context exists.
2. Use Django's built-in `authenticate()` and `login()` / `logout()` — do NOT reimplement session management.
3. The User model extends `tenant_users.tenants.models.UserProfile`, NOT `AbstractUser` — never import from `django.contrib.auth.models.User`.
4. Registration creates the user in the SHARED schema only — tenant membership is handled separately by the tenants spec.
5. All responses use Django Ninja Schema classes — NEVER import `rest_framework`.
</system-reminder>

## Goal

An unauthenticated user can register an account, log in with email and password, and receive a Django session cookie. An authenticated user can call `/api/auth/me` to retrieve their identity (user ID, name, email, role, current tenant) and can log out to destroy the session.

## Definitions

- **UserProfile**: Base class from `django-tenant-users` that provides a global (shared-schema) user with email as the username field and tenant membership tracking.
- **User**: Our custom model extending `UserProfile` with `name` (CharField, max 150) and `role` (CharField, choices: `admin` | `staff`, default `staff`).
- **Session auth**: Django's session framework backed by Redis. On login, a `sessionid` cookie is set. All subsequent requests include it.
- **Public schema**: The `public` PostgreSQL schema where shared models (User, Tenant, Domain) live. Auth endpoints are routed here via `PUBLIC_SCHEMA_URLCONF`.

## Plan

1. Create `apps/users/__init__.py` (empty) and `apps/users/models.py` with the `User` model extending `UserProfile`.
2. Add `AUTH_USER_MODEL = "users.User"` to `config/settings.py`.
3. Create `apps/users/schemas.py` with Django Ninja Schema classes: `LoginIn`, `RegisterIn`, `UserOut`, `ErrorOut`, `SuccessOut`.
4. Create `apps/users/api.py` with a `NinjaAPI` router or `Router` containing 4 endpoints: login, register, logout, me.
5. Implement login: validate email + password via `django.contrib.auth.authenticate()`, call `login(request, user)`, return `UserOut`.
6. Implement register: validate email uniqueness, create user via `User.objects.create_user()`, return `UserOut`. Do NOT auto-login after register.
7. Implement logout: call `logout(request)`, return `{"success": True}`.
8. Implement me: if `request.user.is_authenticated`, return `UserOut` with tenant info from `request.tenant`; else return 401.
9. Wire the router into `config/urls_public.py` under `/api/auth/`.
10. Write tests in `apps/users/tests.py`.
11. Run `uv run python manage.py migrate_schemas --shared` (User is a shared model).
12. Run `black . && ruff check . --fix`, then `uv run python manage.py test`.

## Source Files

- `apps/users/__init__.py` — empty init
- `apps/users/models.py` — User model
- `apps/users/schemas.py` — Ninja request/response schemas
- `apps/users/api.py` — Auth endpoints (login, register, logout, me)
- `apps/users/tests.py` — Test cases
- `apps/users/admin.py` — Register User in Django admin
- `config/settings.py` — Add `AUTH_USER_MODEL`, verify `apps.users` is in `SHARED_APPS`
- `config/urls_public.py` — Include auth router at `/api/auth/`

## Test Cases

```
Given no user exists with email "new@example.com"
When POST /api/auth/register with {"email": "new@example.com", "password": "SecurePass1!", "name": "Test User"}
Then 201 with {"user_id": <int>, "name": "Test User"}

Given a user exists with email "existing@example.com"
When POST /api/auth/register with {"email": "existing@example.com", "password": "Pass1!", "name": "Dup"}
Then 409 with {"detail": "Email already registered"}

Given a user exists with email "user@example.com" and password "SecurePass1!"
When POST /api/auth/login with {"email": "user@example.com", "password": "SecurePass1!"}
Then 200 with {"user_id": <int>, "name": "...", "role": "staff"} and a sessionid cookie is set

Given a user exists with email "user@example.com" and password "SecurePass1!"
When POST /api/auth/login with {"email": "user@example.com", "password": "WrongPass"}
Then 401 with {"detail": "Invalid credentials"}

Given an authenticated session exists
When GET /api/auth/me
Then 200 with {"user_id": <int>, "name": "...", "email": "...", "role": "staff", "tenant": "..."}

Given no session exists (unauthenticated)
When GET /api/auth/me
Then 401 with {"detail": "Authentication required"}

Given an authenticated session exists
When POST /api/auth/logout
Then 200 with {"success": true} and the sessionid cookie is invalidated

Given POST /api/auth/register with {"email": "bad", "password": "x", "name": ""}
When the request is processed
Then 422 validation error (email format invalid, password too short, name required)
```

## Edge Cases

- Email is case-insensitive: "User@Example.com" and "user@example.com" are the same account. Normalize to lowercase before storage.
- Password validation: enforce minimum 8 characters. Use Django's built-in validators.
- Calling logout when not logged in returns 200 (idempotent) — do not error.
- The `me` endpoint must work from both public and tenant contexts. If the user is on a tenant subdomain, include the tenant name; if on the public domain, `tenant` is null.
- CSRF: all POST endpoints require a CSRF token. The test client must handle this. For API clients, use Django Ninja's built-in CSRF handling or configure `csrf_exempt` on the API instance with session auth checking.

## Out of Scope

- Password reset / forgot password flow
- Email verification
- OAuth / social login
- JWT tokens (we use session auth exclusively)
- Staff invitation (that is in the tenants spec)
- Role changes (admin promoting/demoting staff)
- Rate limiting on login attempts

## Extensions

- Add password strength meter feedback in the register response
- Add `last_login` timestamp to the `me` response
- Add session expiry configuration (default 2 weeks, configurable per tenant)
