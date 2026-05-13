# ID Verificator

A Django REST API that verifies a user's identity by combining two independent checks:

1. **OCR** — extracts the name and birth date from a scanned ID document and cross-checks them against the data submitted by the user.
2. **Face comparison** — compares the photo on the ID document with a selfie using [DeepFace](https://github.com/serengil/deepface).

Both checks run asynchronously via a Celery worker. The client gets a `document_id` immediately and polls a status endpoint to retrieve the final result.

---

## Table of contents

- [Stack](#stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Local development (without Docker)](#local-development-without-docker)
- [Environment variables](#environment-variables)
- [API reference](#api-reference)
- [Verification flow](#verification-flow)
- [Security](#security)
- [Running tests](#running-tests)

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Django 5 + Django REST Framework |
| Authentication | JWT via `djangorestframework-simplejwt` |
| API docs | `drf-spectacular` (OpenAPI 3 / Swagger UI) |
| Database | PostgreSQL 15 |
| Async task queue | Celery + Redis 7 |
| OCR | Tesseract via `pytesseract` + `pdf2image` |
| Face verification | DeepFace (ArcFace model) |
| Containerization | Docker + docker-compose |

---

## Project structure

```
id_verificator/
├── src/
│   ├── id_verificator/          # Django project
│   │   ├── settings.py          # Production settings (reads from env)
│   │   ├── settings_local.py    # Local dev overrides (SQLite, eager Celery, relaxed throttles)
│   │   ├── settings_test.py     # Test overrides (SQLite in-memory, no broker)
│   │   ├── urls.py              # Root URL conf (JWT + verification + media + Swagger)
│   │   └── celery.py            # Celery app init
│   └── verification/            # Core app
│       ├── models.py            # Document model (DocumentType choices, updated_at)
│       ├── serializers.py       # VerificationSerializer, DocumentStatusSerializer
│       ├── views.py             # VerificationView, VerificationStatusView
│       ├── media_views.py       # ProtectedMediaView (JWT + ownership check)
│       ├── services.py          # Business logic: OCR, face comparison, result aggregation
│       ├── tasks.py             # verify_document Celery task + webhook dispatch
│       ├── validators.py        # SSRF validator, magic-byte file content validator
│       ├── throttles.py         # LoginRateThrottle, UploadRateThrottle
│       ├── ai_utils.py          # Tesseract OCR helpers (extract, normalise, match)
│       ├── admin.py             # Django admin registration
│       └── urls.py              # /verify/ and /verify/<id>/status/
├── tests/
│   └── verification_tests.py    # Full test suite — 77 tests, 81 %+ coverage
├── Dockerfile                   # Multi-stage build, non-root user
├── docker-compose.yml           # DB, Redis, web (gunicorn), worker
├── pyproject.toml               # pytest + coverage configuration
├── requirements.txt
└── .env.example                 # Template — copy to .env and fill in secrets
```

---

## Getting started

### Prerequisites

- Docker and docker-compose

### 1. Clone and configure

```bash
git clone https://github.com/arthemis24/id_verificator.git
cd id_verificator
cp .env.example .env   # fill in SECRET_KEY and POSTGRES_PASSWORD at minimum
```

### 2. Build and start all services

```bash
docker-compose up --build
```

This starts four containers:

| Container | Role | Exposed |
|---|---|---|
| `id_verificator_db` | PostgreSQL 15 | internal only |
| `id_verificator_redis` | Redis 7 | internal only |
| `id_verificator_web` | Django + gunicorn | `localhost:8001` |
| `id_verificator_worker` | Celery worker | — |

The web container runs `migrate` and `collectstatic` automatically before starting.

### 3. Create a user

```bash
docker-compose exec web python manage.py createsuperuser
```

### 4. Obtain a JWT token

```bash
curl -X POST http://localhost:8001/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "your_user", "password": "your_password"}'
```

Response:
```json
{
  "access": "<access_token>",
  "refresh": "<refresh_token>"
}
```

Use the `access` token as a `Bearer` header on all subsequent requests.

### 5. Browse the API docs

Swagger UI is available at:

```
http://localhost:8001/api/docs/
```

ReDoc is available at:

```
http://localhost:8001/api/redoc/
```

---

## Local development (without Docker)

Requires Python 3.12, Tesseract, and (optionally) a local PostgreSQL/Redis. If you just want to run the app and tests, SQLite + in-process Celery are used automatically.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Apply migrations
DJANGO_SETTINGS_MODULE=id_verificator.settings_local PYTHONPATH=src \
  python3 src/manage.py migrate

# Start the dev server
DJANGO_SETTINGS_MODULE=id_verificator.settings_local PYTHONPATH=src \
  python3 src/manage.py runserver
```

`settings_local.py` defaults:
- SQLite database (`local.db` at project root)
- `CELERY_TASK_ALWAYS_EAGER = True` — tasks run synchronously, no broker needed
- Relaxed throttle rates (1000/min)
- Human-readable colored log output
- `FACE_MODEL = ArcFace`, `FACE_THRESHOLD = 0.50`

---

## Environment variables

All variables are read from the environment (or `.env` via docker-compose). No defaults are safe for production — set at least `SECRET_KEY` and `POSTGRES_PASSWORD`.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | — | Django secret key — **required, no safe default** |
| `DEBUG` | `False` | Set to `True` only in local dev |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated list of allowed hostnames |
| `DB_NAME` | `id_verificator` | PostgreSQL database name |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | — | PostgreSQL password — **required** |
| `DB_HOST` | `localhost` | PostgreSQL host (`db` inside compose) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis broker URL |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Redis result backend URL |
| `FACE_MODEL` | `ArcFace` | DeepFace model (`VGG-Face`, `Facenet512`, `ArcFace`) |
| `FACE_THRESHOLD` | `0.50` | Max face distance to accept as a match (lower = stricter) |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## API reference

All endpoints require `Authorization: Bearer <access_token>` unless noted.

### Obtain token

```
POST /api/token/
```

| Field | Type | Required |
|---|---|---|
| `username` | string | yes |
| `password` | string | yes |

Rate limit: **5 requests / minute per IP**.

Response `200`:
```json
{ "access": "<access_token>", "refresh": "<refresh_token>" }
```

---

### Refresh token

```
POST /api/token/refresh/
```

Body: `{ "refresh": "<refresh_token>" }` → Response `200`: `{ "access": "<new_access_token>" }`

---

### Submit identity verification

```
POST /api/verify/
Content-Type: multipart/form-data
```

Rate limit: **20 requests / hour per authenticated user**.

| Field | Type | Required | Constraints |
|---|---|---|---|
| `first_name` | string | yes | |
| `last_name` | string | yes | |
| `birth_date` | date | yes | `YYYY-MM-DD` |
| `document_type` | string | yes | `passport`, `id_card`, `driver_license`, `residence_permit` |
| `doc_file` | file | yes | PDF, PNG, JPG, JPEG — max 5 MB — content validated by magic bytes |
| `selfie_file` | file | yes | PNG, JPG, JPEG — max 5 MB — content validated by magic bytes |
| `callback_url` | URL | no | HTTPS only, must not resolve to a private/internal IP (SSRF-protected) |

Response `202`:
```json
{
  "message": "Fichiers uploadés et vérification en cours",
  "document_id": 42,
  "callback_url": "https://example.com/webhook"
}
```

The verification runs asynchronously. Use `document_id` to poll the status endpoint. If `callback_url` is provided, a `POST` with the result is sent there once verification completes (3 retries with exponential back-off on failure).

---

### Check verification status

```
GET /api/verify/<document_id>/status/
```

Returns `404` if the document belongs to a different user.

Response `200`:
```json
{
  "id": 42,
  "verified": true,
  "verification_result": {
    "verified": true,
    "face_verified": true,
    "face_score": 0.21,
    "ocr_verified": true,
    "ocr_data": {
      "first_name": "RODDY",
      "last_name": "MBOGNING",
      "birth_date": "14/11/1992",
      "name_check": { "method": "label", "first_name_match": true, "last_name_match": true },
      "raw_text": "..."
    }
  },
  "created_at": "2026-04-15T10:00:00Z",
  "updated_at": "2026-04-15T10:00:05Z"
}
```

While the task is still running, `verified` is `false` and `verification_result` is `null`.

---

### Access uploaded files

```
GET /media/<path>
```

All media files are served through a protected endpoint — direct filesystem paths are not accessible. JWT authentication and document ownership are verified before serving the file.

---

## Verification flow

```
POST /api/verify/
        │
        ├── Validate fields, document_type choices, file extensions & sizes
        ├── Validate file content (magic bytes — rejects disguised files)
        ├── Validate callback_url (SSRF check if provided)
        ├── Save Document to DB (verified=False)
        └── Dispatch verify_document.delay(doc.id)
                │
                ├── 1. OCR  (services.run_ocr)
                │       ├── Extract first_name, last_name, birth_date via Tesseract
                │       └── Cross-check names (label match → full-text fallback)
                │
                ├── 2. Face comparison  (services.run_face_verification)
                │       └── DeepFace.verify(doc_file, selfie_file, model=ArcFace)
                │
                ├── 3. Aggregate  (services.build_verification_result)
                │       verified = face_verified AND ocr_match
                │       → saved to Document.verified + Document.verification_result
                │
                └── 4. Webhook  (tasks._dispatch_webhook, if callback_url set)
                        POST result JSON to callback_url
                        Retries up to 3× with exponential back-off (1 s, 2 s, 4 s)

GET /api/verify/<id>/status/  ← client polls until verification_result is not null
```

---

## Security

### Authentication
JWT Bearer tokens only. The Browsable API is disabled in production (`DEFAULT_RENDERER_CLASSES: [JSONRenderer]`), preventing session-based auth bypass.

### Rate limiting
| Scope | Class | Limit | Applied to |
|---|---|---|---|
| `login` | `AnonRateThrottle` | 5 / minute per IP | `POST /api/token/` |
| `upload` | `UserRateThrottle` | 20 / hour per user | `POST /api/verify/` |

Returns `HTTP 429 Too Many Requests` when exceeded.

### File upload security
- **Extension allowlist**: `.pdf`, `.png`, `.jpg`, `.jpeg`
- **Size limit**: 5 MB per file
- **Magic-byte validation**: first 8 bytes are compared against known signatures (PDF: `%PDF`, JPEG: `\xff\xd8\xff`, PNG: `\x89PNG\r\n\x1a\n`). A file renamed to `.jpg` but containing a different format is rejected.

### SSRF protection
`callback_url`, if supplied, is validated before saving:
1. Scheme must be `http` or `https`
2. IP literals are checked against a blocklist (loopback, RFC-1918, link-local, AWS metadata `169.254.169.254`, etc.)
3. Hostnames are resolved via DNS and each resolved IP is checked against the same blocklist

### Media access control
All files under `/media/` are served by `ProtectedMediaView` — a DRF `APIView` that:
- Requires a valid JWT token
- Verifies the requesting user owns at least one document referencing the file
- Normalises and bounds-checks the path to prevent directory traversal

### HTTP security headers (production)
| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `SECURE_SSL_REDIRECT` | `True` |

---

## Running tests

The test suite uses SQLite in-memory and runs Celery tasks synchronously — no database server or Redis required.

```bash
# Install dependencies (first time)
pip install -r requirements.txt

# Run all tests with coverage
PYTHONPATH=src pytest tests/ -v
```

`pyproject.toml` sets `DJANGO_SETTINGS_MODULE=id_verificator.settings_test` automatically, so no env var is needed.

The suite enforces a **minimum 80 % coverage** threshold (`--cov-fail-under=80`). An HTML coverage report is written to `htmlcov/` after each run.

Test classes:

| Class | What it covers |
|---|---|
| `TestVerificationAPI` | Upload happy path, extension/size/magic-byte validation, auth |
| `TestStatusAPI` | Status polling, ownership isolation, pending state |
| `TestProtectedMedia` | Auth, ownership, path traversal prevention |
| `TestSSRFValidator` | Private IPs, link-local, AWS metadata, scheme blocklist |
| `TestThrottleConfiguration` | Scope names, view wiring, 429 responses |
| `TestDocumentModel` | `__str__`, `updated_at`, type choices validation |
| `TestVerificationServices` | OCR name matching, face result aggregation (unit, no DB) |
| `TestWebhookDispatch` | Payload structure, retries, back-off, no-raise on failure |
| `TestStructuredLogging` | Log level and message correctness via `caplog` |
