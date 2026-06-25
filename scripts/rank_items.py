from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACE = Path(r"D:\Soft\PoE2_Build")


@dataclass
class ParsedItem:
    name: str = ""
    rarity: str = ""
    type_line: str = ""
    item_class: str = ""
    raw: str = ""
    mods: list[str] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\{[^}]+\}", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def split_items(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    item_class_starts = [m.start() for m in re.finditer(r"(?m)^Item Class:\s", normalized)]
    starts = item_class_starts or [m.start() for m in re.finditer(r"(?m)^Rarity:\s", normalized)]
    if len(starts) <= 1:
        return [normalized]
    chunks = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(normalized)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def parse_item(text: str) -> ParsedItem:
    item = ParsedItem(raw=text)
    lines = [normalize_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line and line != "--------"]
    non_type_prefixes = (
        "Requirements:",
        "Sockets:",
        "Item Level:",
        "Level:",
        "Str:",
        "Dex:",
        "Int:",
        "Quality:",
        "Price:",
        "Corrupted",
        "Mirrored",
    )

    for idx, line in enumerate(lines):
        if line.startswith("Item Class:"):
            item.item_class = line.split(":", 1)[1].strip()
        elif line.startswith("Rarity:"):
            item.rarity = line.split(":", 1)[1].strip()
            if idx + 1 < len(lines):
                item.name = lines[idx + 1]
            if idx + 2 < len(lines) and not lines[idx + 2].endswith(":") and not lines[idx + 2].startswith(non_type_prefixes):
                item.type_line = lines[idx + 2]

    skip_prefixes = (
        "Item Class:",
        "Rarity:",
        "Requirements:",
        "Sockets:",
        "Item Level:",
        "Level:",
        "Str:",
        "Dex:",
        "Int:",
        "Quality:",
        "Stack Size:",
        "Note:",
        "Price:",
        "Corrupted",
        "Mirrored",
    )
    name_lines = {item.name, item.type_line}
    for line in lines:
        if not line or line in name_lines:
            continue
        if line.startswith(skip_prefixes) or line.endswith(":"):
            continue
        if re.search(r"(\d|%|\+|increased|reduced|more|less|to Level|Resistance|Spirit|Mana|Life|Energy Shield)", line, re.I):
            item.mods.append(line)
    return item


def current_defense(summary_path: Path) -> dict[str, Any]:
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    defensive = data.get("defensiveStats")
    return defensive if isinstance(defensive, dict) else {}


def missing_resist_weight(defense: dict[str, Any], key: str) -> float:
    current = defense.get(f"{key}Resistance")
    maximum = defense.get(f"{key}ResistanceMax") or 75
    if not isinstance(current, (int, float)):
        return 0.18
    gap = max(0, maximum - current)
    if key == "chaos":
        return 0.22 if current < 50 else 0.08
    if gap >= 25:
        return 0.28
    if gap >= 10:
        return 0.20
    if gap > 0:
        return 0.12
    return 0.04


def first_number(line: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", line)
    return float(match.group(0)) if match else None


def add(item: ParsedItem, value: float, reason: str) -> None:
    if value:
        item.score += value
        item.reasons.append(f"{value:+.1f} {reason}")


def score_item(item: ParsedItem, profile: str, defense: dict[str, Any]) -> ParsedItem:
    text = "\n".join(item.mods)
    item.score = 0.0
    item.reasons = []
    item.warnings = []

    for mod in item.mods:
        low = mod.lower()
        n = first_number(mod) or 0.0

        if low.startswith("minion ") or low.startswith("minions "):
            item.warnings.append(f"Ignored minion-only mod: {mod}")
            continue

        if re.search(r"\+(\d+) to level of all .*spell skills", low):
            add(item, n * 10.0, f"+{n:g} levels to spell skills")
        elif re.search(r"\+(\d+) to level of all .*skills", low):
            add(item, n * 7.0, f"+{n:g} levels to skills")

        if "cast speed" in low:
            add(item, n * 1.4, f"{n:g}% cast speed")
        if "critical hit chance" in low:
            add(item, n * 0.35, f"{n:g}% crit chance")
        if "critical damage bonus" in low or "critical damage" in low:
            add(item, n * 0.8, f"{n:g}% crit damage")
        if "spell damage" in low:
            add(item, n * 0.45, f"{n:g}% spell damage")
        if "physical damage" in low and "attack" not in low:
            add(item, n * 0.28, f"{n:g}% physical/non-attack damage")
        if "gain" in low and "damage as extra" in low:
            add(item, n * 0.75, f"{n:g}% damage as extra")
        if "chaos damage" in low and profile in {"oracle-thrashing-vines", "spell-totem-grim-pillars"}:
            add(item, n * 0.22, f"{n:g}% chaos damage")

        if "maximum life" in low:
            add(item, n * 0.11, f"+{n:g} life")
        if "maximum energy shield" in low or (("energy shield" in low) and "increased" not in low):
            add(item, n * 0.08, f"+{n:g} ES")
        if "increased energy shield" in low:
            add(item, n * 0.06, f"{n:g}% increased ES")
        if "maximum mana" in low:
            add(item, n * 0.05, f"+{n:g} mana")
        if "spirit" in low:
            add(item, n * 0.55, f"+{n:g} spirit")

        if "fire resistance" in low:
            add(item, n * missing_resist_weight(defense, "fire"), f"{n:g}% fire res")
        if "cold resistance" in low:
            add(item, n * missing_resist_weight(defense, "cold"), f"{n:g}% cold res")
        if "lightning resistance" in low:
            add(item, n * missing_resist_weight(defense, "lightning"), f"{n:g}% lightning res")
        if "chaos resistance" in low:
            add(item, n * missing_resist_weight(defense, "chaos"), f"{n:g}% chaos res")
        if "all elemental resistances" in low:
            res_weight = (
                missing_resist_weight(defense, "fire")
                + missing_resist_weight(defense, "cold")
                + missing_resist_weight(defense, "lightning")
            )
            add(item, n * res_weight, f"{n:g}% all elemental res")

        if "intelligence" in low:
            add(item, n * 0.10, f"+{n:g} int")
        if "strength" in low:
            add(item, n * 0.08, f"+{n:g} str")
        if "dexterity" in low:
            add(item, n * 0.06, f"+{n:g} dex")

        if "reduced" in low and ("cast speed" in low or "spell damage" in low):
            add(item, -abs(n) * 1.0, f"negative offensive mod: {mod}")
        if "cannot regenerate mana" in low:
            add(item, -15.0, "mana regeneration lockout")

    if "rune" in text.lower():
        item.warnings.append("Contains rune-like text; include or exclude rune value according to user's rule.")
    if not item.mods:
        item.warnings.append("No mods parsed; paste raw copied item text if possible.")
    return item


def read_input(path: Path | None) -> str:
    if path:
        return path.read_text(encoding="utf-8")
    return sys.stdin.read()


def print_text(items: list[ParsedItem]) -> None:
    for idx, item in enumerate(items, 1):
        label = " ".join(part for part in [item.name, item.type_line] if part).strip() or f"Item {idx}"
        print(f"{idx}. {label}")
        print(f"   score: {item.score:.1f} rarity={item.rarity or '?'} class={item.item_class or '?'}")
        for reason in item.reasons[:8]:
            print(f"   {reason}")
        for warning in item.warnings:
            print(f"   warning: {warning}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Rank copied PoE2 item text with transparent build heuristics.")
    parser.add_argument("--items", type=Path, help="Text file with one or more copied items. Defaults to stdin.")
    parser.add_argument("--profile", default="oracle-thrashing-vines", choices=["oracle-thrashing-vines", "spell-totem-grim-pillars", "generic-caster"])
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--current-summary", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = args.current_summary or (args.workspace / "current_character_summary.json")
    defense = current_defense(summary)
    parsed = [parse_item(chunk) for chunk in split_items(read_input(args.items))]
    ranked = sorted((score_item(item, args.profile, defense) for item in parsed), key=lambda item: item.score, reverse=True)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "rank": idx,
                        "score": round(item.score, 2),
                        "name": item.name,
                        "typeLine": item.type_line,
                        "rarity": item.rarity,
                        "itemClass": item.item_class,
                        "mods": item.mods,
                        "reasons": item.reasons,
                        "warnings": item.warnings,
                    }
                    for idx, item in enumerate(ranked, 1)
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_text(ranked)


if __name__ == "__main__":
    main()
