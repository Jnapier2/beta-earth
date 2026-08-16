# Security policy

## Supported version

Security fixes are evaluated against the current version on `main`.

## Report a vulnerability

Please use GitHub private vulnerability reporting. Include the affected version, reproduction steps, impact, and a proposed mitigation when available. Do not post private player data or a working exploit in a public issue.

## Runtime boundary

Beta Earth is a local, single-player application. Its browser interface binds to `127.0.0.1`, uses an ephemeral session token, validates the local Host header, limits request bodies, and serves restrictive browser security headers. It does not require credentials, elevated privileges, firewall changes, or an internet connection.

Do not expose the included development server through port forwarding, a public tunnel, or a reverse proxy without a separate security review and a production hosting design.
