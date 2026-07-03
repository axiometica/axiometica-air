# Security Policy

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.** Filing one publicly discloses the issue to everyone — including anyone with bad intent — before there's a chance to fix it.

Instead, use GitHub's private vulnerability reporting:

1. Go to the [Security tab](https://github.com/axiometica/axiometica-air/security)
2. Click **Report a vulnerability**
3. Describe the issue — what you found, how to reproduce it, and the potential impact

This opens a private discussion visible only to you and the maintainers, so the issue can be triaged and fixed before public disclosure.

## What to Include

- A clear description of the vulnerability and where it lives (file/endpoint/component)
- Steps to reproduce, or a proof-of-concept if you have one
- The potential impact (what an attacker could actually do with it)
- Your suggested severity, if you have one

## Response Expectations

This is maintained on a best-effort basis, not a guaranteed SLA. What you can expect:

- **Acknowledgement** that we've received your report, typically within a few days
- **An initial assessment** after that — confirmed and being fixed, need more info, or not applicable — with an estimated timeline if confirmed

Response times may be longer around holidays or other periods of reduced availability.

## Supported Versions

This project is developed on a single rolling `main` branch. Security fixes are only guaranteed for the latest release; please update before reporting if you're running an older version.

## Scope

This covers the application code in this repository (backend, frontend, watcher, deployment scripts). It does not cover third-party dependencies — please report those directly to the upstream project, though we'd appreciate a heads-up so we can track exposure here too.

## Secrets at Rest

Connector credentials (ServiceNow, Splunk, webhook secrets), Slack/SMTP credentials, and LLM provider API keys are encrypted at rest using a key from the `SECRET_ENCRYPTION_KEY` environment variable (see `.env.example`). This protects against database-only exposure (a backup leak, a SQL injection read) — it does not protect against a full host/container compromise, since the key lives in the environment alongside the database connection itself.

**Back up `SECRET_ENCRYPTION_KEY` somewhere other than `.env`** — a password manager or your organization's secrets vault. If this key is lost, every encrypted secret in the database becomes permanently unrecoverable; there is no recovery path other than re-entering each credential after generating a new key.
