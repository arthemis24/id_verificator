# ID Verificator

A Django REST API that verifies a user's identity by combining two independent checks:

1. **OCR** — extracts the name and birth date from a scanned ID document and cross-checks them against the data submitted by the user.
2. **Face comparison** — compares the photo on the ID document with a selfie using [DeepFace](https://github.com/serengil/deepface).

Both checks run asynchronously via a Celery worker. The client gets a `document_id` immediately and can poll a status endpoint to retrieve the final result.

---

## Table of contents

- [Stack](#stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Environment variables](#environment-variables)
- [API reference](#api-reference)
- [Verification flow](#verification-flow)
- [Running tests](#running-tests)

---

## Stack

| Layer | Technology |
|---|---|
| Framework | Django 5 + Django REST Framework |
| Authentication | JWT via `djangorestframework-simplejwt` |
| Database | PostgreSQL |
| Async task queue | Celery + Redis |
| OCR | Tesseract OCR via `pytesseract` + `pdf2image` |
| Face verification | DeepFace |
| Containerization | Docker + docker-compose |

---

## Project structure

```
id_verificator/
├── src/
│   ├── id_verificator/          # Django project (settings, urls, celery)
│   │   ├── settings.py          # Main settings (reads from env)
│   │   ├── settings_test.py     # Test settings (SQLite, no broker needed)
│   │   ├── urls.py              # Root URL conf (includes JWT token endpoints)
│   │   └── celery.py            # Celery app init
│   └── verification/            # Core app
│       ├── models.py            # Document model
│       ├── serializers.py       # VerificationSerializer, DocumentStatusSerializer
│       ├── views.py             # VerificationView, VerificationStatusView
│       ├── urls.py              # /verify/ and /verify/<id>/status/
│       ├── tasks.py             # verify_document Celery task (OCR + face check)
│       └── ai_utils.py          # OCR helpers (pytesseract / pdf2image)
├── tests/
│   └── verification_tests.py    # Full test suite (pytest)
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env                         # Environment config (copy from example below)
```

---

## Getting started

### Prerequisites

- Docker and docker-compose

### 1. Clone and configure

```bash
git clone <https://github.com/arthemis24/id_verificator.git>
cd id_verificator
cp .env .env.local   # adjust values if needed
```

### 2. Build and start all services

```bash
docker-compose up --build
```

This starts four containers:

| Container | Role | Port |
|---|---|---|
| `id_verificator_db` | PostgreSQL 15 | 5433 |
| `id_verificator_redis` | Redis 7 | 6379 |
| `id_verificator_web` | Django API | 8001 |
| `id_verificator_worker` | Celery worker | — |

The web container runs `migrate` automatically before starting.

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

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `changez_moi` | Django secret key — **change in production** |
| `DEBUG` | `True` | Set to `False` in production |
| `DB_NAME` | `id_verificator` | PostgreSQL database name |
| `DB_USER` | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | `postgres` | PostgreSQL password |
| `DB_HOST` | `db` | PostgreSQL host (service name in compose) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis broker URL |

---

## API reference

All endpoints require a JWT `Authorization: Bearer <token>` header unless otherwise noted.

### Obtain token

```
POST /api/token/
```

Body (JSON):

| Field | Type | Required |
|---|---|---|
| `username` | string | yes |
| `password` | string | yes |

Response `200`:
```json
{
  "access": "<access_token>",
  "refresh": "<refresh_token>"
}
```

---

### Refresh token

```
POST /api/token/refresh/
```

Body (JSON): `{ "refresh": "<refresh_token>" }`

Response `200`: `{ "access": "<new_access_token>" }`

---

### Submit identity verification

```
POST /api/verify/
Content-Type: multipart/form-data
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `first_name` | string | yes | |
| `last_name` | string | yes | |
| `birth_date` | date | yes | `YYYY-MM-DD` |
| `document_type` | string | yes | e.g. `passport`, `id_card` |
| `doc_file` | file | yes | PDF, PNG, JPG, JPEG — max 5 MB |
| `selfie_file` | file | yes | PNG, JPG, JPEG — max 5 MB |

Response `202`:
```json
{
  "message": "Fichiers uploadés et vérification en cours",
  "document_id": 42
}
```

The verification runs asynchronously. Use `document_id` to poll the status endpoint.

---

### Check verification status

```
GET /api/verify/<document_id>/status/
```

A user can only access their own documents — other IDs return `404`.

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
      "last_name": "MBG",
      "birth_date": "14/11/1992"
    }
  },
  "created_at": "2026-04-15T10:00:00Z"
}
```

While the task is still running, `verified` is `false` and `verification_result` is `null`.

---

## Verification flow

```
POST /api/verify/
        │
        ├── Validate fields & file types/sizes
        ├── Save Document to DB (verified=False)
        └── Dispatch verify_document.delay(doc.id)
                │
                ├── 1. OCR (pytesseract / pdf2image)
                │       ├── Extract first_name, last_name, birth_date from document
                │       └── Cross-check extracted names against submitted values
                │           (fields OCR couldn't read are skipped)
                │
                ├── 2. Face comparison (DeepFace)
                │       └── DeepFace.verify(doc_file, selfie_file)
                │
                └── 3. Aggregate
                        verified = face_verified AND ocr_match
                        → saved to Document.verified + Document.verification_result

GET /api/verify/<id>/status/  ←  client polls until verified ≠ null
```

---

## Running tests

The test suite uses SQLite in memory and does not require a running database or broker.

```bash
# Install dependencies (first time)
pip install -r requirements.txt

# Run all tests
PYTHONPATH=src pytest tests/ -v
```

The suite covers:

- Model creation and field defaults
- File extension and size validation
- OCR parsing helpers (date, first name, last name)
- `ocr_extract_info` with mocked tesseract output and error handling
- API: valid upload, invalid extension, missing field, oversized file, unauthenticated
- API: async task dispatch
- Status endpoint: happy path, pending state, not found, cross-user isolation, unauthenticated
