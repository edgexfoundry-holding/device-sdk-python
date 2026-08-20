# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
TLS/mTLS Certificate Management for EdgeX Device Service.

Provides:
- TLS context creation for server and client
- Certificate loading and validation
- Certificate rotation with hot-reload
- mTLS support for mutual authentication
- Certificate expiration monitoring
"""

from __future__ import annotations

import os
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable, Tuple
from pathlib import Path
from datetime import datetime, timedelta

import ssl

from ..common.utils import EdgexError, EdgexErrorKind, create_edgx_error

_LOGGER = logging.getLogger(__name__)

_CRYPTO_AVAILABLE = None


def _ensure_crypto():
    """Lazily import cryptography. Raises ImportError with helpful message."""
    global _CRYPTO_AVAILABLE
    if _CRYPTO_AVAILABLE is True:
        return
    try:
        import cryptography  # noqa: F401
        _CRYPTO_AVAILABLE = True
    except ImportError:
        _CRYPTO_AVAILABLE = False
        raise ImportError(
            "TLS features require 'cryptography' package. "
            "Install it with: pip install cryptography"
        )


@dataclass
class TLSConfig:
    """TLS configuration for server or client."""
    cert_file: Optional[str] = None
    key_file: Optional[str] = None
    ca_file: Optional[str] = None
    verify_mode: ssl.VerifyMode = ssl.CERT_REQUIRED
    check_hostname: bool = True
    min_version: ssl.TLSVersion = ssl.TLSVersion.TLSv1_2
    max_version: ssl.TLSVersion = ssl.TLSVersion.MAXIMUM_SUPPORTED
    ciphers: Optional[str] = None


@dataclass
class CertificateInfo:
    """Certificate information."""
    subject: str
    issuer: str
    serial_number: str
    not_before: datetime
    not_after: datetime
    is_ca: bool
    subject_alt_names: list = field(default_factory=list)


class TLSManager:
    """
    TLS/mTLS Certificate Manager for EdgeX Device Service.
    """

    def __init__(
        self,
        server_config: Optional[TLSConfig] = None,
        client_config: Optional[TLSConfig] = None,
        auto_renewal: bool = True,
        renewal_threshold_days: int = 30,
        check_interval_hours: int = 24,
        logger: Optional[logging.Logger] = None,
    ):
        self._server_config = server_config or TLSConfig()
        self._client_config = client_config or TLSConfig()
        self._auto_renewal = auto_renewal
        self._renewal_threshold = timedelta(days=renewal_threshold_days)
        self._check_interval = timedelta(hours=check_interval_hours)
        self._logger = logger or logging.getLogger(__name__)

        self._server_context: Optional[ssl.SSLContext] = None
        self._client_context: Optional[ssl.SSLContext] = None
        self._cert_expiry_cache: dict = {}

        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()
        self._lock = threading.RLock()

    def initialize_server_context(self) -> Optional[ssl.SSLContext]:
        """Initialize server SSL context for HTTPS server."""
        _ensure_crypto()
        with self._lock:
            if not self._server_config.cert_file or not self._server_config.key_file:
                self._logger.warning("Server TLS config missing cert/key files")
                return None

            context = self._create_server_context()
            self._server_context = context
            self._cache_cert_expiry(self._server_config.cert_file)
            self._logger.info("Server SSL context initialized")
            return context

    def initialize_client_context(self) -> Optional[ssl.SSLContext]:
        """Initialize client SSL context for outgoing connections."""
        _ensure_crypto()
        with self._lock:
            if not self._client_config.cert_file or not self._client_config.key_file:
                self._logger.warning("Client TLS config missing cert/key files")
                return None

            context = self._create_client_context()
            self._client_context = context
            self._cache_cert_expiry(self._client_config.cert_file)
            self._logger.info("Client SSL context initialized")
            return context

    def _create_server_context(self) -> ssl.SSLContext:
        """Create server SSL context with mTLS support."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = self._server_config.min_version
        context.maximum_version = self._server_config.max_version
        context.verify_mode = self._server_config.verify_mode
        context.check_hostname = self._server_config.check_hostname

        if self._server_config.ciphers:
            context.set_ciphers(self._server_config.ciphers)

        context.load_cert_chain(
            self._server_config.cert_file,
            self._server_config.key_file
        )

        if self._server_config.ca_file:
            context.load_verify_locations(cafile=self._server_config.ca_file)
            context.verify_mode = ssl.CERT_REQUIRED

        return context

    def _create_client_context(self) -> ssl.SSLContext:
        """Create client SSL context with mTLS support."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = self._client_config.min_version
        context.maximum_version = self._client_config.max_version
        context.verify_mode = self._client_config.verify_mode
        context.check_hostname = self._client_config.check_hostname

        if self._client_config.ciphers:
            context.set_ciphers(self._client_config.ciphers)

        context.load_cert_chain(
            self._client_config.cert_file,
            self._client_config.key_file
        )

        if self._client_config.ca_file:
            context.load_verify_locations(cafile=self._client_config.ca_file)

        return context

    def get_server_context(self) -> Optional[ssl.SSLContext]:
        """Get server SSL context."""
        with self._lock:
            if self._server_context is None:
                return self.initialize_server_context()
            return self._server_context

    def get_client_context(self) -> Optional[ssl.SSLContext]:
        """Get client SSL context."""
        with self._lock:
            if self._client_context is None:
                return self.initialize_client_context()
            return self._client_context

    def reload_certificates(self) -> bool:
        """Hot-reload certificates without restart."""
        with self._lock:
            try:
                old_server = self._server_context
                old_client = self._client_context

                self._server_context = None
                self._client_context = None

                self.initialize_server_context()
                self.initialize_client_context()

                self._logger.info("Certificates reloaded successfully")
                return True
            except Exception as e:
                self._logger.error("Failed to reload certificates: %s", e)
                self._server_context = old_server
                self._client_context = old_client
                return False

    def _cache_cert_expiry(self, cert_file: str) -> None:
        """Cache certificate expiry date."""
        try:
            cert = self._load_certificate(cert_file)
            self._cert_expiry_cache[cert_file] = cert.not_after
            self._logger.debug("Cached expiry for %s: %s", cert_file, cert.not_after)
        except Exception as e:
            self._logger.warning("Failed to cache expiry for %s: %s", cert_file, e)

    def _load_certificate(self, cert_file: str):
        """Load X.509 certificate from file."""
        _ensure_crypto()
        from cryptography import x509 as _x509
        with open(cert_file, "rb") as f:
            return _x509.load_pem_x509_certificate(f.read())

    def get_certificate_info(self, cert_file: str) -> Optional[CertificateInfo]:
        """Get certificate information."""
        _ensure_crypto()
        from cryptography import x509 as _x509
        try:
            cert = self._load_certificate(cert_file)
            san = []
            try:
                san_ext = cert.extensions.get_extension_for_oid(
                    _x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                )
                san = [str(name) for name in san_ext.value]
            except _x509.ExtensionNotFound:
                pass

            is_ca = False
            try:
                bc_ext = cert.extensions.get_extension_for_oid(
                    _x509.oid.ExtensionOID.BASIC_CONSTRAINTS
                )
                is_ca = bc_ext.value.ca
            except _x509.ExtensionNotFound:
                pass

            return CertificateInfo(
                subject=cert.subject.rfc4514_string(),
                issuer=cert.issuer.rfc4514_string(),
                serial_number=str(cert.serial_number),
                not_before=cert.not_valid_before_utc,
                not_after=cert.not_valid_after_utc,
                is_ca=is_ca,
                subject_alt_names=san,
            )
        except Exception as e:
            self._logger.warning("Failed to get cert info for %s: %s", cert_file, e)
            return None

    def check_certificate_expiry(self) -> dict:
        """Check all managed certificates for expiry."""
        results = {}
        now = datetime.utcnow()

        for cert_file, expiry in self._cert_expiry_cache.items():
            if not os.path.exists(cert_file):
                results[cert_file] = {"status": "missing", "days_remaining": None}
                continue

            days_remaining = (expiry - datetime.utcnow()).days
            status = "valid"
            if days_remaining <= 0:
                status = "expired"
            elif days_remaining <= 30:
                status = "expiring_soon"

            results[cert_file] = {
                "status": status,
                "expires_at": expiry.isoformat(),
                "days_remaining": days_remaining,
            }

        return results

    def check_and_renew(self) -> bool:
        """Check certificates and trigger renewal if needed."""
        if not self._auto_renewal:
            return False

        results = self.check_certificate_expiry()
        renewed = False

        for cert_file, info in results.items():
            if info["status"] in ("expired", "expiring_soon"):
                self._logger.warning(
                    "Certificate %s is %s (expires in %d days)",
                    cert_file, info["status"], info.get("days_remaining", 0)
                )
                if self._attempt_renewal(cert_file):
                    renewed = True

        return renewed

    def _attempt_renewal(self, cert_file: str) -> bool:
        """Attempt to renew a certificate."""
        self._logger.info("Certificate renewal attempted for %s", cert_file)
        try:
            self.reload_certificates()
            self._cache_cert_expiry(cert_file)
            return True
        except Exception as e:
            self._logger.error("Certificate renewal failed for %s: %s", cert_file, e)
            return False

    def start_monitoring(self) -> None:
        """Start background certificate monitoring."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="tls-cert-monitor"
        )
        self._monitor_thread.start()
        self._logger.info("Certificate monitoring started")

    def stop_monitoring(self) -> None:
        """Stop background certificate monitoring."""
        self._stop_monitor.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
        self._logger.info("Certificate monitoring stopped")

    def _monitor_loop(self) -> None:
        """Background loop for certificate monitoring."""
        while not self._stop_monitor.is_set():
            self.check_and_renew()
            self._stop_monitor.wait(self._check_interval.total_seconds())

    def create_self_signed_cert(
        self,
        subject: str,
        san: list = None,
        valid_days: int = 365,
        key_size: int = 2048,
    ) -> Tuple[str, str]:
        """Generate self-signed certificate for testing."""
        _ensure_crypto()
        from cryptography import x509 as _x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
        )

        subject = issuer = _x509.Name([
            _x509.NameAttribute(NameOID.COMMON_NAME, subject),
        ])

        builder = (
            _x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(_x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=valid_days))
            .add_extension(
                _x509.SubjectAlternativeName(
                    [_x509.DNSName(d) for d in (san or [])]
                ),
                critical=False,
            )
        )

        cert = builder.sign(key, hashes.SHA256())

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        return cert_pem.decode(), key_pem.decode()

    def close(self) -> None:
        """Cleanup resources."""
        self.stop_monitoring()
        self._logger.info("TLS Manager closed")


def create_self_signed_cert(
    subject: str,
    san: list = None,
    valid_days: int = 365,
    key_size: int = 2048,
) -> Tuple[str, str]:
    """Generate self-signed certificate for testing."""
    tls_mgr = TLSManager()
    return tls_mgr.create_self_signed_cert(
        subject=subject,
        san=san,
        valid_days=valid_days,
        key_size=key_size,
    )


def create_server_ssl_context(
    cert_file: str,
    key_file: str,
    ca_file: Optional[str] = None,
    require_client_cert: bool = True,
) -> ssl.SSLContext:
    """Create server SSL context with optional mTLS."""
    _ensure_crypto()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED if require_client_cert else ssl.CERT_NONE
    context.check_hostname = True
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)

    if ca_file:
        context.load_verify_locations(cafile=ca_file)

    return context


def create_client_ssl_context(
    cert_file: str,
    key_file: str,
    ca_file: Optional[str] = None,
    verify_hostname: bool = True,
) -> ssl.SSLContext:
    """Create client SSL context with mTLS support."""
    _ensure_crypto()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)

    if ca_file:
        context.load_verify_locations(cafile=ca_file)

    return context
