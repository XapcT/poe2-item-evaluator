#!/usr/bin/env python3
"""Report large price changes between sale overlay price files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a JSON array of overlay price rows")
    return [row for row in data if isinstance(row, dict)]


def row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    marker = str(row.get("marker") or "")
    x = str(row.get("x") if row.get("x") is not None else "")
    y = str(row.get("y") if row.get("y") is not None else "")
    label = str(row.get("labelRu") or row.get("label") or row.get("name") or "")
    return marker, x, y, label


def price_exalted(row: dict[str, Any]) -> float | None:
    value = row.get("priceExalted")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def display_price(row: dict[str, Any], value: float | None) -> str:
    text = row.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    if value is None:
        return "unknown"
    if value >= 105 and value % 105 == 0:
        return f"{int(value / 105)}d"
    if value >= 105:
        return f"{value / 105:.1f}d"
    return f"{value:g}ex"


def newest_backup(current: Path) -> Path | None:
    candidates = sorted(
        current.parent.glob("overlay_prices.backup_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def audit(
    previous_path: Path,
    current_path: Path,
    min_factor: float,
    min_delta_exalted: float,
) -> list[dict[str, Any]]:
    previous_rows = load_rows(previous_path)
    previous = {row_key(row): row for row in previous_rows}
    previous_by_label: dict[str, dict[str, Any] | None] = {}
    for row in previous_rows:
        label = str(row.get("labelRu") or row.get("label") or row.get("name") or "")
        if not label:
            continue
        previous_by_label[label] = row if label not in previous_by_label else None

    changes: list[dict[str, Any]] = []
    for current_row in load_rows(current_path):
        key = row_key(current_row)
        old_row = previous.get(key)
        if not old_row and key[3]:
            old_row = previous_by_label.get(key[3])
        if not old_row:
            continue
        old_price = price_exalted(old_row)
        new_price = price_exalted(current_row)
        if old_price is None or new_price is None or old_price <= 0:
            continue
        delta = new_price - old_price
        factor = new_price / old_price
        reciprocal_factor = old_price / new_price if new_price > 0 else float("inf")
        if abs(delta) < min_delta_exalted and factor < min_factor and reciprocal_factor < min_factor:
            continue
        changes.append(
            {
                "marker": key[0],
                "x": int(key[1]) if key[1].isdigit() else key[1],
                "y": int(key[2]) if key[2].isdigit() else key[2],
                "label": key[3],
                "oldText": display_price(old_row, old_price),
                "newText": display_price(current_row, new_price),
                "oldPriceExalted": old_price,
                "newPriceExalted": new_price,
                "deltaExalted": delta,
                "factor": factor,
                "oldNote": old_row.get("note"),
                "newNote": current_row.get("note"),
            }
        )
    changes.sort(key=lambda row: abs(row["deltaExalted"]), reverse=True)
    return changes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True, help="Current overlay_prices.json.")
    parser.add_argument(
        "--previous",
        type=Path,
        help="Previous overlay_prices JSON. Defaults to newest overlay_prices.backup_*.json next to --current.",
    )
    parser.add_argument("--min-factor", type=float, default=2.0)
    parser.add_argument("--min-delta-exalted", type=float, default=105.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    current = args.current
    previous = args.previous or newest_backup(current)
    if previous is None:
        raise SystemExit(f"No previous overlay price file found next to {current}")

    changes = audit(previous, current, args.min_factor, args.min_delta_exalted)
    if args.json:
        print(
            json.dumps(
                {
                    "previous": str(previous),
                    "current": str(current),
                    "changedCount": len(changes),
                    "changes": changes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(f"previous={previous}")
    print(f"current={current}")
    if not changes:
        print("No large price changes.")
        return
    print("Large price changes:")
    for row in changes:
        column = int(row["x"]) + 1 if isinstance(row["x"], int) else row["x"]
        line = int(row["y"]) + 1 if isinstance(row["y"], int) else row["y"]
        print(
            f"- {row['marker']} {column}/{line} {row['label']}: "
            f"{row['oldText']} -> {row['newText']} "
            f"({row['factor']:.2f}x, {row['deltaExalted']:+.0f}ex)"
        )
        if row.get("newNote"):
            print(f"  note: {row['newNote']}")


if __name__ == "__main__":
    main()
