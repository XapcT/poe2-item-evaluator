#!/usr/bin/env python3
"""Draw a transparent stash-price overlay from PoE2 stash price-check reports.

The overlay is intentionally local-only. It reads saved JSON reports produced by
poe_stash_pricecheck.py or the custom market-check helpers, then draws compact
price labels at the trade2 stash coordinates. It does not read or modify game
memory and it does not interact with the Path of Exile process.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = Path(r"D:\Soft\PoE2_Build\poe_stash_overlay_profile.json")
DEFAULT_SEARCH_ROOT = Path(r"D:\Soft\PoE2_Build")
DEFAULT_WINDOW_TITLES = ("Path of Exile", "PoeAncientsPriceHelper")
DEFAULT_TAB_MARKERS = ("marker2", "marker1")
DEFAULT_GRID_LEFT = 21
DEFAULT_GRID_TOP = 166
DEFAULT_CELL_SIZE = 70.0
DEFAULT_TAB_SCAN_LEFT = 0
DEFAULT_TAB_SCAN_TOP = 120
DEFAULT_TAB_SCAN_WIDTH = 900
DEFAULT_TAB_SCAN_HEIGHT = 55
TRANSPARENT_COLOR = "#ff00ff"
SLOT_GUARD_STATE_VERSION = 3
SLOT_SAMPLE_POINTS = (
    (0.15, 0.62),
    (0.50, 0.62),
    (0.85, 0.62),
    (0.15, 0.78),
    (0.50, 0.78),
    (0.85, 0.78),
    (0.15, 0.92),
    (0.50, 0.92),
    (0.85, 0.92),
)


@dataclass
class OverlayEntry:
    key: str
    marker: str
    x: int
    y: int
    text: str
    price_exalted: float | None
    source: str
    detail: str = ""


@dataclass
class TabGroup:
    x0: int
    x1: int
    loose_pixels: int
    active_pixels: int
    active_ratio: float
    brightness: float

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1


@dataclass
class TabState:
    active_marker: str | None
    groups: list[TabGroup]
    markers: list[str]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def clean_text(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]", lambda match: match.group(1).split("|")[-1], text or "")


def price_to_text(price: dict[str, Any] | None) -> str | None:
    if not price:
        return None
    amount = price.get("amount")
    currency = price.get("currency")
    if amount is None or currency is None:
        return None
    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return None
    if amount_f.is_integer():
        amount_s = str(int(amount_f))
    else:
        amount_s = f"{amount_f:.2f}".rstrip("0").rstrip(".")
    aliases = {
        "exalted": "ex",
        "divine": "div",
        "chaos": "c",
        "mirror": "mir",
    }
    return f"{amount_s}{aliases.get(str(currency), str(currency))}"


def price_to_exalted(price: dict[str, Any] | None, divine_to_exalted: float, chaos_to_exalted: float) -> float | None:
    if not price:
        return None
    amount = price.get("amount")
    currency = price.get("currency")
    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return None
    if currency == "exalted":
        return amount_f
    if currency == "divine":
        return amount_f * divine_to_exalted
    if currency == "chaos":
        return amount_f / chaos_to_exalted
    if currency == "mirror":
        return amount_f * divine_to_exalted * 300.0
    return None


def entry_key(marker: str, x: int, y: int) -> str:
    return f"{marker}:{x}:{y}"


def marker_label(row: dict[str, Any]) -> str:
    marker = row.get("marker") or row.get("sourceMarker") or row.get("stashName") or ""
    if isinstance(marker, str) and marker:
        return marker
    return "tab"


def explicit_overlay_entry(
    row: dict[str, Any],
    *,
    source: str,
    min_price_exalted: float,
) -> OverlayEntry | None:
    xy = row.get("xy")
    if isinstance(xy, list) and len(xy) >= 2:
        x, y = xy[0], xy[1]
    else:
        x, y = row.get("x"), row.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    text = row.get("text") or row.get("priceText") or row.get("recommendedPrice")
    if not isinstance(text, str) or not text.strip():
        return None
    price_exalted = row.get("priceExalted")
    try:
        price_exalted_f = float(price_exalted) if price_exalted is not None else None
    except (TypeError, ValueError):
        price_exalted_f = None
    if price_exalted_f is not None and price_exalted_f < min_price_exalted:
        return None
    marker = marker_label(row)
    label = row.get("labelRu") or row.get("label") or row.get("note") or ""
    return OverlayEntry(
        key=entry_key(marker, x, y),
        marker=marker,
        x=x,
        y=y,
        text=text.strip(),
        price_exalted=price_exalted_f,
        source=source,
        detail=str(label),
    )


def entry_from_market_row(
    row: dict[str, Any],
    *,
    source: str,
    min_price_exalted: float,
    include_uncertain: bool,
    divine_to_exalted: float,
    chaos_to_exalted: float,
) -> OverlayEntry | None:
    xy = row.get("xy")
    if isinstance(xy, list) and len(xy) >= 2:
        x, y = xy[0], xy[1]
    else:
        x, y = row.get("x"), row.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None

    market = row.get("market") or {}
    floor = market.get("floor") if isinstance(market, dict) else None
    price = floor.get("price") if isinstance(floor, dict) else None
    price_exalted = floor.get("priceExalted") if isinstance(floor, dict) else None
    if price_exalted is None:
        price_exalted = price_to_exalted(price, divine_to_exalted, chaos_to_exalted)
    try:
        price_exalted_f = float(price_exalted) if price_exalted is not None else None
    except (TypeError, ValueError):
        price_exalted_f = None

    text = price_to_text(price)
    if text is None and include_uncertain:
        total = market.get("total") if isinstance(market, dict) else None
        text = f"?{total}" if total else "?"
    if text is None:
        return None
    if price_exalted_f is not None and price_exalted_f < min_price_exalted:
        return None
    if price_exalted_f is None and not include_uncertain:
        return None

    label = row.get("labelRu") or row.get("label") or row.get("tag") or ""
    marker = marker_label(row)
    return OverlayEntry(
        key=entry_key(marker, x, y),
        marker=marker,
        x=x,
        y=y,
        text=text,
        price_exalted=price_exalted_f,
        source=source,
        detail=str(label),
    )


def entry_from_candidate(
    row: dict[str, Any],
    *,
    source: str,
    min_price_exalted: float,
    divine_to_exalted: float,
    chaos_to_exalted: float,
) -> OverlayEntry | None:
    x, y = row.get("x"), row.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    best = row.get("bestMarketCheck") or {}
    floor = best.get("floor") if isinstance(best, dict) else None
    price = floor.get("price") if isinstance(floor, dict) else None
    price_exalted = floor.get("priceExalted") if isinstance(floor, dict) else None
    if price_exalted is None:
        price_exalted = price_to_exalted(price, divine_to_exalted, chaos_to_exalted)
    try:
        price_exalted_f = float(price_exalted) if price_exalted is not None else None
    except (TypeError, ValueError):
        return None
    if price_exalted_f is None or price_exalted_f < min_price_exalted:
        return None
    text = price_to_text(price)
    if text is None:
        return None
    marker = marker_label(row)
    return OverlayEntry(
        key=entry_key(marker, x, y),
        marker=marker,
        x=x,
        y=y,
        text=text,
        price_exalted=price_exalted_f,
        source=source,
        detail=str(row.get("label") or ""),
    )


def load_entries_from_report(path: Path, args: argparse.Namespace) -> list[OverlayEntry]:
    data = load_json(path)
    entries: list[OverlayEntry] = []
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        for row in data["results"]:
            if not isinstance(row, dict):
                continue
            explicit = explicit_overlay_entry(row, source=str(path), min_price_exalted=args.min_price_exalted)
            if explicit:
                entries.append(explicit)
                continue
            entry = entry_from_market_row(
                row,
                source=str(path),
                min_price_exalted=args.min_price_exalted,
                include_uncertain=args.include_uncertain,
                divine_to_exalted=args.divine_to_exalted,
                chaos_to_exalted=args.chaos_to_exalted,
            )
            if entry:
                entries.append(entry)
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        for row in data["candidates"]:
            if not isinstance(row, dict):
                continue
            entry = entry_from_candidate(
                row,
                source=str(path),
                min_price_exalted=args.min_price_exalted,
                divine_to_exalted=args.divine_to_exalted,
                chaos_to_exalted=args.chaos_to_exalted,
            )
            if entry:
                entries.append(entry)
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            explicit = explicit_overlay_entry(row, source=str(path), min_price_exalted=args.min_price_exalted)
            if explicit:
                entries.append(explicit)
                continue
            entry = entry_from_market_row(
                row,
                source=str(path),
                min_price_exalted=args.min_price_exalted,
                include_uncertain=args.include_uncertain,
                divine_to_exalted=args.divine_to_exalted,
                chaos_to_exalted=args.chaos_to_exalted,
            )
            if entry:
                entries.append(entry)
    return entries


def discover_latest_dir(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path.name.startswith("stash_eval_") or path.name.startswith("stash_newparty_") or path.name.startswith("stash_craftcheck_"))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def reports_from_latest_dir(latest_dir: Path) -> list[Path]:
    overlay = latest_dir / "overlay_prices.json"
    if overlay.exists():
        return [overlay]
    names = [
        "overlay_prices.json",
        "custom_market_checks.json",
        "relaxed_market_checks.json",
        "tool_market_report_1mirror.json",
        "tool_market_report_2mirror.json",
    ]
    reports = [latest_dir / name for name in names if (latest_dir / name).exists()]
    reports.extend(sorted(latest_dir.glob("**/custom_market_checks.json")))
    reports.extend(sorted(latest_dir.glob("**/relaxed_market_checks.json")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for report in reports:
        resolved = report.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(report)
    return unique


def collect_entries(args: argparse.Namespace) -> list[OverlayEntry]:
    reports: list[Path] = [Path(path) for path in args.report or []]
    if args.latest:
        latest_dir = discover_latest_dir(args.search_root)
        if latest_dir is None:
            raise SystemExit(f"No stash report directory found under {args.search_root}")
        reports.extend(reports_from_latest_dir(latest_dir))
        print(f"latestDir={latest_dir}", file=sys.stderr)
    if not reports:
        raise SystemExit("Pass --report <json> or --latest")

    merged: dict[str, OverlayEntry] = {}
    for report in reports:
        for entry in load_entries_from_report(report, args):
            current = merged.get(entry.key)
            if current is None:
                merged[entry.key] = entry
                continue
            current_value = current.price_exalted if current.price_exalted is not None else -1
            new_value = entry.price_exalted if entry.price_exalted is not None else -1
            if new_value > current_value:
                merged[entry.key] = entry
    entries = sorted(merged.values(), key=lambda item: (item.marker, item.y, item.x, item.text))
    if args.marker and not getattr(args, "auto_marker", False):
        wanted = set(args.marker)
        entries = [entry for entry in entries if entry.marker in wanted]
    return entries


def print_entries(entries: list[OverlayEntry]) -> None:
    for entry in entries:
        price = "?" if entry.price_exalted is None else f"{entry.price_exalted:g}ex"
        detail = f" | {entry.detail}" if entry.detail else ""
        print(f"{entry.marker} x={entry.x} y={entry.y} col={entry.x + 1} row={entry.y + 1} {entry.text} ({price}){detail}")


def slot_guard_state_path(args: argparse.Namespace) -> Path:
    if args.slot_guard_state:
        return args.slot_guard_state
    return args.search_root / "poe_stash_slot_guard_state.json"


def report_signature(entries: list[OverlayEntry]) -> str:
    parts: list[str] = []
    for source in sorted({entry.source for entry in entries if entry.source}):
        path = Path(source)
        try:
            stat = path.stat()
            parts.append(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}")
        except OSError:
            parts.append(source)
    digest = hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()
    return digest


def decode_fingerprint(value: Any) -> tuple[tuple[int, int, int], ...] | None:
    if not isinstance(value, list):
        return None
    points: list[tuple[int, int, int]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 3:
            return None
        try:
            points.append((int(item[0]), int(item[1]), int(item[2])))
        except (TypeError, ValueError):
            return None
    return tuple(points)


def load_slot_guard_state(
    args: argparse.Namespace,
    signature: str,
) -> tuple[dict[str, tuple[tuple[int, int, int], ...]], set[str]]:
    path = slot_guard_state_path(args)
    if not path.exists():
        return {}, set()
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}, set()
    if (
        not isinstance(data, dict)
        or data.get("version") != SLOT_GUARD_STATE_VERSION
        or data.get("reportSignature") != signature
    ):
        return {}, set()
    raw_baselines = data.get("baselines")
    baselines: dict[str, tuple[tuple[int, int, int], ...]] = {}
    if isinstance(raw_baselines, dict):
        for key, value in raw_baselines.items():
            fingerprint = decode_fingerprint(value)
            if fingerprint is not None:
                baselines[str(key)] = fingerprint
    raw_stale = data.get("staleKeys")
    stale_keys = {str(item) for item in raw_stale} if isinstance(raw_stale, list) else set()
    return baselines, stale_keys


def save_slot_guard_state(
    args: argparse.Namespace,
    signature: str,
    baselines: dict[str, tuple[tuple[int, int, int], ...]],
    stale_keys: set[str],
) -> None:
    path = slot_guard_state_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": SLOT_GUARD_STATE_VERSION,
        "reportSignature": signature,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baselines": {key: [list(point) for point in value] for key, value in sorted(baselines.items())},
        "staleKeys": sorted(stale_keys),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_profile(path: Path) -> dict[str, Any]:
    if path.exists():
        return load_json(path)
    return {}


def save_profile(path: Path, profile: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def find_window(title_fragments: tuple[str, ...]) -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    results: list[tuple[int, str, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if not any(fragment.lower() in title.lower() for fragment in title_fragments):
            return True
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        results.append((hwnd, title, (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)))
        return True

    user32.EnumWindows(enum_proc, 0)
    if not results:
        return None
    results.sort(key=lambda row: row[2][2] * row[2][3], reverse=True)
    return results[0][2]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.wintypes.LONG),
        ("biHeight", ctypes.wintypes.LONG),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.wintypes.LONG),
        ("biYPelsPerMeter", ctypes.wintypes.LONG),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 1),
    ]


def capture_screen_region(left: int, top: int, width: int, height: int) -> tuple[int, int, bytes] | None:
    if os.name != "nt" or width <= 0 or height <= 0:
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc_type = getattr(ctypes.wintypes, "HDC", ctypes.c_void_p)
    hwnd_type = ctypes.wintypes.HWND
    hbitmap_type = getattr(ctypes.wintypes, "HBITMAP", ctypes.c_void_p)
    hgdiobj_type = getattr(ctypes.wintypes, "HGDIOBJ", ctypes.c_void_p)

    user32.GetDC.argtypes = [hwnd_type]
    user32.GetDC.restype = hdc_type
    user32.ReleaseDC.argtypes = [hwnd_type, hdc_type]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [hdc_type]
    gdi32.CreateCompatibleDC.restype = hdc_type
    gdi32.CreateCompatibleBitmap.argtypes = [hdc_type, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = hbitmap_type
    gdi32.SelectObject.argtypes = [hdc_type, hgdiobj_type]
    gdi32.SelectObject.restype = hgdiobj_type
    gdi32.BitBlt.argtypes = [
        hdc_type,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        hdc_type,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = ctypes.wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        hdc_type,
        hbitmap_type,
        ctypes.wintypes.UINT,
        ctypes.wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [hgdiobj_type]
    gdi32.DeleteObject.restype = ctypes.wintypes.BOOL
    gdi32.DeleteDC.argtypes = [hdc_type]
    gdi32.DeleteDC.restype = ctypes.wintypes.BOOL

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    mem_dc = None
    bitmap = None
    old_object = None
    try:
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            return None
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            return None
        old_object = gdi32.SelectObject(mem_dc, bitmap)
        if not gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, left, top, 0x00CC0020):
            return None

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(info),
            0,
        )
        if lines != height:
            return None
        return width, height, buffer.raw
    finally:
        if mem_dc and old_object:
            gdi32.SelectObject(mem_dc, old_object)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def is_loose_tab_gold(r: int, g: int, b: int) -> bool:
    return r > 90 and g > 45 and b < 90 and (r - b) > 45 and (g - b) > 15


def is_active_tab_gold(r: int, g: int, b: int) -> bool:
    return r > 145 and g > 95 and b < 95 and (r - b) > 70 and (g - b) > 35


def detect_tab_groups(width: int, height: int, raw_bgra: bytes, args: argparse.Namespace) -> list[TabGroup]:
    if width <= 0 or height <= 0:
        return []

    min_col_pixels = max(3, int(height * 0.08))
    col_hits = [0] * width
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            offset = row + x * 4
            b = raw_bgra[offset]
            g = raw_bgra[offset + 1]
            r = raw_bgra[offset + 2]
            if is_loose_tab_gold(r, g, b):
                col_hits[x] += 1

    spans: list[tuple[int, int]] = []
    start = -1
    last_hit = -1
    max_gap = max(1, int(args.tab_max_gap))
    for x, hits in enumerate(col_hits):
        if hits >= min_col_pixels:
            if start < 0:
                start = x
            last_hit = x
            continue
        if start >= 0 and x - last_hit > max_gap:
            if last_hit - start + 1 >= args.tab_min_width:
                spans.append((start, last_hit))
            start = -1
            last_hit = -1
    if start >= 0 and last_hit - start + 1 >= args.tab_min_width:
        spans.append((start, last_hit))

    groups: list[TabGroup] = []
    for x0, x1 in spans:
        loose_pixels = 0
        active_pixels = 0
        brightness_sum = 0
        for y in range(height):
            row = y * width * 4
            for x in range(x0, x1 + 1):
                offset = row + x * 4
                b = raw_bgra[offset]
                g = raw_bgra[offset + 1]
                r = raw_bgra[offset + 2]
                if is_loose_tab_gold(r, g, b):
                    loose_pixels += 1
                    brightness_sum += r + g + b
                if is_active_tab_gold(r, g, b):
                    active_pixels += 1
        if loose_pixels <= 0:
            continue
        groups.append(
            TabGroup(
                x0=x0,
                x1=x1,
                loose_pixels=loose_pixels,
                active_pixels=active_pixels,
                active_ratio=active_pixels / loose_pixels,
                brightness=brightness_sum / (loose_pixels * 3),
            )
        )
    return groups


def resolve_tab_markers(profile: dict[str, Any], args: argparse.Namespace) -> list[str]:
    source = args.tab_marker or profile.get("tabMarkers") or args.marker or list(DEFAULT_TAB_MARKERS)
    if isinstance(source, str):
        markers = re.split(r"[\s,]+", source)
    elif isinstance(source, list):
        markers = [str(item) for item in source]
    else:
        markers = list(DEFAULT_TAB_MARKERS)
    return [marker.strip() for marker in markers if marker and marker.strip()]


def detect_tab_state(
    profile: dict[str, Any],
    args: argparse.Namespace,
    *,
    window_left: int,
    window_top: int,
) -> TabState:
    left = int(profile.get("tabScanLeft", args.tab_scan_left))
    top = int(profile.get("tabScanTop", args.tab_scan_top))
    width = int(profile.get("tabScanWidth", args.tab_scan_width))
    height = int(profile.get("tabScanHeight", args.tab_scan_height))
    markers = resolve_tab_markers(profile, args)
    capture = capture_screen_region(window_left + left, window_top + top, width, height)
    if capture is None:
        return TabState(active_marker=None, groups=[], markers=markers)

    captured_width, captured_height, raw_bgra = capture
    groups = detect_tab_groups(captured_width, captured_height, raw_bgra, args)
    if args.debug_tabs:
        compact = ", ".join(
            f"{group.x0}-{group.x1} w={group.width} active={group.active_ratio:.2f}"
            for group in groups
        )
        print(f"tabGroups=[{compact}] markers={markers}", file=sys.stderr)
    if not groups or not markers:
        return TabState(active_marker=None, groups=groups, markers=markers)

    best = max(groups, key=lambda group: (group.active_ratio, group.active_pixels, group.brightness))
    if best.active_ratio < args.tab_active_min_ratio:
        return TabState(active_marker=None, groups=groups, markers=markers)

    sorted_groups = sorted(groups, key=lambda group: group.x0)
    active_index = sorted_groups.index(best)
    if active_index >= len(markers):
        return TabState(active_marker=None, groups=groups, markers=markers)
    return TabState(active_marker=markers[active_index], groups=groups, markers=markers)


def detect_active_marker(
    profile: dict[str, Any],
    args: argparse.Namespace,
    *,
    window_left: int,
    window_top: int,
) -> str | None:
    return detect_tab_state(profile, args, window_left=window_left, window_top=window_top).active_marker


def apply_click_through(root: Any) -> None:
    if os.name != "nt":
        return
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    if not hwnd:
        hwnd = root.winfo_id()
    ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
    ex_style |= 0x00080000  # WS_EX_LAYERED
    ex_style |= 0x00000020  # WS_EX_TRANSPARENT
    ex_style |= 0x00000008  # WS_EX_TOPMOST
    ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style)


def color_for(entry: OverlayEntry) -> tuple[str, str]:
    value = entry.price_exalted
    if value is None:
        return "#a0a0a0", "#202020"
    if value >= 105:
        return "#ffd35a", "#211600"
    if value >= 50:
        return "#ff9f43", "#241000"
    if value >= 10:
        return "#74e2ff", "#001b22"
    return "#b6ff7a", "#102000"


def blend_hex_color(color: str, target: str, opacity: float) -> str:
    opacity = max(0.0, min(1.0, opacity))
    color = color.lstrip("#")
    target = target.lstrip("#")
    if len(color) != 6 or len(target) != 6:
        return f"#{color}"
    parts = []
    for index in (0, 2, 4):
        source_value = int(color[index:index + 2], 16)
        target_value = int(target[index:index + 2], 16)
        mixed = round(target_value + (source_value - target_value) * opacity)
        parts.append(f"{mixed:02x}")
    return "#" + "".join(parts)


def marker_display_name(marker: str) -> str:
    aliases = {
        "marker1": "1 mirror",
        "marker2": "2 mirror",
    }
    return aliases.get(marker, marker)


def draw_empty_status(canvas: Any, profile: dict[str, Any], args: argparse.Namespace, status_text: str) -> None:
    left = int(profile.get("gridLeft", args.grid_left))
    top = int(profile.get("gridTop", args.grid_top))
    text_id = canvas.create_text(
        left + 10,
        top + 10,
        text=status_text,
        fill="#ffd35a",
        font=("Segoe UI", args.font_size, "bold"),
        anchor="nw",
    )
    bbox = canvas.bbox(text_id)
    if bbox:
        pad_x = 7
        pad_y = 3
        rect = canvas.create_rectangle(
            bbox[0] - pad_x,
            bbox[1] - pad_y,
            bbox[2] + pad_x,
            bbox[3] + pad_y,
            fill="#211600",
            outline="#ffd35a",
            width=1,
        )
        canvas.tag_lower(rect, text_id)


def tab_scan_rect(profile: dict[str, Any], args: argparse.Namespace) -> tuple[int, int, int, int]:
    left = int(profile.get("tabScanLeft", args.tab_scan_left))
    top = int(profile.get("tabScanTop", args.tab_scan_top))
    width = int(profile.get("tabScanWidth", args.tab_scan_width))
    height = int(profile.get("tabScanHeight", args.tab_scan_height))
    return left, top, width, height


def grid_rect(profile: dict[str, Any], args: argparse.Namespace) -> tuple[int, int, int, int]:
    left = int(profile.get("gridLeft", args.grid_left))
    top = int(profile.get("gridTop", args.grid_top))
    cell = float(profile.get("cellSize", args.cell_size))
    cols = int(profile.get("columns", args.columns))
    rows = int(profile.get("rows", args.rows))
    return left, top, int(cols * cell), int(rows * cell)


def capture_grid_pixels(
    profile: dict[str, Any],
    args: argparse.Namespace,
    *,
    window_left: int,
    window_top: int,
) -> tuple[int, int, bytes] | None:
    left, top, width, height = grid_rect(profile, args)
    return capture_screen_region(window_left + left, window_top + top, width, height)


def stop_other_overlay_processes() -> None:
    if os.name != "nt":
        return
    current_pid = os.getpid()
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -like 'python*') -and ($_.CommandLine -like '*poe_stash_overlay.py*') "
        f"-and ($_.ProcessId -ne {current_pid}) }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def sample_slot_fingerprint(
    entry: OverlayEntry,
    width: int,
    height: int,
    raw_bgra: bytes,
    profile: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[tuple[int, int, int], ...] | None:
    cell = float(profile.get("cellSize", args.cell_size))
    samples: list[tuple[int, int, int]] = []
    for rel_x, rel_y in SLOT_SAMPLE_POINTS:
        px = int(entry.x * cell + rel_x * cell)
        py = int(entry.y * cell + rel_y * cell)
        if px < 0 or py < 0 or px >= width or py >= height:
            continue
        offset = (py * width + px) * 4
        b = raw_bgra[offset]
        g = raw_bgra[offset + 1]
        r = raw_bgra[offset + 2]
        samples.append((r, g, b))
    if len(samples) < args.slot_guard_min_samples:
        return None
    return tuple(samples)


def slot_fingerprint_looks_empty(
    current: tuple[tuple[int, int, int], ...],
    args: argparse.Namespace,
) -> bool:
    if not current:
        return False
    lumas: list[float] = []
    saturations: list[int] = []
    colored_samples = 0
    for rgb in current:
        luma = sum(rgb) / 3.0
        saturation = max(rgb) - min(rgb)
        lumas.append(luma)
        saturations.append(saturation)
        if saturation >= args.slot_guard_empty_colored_threshold:
            colored_samples += 1
    avg_luma = sum(lumas) / len(lumas)
    max_luma = max(lumas)
    avg_saturation = sum(saturations) / len(saturations)
    return (
        avg_luma <= args.slot_guard_empty_avg_luma
        and max_luma <= args.slot_guard_empty_max_luma
        and avg_saturation <= args.slot_guard_empty_avg_saturation
        and colored_samples <= args.slot_guard_empty_max_colored_samples
    )


def slot_fingerprint_changed(
    baseline: tuple[tuple[int, int, int], ...],
    current: tuple[tuple[int, int, int], ...],
    args: argparse.Namespace,
) -> bool:
    if len(baseline) != len(current):
        return True
    changed_samples = 0
    total_delta = 0.0
    for old_rgb, new_rgb in zip(baseline, current):
        delta = sum(abs(old - new) for old, new in zip(old_rgb, new_rgb)) / 3.0
        total_delta += delta
        if delta >= args.slot_guard_point_threshold:
            changed_samples += 1
    avg_delta = total_delta / max(1, len(baseline))
    return changed_samples >= args.slot_guard_min_changed_samples and avg_delta >= args.slot_guard_avg_threshold


def point_in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    left, top, width, height = rect
    return left <= x <= left + width and top <= y <= top + height


def grid_handle_rect(profile: dict[str, Any], args: argparse.Namespace) -> tuple[int, int, int, int]:
    left = int(profile.get("gridLeft", args.grid_left))
    top = int(profile.get("gridTop", args.grid_top))
    return left, max(0, top - 30), 92, 24


def calibration_button_rects() -> dict[str, tuple[int, int, int, int]]:
    return {
        "done": (12, 98, 104, 28),
        "save": (122, 98, 104, 28),
        "reset": (232, 98, 86, 28),
        "minus": (324, 98, 34, 28),
        "plus": (364, 98, 34, 28),
        "close": (404, 98, 86, 28),
    }


def draw_label_box(canvas: Any, x: int, y: int, text: str, fg: str, bg: str) -> None:
    text_id = canvas.create_text(x, y, text=text, fill=fg, font=("Segoe UI", 10, "bold"), anchor="nw")
    bbox = canvas.bbox(text_id)
    if not bbox:
        return
    rect = canvas.create_rectangle(bbox[0] - 4, bbox[1] - 2, bbox[2] + 4, bbox[3] + 2, fill=bg, outline=fg, width=1)
    canvas.tag_lower(rect, text_id)


def draw_calibration_help(canvas: Any, mode: str) -> None:
    lines = [
        f"calibration: {mode}",
        "drag СЕТКА handle or tab scan box",
        "Готово saves and closes; Сброс restores default",
        "Double Enter also saves and closes",
    ]
    text = "\n".join(lines)
    text_id = canvas.create_text(12, 12, text=text, fill="#ffffff", font=("Segoe UI", 10, "bold"), anchor="nw")
    bbox = canvas.bbox(text_id)
    if bbox:
        rect = canvas.create_rectangle(bbox[0] - 7, bbox[1] - 5, bbox[2] + 7, bbox[3] + 5, fill="#111111", outline="#4aa3ff", width=1)
        canvas.tag_lower(rect, text_id)


def draw_calibration_button(canvas: Any, rect: tuple[int, int, int, int], text: str, *, active: bool = False) -> None:
    left, top, width, height = rect
    outline = "#6cff72" if active else "#4aa3ff"
    canvas.create_rectangle(left, top, left + width, top + height, fill="#111111", outline=outline, width=2)
    canvas.create_text(
        left + width / 2,
        top + height / 2,
        text=text,
        fill="#ffffff",
        font=("Segoe UI", 10, "bold"),
        anchor="center",
    )


def draw_grid_handle(canvas: Any, profile: dict[str, Any], args: argparse.Namespace, selected_mode: str | None) -> None:
    rect = grid_handle_rect(profile, args)
    left, top, width, height = rect
    outline = "#6cff72" if selected_mode == "grid" else "#ffd35a"
    canvas.create_rectangle(left, top, left + width, top + height, fill="#181100", outline=outline, width=2)
    canvas.create_text(
        left + width / 2,
        top + height / 2,
        text="СЕТКА",
        fill=outline,
        font=("Segoe UI", 10, "bold"),
        anchor="center",
    )


def draw_calibration_controls(canvas: Any, profile: dict[str, Any], args: argparse.Namespace, selected_mode: str | None) -> None:
    draw_grid_handle(canvas, profile, args, selected_mode)
    labels = {
        "done": "Готово",
        "save": "Сохранить",
        "reset": "Сброс",
        "minus": "-",
        "plus": "+",
        "close": "Закрыть",
    }
    for action, rect in calibration_button_rects().items():
        draw_calibration_button(canvas, rect, labels[action], active=action == "done")


def reset_calibration(profile: dict[str, Any], args: argparse.Namespace) -> None:
    profile["gridLeft"] = args.grid_left
    profile["gridTop"] = args.grid_top
    profile["cellSize"] = args.cell_size
    profile["tabScanLeft"] = args.tab_scan_left
    profile["tabScanTop"] = args.tab_scan_top
    profile["tabScanWidth"] = args.tab_scan_width
    profile["tabScanHeight"] = args.tab_scan_height


def draw_tab_scan_overlay(
    canvas: Any,
    profile: dict[str, Any],
    args: argparse.Namespace,
    tab_state: TabState | None,
    selected_mode: str | None,
) -> None:
    left, top, width, height = tab_scan_rect(profile, args)
    outline = "#6cff72" if selected_mode == "tab" else "#4aa3ff"
    canvas.create_rectangle(left, top, left + width, top + height, outline=outline, width=3)
    draw_label_box(canvas, left + 6, top + 5, "tab scan", outline, "#0b1420")

    if tab_state is None:
        return
    groups = sorted(tab_state.groups, key=lambda group: group.x0)
    for index, group in enumerate(groups):
        marker = tab_state.markers[index] if index < len(tab_state.markers) else f"tab{index + 1}"
        is_active = marker == tab_state.active_marker
        color = "#6cff72" if is_active else "#ffd35a"
        canvas.create_rectangle(
            left + group.x0,
            top + 3,
            left + group.x1,
            top + height - 3,
            outline=color,
            width=2,
        )
        suffix = " active" if is_active else ""
        draw_label_box(canvas, left + group.x0 + 4, top + height - 18, f"{marker}{suffix}", color, "#181100")


def draw_price_label(
    canvas: Any,
    entry: OverlayEntry,
    profile: dict[str, Any],
    args: argparse.Namespace,
    *,
    opacity: float = 1.0,
) -> None:
    left = int(profile.get("gridLeft", args.grid_left))
    top = int(profile.get("gridTop", args.grid_top))
    cell = float(profile.get("cellSize", args.cell_size))
    fg, bg = color_for(entry)
    if opacity < 1.0:
        fg = blend_hex_color(fg, "#2b2b2b", opacity)
        bg = blend_hex_color(bg, "#090909", opacity)
    cx = left + entry.x * cell + cell * 0.5
    cy = top + entry.y * cell + cell * 0.18
    text_id = canvas.create_text(
        cx,
        cy,
        text=entry.text,
        fill=fg,
        font=("Segoe UI", args.font_size, "bold"),
        anchor="n",
        tags=("price-label",),
    )
    bbox = canvas.bbox(text_id)
    if bbox:
        pad_x = 5
        pad_y = 2
        rect = canvas.create_rectangle(
            bbox[0] - pad_x,
            bbox[1] - pad_y,
            bbox[2] + pad_x,
            bbox[3] + pad_y,
            fill=bg,
            outline=fg,
            width=1,
            tags=("price-label",),
        )
        canvas.tag_lower(rect, text_id)


def draw_overlay(
    canvas: Any,
    entries: list[OverlayEntry],
    profile: dict[str, Any],
    args: argparse.Namespace,
    status_text: str | None = None,
    tab_state: TabState | None = None,
    selected_mode: str | None = None,
    fading_entries: list[tuple[OverlayEntry, float]] | None = None,
) -> None:
    left = int(profile.get("gridLeft", args.grid_left))
    top = int(profile.get("gridTop", args.grid_top))
    cell = float(profile.get("cellSize", args.cell_size))
    cols = int(profile.get("columns", args.columns))
    rows = int(profile.get("rows", args.rows))
    canvas.delete("all")

    if args.show_grid or args.calibrate:
        color = "#b59445"
        for col in range(cols + 1):
            x = left + col * cell
            canvas.create_line(x, top, x, top + rows * cell, fill=color, width=1)
        for row in range(rows + 1):
            y = top + row * cell
            canvas.create_line(left, y, left + cols * cell, y, fill=color, width=1)

    for entry in entries:
        if entry.x < 0 or entry.y < 0 or entry.x >= cols or entry.y >= rows:
            continue
        draw_price_label(canvas, entry, profile, args)

    for entry, opacity in fading_entries or []:
        if entry.x < 0 or entry.y < 0 or entry.x >= cols or entry.y >= rows:
            continue
        draw_price_label(canvas, entry, profile, args, opacity=opacity)

    if status_text:
        draw_empty_status(canvas, profile, args, status_text)

    if args.show_tab_scan or args.calibrate:
        draw_tab_scan_overlay(canvas, profile, args, tab_state, selected_mode)

    if args.calibrate:
        draw_calibration_controls(canvas, profile, args, selected_mode)
        draw_calibration_help(canvas, selected_mode or "grid")


def run_overlay(entries: list[OverlayEntry], args: argparse.Namespace) -> None:
    import tkinter as tk

    profile = load_profile(args.profile)

    def find_target_window() -> tuple[int, int, int, int] | None:
        if args.window_title:
            return find_window(tuple(args.window_title))
        if args.auto_window:
            return find_window(DEFAULT_WINDOW_TITLES)
        return None

    window_rect = find_target_window()
    while window_rect is None and args.wait_for_window:
        time.sleep(max(0.1, args.window_poll_ms / 1000.0))
        window_rect = find_target_window()

    if window_rect:
        win_left, win_top, win_width, win_height = window_rect
    else:
        win_left, win_top, win_width, win_height = args.window_left, args.window_top, args.window_width, args.window_height
    window_state = {"left": win_left, "top": win_top, "width": win_width, "height": win_height}

    root = tk.Tk()
    root.title("PoE2 Stash Price Overlay")
    root.geometry(f"{win_width}x{win_height}+{win_left}+{win_top}")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=TRANSPARENT_COLOR)
    try:
        root.attributes("-transparentcolor", TRANSPARENT_COLOR)
    except tk.TclError:
        root.attributes("-alpha", args.alpha)

    canvas = tk.Canvas(root, width=win_width, height=win_height, bg=TRANSPARENT_COLOR, highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    profile.setdefault("gridLeft", args.grid_left)
    profile.setdefault("gridTop", args.grid_top)
    profile.setdefault("cellSize", args.cell_size)
    profile.setdefault("columns", args.columns)
    profile.setdefault("rows", args.rows)
    profile.setdefault("tabScanLeft", args.tab_scan_left)
    profile.setdefault("tabScanTop", args.tab_scan_top)
    profile.setdefault("tabScanWidth", args.tab_scan_width)
    profile.setdefault("tabScanHeight", args.tab_scan_height)
    profile.setdefault("tabMarkers", list(DEFAULT_TAB_MARKERS))

    slot_guard_report_signature = report_signature(entries)
    loaded_baselines, loaded_stale_keys = (
        load_slot_guard_state(args, slot_guard_report_signature) if args.slot_guard else ({}, set())
    )
    display_entries = list(entries) if not args.auto_marker else []
    display_status: str | None = None
    current_marker: str | None = "__unset__"
    slot_guard_baselines: dict[str, tuple[tuple[int, int, int], ...]] = loaded_baselines
    slot_guard_stale_keys: set[str] = loaded_stale_keys
    slot_guard_pending_stale: dict[str, float] = {}
    slot_guard_fading: dict[str, tuple[OverlayEntry, float]] = {}
    fade_animation_active = {"value": False}
    calibration_mode = {"name": "grid"}
    drag_state: dict[str, Any] = {"active": False, "x": 0, "y": 0, "mode": "grid"}
    enter_state = {"last": 0.0}

    def active_marker_key() -> str | None:
        return None if current_marker in {"__unset__", "__all__"} else current_marker

    def fading_for_current_marker(now: float | None = None) -> list[tuple[OverlayEntry, float]]:
        if now is None:
            now = time.monotonic()
        fade_ms = max(0.0, args.slot_guard_fade_ms)
        active_marker = active_marker_key()
        faded: list[tuple[OverlayEntry, float]] = []
        for entry, fade_start in slot_guard_fading.values():
            if args.auto_marker and entry.marker != active_marker:
                continue
            if fade_ms <= 0:
                continue
            opacity = max(0.0, 1.0 - ((now - fade_start) * 1000.0 / fade_ms))
            if opacity > 0:
                faded.append((entry, opacity))
        return faded

    def redraw() -> None:
        tab_state = None
        if args.show_tab_scan or args.calibrate:
            tab_state = detect_tab_state(
                profile,
                args,
                window_left=window_state["left"],
                window_top=window_state["top"],
            )
        draw_overlay(
            canvas,
            display_entries,
            profile,
            args,
            display_status,
            tab_state,
            calibration_mode["name"],
            fading_for_current_marker(),
        )

    def tick_fades() -> None:
        now = time.monotonic()
        fade_ms = max(0.0, args.slot_guard_fade_ms)
        expired = [
            key
            for key, (_entry, fade_start) in slot_guard_fading.items()
            if fade_ms <= 0 or (now - fade_start) * 1000.0 >= fade_ms
        ]
        for key in expired:
            slot_guard_fading.pop(key, None)
        if slot_guard_fading:
            redraw()
            root.after(max(16, args.slot_guard_fade_frame_ms), tick_fades)
        else:
            fade_animation_active["value"] = False
            if expired:
                redraw()

    def ensure_fade_tick() -> None:
        if fade_animation_active["value"]:
            return
        fade_animation_active["value"] = True
        root.after(max(16, args.slot_guard_fade_frame_ms), tick_fades)

    def selected_entries(next_marker: str | None) -> list[OverlayEntry]:
        if args.auto_marker:
            if next_marker is None:
                return []
            return [entry for entry in entries if entry.marker == next_marker]
        return list(entries)

    def stale_status_text(next_marker: str | None, selected_count: int, visible_count: int) -> str | None:
        if visible_count > 0:
            return None
        if selected_count > 0 and slot_guard_stale_keys:
            name = marker_display_name(next_marker) if next_marker else "prices"
            return f"{name}: слот изменился, нужна новая оценка"
        if args.auto_marker and next_marker is not None and args.show_empty_status:
            return f"{marker_display_name(next_marker)}: 0 >= {args.min_price_exalted:g}ex"
        return None

    def update_display_entries(next_marker: str | None, *, force: bool = False) -> None:
        nonlocal current_marker, display_entries, display_status
        selection_key = next_marker if args.auto_marker else "__all__"
        if not force and selection_key == current_marker:
            return
        current_marker = selection_key
        selected = selected_entries(next_marker)
        display_entries = [entry for entry in selected if entry.key not in slot_guard_stale_keys]
        fading_count = sum(1 for entry in selected if entry.key in slot_guard_fading)
        display_status = stale_status_text(next_marker, len(selected), len(display_entries) + fading_count)
        if args.debug_tabs:
            marker_text = next_marker if next_marker is not None else "none"
            print(f"activeMarker={marker_text} entries={len(display_entries)}", file=sys.stderr)
        redraw()

    def confirm_stale_candidate(entry: OverlayEntry, reason: str, now: float) -> bool:
        if entry.key in slot_guard_stale_keys:
            return False
        first_seen = slot_guard_pending_stale.get(entry.key)
        if first_seen is None:
            slot_guard_pending_stale[entry.key] = now
            if args.debug_slot_guard:
                print(f"slotGuard=pending reason={reason} key={entry.key} text={entry.text}", file=sys.stderr)
            return False
        delay_ms = max(0.0, args.slot_guard_disappear_delay_ms)
        if (now - first_seen) * 1000.0 < delay_ms:
            return False
        slot_guard_pending_stale.pop(entry.key, None)
        slot_guard_stale_keys.add(entry.key)
        slot_guard_baselines.pop(entry.key, None)
        if args.slot_guard_fade_ms > 0:
            slot_guard_fading[entry.key] = (entry, now)
            ensure_fade_tick()
        if args.debug_slot_guard:
            print(f"slotGuard=fade reason={reason} key={entry.key} text={entry.text}", file=sys.stderr)
        return True

    def check_slot_guard() -> None:
        if not args.slot_guard or args.calibrate or not display_entries:
            return
        if args.auto_marker:
            detected_marker = detect_active_marker(
                profile,
                args,
                window_left=window_state["left"],
                window_top=window_state["top"],
            )
            expected_marker = None if current_marker in {"__unset__", "__all__"} else current_marker
            if detected_marker != expected_marker:
                update_display_entries(detected_marker, force=True)
                if args.debug_slot_guard:
                    expected_text = expected_marker if expected_marker is not None else "none"
                    detected_text = detected_marker if detected_marker is not None else "none"
                    print(
                        f"slotGuard=skip inactive expected={expected_text} detected={detected_text}",
                        file=sys.stderr,
                    )
                return
        labels_were_hidden = False
        try:
            hide_labels_ms = max(0.0, args.slot_guard_hide_labels_ms)
            if hide_labels_ms > 0:
                canvas.itemconfigure("price-label", state="hidden")
                labels_were_hidden = True
                root.update_idletasks()
                time.sleep(hide_labels_ms / 1000.0)
            capture = capture_grid_pixels(
                profile,
                args,
                window_left=window_state["left"],
                window_top=window_state["top"],
            )
        finally:
            if labels_were_hidden:
                canvas.itemconfigure("price-label", state="normal")
                root.update_idletasks()
        if capture is None:
            return
        width, height, raw_bgra = capture
        state_changed = False
        visibility_changed = False
        empty_candidates: list[OverlayEntry] = []
        stale_candidates: list[OverlayEntry] = []
        candidate_keys: set[str] = set()
        for entry in display_entries:
            if entry.key in slot_guard_stale_keys:
                continue
            current = sample_slot_fingerprint(entry, width, height, raw_bgra, profile, args)
            if current is None:
                continue
            if slot_fingerprint_looks_empty(current, args):
                empty_candidates.append(entry)
                continue
            baseline = slot_guard_baselines.get(entry.key)
            if baseline is None:
                slot_guard_baselines[entry.key] = current
                state_changed = True
                continue
            if slot_fingerprint_changed(baseline, current, args):
                stale_candidates.append(entry)
        now = time.monotonic()
        for entry in empty_candidates:
            candidate_keys.add(entry.key)
            if confirm_stale_candidate(entry, "empty", now):
                state_changed = True
                visibility_changed = True
        if stale_candidates:
            max_mass_changes = max(
                args.slot_guard_mass_change_min,
                int(max(1, len(display_entries)) * args.slot_guard_mass_change_ratio),
            )
            if len(stale_candidates) >= max_mass_changes:
                if args.debug_slot_guard:
                    print(
                        f"slotGuard=skip mass-change candidates={len(stale_candidates)} entries={len(display_entries)}",
                        file=sys.stderr,
                    )
            else:
                for entry in stale_candidates:
                    candidate_keys.add(entry.key)
                    if confirm_stale_candidate(entry, "changed", now):
                        state_changed = True
                        visibility_changed = True
        for entry in display_entries:
            if entry.key not in candidate_keys:
                slot_guard_pending_stale.pop(entry.key, None)
        if state_changed:
            save_slot_guard_state(args, slot_guard_report_signature, slot_guard_baselines, slot_guard_stale_keys)
        if visibility_changed:
            update_display_entries(active_marker_key(), force=True)

    def poll_active_tab() -> None:
        marker = detect_active_marker(
            profile,
            args,
            window_left=window_state["left"],
            window_top=window_state["top"],
        )
        update_display_entries(marker)
        root.after(args.tab_poll_ms, poll_active_tab)

    def poll_slot_guard() -> None:
        check_slot_guard()
        root.after(args.slot_guard_poll_ms, poll_slot_guard)

    def poll_window() -> None:
        rect = find_target_window()
        if rect is None:
            if args.exit_with_window:
                root.destroy()
                return
        else:
            left, top, width, height = rect
            if (
                left != window_state["left"]
                or top != window_state["top"]
                or width != window_state["width"]
                or height != window_state["height"]
            ):
                window_state.update({"left": left, "top": top, "width": width, "height": height})
                root.geometry(f"{width}x{height}+{left}+{top}")
                canvas.configure(width=width, height=height)
                redraw()
        root.after(args.window_poll_ms, poll_window)

    def poll_calibration() -> None:
        redraw()
        root.after(args.tab_poll_ms, poll_calibration)

    def save_and_print() -> None:
        save_profile(args.profile, profile)
        print(f"savedProfile={args.profile}")

    def save_and_close() -> None:
        save_and_print()
        root.destroy()

    def handle_calibration_action(action: str) -> bool:
        if action == "done":
            save_and_close()
            return True
        if action == "save":
            save_and_print()
            redraw()
            return True
        if action == "reset":
            reset_calibration(profile, args)
            calibration_mode["name"] = "grid"
            redraw()
            return True
        if action == "minus":
            profile["cellSize"] = max(10, float(profile.get("cellSize", args.cell_size)) - 1)
            redraw()
            return True
        if action == "plus":
            profile["cellSize"] = float(profile.get("cellSize", args.cell_size)) + 1
            redraw()
            return True
        if action == "close":
            root.destroy()
            return True
        return False

    def calibration_action_at(x: int, y: int) -> str | None:
        for action, rect in calibration_button_rects().items():
            if point_in_rect(x, y, rect):
                return action
        return None

    def on_key(event: Any) -> None:
        step = 10 if event.state & 0x0001 else 1
        resizing = bool(event.state & 0x0004)
        key = event.keysym
        if key == "Escape":
            root.destroy()
            return
        if key in {"Return", "KP_Enter"}:
            now = time.monotonic()
            if now - enter_state["last"] <= 1.0:
                save_and_close()
            enter_state["last"] = now
            return
        if key == "Tab":
            calibration_mode["name"] = "tab" if calibration_mode["name"] == "grid" else "grid"
            redraw()
            return
        if key.lower() == "s":
            save_and_print()
            return
        if key.lower() == "g":
            calibration_mode["name"] = "grid"
            redraw()
            return
        if key.lower() == "t":
            calibration_mode["name"] = "tab"
            redraw()
            return
        if key.lower() in {"plus", "equal", "kp_add"}:
            profile["cellSize"] = float(profile.get("cellSize", args.cell_size)) + step
        elif key.lower() in {"minus", "underscore", "kp_subtract"}:
            profile["cellSize"] = max(10, float(profile.get("cellSize", args.cell_size)) - step)
        elif calibration_mode["name"] == "tab" and key in {"Left", "Right", "Up", "Down"}:
            if resizing:
                if key == "Left":
                    profile["tabScanWidth"] = max(40, int(profile.get("tabScanWidth", args.tab_scan_width)) - step)
                elif key == "Right":
                    profile["tabScanWidth"] = int(profile.get("tabScanWidth", args.tab_scan_width)) + step
                elif key == "Up":
                    profile["tabScanHeight"] = max(20, int(profile.get("tabScanHeight", args.tab_scan_height)) - step)
                elif key == "Down":
                    profile["tabScanHeight"] = int(profile.get("tabScanHeight", args.tab_scan_height)) + step
            else:
                if key == "Left":
                    profile["tabScanLeft"] = int(profile.get("tabScanLeft", args.tab_scan_left)) - step
                elif key == "Right":
                    profile["tabScanLeft"] = int(profile.get("tabScanLeft", args.tab_scan_left)) + step
                elif key == "Up":
                    profile["tabScanTop"] = int(profile.get("tabScanTop", args.tab_scan_top)) - step
                elif key == "Down":
                    profile["tabScanTop"] = int(profile.get("tabScanTop", args.tab_scan_top)) + step
        elif key == "Left":
            profile["gridLeft"] = int(profile.get("gridLeft", args.grid_left)) - step
        elif key == "Right":
            profile["gridLeft"] = int(profile.get("gridLeft", args.grid_left)) + step
        elif key == "Up":
            profile["gridTop"] = int(profile.get("gridTop", args.grid_top)) - step
        elif key == "Down":
            profile["gridTop"] = int(profile.get("gridTop", args.grid_top)) + step
        redraw()

    def on_mouse_down(event: Any) -> None:
        if not args.calibrate:
            return
        action = calibration_action_at(event.x, event.y)
        if action is not None:
            handle_calibration_action(action)
            return
        if point_in_rect(event.x, event.y, grid_handle_rect(profile, args)):
            calibration_mode["name"] = "grid"
        elif point_in_rect(event.x, event.y, tab_scan_rect(profile, args)):
            calibration_mode["name"] = "tab"
        else:
            drag_state["active"] = False
            return
        drag_state.update({"active": True, "x": event.x, "y": event.y, "mode": calibration_mode["name"]})
        redraw()

    def on_mouse_drag(event: Any) -> None:
        if not args.calibrate or not drag_state.get("active"):
            return
        dx = int(event.x - drag_state["x"])
        dy = int(event.y - drag_state["y"])
        drag_state["x"] = event.x
        drag_state["y"] = event.y
        if drag_state.get("mode") == "tab":
            profile["tabScanLeft"] = int(profile.get("tabScanLeft", args.tab_scan_left)) + dx
            profile["tabScanTop"] = int(profile.get("tabScanTop", args.tab_scan_top)) + dy
        else:
            profile["gridLeft"] = int(profile.get("gridLeft", args.grid_left)) + dx
            profile["gridTop"] = int(profile.get("gridTop", args.grid_top)) + dy
        redraw()

    def on_mouse_up(_event: Any) -> None:
        drag_state["active"] = False

    root.bind("<Key>", on_key)
    canvas.bind("<Key>", on_key)
    root.bind("<ButtonPress-1>", on_mouse_down)
    root.bind("<B1-Motion>", on_mouse_drag)
    root.bind("<ButtonRelease-1>", on_mouse_up)
    canvas.bind("<ButtonPress-1>", on_mouse_down)
    canvas.bind("<B1-Motion>", on_mouse_drag)
    canvas.bind("<ButtonRelease-1>", on_mouse_up)
    update_display_entries(None, force=True)
    if args.calibrate:
        root.after(100, lambda: (root.lift(), root.focus_force(), canvas.focus_set()))
        try:
            root.grab_set()
        except tk.TclError:
            pass
    if args.auto_marker:
        root.after(100, poll_active_tab)
    if args.slot_guard and not args.calibrate:
        root.after(args.slot_guard_poll_ms, poll_slot_guard)
    if args.follow_window or args.exit_with_window:
        root.after(args.window_poll_ms, poll_window)
    if args.calibrate:
        root.after(args.tab_poll_ms, poll_calibration)
    if args.click_through and not args.calibrate:
        root.after(500, lambda: apply_click_through(root))
    root.after(1000, lambda: root.lift())
    print_entries(entries)
    if args.calibrate:
        print("Calibration: arrows move grid, Shift+arrows move faster, +/- changes cell size, S saves, Esc exits.")
    if args.auto_marker:
        markers = ", ".join(resolve_tab_markers(profile, args))
        print(f"Auto-marker: watching active tab color for markers [{markers}]. Hidden until one is active.")
    if args.slot_guard and not args.calibrate:
        print("Slot guard: hiding labels whose sampled slot pixels changed since this overlay run.")
    root.mainloop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transparent PoE2 stash price overlay")
    parser.add_argument("--report", action="append", help="Price-check report JSON. Can be passed multiple times.")
    parser.add_argument("--latest", action="store_true", help="Use the newest stash_eval/stash_newparty/stash_craftcheck directory.")
    parser.add_argument("--search-root", type=Path, default=DEFAULT_SEARCH_ROOT)
    parser.add_argument("--marker", action="append", help="Only show this marker/tab label, e.g. marker1 or marker2.")
    parser.add_argument("--auto-marker", action="store_true", help="Watch the visible stash-tab strip and show only the active marker tab.")
    parser.add_argument("--tab-marker", action="append", help="Marker order in the visible tab strip, e.g. marker2 then marker1.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--min-price-exalted", type=float, default=10.0)
    parser.add_argument("--include-uncertain", action="store_true")
    parser.add_argument("--divine-to-exalted", type=float, default=105.0)
    parser.add_argument("--chaos-to-exalted", type=float, default=10.0)
    parser.add_argument("--columns", type=int, default=12)
    parser.add_argument("--rows", type=int, default=12)
    parser.add_argument("--grid-left", type=int, default=DEFAULT_GRID_LEFT)
    parser.add_argument("--grid-top", type=int, default=DEFAULT_GRID_TOP)
    parser.add_argument("--cell-size", type=float, default=DEFAULT_CELL_SIZE)
    parser.add_argument("--window-left", type=int, default=0)
    parser.add_argument("--window-top", type=int, default=0)
    parser.add_argument("--window-width", type=int, default=900)
    parser.add_argument("--window-height", type=int, default=950)
    parser.add_argument("--window-title", action="append", help="Window title fragment to follow, e.g. Path of Exile.")
    parser.add_argument("--auto-window", action="store_true", default=True)
    parser.add_argument("--no-auto-window", action="store_false", dest="auto_window")
    parser.add_argument("--wait-for-window", action="store_true", help="Wait until the game/helper window exists before showing overlay.")
    parser.add_argument("--exit-with-window", action="store_true", help="Close the overlay when the target window disappears.")
    parser.add_argument("--follow-window", action="store_true", help="Keep overlay aligned if the target window moves or resizes.")
    parser.add_argument("--window-poll-ms", type=int, default=1000)
    parser.add_argument("--agent-id", help="Opaque owner id for manager/startup tools.")
    parser.add_argument("--tab-scan-left", type=int, default=DEFAULT_TAB_SCAN_LEFT)
    parser.add_argument("--tab-scan-top", type=int, default=DEFAULT_TAB_SCAN_TOP)
    parser.add_argument("--tab-scan-width", type=int, default=DEFAULT_TAB_SCAN_WIDTH)
    parser.add_argument("--tab-scan-height", type=int, default=DEFAULT_TAB_SCAN_HEIGHT)
    parser.add_argument("--tab-min-width", type=int, default=150)
    parser.add_argument("--tab-max-gap", type=int, default=1)
    parser.add_argument("--tab-active-min-ratio", type=float, default=0.72)
    parser.add_argument("--tab-poll-ms", type=int, default=500)
    parser.add_argument("--debug-tabs", action="store_true")
    parser.add_argument("--slot-guard", action="store_true", default=None, help="Hide labels when sampled slot pixels change after the overlay captures a baseline. Defaults to on with --auto-marker.")
    parser.add_argument("--no-slot-guard", action="store_false", dest="slot_guard")
    parser.add_argument("--slot-guard-state", type=Path, help="Persistent slot-guard state file. Defaults to <search-root>/poe_stash_slot_guard_state.json.")
    parser.add_argument("--slot-guard-poll-ms", type=int, default=1000)
    parser.add_argument("--slot-guard-hide-labels-ms", type=float, default=0.0)
    parser.add_argument("--slot-guard-disappear-delay-ms", type=float, default=650.0)
    parser.add_argument("--slot-guard-fade-ms", type=float, default=600.0)
    parser.add_argument("--slot-guard-fade-frame-ms", type=int, default=50)
    parser.add_argument("--slot-guard-point-threshold", type=float, default=28.0)
    parser.add_argument("--slot-guard-avg-threshold", type=float, default=18.0)
    parser.add_argument("--slot-guard-min-changed-samples", type=int, default=3)
    parser.add_argument("--slot-guard-min-samples", type=int, default=5)
    parser.add_argument("--slot-guard-empty-avg-luma", type=float, default=16.0)
    parser.add_argument("--slot-guard-empty-max-luma", type=float, default=30.0)
    parser.add_argument("--slot-guard-empty-avg-saturation", type=float, default=8.0)
    parser.add_argument("--slot-guard-empty-colored-threshold", type=float, default=12.0)
    parser.add_argument("--slot-guard-empty-max-colored-samples", type=int, default=1)
    parser.add_argument("--slot-guard-mass-change-ratio", type=float, default=0.45)
    parser.add_argument("--slot-guard-mass-change-min", type=int, default=4)
    parser.add_argument("--debug-slot-guard", action="store_true")
    parser.add_argument("--show-empty-status", action="store_true", default=True)
    parser.add_argument("--no-empty-status", action="store_false", dest="show_empty_status")
    parser.add_argument("--font-size", type=int, default=13)
    parser.add_argument("--alpha", type=float, default=0.85)
    parser.add_argument("--show-grid", action="store_true")
    parser.add_argument("--show-tab-scan", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--click-through", action="store_true", default=True)
    parser.add_argument("--no-click-through", action="store_false", dest="click_through")
    parser.add_argument("--singleton", action="store_true", default=True, help="Stop other poe_stash_overlay.py processes before starting.")
    parser.add_argument("--allow-multiple", action="store_false", dest="singleton")
    parser.add_argument("--dry-run", action="store_true", help="Print overlay labels and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.slot_guard is None:
        args.slot_guard = bool(args.auto_marker)
    if args.singleton and not args.dry_run:
        stop_other_overlay_processes()
    entries = collect_entries(args)
    if args.dry_run:
        print_entries(entries)
        return 0
    if not entries and not (args.auto_marker and args.show_empty_status):
        print("No overlay entries after filtering. Lower --min-price-exalted or pass --include-uncertain.")
        return 0
    run_overlay(entries, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
