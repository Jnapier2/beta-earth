# Security Policy

## Supported version

Security fixes are evaluated against the current portfolio release, `0.4.11`.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or security-advisory feature for this repository. Include the affected version, reproduction steps, impact, and any proposed mitigation. Do not include private player data or publish an exploitable report in a public issue before it can be reviewed.

## Security boundary

Beta Earth is designed for local, single-user execution. The included HTTP adapter binds to `127.0.0.1` and is not an internet-facing production server. Do not alter the bind address or expose it through port forwarding, a reverse proxy, or a public tunnel without performing a separate security review and replacing the development server with an appropriate production boundary.

The application does not require credentials, API keys, elevated privileges, firewall changes, or internet access. Save data and logs remain in ignored project-local directories.

