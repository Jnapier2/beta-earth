# Testing

Run the public test suite from the repository root:

```bash
python -m unittest discover -s tests -t . -v
```

Run the startup check independently:

```bash
python run_beta_earth.py --dry-run --no-browser
```

The suite uses only Python's standard library and creates temporary state outside the repository where possible. Coverage includes:

- domain and cross-catalog validation;
- command authorization and optimistic revision conflicts;
- mission, reward, inventory, wallet, and one-time barter flows;
- save migration, corruption handling, atomic persistence, and reset behavior;
- loopback HTTP routes, request validation, security headers, and host/origin boundaries;
- player-identity hardening, bounded document reads, and instance ownership;
- HUD action parity, reconnect behavior, and accessibility-related static contracts;
- startup validation without opening the browser or entering a persistent server loop.

GitHub Actions runs compilation and the suite on supported Python versions. Platform-specific tests use explicit skips when the underlying operating-system primitive is unavailable.

