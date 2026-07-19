
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

# Variables that can inject code/plugins/configuration into otherwise isolated Python,
# test, coverage, and Node subprocesses. The parent environment is never modified.
_INJECTION_VARIABLES = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONBREAKPOINT",
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "COVERAGE_PROCESS_START",
    "COVERAGE_FILE",
    "COV_CORE_CONFIG",
    "COV_CORE_DATAFILE",
    "NODE_OPTIONS",
}


def sanitized_child_environment(
    base: Mapping[str, str] | None = None,
    *,
    temp_dir: Path | None = None,
) -> dict[str, str]:
    """Return a subprocess environment isolated from common injection variables.

    This is deliberately scoped to the child process. It does not edit PATH, registry,
    system/user environment variables, security exclusions, or the caller's environment.
    """

    environment = dict(os.environ if base is None else base)
    for key in _INJECTION_VARIABLES:
        environment.pop(key, None)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
        }
    )
    if temp_dir is not None:
        resolved = temp_dir.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        environment["TEMP"] = str(resolved)
        environment["TMP"] = str(resolved)
        environment["TMPDIR"] = str(resolved)
    return environment


def lower_current_process_priority() -> str:
    """Best-effort, process-local pressure reduction for diagnostics/export work."""

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_current_process = kernel32.GetCurrentProcess
            get_current_process.restype = ctypes.c_void_p
            set_priority_class = kernel32.SetPriorityClass
            set_priority_class.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            set_priority_class.restype = ctypes.c_int
            below_normal_priority_class = 0x00004000
            if set_priority_class(get_current_process(), below_normal_priority_class):
                return "below_normal"
            return f"normal(set_failed:{ctypes.get_last_error()})"
        except Exception as exc:  # best-effort only
            return f"normal(unavailable:{type(exc).__name__})"
    try:
        os.nice(5)
        return "nice+5"
    except (AttributeError, OSError) as exc:
        return f"normal(unavailable:{type(exc).__name__})"
