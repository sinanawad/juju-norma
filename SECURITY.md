# Security Policy

`juju-norma` is a **sterile machine-charm calibration harness** for Juju CI — not
a production service. It ships no credentials and stores no user data; its
workload is a throwaway HTTP echo server. Some endpoints are intentionally
unauthenticated for testing (see `docs/FINDINGS.md` §0) and **must not** be copied
into a production charm.

## Reporting a vulnerability

Please report security issues — especially anything that could affect the Juju
engine or CI it calibrates — privately via
[GitHub Security Advisories](https://github.com/sinanawad/juju-norma/security/advisories/new)
rather than a public issue.

We aim to acknowledge reports within a few working days.

## Supported versions

This charm tracks the `main` branch against Juju 4.0+. Fixes land on `main`;
there are no long-lived support branches.
