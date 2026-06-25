#!/usr/bin/env python3
"""Prepare a local Path of Building 2 runtime for this skill.

The skill does not vendor PoB2. This script locates an existing PoB2 install or
downloads the official PathOfBuildingCommunity/PathOfBuilding-PoE2 manifest into
a local cache, then applies the small bundled headless adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO = "PathOfBuildingCommunity/PathOfBuilding-PoE2"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}"
DEFAULT_BRANCH = "master"
DEFAULT_PLATFORM = "win32"
EXE_NAME = "Path of Building-PoE2.exe"
HEADLESS_MODULE = Path(__file__).resolve().parents[1] / "assets" / "pob2-headless" / "HeadlessDpsCalc.lua"
HEADLESS_MARKER = 'os.getenv("POB_HEADLESS_CALC_CONFIG")'


@dataclass
class FileRecord:
    name: str
    part: str
    sha1: str
    platform: str | None


def default_cache_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "Codex" / "poe2-item-evaluator" / "PathOfBuilding-PoE2"
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "codex" / "poe2-item-evaluator" / "PathOfBuilding-PoE2"


def candidate_paths(extra: list[str] | None = None) -> list[Path]:
    values: list[Path] = []
    for env_name in ("POB2_PATH", "PATH_OF_BUILDING_POE2", "POB_PATH"):
        value = os.environ.get(env_name)
        if value:
            values.append(Path(value))
    if extra:
        values.extend(Path(x) for x in extra if x)
    values.extend(
        [
            Path(r"D:\Soft\PathOfBuilding-PoE2"),
            Path(r"D:\Soft\PoE2_Build\pob2_v0.21.1_extract"),
            default_cache_dir(),
        ]
    )
    seen: set[str] = set()
    result: list[Path] = []
    for path in values:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def is_valid_pob(path: Path) -> bool:
    return (path / EXE_NAME).exists() and (path / "Launch.lua").exists() and (path / "manifest.xml").exists()


def find_existing(extra: list[str] | None = None) -> Path | None:
    for path in candidate_paths(extra):
        if is_valid_pob(path):
            return path
    return None


def read_version(path: Path) -> dict[str, str | None]:
    manifest = path / "manifest.xml"
    if not manifest.exists():
        return {"number": None, "branch": None, "platform": None}
    try:
        root = ET.parse(manifest).getroot()
        version = root.find("Version")
        if version is None:
            return {"number": None, "branch": None, "platform": None}
        return {
            "number": version.attrib.get("number"),
            "branch": version.attrib.get("branch"),
            "platform": version.attrib.get("platform"),
        }
    except ET.ParseError:
        return {"number": None, "branch": None, "platform": None}


def download_bytes(url: str, *, tries: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "poe2-item-evaluator-bootstrap"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == tries:
                break
            time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"Failed to download {url}: {last_error}") from last_error


def parse_manifest(manifest_text: bytes, *, branch: str, platform_name: str) -> tuple[dict[str, dict[str, str]], list[FileRecord]]:
    root = ET.fromstring(manifest_text)
    sources: dict[str, dict[str, str]] = {}
    files: list[FileRecord] = []
    for node in root:
        if node.tag == "Source":
            part = node.attrib["part"]
            source_platform = node.attrib.get("platform") or "any"
            sources.setdefault(part, {})[source_platform] = node.attrib["url"].replace("{branch}", branch)
        elif node.tag == "File":
            file_platform = node.attrib.get("platform")
            if file_platform and file_platform != platform_name:
                continue
            files.append(
                FileRecord(
                    name=node.attrib["name"].replace("{space}", " "),
                    part=node.attrib["part"],
                    sha1=node.attrib["sha1"],
                    platform=file_platform,
                )
            )
    return sources, files


def source_for(record: FileRecord, sources: dict[str, dict[str, str]], platform_name: str) -> str:
    part_sources = sources.get(record.part) or {}
    source = part_sources.get(platform_name) or part_sources.get("any")
    if not source:
        raise RuntimeError(f"No source for manifest part {record.part!r}")
    if source.endswith(".zip"):
        raise RuntimeError(f"Zip sources are not supported by this bootstrap yet: {source}")
    return source


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def download_manifest_install(target: Path, *, branch: str, platform_name: str, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    manifest_url = f"{RAW_BASE}/{branch}/manifest.xml"
    manifest_text = download_bytes(manifest_url)
    sources, files = parse_manifest(manifest_text, branch=branch, platform_name=platform_name)
    if dry_run:
        total_name_bytes = sum(len(record.name) for record in files)
        return {
            "target": str(target),
            "manifestUrl": manifest_url,
            "fileCount": len(files),
            "nameBytes": total_name_bytes,
            "dryRun": True,
        }
    if target.exists() and force:
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for index, record in enumerate(files, 1):
        rel = Path(record.name)
        out_path = target / rel
        if out_path.exists():
            existing = out_path.read_bytes()
            if sha1(existing) == record.sha1:
                continue
        source = source_for(record, sources, platform_name)
        url = source + urllib.parse.quote(record.name.replace("\\", "/"))
        data = download_bytes(url)
        if sha1(data) != record.sha1 and sha1(data.replace(b"\n", b"\r\n")) != record.sha1:
            raise RuntimeError(f"Hash mismatch for {record.name}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(out_path)
        if index % 100 == 0:
            print(f"downloaded {index}/{len(files)}", file=sys.stderr)
    (target / "manifest.xml").write_bytes(manifest_text)
    return {
        "target": str(target),
        "manifestUrl": manifest_url,
        "fileCount": len(files),
        "installed": True,
    }


def patch_launch(path: Path) -> bool:
    launch = path / "Launch.lua"
    text = launch.read_text(encoding="utf-8")
    if HEADLESS_MARKER in text:
        return False
    needle = """\t\terrMsg = PCall(self.main.Init, self.main)
\t\tif errMsg then
\t\t\tself:ShowErrMsg(\"In 'Init': %s\", errMsg)
\t\tend
"""
    replacement = """\t\terrMsg = PCall(self.main.Init, self.main)
\t\tif errMsg then
\t\t\tself:ShowErrMsg(\"In 'Init': %s\", errMsg)
\t\telseif os.getenv(\"POB_HEADLESS_CALC_CONFIG\") then
\t\t\terrMsg = PCall(function()
\t\t\t\tLoadModule(\"Modules/HeadlessDpsCalc\")
\t\t\tend)
\t\t\tif errMsg then
\t\t\t\tlocal outPath = os.getenv(\"POB_HEADLESS_CALC_OUT\") or \"headless_dps_error.txt\"
\t\t\t\tlocal out = io.open(outPath, \"w\")
\t\t\t\tif out then
\t\t\t\t\tout:write(\"HeadlessDpsCalc error: \", tostring(errMsg), \"\\n\")
\t\t\t\t\tout:close()
\t\t\t\tend
\t\t\tend
\t\t\tExit()
\t\t\treturn
\t\tend
"""
    if needle not in text:
        raise RuntimeError("Launch.lua patch anchor not found; PoB2 layout may have changed")
    launch.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
    return True


def install_headless(path: Path) -> dict[str, Any]:
    if not HEADLESS_MODULE.exists():
        raise RuntimeError(f"Headless adapter missing: {HEADLESS_MODULE}")
    modules = path / "Modules"
    modules.mkdir(parents=True, exist_ok=True)
    target_module = modules / "HeadlessDpsCalc.lua"
    shutil.copy2(HEADLESS_MODULE, target_module)
    patched = patch_launch(path)
    return {
        "headlessModule": str(target_module),
        "launchPatched": patched,
        "headlessReady": HEADLESS_MARKER in (path / "Launch.lua").read_text(encoding="utf-8"),
    }


def headless_status(path: Path) -> dict[str, Any]:
    launch = path / "Launch.lua"
    module = path / "Modules" / "HeadlessDpsCalc.lua"
    launch_text = launch.read_text(encoding="utf-8") if launch.exists() else ""
    return {
        "headlessModule": str(module),
        "headlessModuleExists": module.exists(),
        "headlessReady": HEADLESS_MARKER in launch_text and module.exists(),
    }


def emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Locate or install Path of Building 2 for poe2-item-evaluator.")
    parser.add_argument("--pob-path", action="append", default=[], help="Candidate PoB2 install path; may be repeated.")
    parser.add_argument("--target", default=None, help="Install target when --install is used.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="PoB2 GitHub branch to install.")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, help="PoB2 manifest platform.")
    parser.add_argument("--install", action="store_true", help="Download PoB2 into the target/cache if no valid install exists.")
    parser.add_argument("--prepare-headless", action="store_true", help="Apply the bundled headless adapter to the selected runtime.")
    parser.add_argument("--force", action="store_true", help="Replace target directory during install.")
    parser.add_argument("--dry-run", action="store_true", help="Download and parse the manifest without installing files.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    target = Path(args.target) if args.target else default_cache_dir()
    existing = find_existing(args.pob_path)
    payload: dict[str, Any] = {
        "candidatePaths": [str(path) for path in candidate_paths(args.pob_path)],
        "found": str(existing) if existing else None,
    }
    if existing and not args.force and not args.dry_run:
        payload.update(
            {
                "pobPath": str(existing),
                "pobExe": str(existing / EXE_NAME),
                "version": read_version(existing),
            }
        )
        if args.prepare_headless:
            payload.update(install_headless(existing))
        else:
            payload.update(headless_status(existing))
        emit(payload, as_json=args.json)
        return 0
    if not args.install and not args.dry_run:
        payload["message"] = "No valid PoB2 runtime found. Re-run with --install to download it."
        emit(payload, as_json=args.json)
        return 2
    install_result = download_manifest_install(
        target,
        branch=args.branch,
        platform_name=args.platform,
        force=args.force,
        dry_run=args.dry_run,
    )
    payload.update(install_result)
    if not args.dry_run:
        payload.update(
            {
                "pobPath": str(target),
                "pobExe": str(target / EXE_NAME),
                "version": read_version(target),
            }
        )
        payload.update(install_headless(target))
    emit(payload, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
