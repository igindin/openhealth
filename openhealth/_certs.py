"""Гарантируем, что Python найдёт CA-сертификаты для исходящего HTTPS.

На python.org-сборках macOS системный CA-бандл для модуля ``ssl`` не настроен
(не запускали Install Certificates.command), из-за чего любой ``urlopen`` к https
падает с ``CERTIFICATE_VERIFY_FAILED``, а коннекторы (погода, WHOOP, Withings)
молча возвращают ``None``. Здесь, до первого сетевого вызова, подставляем рабочий
CA-бандл в ``SSL_CERT_FILE``, если стандартный путь пуст и переменная не задана.

Stdlib-only, кросс-платформенно, no-op на системах с уже настроенными сертификатами.
"""

import os
import ssl


# Порядок: системные бандлы основных ОС. certifi (если установлен) пробуем первым.
_CANDIDATE_BUNDLES = (
    "/etc/ssl/cert.pem",                     # macOS (LibreSSL), некоторые BSD
    "/etc/ssl/certs/ca-certificates.crt",    # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",      # RHEL/Fedora/CentOS
    "/etc/ssl/ca-bundle.pem",                # openSUSE
    "/opt/homebrew/etc/openssl@3/cert.pem",  # Homebrew (Apple Silicon)
    "/usr/local/etc/openssl@3/cert.pem",     # Homebrew (Intel)
)


def ensure_ca_certs():
    """Подставить SSL_CERT_FILE при необходимости. Вернуть выбранный путь или None.

    Вызывать ОДИН РАЗ при старте процесса, до любого сетевого вызова.
    """
    # Уважаем явную конфигурацию окружения — ничего не навязываем.
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return os.environ.get("SSL_CERT_FILE")

    # Если у Python уже есть рабочий дефолтный бандл — не трогаем (Linux/настроенный mac).
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
