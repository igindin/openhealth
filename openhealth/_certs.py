"""Make sure Python can find CA certificates for outgoing HTTPS.

On python.org macOS builds the system CA bundle is not wired into the ``ssl``
module (Install Certificates.command was never run), so any ``urlopen`` over
https fails with ``CERTIFICATE_VERIFY_FAILED`` and the connectors (weather,
WHOOP, Withings) silently return ``None``. Here, before the first network call,
we point ``SSL_CERT_FILE`` at a working CA bundle if the default path is empty
and the variable is not already set.

Stdlib-only, cross-platform, a no-op on systems where certificates already work.
"""

import os
import ssl


# Order: system bundles of the major OSes. certifi (if installed) is tried first.
_CANDIDATE_BUNDLES = (
    "/etc/ssl/cert.pem",                     # macOS (LibreSSL), some BSDs
    "/etc/ssl/certs/ca-certificates.crt",    # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",      # RHEL/Fedora/CentOS
    "/etc/ssl/ca-bundle.pem",                # openSUSE
    "/opt/homebrew/etc/openssl@3/cert.pem",  # Homebrew (Apple Silicon)
    "/usr/local/etc/openssl@3/cert.pem",     # Homebrew (Intel)
)


def ensure_ca_certs():
    """Set SSL_CERT_FILE if needed. Return the chosen path, or None.

    Call this ONCE at process start, before any network call.
    """
    # Respect an explicit environment configuration - never override it.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return os.environ.get("SSL_CERT_FILE")

    # If Python already has a working default bundle, leave it alone (Linux / configured mac).
    try:
        cafile = ssl.get_default_verify_paths().cafile
    except Exception:
        cafile = None
    if cafile and os.path.isfile(cafile):
        return cafile

    candidates = []
    try:
        import certifi  # type: ignore
        candidates.append(certifi.where())
    except Exception:
        pass
    candidates.extend(_CANDIDATE_BUNDLES)

    for path in candidates:
        if path and os.path.isfile(path):
            os.environ["SSL_CERT_FILE"] = path
            return path
    return None
