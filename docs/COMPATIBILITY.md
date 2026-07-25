# Compatibility

| Area | Support |
|---|---|
| Primary platform | Windows 11-first |
| Python | 3.11, 3.12, 3.13 |
| Runtime dependencies | Python standard library only |
| Browser transport | Local `http://127.0.0.1:<OS-assigned-port>` |
| Save schema | `4.0` |
| Folder move or rename | Supported through project-relative paths |
| Paths containing spaces | Supported |
| Elevated privileges | Not required |
| Internet hosting | Not supported by the bundled HTTP adapter |
| Automated source validation | Windows and Ubuntu hosted CI across Python 3.11-3.13 |
| Machine-local installation | Supported through independent project folders and local state |
| Fleet Verified | **Not claimed**; ALPHA, ASCEND, and DeusEx still require relevant target-machine smoke tests |

Runtime output directories (`state`, `logs`, `diagnostics`, `exports`, and `temp`) must remain ordinary folders inside the project. The application rejects symlink, junction, or reparse-point redirection for those locations.

The Windows launcher selects an installed Python 3.11-3.13 runtime and does not install or modify runtimes automatically. It forwards operator arguments to `run_beta_earth.py`, so the same launcher can perform a bounded startup check:

```powershell
.\START_BETA_EARTH.bat --dry-run --no-browser
```

Other operating systems can start or validate the project directly with Python. Windows remains the primary supported environment. Hosted CI demonstrates source portability; it does not replace real acceptance testing on the three named computers.

When installed on more than one computer, each copy must keep its own runtime state, logs, locks, diagnostics, and exports. Do not share a live runtime directory through Drive or run the same unique state-mutating session from multiple machines.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
