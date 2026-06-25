from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACE = Path(r"D:\Soft\PoE2_Build")
DEFAULT_POB = Path(r"D:\Soft\PathOfBuilding-PoE2")
BUILDPLANNER_PATHS = [
    Path(r"C:\Users\Hatzy\Documents\My Games\Path of Exile 2\BuildPlanner"),
    Path(r"C:\Users\Hatzy\OneDrive\Документы\My Games\Path of Exile 2\BuildPlanner"),
]


def read_pob_version(pob_dir: Path) -> str | None:
    manifest = pob_dir / "manifest.xml"
    if not manifest.exists():
        return None
    try:
        root = ET.parse(manifest).getroot()
    except ET.ParseError:
        return None
    version = root.find("Version")
    return version.attrib.get("number") if version is not None else None


def summarize_pob_oauth(pob_dir: Path) -> dict[str, Any]:
    settings = pob_dir / "Settings.xml"
    info: dict[str, Any] = {
        "settings": str(settings),
        "settingsExists": settings.exists(),
        "hasAccessToken": False,
        "hasRefreshToken": False,
    }
    if not settings.exists():
        return info
    try:
        root = ET.parse(settings).getroot()
        accounts = root.find(".//Accounts")
    except Exception:
        info["parseError"] = True
        return info
    if accounts is None:
        info["accountsNode"] = False
        return info
    token_expiry = 0
    try:
        token_expiry = int(float(accounts.attrib.get("tokenExpiry") or "0"))
    except ValueError:
        pass
    info.update(
        {
            "accountsNode": True,
            "hasAccessToken": bool(accounts.attrib.get("lastToken")),
            "hasRefreshToken": bool(accounts.attrib.get("lastRefreshToken")),
            "tokenExpiry": token_expiry,
            "tokenExpired": token_expiry <= int(time.time()) + 60,
            "lastRealm": accounts.attrib.get("lastRealm"),
            "accountCount": len(list(accounts.findall("Account"))),
        }
    )
    return info


def file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def list_files(path: Path, patterns: list[str], limit: int = 20) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in patterns:
        for item in path.glob(pattern):
            key = str(item).lower()
            if item.is_file() and key not in seen:
                seen.add(key)
                out.append(file_info(item))
    out.sort(key=lambda item: item["mtime"], reverse=True)
    return out[:limit]


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def summarize_current(workspace: Path) -> dict[str, Any] | None:
    data = read_json(workspace / "current_character_summary.json")
    if not isinstance(data, dict):
        return None
    defensive = data.get("defensiveStats") if isinstance(data.get("defensiveStats"), dict) else {}
    return {
        "name": data.get("name"),
        "account": data.get("account"),
        "league": data.get("league"),
        "level": data.get("level"),
        "class": data.get("class"),
        "updatedUtc": data.get("updatedUtc"),
        "lastCheckedUtc": data.get("lastCheckedUtc"),
        "passiveTreeName": data.get("passiveTreeName"),
        "life": defensive.get("life"),
        "energyShield": defensive.get("energyShield"),
        "mana": defensive.get("mana"),
        "spirit": defensive.get("spirit"),
        "effectiveHealthPool": defensive.get("effectiveHealthPool"),
        "resistances": {
            "fire": defensive.get("fireResistance"),
            "cold": defensive.get("coldResistance"),
            "lightning": defensive.get("lightningResistance"),
            "chaos": defensive.get("chaosResistance"),
        },
        "skills": data.get("skills", [])[:8],
    }


def summarize_xml_builds(pob_dir: Path, workspace: Path) -> list[dict[str, Any]]:
    candidates = []
    candidates.extend(pob_dir.glob("Builds/*.xml"))
    candidates.extend(pob_dir.glob("*.xml"))
    candidates.extend(workspace.glob("*.xml"))
    seen = set()
    out: list[dict[str, Any]] = []
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name.lower() in {"manifest.xml", "settings.xml", "remote_manifest.xml"}:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        info = file_info(path)
        try:
            root = ET.parse(path).getroot()
            build = root.find("Build")
            if build is not None:
                info["buildName"] = build.attrib.get("title") or build.attrib.get("className")
                info["level"] = build.attrib.get("level")
                info["className"] = build.attrib.get("className")
            tree = root.find("Tree")
            if tree is not None:
                info["activeSpec"] = tree.attrib.get("activeSpec")
                info["specCount"] = len(tree.findall("Spec"))
        except Exception:
            info["parseError"] = True
        out.append(info)
    return out[:20]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Collect local PoB2/PoE2 build context.")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--pob", type=Path, default=DEFAULT_POB)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    workspace = args.workspace
    pob_dir = args.pob
    context = {
        "workspace": str(workspace),
        "pob": {
            "path": str(pob_dir),
            "exists": pob_dir.exists(),
            "version": read_pob_version(pob_dir),
            "exe": str(pob_dir / "Path of Building-PoE2.exe"),
            "exeExists": (pob_dir / "Path of Building-PoE2.exe").exists(),
            "oauth": summarize_pob_oauth(pob_dir),
        },
        "buildPlanner": [
            {
                "path": str(path),
                "exists": path.exists(),
                "files": list_files(path, ["*.build", "*.json"], limit=20),
            }
            for path in BUILDPLANNER_PATHS
        ],
        "currentCharacter": summarize_current(workspace),
        "workspaceFiles": {
            "summaries": list_files(workspace, ["*.summary.json", "current_character_summary.json"], limit=30),
            "currentSnapshots": list_files(workspace, ["*.current.json", "me_model*.json"], limit=30),
            "candidateFiles": list_files(workspace, ["*candidate*.json", "*candidates*.json", "*trade*.html"], limit=30),
            "pobExports": list_files(workspace, ["*.pob.txt", "*.xml", "*.build"], limit=30),
        },
        "xmlBuilds": summarize_xml_builds(pob_dir, workspace),
    }

    if args.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return

    print(f"Workspace: {workspace}")
    print(f"PoB2: {pob_dir} version={context['pob']['version']} exe={context['pob']['exeExists']}")
    oauth = context["pob"].get("oauth") or {}
    print(
        "PoB OAuth: "
        f"access={oauth.get('hasAccessToken')} refresh={oauth.get('hasRefreshToken')} "
        f"expired={oauth.get('tokenExpired')}"
    )
    current = context.get("currentCharacter")
    if current:
        print(
            "Current snapshot: "
            f"{current.get('name')} lvl {current.get('level')} {current.get('class')} "
            f"updated={current.get('updatedUtc')}"
        )
    print("BuildPlanner:")
    for entry in context["buildPlanner"]:
        print(f"  {entry['path']} exists={entry['exists']} files={len(entry['files'])}")
    print("Recent XML/build files:")
    for item in context["xmlBuilds"][:8]:
        label = item.get("buildName") or item["name"]
        print(f"  {label}: {item['path']}")


if __name__ == "__main__":
    main()
