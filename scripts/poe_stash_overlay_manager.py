#!/usr/bin/env python3
"""Manage the local PoE2 stash price overlay.

This helper keeps the long overlay command stable for agents. It can run the
one-time overlay, open calibration, and register a per-agent Windows Run entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
OVERLAY_SCRIPT = SCRIPT_DIR / "poe_stash_overlay.py"
DEFAULT_PROFILE = Path(r"D:\Soft\PoE2_Build\poe_stash_overlay_profile.json")
DEFAULT_SEARCH_ROOT = Path(r"D:\Soft\PoE2_Build")
STATE_FILE = DEFAULT_SEARCH_ROOT / "poe_stash_overlay_agents.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_PREFIX = "PoE2StashOverlay_"
DEFAULT_TAB_MARKERS = ("marker2", "marker1")


def default_agent_id() -> str:
    env_id = os.environ.get("CODEX_THREAD_ID")
    if env_id:
        return env_id
    basis = f"{Path.cwd()}|{OVERLAY_SCRIPT}"
    digest = hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:12]
    return f"manual-{digest}"


def sanitize_agent_id(agent_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in agent_id)
    return safe[:80] or "default"


def run_value_name(agent_id: str) -> str:
    return RUN_VALUE_PREFIX + sanitize_agent_id(agent_id)


def pythonw_path() -> str:
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return str(current)


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_agent_state(agent_id: str, **fields: Any) -> None:
    state = load_state()
    agents = state.setdefault("agents", {})
    agent_state = agents.setdefault(agent_id, {})
    agent_state.update(fields)
    agent_state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_state(state)


def process_query(agent_id: str | None = None) -> list[dict[str, Any]]:
    agent_filter = ""
    if agent_id:
        escaped = agent_id.replace("'", "''")
        agent_filter = f" -and ($_.CommandLine -like '*--agent-id*{escaped}*')"
    command = (
        "$items = Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -like 'python*') -and ($_.CommandLine -like '*poe_stash_overlay.py*')"
        f"{agent_filter} }} | "
        "Select-Object ProcessId,Name,CommandLine; "
        "if ($items) { $items | ConvertTo-Json -Compress }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout.strip()
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, dict):
        return [data]
    return data


def stop_processes(agent_id: str) -> int:
    escaped = agent_id.replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -like 'python*') -and ($_.CommandLine -like '*poe_stash_overlay.py*') "
        f"-and ($_.CommandLine -like '*--agent-id*{escaped}*') }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=False)
    return len(process_query(agent_id))


def overlay_command(args: argparse.Namespace, *, calibrate: bool = False) -> list[str]:
    command = [
        str(OVERLAY_SCRIPT),
        "--latest",
        "--auto-marker",
        "--profile",
        str(args.profile),
        "--search-root",
        str(args.search_root),
        "--min-price-exalted",
        str(args.min_price_exalted),
        "--agent-id",
        args.agent_id,
    ]
    for marker in args.tab_marker or DEFAULT_TAB_MARKERS:
        command.extend(["--tab-marker", marker])
    if args.wait_for_game:
        command.append("--wait-for-window")
    if args.exit_with_game:
        command.append("--exit-with-window")
    if args.follow_game:
        command.append("--follow-window")
    if args.debug_tabs:
        command.append("--debug-tabs")
    if args.slot_guard:
        command.extend(["--slot-guard", "--slot-guard-poll-ms", str(args.slot_guard_poll_ms)])
    if args.debug_slot_guard:
        command.append("--debug-slot-guard")
    if calibrate:
        command.extend(["--calibrate", "--show-grid", "--show-tab-scan", "--no-click-through"])
    elif args.show_tab_scan:
        command.append("--show-tab-scan")
    if args.no_empty_status:
        command.append("--no-empty-status")
    return command


def launch_overlay(args: argparse.Namespace, *, calibrate: bool = False) -> int:
    if args.stop_existing:
        stop_processes(args.agent_id)
    command = overlay_command(args, calibrate=calibrate)
    subprocess.Popen([pythonw_path(), *command], close_fds=True)
    update_agent_state(
        args.agent_id,
        lastCommand=[pythonw_path(), *command],
        autostartValue=run_value_name(args.agent_id),
        profile=str(args.profile),
        searchRoot=str(args.search_root),
    )
    print(f"started agentId={args.agent_id}")
    return 0


def set_autostart(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("Windows autostart is only supported on Windows.")
    import winreg

    command = subprocess.list2cmdline([pythonw_path(), *overlay_command(args, calibrate=False)])
    value_name = run_value_name(args.agent_id)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
    update_agent_state(
        args.agent_id,
        autostart=True,
        autostartValue=value_name,
        autostartCommand=command,
        profile=str(args.profile),
        searchRoot=str(args.search_root),
    )
    print(f"enabled autostart value={value_name}")
    if args.start_now:
        launch_overlay(args, calibrate=False)
    return 0


def clear_autostart(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise SystemExit("Windows autostart is only supported on Windows.")
    import winreg

    value_name = run_value_name(args.agent_id)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
        removed = True
    except FileNotFoundError:
        removed = False
    update_agent_state(args.agent_id, autostart=False, autostartValue=value_name, autostartCommand=None)
    if args.stop_running:
        stop_processes(args.agent_id)
    print(f"disabled autostart value={value_name} removed={removed}")
    return 0


def read_autostart(agent_id: str) -> str | None:
    if os.name != "nt":
        return None
    import winreg

    value_name = run_value_name(agent_id)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return str(value)
    except FileNotFoundError:
        return None


def print_status(args: argparse.Namespace) -> int:
    processes = process_query(args.agent_id)
    autostart = read_autostart(args.agent_id)
    print(json.dumps(
        {
            "agentId": args.agent_id,
            "running": processes,
            "autostartValue": run_value_name(args.agent_id),
            "autostartCommand": autostart,
            "stateFile": str(STATE_FILE),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage PoE2 stash overlay")
    parser.add_argument("action", choices=["calibrate", "start", "stop", "status", "enable-autostart", "disable-autostart"])
    parser.add_argument("--agent-id", default=default_agent_id())
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--search-root", type=Path, default=DEFAULT_SEARCH_ROOT)
    parser.add_argument("--tab-marker", action="append")
    parser.add_argument("--min-price-exalted", type=float, default=10.0)
    parser.add_argument("--wait-for-game", action="store_true", default=True)
    parser.add_argument("--no-wait-for-game", action="store_false", dest="wait_for_game")
    parser.add_argument("--exit-with-game", action="store_true", default=True)
    parser.add_argument("--no-exit-with-game", action="store_false", dest="exit_with_game")
    parser.add_argument("--follow-game", action="store_true", default=True)
    parser.add_argument("--no-follow-game", action="store_false", dest="follow_game")
    parser.add_argument("--stop-existing", action="store_true", default=True)
    parser.add_argument("--no-stop-existing", action="store_false", dest="stop_existing")
    parser.add_argument("--show-tab-scan", action="store_true")
    parser.add_argument("--debug-tabs", action="store_true")
    parser.add_argument("--slot-guard", action="store_true", default=True)
    parser.add_argument("--no-slot-guard", action="store_false", dest="slot_guard")
    parser.add_argument("--slot-guard-poll-ms", type=int, default=1000)
    parser.add_argument("--debug-slot-guard", action="store_true")
    parser.add_argument("--no-empty-status", action="store_true")
    parser.add_argument("--start-now", action="store_true")
    parser.add_argument("--stop-running", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "calibrate":
        return launch_overlay(args, calibrate=True)
    if args.action == "start":
        return launch_overlay(args, calibrate=False)
    if args.action == "stop":
        remaining = stop_processes(args.agent_id)
        print(f"stopped agentId={args.agent_id} remaining={remaining}")
        return 0
    if args.action == "status":
        return print_status(args)
    if args.action == "enable-autostart":
        return set_autostart(args)
    if args.action == "disable-autostart":
        return clear_autostart(args)
    parser.error(f"Unsupported action: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
