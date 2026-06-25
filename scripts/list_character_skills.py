#!/usr/bin/env python3
"""Print a numbered target-skill menu from a PoE2 character API JSON file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TAG_RE = re.compile(r"\[[^\]|]+\|([^\]]+)\]|\[([^\]]+)\]")


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return TAG_RE.sub(lambda match: match.group(1) or match.group(2) or "", text)


def load_character(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("character"), dict):
        return data["character"]
    if isinstance(data, dict):
        return data
    raise SystemExit(f"Unsupported character JSON shape: {path}")


def property_text(item: dict[str, Any]) -> str:
    names = [clean_text(prop.get("name")) for prop in item.get("properties") or []]
    return ", ".join(name for name in names if name)


def gem_level(item: dict[str, Any]) -> str | None:
    for prop in item.get("properties") or []:
        if clean_text(prop.get("name")) == "Level":
            values = prop.get("values") or []
            if values and values[0]:
                return str(values[0][0])
    return None


def support_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for child in item.get("socketedItems") or []:
        if child.get("support"):
            name = clean_text(child.get("typeLine") or child.get("baseType") or child.get("name"))
            if name:
                names.append(name)
    return names


def has_any_tag(item: dict[str, Any], tags: tuple[str, ...]) -> bool:
    text = property_text(item).lower()
    type_line = clean_text(item.get("typeLine") or item.get("baseType")).lower()
    return any(tag.lower() in text or tag.lower() in type_line for tag in tags)


def entry_for(
    item: dict[str, Any],
    *,
    index_key: str,
    parent: dict[str, Any] | None = None,
    delivery: str = "self-cast/direct",
    include_supports_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = clean_text(item.get("typeLine") or item.get("baseType") or item.get("name"))
    parent_name = clean_text(parent.get("typeLine") or parent.get("baseType") or parent.get("name")) if parent else None
    return {
        "index_key": index_key,
        "name": name,
        "label": f"{name} via {parent_name}" if parent_name else name,
        "delivery": delivery,
        "parent": parent_name,
        "level": gem_level(item),
        "tags": property_text(item),
        "supports": support_names(include_supports_from or item),
        "is_support": bool(item.get("support")),
        "api_id": item.get("id"),
    }


def build_entries(character: dict[str, Any], include_utility: bool = True) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    skills = character.get("skills") or []

    for skill_idx, skill in enumerate(skills):
        parent_is_totem = has_any_tag(skill, ("Totem",))
        parent_is_meta = has_any_tag(skill, ("Totem", "Meta", "Trigger", "Conditional", "Invocation"))
        if not skill.get("support"):
            if include_utility or not has_any_tag(skill, ("Aura", "Persistent", "Buff")):
                if parent_is_totem:
                    delivery = "totem/meta shell"
                elif parent_is_meta:
                    delivery = "meta/trigger shell"
                elif has_any_tag(skill, ("Aura", "Persistent", "Buff")):
                    delivery = "utility/buff"
                else:
                    delivery = "self-cast/direct"
                entries.append(entry_for(skill, index_key=f"{skill_idx}", delivery=delivery))

        for child_idx, child in enumerate(skill.get("socketedItems") or []):
            child_is_active = not child.get("support")
            child_is_trigger_like = bool(child.get("support")) and has_any_tag(child, ("Trigger", "Payoff"))
            if not (child_is_active or child_is_trigger_like):
                continue
            if not parent_is_meta and child_is_active:
                continue

            if parent_is_totem:
                delivery = "totem"
            elif has_any_tag(skill, ("Trigger", "Invocation")) or child_is_trigger_like:
                delivery = "trigger/autocast"
            else:
                delivery = "meta"

            entries.append(
                entry_for(
                    child,
                    index_key=f"{skill_idx}.{child_idx}",
                    parent=skill,
                    delivery=delivery,
                    include_supports_from=skill,
                )
            )

    return entries


def print_menu(entries: list[dict[str, Any]]) -> None:
    for number, entry in enumerate(entries, start=1):
        level = f" lvl {entry['level']}" if entry.get("level") else ""
        tags = f" [{entry['tags']}]" if entry.get("tags") else ""
        supports = f"; supports: {', '.join(entry['supports'])}" if entry.get("supports") else ""
        print(f"{number}. {entry['label']}{level} - {entry['delivery']}{tags}{supports}")


def main() -> int:
    parser = argparse.ArgumentParser(description="List selectable PoE2 target skills from a saved character JSON")
    parser.add_argument("--character", required=True, help="Path to poe_account_api.py character JSON output")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a human menu")
    parser.add_argument("--select", type=int, help="Print only the selected 1-based entry")
    parser.add_argument("--hide-utility", action="store_true", help="Hide aura/persistent/buff-only main skills")
    args = parser.parse_args()

    character = load_character(Path(args.character))
    entries = build_entries(character, include_utility=not args.hide_utility)

    if args.select is not None:
        if args.select < 1 or args.select > len(entries):
            raise SystemExit(f"Selection out of range: {args.select}; valid range is 1..{len(entries)}")
        selected = entries[args.select - 1]
        if args.json:
            print(json.dumps(selected, ensure_ascii=False, indent=2))
        else:
            print_menu([selected])
        return 0

    if args.json:
        print(json.dumps({"character": character.get("name"), "entries": entries}, ensure_ascii=False, indent=2))
    else:
        print_menu(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
