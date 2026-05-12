import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "changeme-override-in-production")
DEBUG = os.environ.get("DEBUG", "False") == "True"

_allowed = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_spectacular',
    'verification',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # Production: disable the Browsable API — it leaks endpoint structure and
    # accepts session-based auth which bypasses our JWT-only policy.
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon':   '60/minute',
        'user':   '300/minute',
        'login':  '5/minute',
        'upload': '20/hour',
    },
}

# DRF throttling requires a cache backend.
# Production uses Redis (shared across all gunicorn workers + Celery nodes).
# settings_local.py and settings_test.py override this with LocMemCache / DummyCache.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
    }
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'ID Verificator API',
    'DESCRIPTION': (
        'REST API for identity verification.\n\n'
        'Each request requires a JWT Bearer token obtained from `POST /api/token/`.\n\n'
        '**Verification flow**\n'
        '1. `POST /api/verify/` — upload an ID document + selfie. Returns a `document_id`.\n'
        '2. A Celery worker runs OCR on the document and compares faces with DeepFace.\n'
        '3. `GET /api/verify/{id}/status/` — poll until `verification_result` is not null.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SECURITY': [{'jwtAuth': []}],
    'COMPONENT_SPLIT_REQUEST': True,
    'SWAGGER_UI_SETTINGS': {
        'persistAuthorization': True,
        'displayRequestDuration': True,
        'filter': True,
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

ROOT_URLCONF = 'id_verificator.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'id_verificator.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get("DB_NAME", "id_verificator"),
        'USER': os.environ.get("DB_USER", "postgres"),
        'PASSWORD': os.environ.get("DB_PASSWORD", "postgres"),
        'HOST': os.environ.get("DB_HOST", "localhost"),
        'PORT': os.environ.get("DB_PORT", 5432),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ── Security headers ──────────────────────────────────────────────────────────
#
# These are enforced by Django's SecurityMiddleware (already first in
# MIDDLEWARE). Each setting adds one HTTP response header that instructs
# browsers on how to handle the response safely.

# Prevent MIME-type sniffing: tells the browser to trust the declared
# Content-Type and not guess. Stops IE/Chrome from executing a JS file
# that was uploaded as image/jpeg.
SECURE_CONTENT_TYPE_NOSNIFF = True

# Prevent the admin UI (and any future HTML pages) from being embedded in
# an iframe on a third-party site — closes clickjacking attack surface.
X_FRAME_OPTIONS = "DENY"

# Control what Referer header is sent on cross-origin requests.
# 'strict-origin-when-cross-origin' sends the full URL only to same-origin
# requests, and only the origin (no path) to cross-origin HTTPS requests.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# HTTP Strict Transport Security: once a browser has seen this header it will
# only ever connect to this domain over HTTPS, even if the user types http://.
# 1 year (31 536 000 s) is the recommended preload duration.
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Redirect every plain-HTTP request to HTTPS at the Django layer.
# settings_local.py overrides this to False so the dev server still works.
SECURE_SSL_REDIRECT = True

# Cookies must only be transmitted over HTTPS and must not be readable by JS.
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# ── Structured logging ────────────────────────────────────────────────────────
#
# Why structured logging?
#   Plain-text logs are hard to query in production log aggregators (Datadog,
#   CloudWatch, Loki). JSON-formatted lines let you filter by field without
#   regex. Every log record emitted by the app includes:
#       timestamp, level, logger name, message, and any extra fields.
#
# Format: JSON in production, human-readable in local dev (overridden in
#         settings_local.py).
#
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "json": {
            "()": "logging.Formatter",
            # Produces a parseable one-liner. Replace with python-json-logger
            # for richer structured output (pip install python-json-logger).
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":%(message)s}'
            ),
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },

    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },

    "loggers": {
        # Django internals — suppress noisy DEBUG output in production
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # Our application code — full visibility
        "verification": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        # Celery worker
        "celery": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
