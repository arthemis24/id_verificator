from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """
    Brute-force protection on POST /api/token/.

    Keyed by client IP (not user — the attacker isn't authenticated yet).
    Rate is intentionally strict: 5 attempts/minute is enough for a human
    but makes credential-stuffing attacks impractical.

    Why AnonRateThrottle:
        The login endpoint is called before authentication succeeds, so there
        is no user identity to key on. IP-based throttling is the correct
        primitive here. In production, ensure your reverse proxy forwards the
        real client IP via X-Forwarded-For and set NUM_PROXIES accordingly.
    """
    scope = "login"


class UploadRateThrottle(UserRateThrottle):
    """
    Flood / abuse protection on POST /api/verify/.

    Keyed by authenticated user. Each verification triggers OCR (CPU-heavy)
    and a DeepFace comparison (GPU/CPU-heavy), so a low per-user cap prevents
    a single account from monopolising worker resources or accumulating a large
    dataset of other people's documents.
    """
    scope = "upload"
