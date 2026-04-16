from .settings import *  # noqa: F401, F403
import os

BASE_DIR_LOCAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR_LOCAL, "local.db"),
    }
}

# Celery runs tasks synchronously so no broker is needed locally
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Tesseract binary path — auto-detected, override via env var if needed
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "/usr/bin/tesseract")

# Tell DeepFace to use tf-keras (required for tensorflow >= 2.16)
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")

# ── Face verification settings ────────────────────────────────────────────────
#
# Model options (accuracy ↑ = slower download on first run):
#   "VGG-Face"   — default, fast, threshold ~0.40
#   "Facenet512" — more accurate, threshold ~0.30
#   "ArcFace"    — state-of-the-art accuracy, threshold ~0.68
#
# Threshold: maximum allowed distance to consider two faces the same person.
# Lower  = stricter (fewer false accepts, more false rejects).
# Higher = looser  (more false accepts, fewer false rejects).
# Set to None to use DeepFace's built-in default for the chosen model.
#
FACE_MODEL     = os.environ.get("FACE_MODEL",     "ArcFace")
FACE_THRESHOLD = os.environ.get("FACE_THRESHOLD", "0.50")
