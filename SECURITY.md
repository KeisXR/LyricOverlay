# Security Policy

## Reporting a vulnerability

Do not publish exploit details, credentials, private media metadata, or other
sensitive material in a public issue.

Use GitHub's private vulnerability-reporting form from the repository's
**Security** tab when it is available. If the private form is not available,
open a minimal public issue requesting a private contact channel without
including the vulnerability details.

Include the affected version or commit, platform, reproduction conditions, and
impact. Remove song lyrics, API keys, pairing tokens, log data unrelated to the
problem, and other personal information from reports.

## Release integrity and signing status

Current Lyricaod release artifacts are **not Authenticode- or GPG-signed** unless
a specific release explicitly states otherwise. A self-signed certificate added
to a user's Trusted Root store is not an acceptable substitute for publisher
identity and is not required by this project.

Each packaged directory contains `SHA256SUMS`. Verify it from inside the
extracted package before running the application:

```bash
python scripts/write_checksums.py verify dist/Lyricaod
```

For downloaded packages without the repository checkout, use a trusted
SHA-256 utility to compare each file against `SHA256SUMS`. A checksum detects
accidental or malicious modification only when the checksum manifest itself is
obtained from a trusted source; it does not identify the publisher.

A future signed release must use a certificate issued for the publisher and a
private key supplied to the release workflow through protected CI secrets or a
hardware-backed signing service. Private keys, certificate-export passwords,
and signing credentials must never be committed to the repository. Pull
requests and forks without signing secrets must continue to build safely and
must not claim that their artifacts are signed.

## Supported versions

Until a stable release channel and security-maintenance window are documented,
security fixes target the current `main` branch and the most recent published
release when practical.
