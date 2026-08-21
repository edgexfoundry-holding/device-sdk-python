# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
JWT Authentication module for EdgeX Device Service SDK.

Implements JWT token validation for API endpoints in secure mode.
Compatible with OpenBao/Vault JWT tokens as per EdgeX v4.0.2 specification.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional, Dict, Any, List
from functools import lru_cache

import jwt
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ...common.consts import CORRELATION_HEADER
from ...common.utils import EdgexError, create_edgx_error, EdgexErrorKind

__all__ = [
    "JWTAuthMiddleware",
    "JWTAuthenticator",
    "get_jwt_authenticator",
    "is_public_endpoint",
    "JWTAuthError",
]

_LOGGER = logging.getLogger(__name__)

# Public endpoints that don't require authentication
_PUBLIC_ENDPOINTS = {
    "/api/v3/ping",
    "/api/v3/version",
    "/api/v3/config",
    "/api/v3/metrics",
    "/health",
    "/favicon.ico",
}

# Paths that start with these prefixes are public
_PUBLIC_PREFIXES = (
    "/api/v3/ping",
    "/api/v3/version",
    "/api/v3/config",
    "/api/v3/metrics",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class JWTAuthError(EdgexError):
    """JWT Authentication error."""

    def __init__(self, kind: EdgexErrorKind, message: str):
        super().__init__(kind, message)


class JWTAuthenticator:
    """JWT Token validator for EdgeX services."""

    def __init__(
        self,
        public_key: Optional[str] = None,
        jwks_url: Optional[str] = None,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        algorithm: str = "RS256",
        leeway: int = 60,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize JWT Authenticator.

        Args:
            public_key: PEM-encoded public key for RS256 verification
            jwks_url: JWKS endpoint URL for key rotation (e.g., OpenBao OIDC)
            issuer: Expected JWT issuer claim
            audience: Expected audience claim
            algorithm: JWT algorithm (default RS256)
            leeway: Clock skew tolerance in seconds
            logger: Optional logger
        """
        self._public_key = public_key
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._algorithm = algorithm
        self._leeway = leeway
        self._logger = logger or _LOGGER
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._jwks_cache_time = 0.0
        self._jwks_ttl = 300  # 5 minutes

    def _get_public_key(self) -> str:
        """Get public key for verification."""
        if self._public_key:
            return self._public_key

        if self._jwks_url:
            return self._fetch_jwks_key()

        raise RuntimeError("No public key or JWKS URL configured")

    def _fetch_jwks_key(self) -> str:
        """Fetch and cache JWKS keys from OpenBao/OIDC endpoint."""
        import requests

        now = time.time()
        if self._jwks_cache and (now - self._jwks_cache_time) < self._jwks_ttl:
            return self._jwks_cache

        try:
            resp = requests.get(self._jwks_url, timeout=10)
            resp.raise_for_status()
            self._jwks_cache = resp.json()
            self._jwks_cache_time = time.time()
            return self._jwks_cache
        except Exception as e:
            raise RuntimeError(f"Failed to fetch JWKS: {e}")

    def validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT token and return claims.

        Args:
            token: JWT token string

        Returns:
            Decoded token claims

        Raises:
            JWTAuthError: If token is invalid
        """
        if not token:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, "Missing authentication token")

        # Resolve the verification key outside the token checks so that a missing
        # key / JWKS endpoint is treated as a configuration error (propagates as
        # RuntimeError -> 500) rather than a client-side token failure.
        public_key = self._get_public_key()

        try:
            # Decode and verify token
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[self._algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"verify_signature": True, "verify_exp": True, "verify_aud": bool(self._audience)}
            )

            return payload

        except jwt.ExpiredSignatureError:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, "Token has expired")
        except jwt.InvalidAudienceError:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, "Invalid audience")
        except jwt.InvalidIssuerError:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, "Invalid issuer")
        except jwt.InvalidSignatureError:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, "Invalid signature")
        except jwt.InvalidTokenError as e:
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, f"Invalid token: {e}")
        except Exception as e:
            self._logger.error("JWT validation error: %s", e)
            raise JWTAuthError(EdgexErrorKind.CONTRACT_INVALID, f"Token validation failed: {e}")

    def extract_token_from_header(self, auth_header: Optional[str]) -> Optional[str]:
        """Extract Bearer token from Authorization header."""
        if not auth_header:
            return None
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for JWT authentication."""

    def __init__(
        self,
        app,
        authenticator: JWTAuthenticator,
        public_paths: Optional[List[str]] = None,
        public_prefixes: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self._authenticator = authenticator
        self._public_paths = set(public_paths or _PUBLIC_ENDPOINTS)
        self._public_prefixes = tuple(public_prefixes or _PUBLIC_PREFIXES)

    def is_public_path(self, path: str) -> bool:
        """Check if path is public (no auth required)."""
        if path in self._public_paths:
            return True
        for prefix in self._public_prefixes:
            if path.startswith(prefix):
                return True
        return False

    async def dispatch(self, request: Request, call_next):
        # Skip authentication for public endpoints
        if self.is_public_path(request.url.path):
            return await call_next(request)

        # Extract and validate JWT
        auth_header = request.headers.get("Authorization")
        token = self._authenticator.extract_token_from_header(auth_header)

        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            claims = self._authenticator.validate_token(token)
            # Store claims in request state for downstream use
            request.state.jwt_claims = claims
            request.state.user_id = claims.get("sub") or claims.get("username")
        except JWTAuthError as e:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"message": e.message},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


# Global authenticator instance
_authenticator: Optional[JWTAuthenticator] = None


def get_jwt_authenticator(
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> JWTAuthenticator:
    """Get or create global JWT authenticator instance."""
    global _authenticator

    if _authenticator is None:
        _authenticator = JWTAuthenticator(
            public_key=public_key,
            jwks_url=jwks_url,
            issuer=issuer,
            audience=audience,
        )
    return _authenticator


def is_public_endpoint(path: str) -> bool:
    """Check if endpoint is public (no auth required)."""
    if path in _PUBLIC_ENDPOINTS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def setup_jwt_auth(
    app,
    public_key: Optional[str] = None,
    jwks_url: Optional[str] = None,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
    public_paths: Optional[List[str]] = None,
    public_prefixes: Optional[List[str]] = None,
) -> JWTAuthMiddleware:
    """Setup JWT authentication middleware on FastAPI app.

    Args:
        app: FastAPI application
        public_key: PEM public key for RS256
        jwks_url: JWKS URL for key rotation
        issuer: Expected JWT issuer
        audience: Expected audience
        public_paths: Additional public paths
        public_prefixes: Additional public prefixes

    Returns:
        JWTAuthMiddleware instance
    """
    authenticator = get_jwt_authenticator(
        public_key=public_key,
        jwks_url=jwks_url,
        issuer=issuer,
        audience=audience,
    )

    middleware = JWTAuthMiddleware(
        app,
        authenticator,
        public_paths=public_paths,
        public_prefixes=public_prefixes,
    )
    app.add_middleware(type(middleware), authenticator=authenticator,
                      public_paths=public_paths, public_prefixes=public_prefixes)
    return middleware