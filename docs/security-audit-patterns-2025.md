# Security Audit Patterns 2025-2026

Generated: 2026-04-02
Purpose: Actionable grep-able patterns for the @security-engineer agent test suite.

---

## 1. TOP 10 SECURITY ISSUES IN DJANGO / FASTAPI / NODE.JS (2025)

### 1.1 SQL Injection via Raw Queries (Django)

**Real CVE:** CVE-2024-53908 -- SQL injection in `HasKey` lookup on Oracle.
**Real CVE:** CVE-2025-59681, CVE-2025-57833 -- SQL injection in column aliases.

```
# GREP PATTERNS (vulnerable):
\.raw\(.*%s
\.raw\(.*\.format\(
\.raw\(.*f"
cursor\.execute\(.*f"
cursor\.execute\(.*\.format\(
cursor\.execute\(.*%.*%
\.extra\(
RawSQL\(
```

**Vulnerable code:**
```python
# BAD: string interpolation in raw SQL
User.objects.raw(f"SELECT * FROM users WHERE name = '{name}'")
cursor.execute("SELECT * FROM users WHERE id = %s" % user_id)
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
Model.objects.extra(where=[f"name = '{name}'"])
```

**Secure code:**
```python
# GOOD: parameterized
User.objects.raw("SELECT * FROM users WHERE name = %s", [name])
cursor.execute("SELECT * FROM users WHERE id = %s", [user_id])
Model.objects.filter(name=name)
```

### 1.2 Django `mark_safe` / `|safe` XSS

```
# GREP PATTERNS (vulnerable):
mark_safe\(
\|safe
SafeData
format_html\(.*\{
```

**Vulnerable code:**
```python
from django.utils.safestring import mark_safe
mark_safe(f"<div>{user_input}</div>")  # XSS if user_input is unescaped
```

```html
<!-- BAD: bypasses auto-escaping -->
{{ user_comment|safe }}
```

**Secure code:**
```python
from django.utils.html import format_html
format_html("<div>{}</div>", user_input)  # Auto-escapes
```

### 1.3 Django DEBUG=True in Production

**Real CVE exposure:** Stack traces reveal SECRET_KEY, database credentials, installed apps.

```
# GREP PATTERNS:
DEBUG\s*=\s*True
DEBUG\s*=\s*1
```

### 1.4 Timing Attack on Authentication (Django)

**Real CVE:** CVE-2024-39329 -- Username enumeration via timing in `ModelBackend.authenticate()`.

```
# GREP PATTERNS (vulnerable):
if user.password == provided_password
== .*password
password .*==
!=.*password
api_key == request
secret == request
token == provided
```

**Secure code:**
```python
import hmac
hmac.compare_digest(a, b)  # Constant-time comparison
```

### 1.5 Django `strip_tags()` DoS

**Real CVE:** CVE-2024-53907 -- DoS via nested incomplete HTML entities in `strip_tags()`.

```
# GREP PATTERNS:
strip_tags\(
striptags
urlize\(
urlizetrunc\(
```

Mitigation: Validate/limit input length BEFORE calling these functions.

### 1.6 Django Path Traversal in Custom Storage

**Real CVE:** CVE-2024-39330 -- Directory traversal in custom `Storage.generate_filename()`.

```
# GREP PATTERNS (vulnerable):
class.*Storage.*:
def generate_filename
def save.*filename
os\.path\.join\(.*request
```

Check: Any custom Storage subclass MUST call `super().generate_filename()` or reimplement path validation.

### 1.7 Unrestricted File Upload

```
# GREP PATTERNS:
FileField\(
ImageField\(
request\.FILES
uploaded_file\.name
content_type
```

Check: Validate file extension, MIME type, and file size. Never trust `content_type` from the client.

### 1.8 SSRF via User-Supplied URLs

```
# GREP PATTERNS:
requests\.get\(.*request
requests\.post\(.*request
urllib\.request\.urlopen\(
httpx\.get\(.*request
fetch\(.*req\.body
http\.get\(.*req\.
```

Check: Allowlist target domains. Block private IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.169.254).

### 1.9 Mass Assignment / Over-Posting

```
# GREP PATTERNS (vulnerable):
\.update\(\*\*request\.data
\.update\(\*\*request\.POST
Model\(\*\*request
serializer\.save\(
form = .*Form\(request\.POST\)
exclude\s*=\s*\[
```

Check: Always use explicit field allowlists, never `exclude`.

### 1.10 Insecure Deserialization

```
# GREP PATTERNS:
pickle\.loads\(
pickle\.load\(
yaml\.load\((?!.*SafeLoader)
yaml\.load\((?!.*safe_load)
marshal\.loads\(
shelve\.open\(
```

**Vulnerable code:**
```python
import pickle
data = pickle.loads(request.body)  # Arbitrary code execution
import yaml
data = yaml.load(user_input)  # Code execution via !!python/object
```

**Secure code:**
```python
import yaml
data = yaml.safe_load(user_input)
```

---

## 2. NEW ATTACK VECTORS IN 2025

### 2.1 AI/LLM-Specific Attacks (OWASP LLM Top 10)

**LLM01 -- Prompt Injection:**
```
# GREP PATTERNS (vulnerable):
openai\..*create\(.*user_input
\.invoke\(.*Payload.*user
prompt.*=.*f".*{.*user
prompt.*=.*format\(.*request
messages.*\{.*role.*user.*content.*request
```

**Vulnerable code:**
```python
# BAD: unsanitized user input in prompt
prompt = f"Summarize this: {request.data['text']}"
response = openai.chat.completions.create(messages=[{"role": "user", "content": prompt}])
```

**LLM02 -- Insecure Output Handling:**
```
# GREP PATTERNS:
\.html\(.*ai_response
mark_safe\(.*llm
innerHTML.*=.*ai
eval\(.*response
exec\(.*response
```

Check: ALWAYS sanitize LLM output with `strip_tags()` before storage or display. Treat as untrusted input.

### 2.2 Supply Chain Attacks (NEW: OWASP A03:2025)

This is a **new entry** in OWASP Top 10 2025 (was not in 2021 list).

```
# GREP PATTERNS in package files:
# Check pyproject.toml / requirements.txt for:
#   - Packages with typosquatted names (e.g., djang0, dajngo, requets)
#   - Packages with no pinned versions
#   - Packages from non-standard indexes
#   - install_requires with >= but no upper bound

# Check package.json for:
#   - "preinstall" or "postinstall" scripts
#   - Dependencies pointing to git URLs or tarballs
#   - "*" version specifiers
```

```
# GREP in package.json:
"preinstall"
"postinstall"
"prepare"
"prepublish"
```

**Dependency confusion detection:**
```
# Check for private packages that could be name-squatted:
# In .npmrc or pip.conf -- verify private registry is configured
# In pyproject.toml -- check [tool.uv.sources] or --index-url
```

### 2.3 DoubleClickjacking (NEW 2024)

A new UI redressing attack that bypasses traditional clickjacking defenses including `X-Frame-Options` and SameSite cookies.

```
# GREP PATTERNS (check headers are set):
X-Frame-Options
Content-Security-Policy.*frame-ancestors
SECURE_BROWSER_XSS_FILTER
```

### 2.4 Web Cache Deception / Poisoning (2024-2025)

**Portswigger #9 of 2024.** Exploits inconsistent path normalization between cache and origin.

```
# GREP PATTERNS:
@cache_page
cache\.set\(
Vary.*header
CACHE_MIDDLEWARE
```

Check: Cache keys must include tenant identifier. Never cache authenticated responses without proper Vary headers.

### 2.5 OAuth Flow Hijacking via Cookie Tossing (2024)

```
# GREP PATTERNS:
SOCIAL_AUTH
oauth
allauth
SESSION_COOKIE_DOMAIN
CSRF_COOKIE_DOMAIN
```

Check: Restrict cookie scope, validate state parameter in OAuth flows.

### 2.6 HTTP Request Smuggling (TE.0 variant, 2024)

```
# GREP PATTERNS in server configs:
Transfer-Encoding
Content-Length
```

Mitigation: Use HTTP/2 end-to-end. Reject ambiguous Transfer-Encoding headers.

### 2.7 FastAPI-Specific: Timing Side-Channel in API Key Verification

**Real CVE:** CVE-2026-23996 -- `fastapi-api-key` timing attack.
**Real CVE:** CVE-2025-54365 -- `fastapi-guard` regex bypass.

```
# GREP PATTERNS:
api_key ==
token ==
secret ==
key == request
```

Always use `hmac.compare_digest()` for secret comparison.

### 2.8 ReDoS in Input Validation

**Real CVE:** CVE-2025-53539 -- ReDoS in `fastapi-guard`.

```
# GREP PATTERNS:
re\.compile\(
re\.match\(
re\.search\(
RegExp\(
new RegExp\(
/\(.*\+\).*\+/
/\(.*\*\).*\*/
```

Check: Look for nested quantifiers `(a+)+`, `(a*)*`, `([a-z]+)*` -- these are ReDoS-prone.

---

## 3. GRAPHQL SECURITY PATTERNS (Saleor-Relevant)

### 3.1 Introspection Enabled in Production

```graphql
# ATTACK QUERY:
{ __schema { types { name fields { name type { name } } } } }
{ __type(name: "User") { fields { name type { name } } } }
```

```
# GREP PATTERNS:
introspection.*True
introspection.*true
enable_introspection
GRAPHQL_INTROSPECTION
__schema
__type
```

### 3.2 Query Depth Attack (DoS)

```graphql
# ATTACK QUERY:
query evil {
  album(id: 42) {
    songs { album { songs { album { songs { album { id } } } } } }
  }
}
```

```
# GREP PATTERNS (check mitigations exist):
depth_limit
max_depth
query_depth
MaxQueryDepthValidator
DepthAnalysisBackend
```

If none found, the API is vulnerable.

### 3.3 Alias-Based Batching Attack (Rate Limit Bypass)

```graphql
# ATTACK QUERY:
query {
  a1: login(user: "admin", pass: "pass1") { token }
  a2: login(user: "admin", pass: "pass2") { token }
  a3: login(user: "admin", pass: "pass3") { token }
  # ... 1000 aliases = 1000 login attempts in 1 request
}
```

```
# GREP PATTERNS (check mitigations):
max_aliases
alias_limit
query_cost
rate_limit.*per_operation
```

### 3.4 Field Suggestion Information Disclosure

When a query uses a wrong field name, GraphQL often suggests valid field names.

```
# GREP PATTERNS:
did_you_mean
suggestion
DisableIntrospection
```

Disable field suggestions in production.

### 3.5 Unbounded Pagination (Resource Exhaustion)

```graphql
# ATTACK QUERY:
query { users(first: 99999999) { edges { node { id email } } } }
```

```
# GREP PATTERNS:
first.*=.*request
max_page_size
DEFAULT_PAGE_SIZE
pagination.*limit
```

Check: Enforce a `max_page_size` (typically 100).

### 3.6 Authorization Bypass (IDOR via GraphQL)

```graphql
# ATTACK: access another tenant's data by guessing ID
query { order(id: "T3JkZXI6MTIz") { customer { email } } }
mutation { deleteDocument(id: "OTHER_TENANT_DOC_ID") { ok } }
```

```
# GREP PATTERNS:
resolve.*id.*=.*info\.context
get_object_or_404
\.objects\.get\(id=
\.objects\.filter\(id=
```

Check: Every resolver must filter by current tenant/user, not just by ID.

### 3.7 Mutation Without Authentication

```
# GREP PATTERNS:
@permission_classes\(\[\]\)
permission_classes.*AllowAny
IsAuthenticated.*not.*in
login_required.*False
```

### 3.8 GraphQL Injection via Variables

```graphql
# ATTACK:
query { user(name: "admin' OR '1'='1") { id } }
```

```
# GREP PATTERNS (vulnerable):
resolve.*kwargs\[
\.format\(.*kwargs
f".*{kwargs
\.raw\(.*variable
```

---

## 4. TYPESCRIPT / NODE.JS MONOREPO SECURITY PITFALLS

### 4.1 Prototype Pollution

```
# GREP PATTERNS:
__proto__
constructor\[
Object\.assign\(.*req\.body
Object\.assign\(.*request
\.merge\(.*req
lodash\.merge\(
_.merge\(
_.defaultsDeep\(
```

**Vulnerable code:**
```javascript
const config = {};
Object.assign(config, req.body); // __proto__ pollution
_.merge(config, userInput);      // deep merge pollution
```

**Secure code:**
```javascript
const config = Object.create(null);
// Or use Map instead of plain objects
```

### 4.2 Cross-Package Dependency Confusion in Monorepos

```
# GREP PATTERNS in package.json:
"dependencies".*"@internal/
"workspace:
"link:
"file:
```

Risk: Internal packages (e.g., `@mycompany/utils`) can be typosquatted on public npm. If `.npmrc` does not properly route scoped packages to private registry, `npm install` fetches the public malicious version.

```
# Check .npmrc:
@mycompany:registry=https://private.registry.com
```

### 4.3 Shared node_modules Escalation

In monorepos, a compromised dependency in one package can access sibling packages via hoisted `node_modules`.

```
# GREP PATTERNS:
"workspaces"
"nohoist"
node_modules/\.package-lock
```

### 4.4 `eval()` and Dynamic Code Execution

```
# GREP PATTERNS:
eval\(
new Function\(
setTimeout\(.*string
setInterval\(.*string
child_process\.exec\(
execSync\(
```

### 4.5 `innerHTML` / DOM XSS

```
# GREP PATTERNS:
innerHTML
outerHTML
document\.write\(
insertAdjacentHTML
dangerouslySetInnerHTML
v-html
\[innerHTML\]
```

### 4.6 Path Traversal via `path.join`

```
# GREP PATTERNS:
path\.join\(.*req\.
path\.resolve\(.*req\.
fs\.readFile\(.*req
fs\.writeFile\(.*req
fs\.unlink\(.*req
```

**Vulnerable code:**
```javascript
const file = path.join('/uploads', req.params.filename);
// req.params.filename = "../../etc/passwd" => path traversal
```

### 4.7 Missing Rate Limiting on Express/Fastify

```
# GREP PATTERNS (check exists):
express-rate-limit
rate-limit
rateLimit
throttle
```

### 4.8 Insecure JWT Handling

```
# GREP PATTERNS:
algorithm.*none
algorithms.*\[.*none
verify.*false
jwt\.decode\((?!.*verify)
JWT_SECRET.*=.*["']
```

---

## 5. OWASP TOP 10 2025 vs 2021 -- CHANGES

| 2025 Code | 2025 Name | 2021 Equivalent | Change |
|-----------|-----------|-----------------|--------|
| A01:2025 | Broken Access Control | A01:2021 | Same position |
| A02:2025 | Security Misconfiguration | A05:2021 | Moved UP from #5 |
| **A03:2025** | **Software Supply Chain Failures** | **NEW** | **DID NOT EXIST in 2021** |
| A04:2025 | Cryptographic Failures | A02:2021 | Moved DOWN from #2 |
| A05:2025 | Injection | A03:2021 | Moved DOWN from #3 |
| A06:2025 | Insecure Design | A04:2021 | Moved DOWN from #4 |
| A07:2025 | Authentication Failures | A07:2021 (renamed) | Was "Identification and Authentication Failures" |
| A08:2025 | Software or Data Integrity Failures | A08:2021 | Same position |
| A09:2025 | Security Logging and Alerting Failures | A09:2021 (renamed) | Was "Security Logging and Monitoring Failures" |
| **A10:2025** | **Mishandling of Exceptional Conditions** | **NEW** | **Replaced "SSRF" from A10:2021** |

**Key takeaways for the security agent:**
1. **Supply chain is now #3** -- dependency auditing is mandatory, not optional.
2. **SSRF dropped off the top 10** but is still in OWASP API Security Top 10 (API7:2023).
3. **Misconfiguration moved up** -- DEBUG=True, exposed admin panels, default credentials.
4. **Exception handling is now top 10** -- unhandled exceptions leaking stack traces, improper error responses.

---

## 6. REAL CVEs FROM DJANGO AND FASTAPI (2024-2025)

### Django CVEs

| CVE | Date | Severity | Vulnerability | Grep Pattern |
|-----|------|----------|---------------|-------------|
| CVE-2024-24680 | 2024-02 | Moderate | DoS in `intcomma` template filter | `intcomma` |
| CVE-2024-38875 | 2024-07 | Moderate | DoS in `urlize()` via bracket sequences | `urlize\(`, `urlizetrunc\(` |
| CVE-2024-39329 | 2024-07 | Low | Timing attack username enumeration | `ModelBackend`, `authenticate\(` |
| CVE-2024-39330 | 2024-07 | Low | Path traversal in custom Storage.save() | `class.*Storage`, `generate_filename` |
| CVE-2024-39614 | 2024-07 | Moderate | DoS in `get_supported_language_variant()` | `get_supported_language_variant` |
| CVE-2024-53907 | 2024-12 | Moderate | DoS in `strip_tags()` nested entities | `strip_tags\(`, `striptags` |
| CVE-2024-53908 | 2024-12 | High | SQL injection in `HasKey` on Oracle | `HasKey`, `has_key`, `__has_key` |
| CVE-2025-57833 | 2025-09 | High | SQL injection via column aliases | `.alias\(`, `.annotate\(.*RawSQL` |
| CVE-2025-59681 | 2025-10 | High | SQL injection via column aliases | `.alias\(`, `RawSQL\(` |
| CVE-2026-1207 | 2026-02 | High | SQL injection | `.raw\(`, `cursor.execute\(` |
| CVE-2026-1287 | 2026-02 | High | SQL injection | `.raw\(`, `cursor.execute\(` |
| CVE-2026-25673 | 2026-03 | Moderate | Uncontrolled resource consumption | Input length validation |

### FastAPI Ecosystem CVEs

| CVE | Date | Severity | Vulnerability | Grep Pattern |
|-----|------|----------|---------------|-------------|
| CVE-2024-42818 | 2024-08 | Moderate | XSS in fastapi-admin config-create | `fastapi-admin`, `config.*create` |
| CVE-2025-53539 | 2025-07 | Moderate | ReDoS in fastapi-guard | `fastapi-guard`, `re\.compile` |
| CVE-2025-54365 | 2025-07 | High | Regex bypass in fastapi-guard | `fastapi-guard`, IP validation |
| CVE-2026-23996 | 2026-01 | Low | Timing side-channel in fastapi-api-key | `api_key ==`, `==.*api_key` |

---

## 7. COMPOSITE GREP CHEAT SHEET

One-liners for the security agent to run against any codebase:

```bash
# --- CRITICAL: SQL Injection ---
grep -rn "\.raw\(.*f['\"]" --include="*.py"
grep -rn "\.raw\(.*\.format" --include="*.py"
grep -rn "cursor\.execute.*f['\"]" --include="*.py"
grep -rn "\.extra(" --include="*.py"
grep -rn "RawSQL(" --include="*.py"

# --- CRITICAL: XSS ---
grep -rn "mark_safe(" --include="*.py"
grep -rn "|safe" --include="*.html"
grep -rn "innerHTML" --include="*.js" --include="*.ts" --include="*.tsx"
grep -rn "dangerouslySetInnerHTML" --include="*.tsx" --include="*.jsx"
grep -rn "v-html" --include="*.vue"

# --- CRITICAL: Code Execution ---
grep -rn "eval(" --include="*.py" --include="*.js" --include="*.ts"
grep -rn "exec(" --include="*.py"
grep -rn "pickle\.load" --include="*.py"
grep -rn "yaml\.load(" --include="*.py" | grep -v "SafeLoader" | grep -v "safe_load"
grep -rn "subprocess.*shell=True" --include="*.py"
grep -rn "os\.system(" --include="*.py"

# --- HIGH: Authentication ---
grep -rn "== .*password\|password.*==" --include="*.py" --include="*.js"
grep -rn "DEBUG\s*=\s*True" --include="*.py"
grep -rn "SECRET_KEY.*=.*['\"]" --include="*.py"
grep -rn "AllowAny" --include="*.py"

# --- HIGH: SSRF ---
grep -rn "requests\.get(.*request\." --include="*.py"
grep -rn "urllib.*urlopen.*request\." --include="*.py"
grep -rn "httpx.*request\." --include="*.py"

# --- HIGH: Prototype Pollution (JS) ---
grep -rn "__proto__" --include="*.js" --include="*.ts"
grep -rn "Object\.assign.*req\.body" --include="*.js" --include="*.ts"
grep -rn "\.merge(.*req\." --include="*.js" --include="*.ts"

# --- MEDIUM: Django-Specific ---
grep -rn "strip_tags(" --include="*.py"
grep -rn "urlize(" --include="*.py"
grep -rn "@csrf_exempt" --include="*.py"
grep -rn "CSRF_COOKIE_SECURE\s*=\s*False" --include="*.py"
grep -rn "SESSION_COOKIE_SECURE\s*=\s*False" --include="*.py"

# --- MEDIUM: Supply Chain ---
grep -rn '"preinstall"\|"postinstall"' --include="package.json"
grep -rn '"*"' --include="package.json"

# --- MEDIUM: GraphQL ---
grep -rn "introspection.*[Tt]rue" --include="*.py" --include="*.js" --include="*.ts"
grep -rn "__schema" --include="*.graphql" --include="*.gql"

# --- MEDIUM: LLM/AI ---
grep -rn 'f".*{.*user\|f".*{.*request' --include="*.py" | grep -i "prompt\|llm\|openai\|chat"
grep -rn "mark_safe.*\(.*response\|\.html.*ai_\|\.html.*llm" --include="*.py"

# --- LOW: Information Disclosure ---
grep -rn "\.objects\.all()" --include="*.py"
grep -rn "traceback\.print_exc" --include="*.py"
grep -rn "print(.*password\|print(.*secret\|print(.*token" --include="*.py"
```

---

## 8. OWASP API SECURITY TOP 10 (2023) -- Quick Reference

| Code | Name | Key grep pattern |
|------|------|-----------------|
| API1 | Broken Object Level Authorization | `.objects.get(id=` without tenant filter |
| API2 | Broken Authentication | `AllowAny`, missing `@login_required` |
| API3 | Broken Object Property Level Authorization | `**request.data`, `exclude = [` |
| API4 | Unrestricted Resource Consumption | Missing `@ratelimit`, no pagination limits |
| API5 | Broken Function Level Authorization | `@csrf_exempt`, missing `@permission_required` |
| API6 | Unrestricted Access to Sensitive Business Flows | No bot detection on registration/checkout |
| API7 | Server Side Request Forgery | `requests.get(user_url)` |
| API8 | Security Misconfiguration | `DEBUG = True`, `CORS_ALLOW_ALL_ORIGINS` |
| API9 | Improper Inventory Management | Unreferenced API endpoints, old API versions live |
| API10 | Unsafe Consumption of APIs | Trusting third-party API responses without validation |
