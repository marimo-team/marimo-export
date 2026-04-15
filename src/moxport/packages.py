from __future__ import annotations

import json
from typing import Literal

import httpx

from .errors import PackageOperationError
from .models import PackageListResult, PackageOperationResult


class NotebookPackagesClient:
    def __init__(
        self,
        *,
        http: httpx.Client,
        base_url: str,
        token: str | None,
        session_id: str,
        default_manager: str = "uv",
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session_id = session_id
        self._default_manager = default_manager

    def _headers(self, *, include_session: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if include_session:
            headers["Marimo-Session-Id"] = self._session_id
        return headers

    def list(self) -> PackageListResult:
        response = self._http.get(
            f"{self._base_url}/api/packages/list",
            headers=self._headers(),
        )
        response.raise_for_status()
        return PackageListResult.model_validate(response.json())

    def add(
        self,
        package: str,
        *,
        upgrade: bool = False,
        group: str | None = None,
    ) -> PackageOperationResult:
        payload: dict[str, object] = {"package": package, "upgrade": upgrade}
        if group is not None:
            payload["group"] = group
        response = self._http.post(
            f"{self._base_url}/api/packages/add",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        result = PackageOperationResult.model_validate(response.json())
        if not result.success:
            raise PackageOperationError(result.error or f"Failed to add {package}")
        return result

    def remove(
        self, package: str, *, group: str | None = None
    ) -> PackageOperationResult:
        payload: dict[str, object] = {"package": package}
        if group is not None:
            payload["group"] = group
        response = self._http.post(
            f"{self._base_url}/api/packages/remove",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        result = PackageOperationResult.model_validate(response.json())
        if not result.success:
            raise PackageOperationError(result.error or f"Failed to remove {package}")
        return result

    def install_missing(
        self,
        *packages: str,
        source: Literal["kernel", "server"] = "kernel",
        manager: str | None = None,
    ) -> PackageOperationResult:
        versions = {package: "" for package in packages}
        if not versions:
            raise PackageOperationError(
                "install_missing() requires at least one package"
            )
        payload = {
            "manager": manager or self._default_manager,
            "versions": versions,
            "source": source,
        }
        response = self._http.post(
            f"{self._base_url}/api/kernel/install_missing_packages",
            headers={
                **self._headers(include_session=True),
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        result = PackageOperationResult.model_validate(response.json())
        if not result.success:
            raise PackageOperationError(
                result.error
                or f"Failed to install missing packages: {json.dumps(versions)}"
            )
        return result
