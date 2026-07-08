# Security Policy

Rapier Runtime is an AI-governance tool from **ResourceForge**, so its own
security is part of its purpose. We take reports seriously and aim to respond
quickly.

## Reporting a vulnerability

**Please do not open a public issue for security reports.**

Report privately via GitHub's **[Security Advisories](https://github.com/muddyone/rapier-runtime/security/advisories/new)**
(Report a vulnerability), or by email to:

> `contact@resourceforge.com`

Include, where possible: the affected version/commit, a description, and a
minimal reproduction. We support coordinated disclosure and will credit
reporters who wish to be named.

## Scope

In scope: the runtime itself — secret handling, the manifest loader, the
grounding/verification backends (URL/file/registry resolvers), run-artifact
persistence, and the CLI.

Out of scope: vulnerabilities in the underlying model providers or their SDKs
(report those to the respective vendor), and issues that require an already
fully-compromised host or malicious operator. See
[`docs/threat-model.md`](docs/threat-model.md) for the full model.

## Supported versions

Pre-1.0: only the latest `main` is supported. Security fixes land there first.

## Handling of secrets and data

- API keys are read from the environment only and are redacted from all logs,
  traces, and persisted artifacts.
- Run artifacts are opt-in (`--ledger-dir`), written owner-only, and
  git-ignored. Decision content is sent to the model vendors you configure —
  this is the tool's function and is documented in the threat model.
