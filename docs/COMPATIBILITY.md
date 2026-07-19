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

Runtime output directories (`state`, `logs`, `diagnostics`, `exports`, and `temp`) must remain ordinary folders inside the project. The application rejects symlink, junction, or reparse-point redirection for those locations.

The Windows launcher selects an installed Python 3.11-3.13 runtime and does not install or modify runtimes automatically. Other operating systems can start the project directly with `python run_beta_earth.py`; Windows remains the primary supported environment.

