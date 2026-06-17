import ipaddress
import os
import socket
import urllib.parse

from django.core.exceptions import ValidationError

# ── Magic-byte signatures ─────────────────────────────────────────────────────
#
# Extension-only validation is trivially bypassed: rename shell.php to id.jpg
# and the file passes. Magic-byte inspection reads the actual binary header of
# the file and rejects anything whose content does not match its declared type.
#
# How it works: every file format begins with a known fixed byte sequence
# (the "magic number"). We read the first 8 bytes and compare against the
# expected signatures for the declared extension.

_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    ".pdf":  [b"%PDF"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png":  [b"\x89PNG\r\n\x1a\n"],
}

_READABLE_NAMES: dict[str, str] = {
    ".pdf":  "PDF",
    ".jpeg": "JPEG",
    ".png":  "PNG",
}

# Normalise .jpg → .jpeg so both aliases share the same signature entry.
_EXT_ALIASES: dict[str, str] = {".jpg": ".jpeg"}


def validate_file_content(file) -> None:
    """
    Verify that the uploaded file's binary content matches its declared
    extension using magic-byte inspection.

    Attack vector prevented:
        An attacker renames a malicious file (e.g. a PHP shell or an HTML/JS
        file) to ``evil.jpg``. Extension-only validation passes it through.
        The file is stored on the server, and if it is later executed or
        rendered in a browser, it triggers XSS, RCE, or drive-by downloads.

    Defence:
        Read the first 8 bytes of the uploaded file and compare them against
        the known magic-byte prefix for the declared extension. A mismatch
        means the file is disguised and must be rejected.

    Note on seek():
        Django wraps uploads in ``InMemoryUploadedFile`` or
        ``TemporaryUploadedFile`` — both support ``seek(0)``. We always reset
        the file pointer after reading so downstream validators and the ORM
        storage backend receive the complete file.
    """
    raw_ext = os.path.splitext(file.name)[1].lower()
    ext = _EXT_ALIASES.get(raw_ext, raw_ext)
    expected_signatures = _MAGIC_SIGNATURES.get(ext)

    if expected_signatures is None:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Accepted: PDF, JPG, JPEG, PNG."
        )

    try:
        header = file.read(8)
        file.seek(0)
    except Exception:
        raise ValidationError("Could not read file content for validation.")

    if not any(header.startswith(sig) for sig in expected_signatures):
        raise ValidationError(
            f"File content does not match its declared type ({_READABLE_NAMES.get(ext, ext)}). "
            "Uploading disguised or corrupted files is not permitted."
        )

# All RFC-1918 / link-local / loopback / metadata address spaces that must never
# be reachable via a caller-supplied URL (SSRF prevention).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),         # "this" network
    ipaddress.ip_network("10.0.0.0/8"),         # RFC-1918 private
    ipaddress.ip_network("100.64.0.0/10"),      # shared address space
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local / AWS metadata
    ipaddress.ip_network("172.16.0.0/12"),      # RFC-1918 private
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("192.168.0.0/16"),     # RFC-1918 private
    ipaddress.ip_network("198.18.0.0/15"),      # benchmark testing
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2 (docs)
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3 (docs)
    ipaddress.ip_network("224.0.0.0/4"),        # multicast
    ipaddress.ip_network("240.0.0.0/4"),        # reserved
    ipaddress.ip_network("255.255.255.255/32"), # broadcast
    # IPv6
    ipaddress.ip_network("::1/128"),            # loopback
    ipaddress.ip_network("fc00::/7"),           # unique local
    ipaddress.ip_network("fe80::/10"),          # link-local
    ipaddress.ip_network("ff00::/8"),           # multicast
]


def validate_no_ssrf(url: str) -> None:
    """
    Reject any URL that resolves to a private, loopback, link-local, or
    otherwise internal address to prevent Server-Side Request Forgery.

    Attack vector: an attacker supplies callback_url=http://169.254.169.254/
    (AWS instance metadata) or callback_url=http://10.0.0.1/ (internal service)
    and the server becomes an unauthenticated proxy into the private network.

    Defence layers:
      1. Scheme whitelist — only http/https allowed.
      2. Hostname resolution — the hostname is resolved to ALL its IP addresses.
      3. IP blocklist  — every resolved IP is checked against blocked ranges.

    Note: DNS rebinding is not fully mitigated here (the IP may change between
    validation and the actual request). For stricter protection, use a dedicated
    egress proxy that enforces the same rules at the network level.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise ValidationError("callback_url is not a valid URL.")

    if parsed.scheme not in ("http", "https"):
        raise ValidationError("callback_url must use http or https.")

    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("callback_url has no valid hostname.")

    # Reject bare IP literals that are already in a blocked range
    # (avoids a DNS lookup for obvious cases like http://127.0.0.1/)
    try:
        ip_literal = ipaddress.ip_address(hostname)
        _assert_ip_allowed(ip_literal, url)
        return
    except ValueError:
        pass  # hostname is a domain name — proceed to DNS resolution

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValidationError(
            f"callback_url hostname '{hostname}' could not be resolved. "
            "Ensure the URL is publicly reachable."
        )

    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        try:
            _assert_ip_allowed(ipaddress.ip_address(ip_str), url)
        except ValueError:
            pass


def _assert_ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, url: str) -> None:
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved or ip.is_multicast:
        raise ValidationError(
            f"callback_url resolves to a restricted address ({ip}) and cannot be used."
        )
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            raise ValidationError(
                f"callback_url resolves to a restricted address range ({ip}) and cannot be used."
            )
