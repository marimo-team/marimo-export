"""Compute export producer identity without starting a notebook process."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from marimo_export._environment import environment_identity
from marimo_export._format import digest
from marimo_export._identity import require_implementation_stable
from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export._notebook import _notebook_path, _read_stable_source, document_sha256
from marimo_export._portable import validate_portable_basename
from marimo_export.errors import CompatibilityError, ExecutionError
from marimo_export.index import ProducerProvenance
from marimo_export.spec import StrPath


@dataclass(frozen=True, slots=True)
class ProducerIdentity:
    """Stable producer facts used for repository reuse and export provenance."""

    source: Path | None
    filename: str | None
    source_sha256: str
    document_sha256: str
    producer_sha256: str
    marimo_version: str
    marimo_export_version: str
    implementation_sha256: str
    environment_sha256: str

    def __post_init__(self) -> None:
        if self.source is not None:
            if not isinstance(self.source, Path) or not self.source.is_absolute():
                raise ValueError("producer source must be an absolute pathlib.Path or None")
            try:
                metadata = self.source.lstat()
            except OSError as error:
                raise ValueError("producer source must be available") from error
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("producer source must be a regular file")
            if self.filename != self.source.name:
                raise ValueError("producer filename must match its source basename")
        if self.filename is not None:
            validate_portable_basename(self.filename, "producer filename")
        for name in (
            "source_sha256",
            "document_sha256",
            "producer_sha256",
            "implementation_sha256",
            "environment_sha256",
        ):
            digest(getattr(self, name), f"producer {name}")
        for name in ("marimo_version", "marimo_export_version"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 255
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"producer {name} must be a bounded nonempty string")

    @property
    def provenance(self) -> ProducerProvenance:
        return ProducerProvenance(
            marimo=self.marimo_version,
            marimo_export=self.marimo_export_version,
            implementation_sha256=self.implementation_sha256,
        )


def producer_identity(source: StrPath) -> ProducerIdentity:
    """Return exact source and runtime identity without executing notebook code."""

    notebook = _notebook_path(source)
    _, source_sha256, source_revision = _read_stable_source(notebook)
    document = document_sha256(notebook)
    _, after_sha256, after_revision = _read_stable_source(notebook)
    if after_sha256 != source_sha256 or after_revision != source_revision:
        raise RuntimeError("notebook source changed while producer identity was computed")

    return _identity_from_facts(
        document_sha256=document,
        source_sha256=source_sha256,
        source=notebook,
        filename=notebook.name,
    )


def runtime_producer_identity(
    *,
    document_sha256: str,
    source: StrPath | None = None,
    source_sha256: str | None = None,
    filename: str | None = None,
) -> ProducerIdentity:
    """Return producer identity from runtime facts available inside a kernel."""

    if source is not None:
        stable = producer_identity(source)
        if stable.document_sha256 != document_sha256:
            raise ExecutionError(
                "the runtime source does not match the inspected notebook document",
                code="parent_document_changed",
                details={
                    "expected": document_sha256,
                    "actual": stable.document_sha256,
                },
            )
        return stable
    return _identity_from_facts(
        document_sha256=document_sha256,
        source_sha256=document_sha256 if source_sha256 is None else source_sha256,
        source=None,
        filename=filename,
    )


def _identity_from_facts(
    *,
    document_sha256: str,
    source_sha256: str,
    source: Path | None,
    filename: str | None,
) -> ProducerIdentity:
    marimo_version = _required_distribution_version("marimo")
    export_version = _distribution_version("marimo-export", fallback="0.0.0")
    implementation = require_implementation_stable()
    environment = environment_identity(source)
    producer = sha256_bytes(
        canonical_bytes(
            {
                "document_sha256": document_sha256,
                "environment_sha256": environment,
                "implementation_sha256": implementation,
                "marimo": marimo_version,
                "marimo_export": export_version,
                "platform": {
                    "machine": platform.machine(),
                    "system": platform.system(),
                },
                "python": {
                    "abi": sys.implementation.cache_tag or "unknown",
                    "implementation": sys.implementation.name,
                    "version": platform.python_version(),
                },
                "source_sha256": source_sha256,
            }
        )
    )
    return ProducerIdentity(
        source=source,
        filename=filename,
        source_sha256=source_sha256,
        document_sha256=document_sha256,
        producer_sha256=producer,
        marimo_version=marimo_version,
        marimo_export_version=export_version,
        implementation_sha256=implementation,
        environment_sha256=environment,
    )


def producer_sha256(source: StrPath) -> str:
    """Return the spec-independent producer identity for one notebook source."""

    return producer_identity(source).producer_sha256


def managed_runtime_source(runtime_path: str | None) -> str | None:
    """Return the authored source path selected for an attached runtime."""

    return os.environ.get("MARIMO_EXPORT_MANAGED_SOURCE") or runtime_path


def _required_distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise CompatibilityError(
            f"required runtime distribution {name!r} is unavailable",
            code="runtime_distribution_unavailable",
        ) from error


def _distribution_version(name: str, *, fallback: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return fallback


__all__ = [
    "ProducerIdentity",
    "managed_runtime_source",
    "producer_identity",
    "producer_sha256",
    "runtime_producer_identity",
]
