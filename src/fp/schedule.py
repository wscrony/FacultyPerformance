"""macOS launchd agent for the periodic `fp update all` (user-level, no root)."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from fp import paths

LABEL = "kr.ac.postech.dblab.facultyperformance"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist(weekday: int = 0, hour: int = 3, minute: int = 0) -> bytes:
    fp_bin = paths.ROOT / ".venv" / "bin" / "fp"
    log = paths.DATA / "logs"
    # claude CLI (for Haiku extraction) and git must be on PATH inside launchd's minimal environment
    # prefer the standalone install over terminal-app wrappers (e.g. cmux injects session hooks)
    local = Path.home() / ".local" / "bin"
    claude_dir = str(local) if (local / "claude").exists() else str(Path(shutil.which("claude") or "/usr/bin/x").parent)
    git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
    dirs = list(dict.fromkeys([claude_dir, git_dir, "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    path = ":".join(dirs)
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(fp_bin), "update", "all"],
        "WorkingDirectory": str(paths.ROOT),
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log / "update.log"),
        "StandardErrorPath": str(log / "update.log"),
        "LowPriorityIO": True,
        "Nice": 10,
    })


def install(weekday: int = 0, hour: int = 3, minute: int = 0) -> str:
    (paths.DATA / "logs").mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    PLIST.write_bytes(plist(weekday, hour, minute))
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], check=True)
    return str(PLIST)


def uninstall() -> bool:
    if not PLIST.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    PLIST.unlink()
    return True


def status() -> str:
    if not PLIST.exists():
        return "not installed"
    r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    state = next((l.strip() for l in r.stdout.splitlines() if "state =" in l or "last exit code" in l), "loaded?")
    cal = plistlib.loads(PLIST.read_bytes())["StartCalendarInterval"]
    return f"installed: weekday {cal['Weekday']} {cal['Hour']:02d}:{cal['Minute']:02d} ({state}) — {PLIST}"
