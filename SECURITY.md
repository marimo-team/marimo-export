# Security policy

## Supported versions

Security fixes target the latest published marimo-export release and the main
branch. The project will publish a broader support policy when it maintains more
than one release line.

## Report a vulnerability

Do not open a public issue with vulnerability details.

Use GitHub private vulnerability reporting for this repository when it is
available. Otherwise, open a minimal public issue asking the maintainers for a
private reporting path. Do not include technical details, reproduction steps,
credentials, hostnames, logs, screenshots, notebook source, or exported data in
that issue.

Include these details in the private report:

- Affected version or commit.
- The affected boundary, such as notebook or exporter execution, live transport
  and authentication, repository storage, directory replacement, export
  integrity, browser loading, interactive mounting, or package distribution.
- Impact and minimal reproduction steps.
- Any known mitigation.

Never include real secrets. Identify the credential type and location when a
committed or logged credential is involved, then rotate it before sharing more
evidence.

## Trust boundaries

Preparing or capturing an export executes notebook and exporter code with the
producer process's filesystem, credential, package, and network access. Managed
process ownership supplies startup, timeout, cancellation, and cleanup. It is
not an operating-system security sandbox.

Opening and verifying a notebook export establish consistency with its loaded
`index.json`. The application authenticates the publisher and delivery origin.
Mounting an AnyWidget, Vega-Lite chart, or custom interactive representation
grants that code the browser page's authority. Exported HTML remains untrusted
markup until the application applies its rendering and sanitization policy.

Read [Integrity and trust](docs/concepts/integrity-and-trust.md) for the public
model and [Architecture](development_docs/architecture.md#trust-boundaries) for
internal ownership.
