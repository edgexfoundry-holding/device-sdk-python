# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Profile Scan Request DTO for EdgeX Device Service.

Mirrors `requests.ProfileScanRequest` from go-mod-core-contracts v4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from device_sdk_py.internal.common.consts import API_VERSION


@dataclass
class BaseRequest:
    """Base request with API version and request ID."""

    api_version: str = API_VERSION
    request_id: str = ""

    def __post_init__(self):
        if not self.api_version:
            self.api_version = API_VERSION


@dataclass
class ProfileScanRequest:
    """Request payload for the POST `/api/v3/profilescan` endpoint.

    Mirrors `requests.ProfileScanRequest` from go-mod-core-contracts v4.
    """

    base_request: BaseRequest = field(default_factory=BaseRequest)
    device_name: str = ""
    profile_name: str = ""
    options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.base_request.api_version == "":
            self.base_request.api_version = API_VERSION

    @property
    def request_id(self) -> str:
        return self.base_request.request_id

    @request_id.setter
    def request_id(self, value: str):
        self.base_request.request_id = value

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProfileScanRequest":
        """Create ProfileScanRequest from dictionary."""
        base_req = BaseRequest(
            api_version=data.get("apiVersion", API_VERSION),
            request_id=data.get("requestId", ""),
        )
        return cls(
            base_request=base_req,
            device_name=data.get("deviceName", ""),
            profile_name=data.get("profileName", ""),
            options=data.get("options", {}) or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "apiVersion": self.base_request.api_version,
            "requestId": self.base_request.request_id,
            "deviceName": self.device_name,
            "profileName": self.profile_name,
            "options": self.options,
        }