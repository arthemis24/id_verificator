from .settings import *  # noqa: F401, F403

# Tests run over plain HTTP
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable throttling in tests — we test the throttle classes in isolation,
# not via the full request stack, to avoid brittle timing-dependent assertions.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    # Disable the global throttle classes; views with explicit throttle_classes
    # still work because their scope rates are still defined below.
    'DEFAULT_THROTTLE_CLASSES': [],
    'DEFAULT_THROTTLE_RATES': {
        'anon':   '1000/minute',
        'user':   '1000/minute',
        'login':  '1000/minute',
        'upload': '1000/hour',
    },
}

# Run Celery tasks synchronously — no broker/result-store connection needed.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_RESULT_BACKEND = "cache+memory://"

# Allow caplog to capture verification log records.
# Production sets propagate=False to avoid double-logging via the root handler;
# tests need propagate=True so pytest's caplog handler (attached to root) sees them.
LOGGING["loggers"]["verification"]["propagate"] = True  # noqa: F405
