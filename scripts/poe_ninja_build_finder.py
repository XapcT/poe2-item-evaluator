#!/usr/bin/env python3
"""Find and summarize poe.ninja PoE2 builds for respec planning.

The poe.ninja builds search endpoint returns application/x-protobuf, while
character details are JSON. This script decodes the search payload, fetches a
small set of matching character details, and emits a report that can be used as
input for exact PoB2 validation.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from pathlib import Path
from typing import Any


BASE_URL = "https://poe.ninja"
DEFAULT_LEAGUE = "runesofaldur"
DEFAULT_COLUMNS = [
    "character",
    "level",
    "life",
    "energyshield",
    "mana",
    "ehp",
    "keystoneskill",
]


class ProtoError(RuntimeError):
    pass


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        if pos >= len(data):
            raise ProtoError("Unexpected end of varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
        if shift > 70:
            raise ProtoError("Varint is too long")


def int32(value: int) -> int:
    return value - 0x100000000 if value >= 0x80000000 else value


def iter_fields(data: bytes):
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field_no = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            value, pos = read_varint(data, pos)
            yield field_no, wire_type, value
        elif wire_type == 1:
            if pos + 8 > len(data):
                raise ProtoError("Unexpected end of fixed64")
            yield field_no, wire_type, data[pos : pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = read_varint(data, pos)
            end = pos + length
            if end > len(data):
                raise ProtoError("Unexpected end of length-delimited field")
            yield field_no, wire_type, data[pos:end]
            pos = end
        elif wire_type == 5:
            if pos + 4 > len(data):
                raise ProtoError("Unexpected end of fixed32")
            yield field_no, wire_type, data[pos : pos + 4]
            pos += 4
        else:
            raise ProtoError(f"Unsupported wire type {wire_type}")


def decode_packed_ints(payload: bytes) -> list[int]:
    values: list[int] = []
    pos = 0
    while pos < len(payload):
        value, pos = read_varint(payload, pos)
        values.append(int32(value))
    return values


Schema = dict[int, tuple[str, str, bool, Any]]


def decode_message(data: bytes, schema: Schema) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field_no, wire_type, raw in iter_fields(data):
        spec = schema.get(field_no)
        if not spec:
            continue
        name, kind, repeated, child = spec
        if kind == "int":
            if wire_type == 0:
                value: Any = int32(int(raw))
            elif wire_type == 2 and repeated:
                value = decode_packed_ints(raw)
            else:
                continue
        elif kind == "bool":
            if wire_type != 0:
                continue
            value = bool(raw)
        elif kind == "str":
            if wire_type != 2:
                continue
            value = raw.decode("utf-8", errors="replace")
        elif kind == "double":
            if wire_type == 1:
                value = struct.unpack("<d", raw)[0]
            elif wire_type == 5:
                value = float(struct.unpack("<f", raw)[0])
            else:
                continue
        elif kind == "message":
            if wire_type != 2:
                continue
            value = decode_message(raw, child)
        elif kind == "map_str_str":
            if wire_type != 2:
                continue
            entry = decode_message(raw, MAP_STR_STR_SCHEMA)
            if "key" in entry:
                out.setdefault(name, {})[entry["key"]] = entry.get("value", "")
            continue
        else:
            continue

        if repeated:
            if isinstance(value, list) and kind == "int":
                out.setdefault(name, []).extend(value)
            else:
                out.setdefault(name, []).append(value)
        else:
            out[name] = value
    return out


MAP_STR_STR_SCHEMA: Schema = {
    1: ("key", "str", False, None),
    2: ("value", "str", False, None),
}
DIMENSION_COUNT_SCHEMA: Schema = {
    1: ("key", "int", False, None),
    2: ("count", "int", False, None),
}
DIMENSION_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("dictionaryId", "str", False, None),
    3: ("counts", "message", True, DIMENSION_COUNT_SCHEMA),
}
INTEGER_DIMENSION_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("minValue", "int", False, None),
    3: ("maxValue", "int", False, None),
}
FLOAT_DIMENSION_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("minValue", "double", False, None),
    3: ("maxValue", "double", False, None),
}
PERFORMANCE_SCHEMA: Schema = {
    1: ("name", "str", False, None),
    2: ("ms", "double", False, None),
}
VALUE_SCHEMA: Schema = {
    1: ("str", "str", False, None),
    2: ("number", "int", False, None),
    3: ("numbers", "int", True, None),
    4: ("strs", "str", True, None),
    5: ("boolean", "bool", False, None),
}
VALUE_LIST_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("values", "message", True, VALUE_SCHEMA),
}
DICTIONARY_REF_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("hash", "str", False, None),
}
FIELD_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("type", "str", False, None),
    3: ("name", "str", False, None),
    4: ("valueListIds", "str", True, None),
    5: ("sortId", "str", False, None),
    6: ("integerDimensionId", "str", False, None),
    7: ("properties", "map_str_str", False, None),
    8: ("mainFieldId", "str", False, None),
    9: ("description", "str", False, None),
    10: ("group", "str", False, None),
    11: ("pinned", "bool", False, None),
}
FIELD_DESCRIPTOR_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("name", "str", False, None),
    3: ("optional", "bool", False, None),
    4: ("description", "str", False, None),
    5: ("group", "str", False, None),
    6: ("pinned", "bool", False, None),
}
SECTION_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("type", "str", False, None),
    3: ("name", "str", False, None),
    4: ("dimensionId", "str", False, None),
    5: ("properties", "map_str_str", False, None),
}
DICTIONARY_PROPERTY_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("values", "str", True, None),
}
DICTIONARY_SCHEMA: Schema = {
    1: ("id", "str", False, None),
    2: ("values", "str", True, None),
    3: ("properties", "message", True, DICTIONARY_PROPERTY_SCHEMA),
}
SEARCH_RESULT_SCHEMA: Schema = {
    1: ("total", "int", False, None),
    2: ("dimensions", "message", True, DIMENSION_SCHEMA),
    3: ("integerDimensions", "message", True, INTEGER_DIMENSION_SCHEMA),
    4: ("performancePoints", "message", True, PERFORMANCE_SCHEMA),
    5: ("valueLists", "message", True, VALUE_LIST_SCHEMA),
    6: ("dictionaries", "message", True, DICTIONARY_REF_SCHEMA),
    7: ("fields", "message", True, FIELD_SCHEMA),
    8: ("sections", "message", True, SECTION_SCHEMA),
    9: ("fieldDescriptors", "message", True, FIELD_DESCRIPTOR_SCHEMA),
    10: ("defaultFieldIds", "str", True, None),
    11: ("floatDimensions", "message", True, FLOAT_DIMENSION_SCHEMA),
}
WRAPPER_SCHEMA: Schema = {
    1: ("result", "message", False, SEARCH_RESULT_SCHEMA),
}


def http_bytes(url: str, *, tries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json, application/x-protobuf, */*",
                    "User-Agent": "poe2-item-evaluator build finder",
                },
            )
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < tries:
                time.sleep(1 + attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def http_json(url: str) -> Any:
    body = http_bytes(url)
    return json.loads(body.decode("utf-8-sig"))


def find_snapshot(league_slug: str) -> dict[str, Any]:
    index = http_json(f"{BASE_URL}/poe2/api/data/index-state")
    snapshots = index.get("snapshotVersions") or []
    for snapshot in snapshots:
        if snapshot.get("url") == league_slug:
            return snapshot
    known = ", ".join(x.get("url", "?") for x in snapshots[:20])
    raise RuntimeError(f"League snapshot not found for {league_slug!r}. Known: {known}")


def search_builds(snapshot: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    version = snapshot["version"]
    params = urllib.parse.urlencode(query)
    url = f"{BASE_URL}/poe2/api/builds/{version}/search?{params}"
    payload = http_bytes(url)
    wrapper = decode_message(payload, WRAPPER_SCHEMA)
    result = wrapper.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("poe.ninja search result missing decoded payload")
    result["_url"] = url
    return result


def fetch_dictionaries(search_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ref in search_result.get("dictionaries") or []:
        digest = ref.get("hash")
        if not digest:
            continue
        data = http_bytes(f"{BASE_URL}/poe2/api/builds/dictionary/{digest}")
        decoded = decode_message(data, DICTIONARY_SCHEMA)
        decoded["hash"] = digest
        if decoded.get("id"):
            out[decoded["id"]] = decoded
    return out


def value_lists(search_result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {item.get("id", ""): item.get("values", []) for item in search_result.get("valueLists") or []}


def dict_value(dictionaries: dict[str, dict[str, Any]], dictionary_id: str, index: int | None) -> str | None:
    if index is None:
        return None
    values = (dictionaries.get(dictionary_id) or {}).get("values") or []
    if 0 <= index < len(values):
        return values[index]
    return None


def as_number(value: dict[str, Any] | None) -> int | float | None:
    if not value:
        return None
    return value.get("number")


def as_str(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    if "str" in value:
        return value["str"]
    if "number" in value:
        return str(value["number"])
    return None


def decode_name_list(
    value: dict[str, Any] | None,
    dictionaries: dict[str, dict[str, Any]],
    dictionary_id: str,
) -> list[str]:
    if not value:
        return []
    names: list[str] = []
    for index in value.get("numbers") or []:
        name = dict_value(dictionaries, dictionary_id, index)
        if name:
            names.append(name)
    return names


def normalize_search_rows(
    search_result: dict[str, Any],
    dictionaries: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    lists = value_lists(search_result)
    row_count = max((len(values) for values in lists.values()), default=0)
    rows: list[dict[str, Any]] = []
    for index in range(min(row_count, limit)):
        row: dict[str, Any] = {"row": index}

        def get(list_id: str) -> dict[str, Any] | None:
            values = lists.get(list_id) or []
            return values[index] if index < len(values) else None

        row["name"] = as_str(get("name"))
        row["account"] = as_str(get("account"))
        row["level"] = as_number(get("level"))
        class_id = as_number(get("class"))
        row["class"] = dict_value(dictionaries, "class", int(class_id) if class_id is not None else None)
        for stat in ("life", "energyshield", "mana", "ward", "spirit"):
            row[stat] = as_number(get(stat))
        row["ehp"] = as_str(get("ehp"))
        row["skills"] = decode_name_list(get("skills"), dictionaries, "gem")
        row["allskills"] = decode_name_list(get("allskills"), dictionaries, "gem")
        row["spiritgems"] = decode_name_list(get("spiritgems"), dictionaries, "gem")
        row["keypassives"] = decode_name_list(get("keypassives"), dictionaries, "keypassive")
        row["items"] = decode_name_list(get("items"), dictionaries, "item")
        row["uniqueitems"] = decode_name_list(get("uniqueitems"), dictionaries, "item")
        for list_id in sorted(k for k in lists if k.startswith("linkedgems-") or k.startswith("dps-")):
            value = get(list_id)
            if list_id.startswith("linkedgems-"):
                row[list_id] = decode_name_list(value, dictionaries, "gem")
            elif value:
                row[list_id] = {
                    "skill": dict_value(dictionaries, "gem", value.get("number")),
                    "value": value.get("str"),
                    "parts": value.get("numbers") or [],
                }
        rows.append(row)
    return rows


def parse_compact_number(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([kmb])?", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    elif suffix == "b":
        number *= 1_000_000_000
    return number


def character_url(snapshot: dict[str, Any], account: str, name: str, time_machine: str = "") -> str:
    query = urllib.parse.urlencode(
        {
            "account": account,
            "name": name,
            "overview": snapshot["snapshotName"],
            "timeMachine": time_machine,
        }
    )
    return f"{BASE_URL}/poe2/api/builds/{snapshot['version']}/character?{query}"


def fetch_character(snapshot: dict[str, Any], account: str, name: str, time_machine: str = "") -> dict[str, Any]:
    url = character_url(snapshot, account, name, time_machine)
    data = http_json(url)
    data["_url"] = url
    return data


def flatten_names(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name:
            out.append(name)
        for nested in value.values():
            out.extend(flatten_names(nested))
    elif isinstance(value, list):
        for item in value:
            out.extend(flatten_names(item))
    return out


def item_data(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("itemData") if isinstance(item.get("itemData"), dict) else item


def item_label(data: dict[str, Any]) -> str:
    name = data.get("name") or ""
    type_line = data.get("typeLine") or data.get("baseType") or ""
    if name and type_line and name != type_line:
        return f"{name} {type_line}"
    return name or type_line or "<unknown item>"


def item_unique_label(data: dict[str, Any]) -> str | None:
    rarity = str(data.get("frameTypeId") or data.get("rarity") or "")
    frame = data.get("frameType")
    if "unique" in rarity.lower() or frame == 3:
        return data.get("name") or data.get("typeLine") or item_label(data)
    return None


def summarize_character(character: dict[str, Any]) -> dict[str, Any]:
    stats = character.get("defensiveStats") if isinstance(character.get("defensiveStats"), dict) else {}
    raw_items = character.get("items") or character.get("equipment") or []
    items = [item_data(x) for x in raw_items if isinstance(x, dict)]
    unique_items = sorted({label for item in items if (label := item_unique_label(item))})
    all_item_labels = [item_label(item) for item in items]
    skill_names = sorted(set(flatten_names(character.get("skills") or character.get("gems") or [])))
    keystones = [x.get("name") for x in character.get("keystones") or [] if isinstance(x, dict) and x.get("name")]
    passives = character.get("passiveSelection") or []
    return {
        "account": character.get("account"),
        "name": character.get("name") or (character.get("character") or {}).get("name"),
        "class": character.get("class"),
        "level": character.get("level"),
        "league": character.get("league"),
        "defensiveStats": {
            "life": stats.get("life"),
            "energyShield": stats.get("energyShield"),
            "mana": stats.get("mana"),
            "spirit": stats.get("spirit"),
            "effectiveHealthPool": stats.get("effectiveHealthPool"),
            "fireResistance": stats.get("fireResistance"),
            "coldResistance": stats.get("coldResistance"),
            "lightningResistance": stats.get("lightningResistance"),
            "chaosResistance": stats.get("chaosResistance"),
            "lowestMaximumHitTaken": stats.get("lowestMaximumHitTaken"),
        },
        "skills": skill_names,
        "keystones": keystones,
        "uniqueItems": unique_items,
        "items": all_item_labels,
        "passiveSelection": passives,
        "passiveCounts": character.get("passiveCounts"),
        "pathOfBuildingExport": character.get("pathOfBuildingExport"),
        "url": character.get("_url"),
    }


def normalize_character_json(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("character"), dict):
        return data["character"]
    return data


def load_current(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return summarize_character(normalize_character_json(data))


def contains_name(names: list[str], needle: str | None) -> bool:
    if not needle:
        return True
    folded = needle.casefold()
    return any(folded in name.casefold() for name in names)


def score_reference(
    summary: dict[str, Any],
    row: dict[str, Any],
    *,
    target_skill: str | None,
    delivery_skill: str | None,
    current: dict[str, Any] | None,
    required_keystones: list[str],
) -> float:
    score = 0.0
    stats = summary.get("defensiveStats") or {}
    mana = stats.get("mana") or row.get("mana") or 0
    score += min(float(mana) / 1000.0, 12.0)
    if contains_name(summary.get("skills") or [], target_skill):
        score += 10
    if contains_name(summary.get("skills") or [], delivery_skill):
        score += 4
    for keystone in required_keystones:
        if contains_name(summary.get("keystones") or [], keystone):
            score += 3
    if current:
        current_uniques = set(current.get("uniqueItems") or [])
        score += 1.5 * len(current_uniques.intersection(summary.get("uniqueItems") or []))
    for key, value in row.items():
        if key.startswith("dps-") and isinstance(value, dict):
            dps_value = parse_compact_number(value.get("value"))
            if dps_value:
                score += min(math.log10(max(dps_value, 1)), 8)
    return score


def compare_patterns(
    references: list[dict[str, Any]],
    current: dict[str, Any] | None,
    *,
    min_frequency: float,
) -> dict[str, Any]:
    threshold = max(1, math.ceil(len(references) * min_frequency))
    current_uniques = set((current or {}).get("uniqueItems") or [])
    current_keystones = set((current or {}).get("keystones") or [])
    current_passives = set((current or {}).get("passiveSelection") or [])

    unique_counter: Counter[str] = Counter()
    keystone_counter: Counter[str] = Counter()
    passive_counter: Counter[int] = Counter()
    for ref in references:
        unique_counter.update(ref.get("uniqueItems") or [])
        keystone_counter.update(ref.get("keystones") or [])
        passive_counter.update(ref.get("passiveSelection") or [])

    common_uniques = [
        {"name": name, "count": count, "missingFromCurrent": name not in current_uniques}
        for name, count in unique_counter.most_common()
        if count >= threshold
    ]
    common_keystones = [
        {"name": name, "count": count, "missingFromCurrent": name not in current_keystones}
        for name, count in keystone_counter.most_common()
        if count >= threshold
    ]
    common_passives = [
        {"id": node_id, "count": count, "missingFromCurrent": node_id not in current_passives}
        for node_id, count in passive_counter.most_common()
        if count >= threshold
    ]
    return {
        "threshold": threshold,
        "commonUniqueItems": common_uniques,
        "commonKeystones": common_keystones,
        "commonPassiveIds": common_passives[:200],
        "missingCommonPassiveIds": [x for x in common_passives if x["missingFromCurrent"]][:80],
    }


def safe_stem(text: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._")
    return stem or "poe_ninja_build"


def decode_pob_export(encoded: str) -> bytes:
    padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
    return zlib.decompress(base64.urlsafe_b64decode(padded))


def save_reference_artifacts(
    details: list[dict[str, Any]],
    save_dir: Path,
    *,
    decode_xml: bool,
) -> list[dict[str, str]]:
    save_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, str]] = []
    for character in details:
        name = str(character.get("name") or "unknown")
        account = str(character.get("account") or "unknown")
        stem = safe_stem(f"{name}_{account}")
        json_path = save_dir / f"{stem}.json"
        json_path.write_text(json.dumps(character, ensure_ascii=False, indent=2), encoding="utf-8")
        entry = {"json": str(json_path)}
        export = character.get("pathOfBuildingExport")
        if isinstance(export, str) and export:
            pob_path = save_dir / f"{stem}.pob.txt"
            pob_path.write_text(export, encoding="utf-8")
            entry["pob"] = str(pob_path)
            if decode_xml:
                try:
                    xml_path = save_dir / f"{stem}.xml"
                    xml_path.write_bytes(decode_pob_export(export))
                    entry["xml"] = str(xml_path)
                except Exception as exc:
                    entry["xmlError"] = str(exc)
        written.append(entry)
    return written


def build_query(args: argparse.Namespace, snapshot: dict[str, Any]) -> dict[str, str]:
    columns = list(DEFAULT_COLUMNS)
    target_skill = base_target_skill(args.skill)
    if target_skill:
        columns.append(f"dps-{target_skill}")
    if args.columns:
        for item in args.columns.split(","):
            item = item.strip()
            if item and item not in columns:
                columns.append(item)
    query: dict[str, str] = {
        "overview": snapshot["snapshotName"],
        "columns": ",".join(columns),
    }
    if args.class_name:
        query["class"] = args.class_name
    if args.min_level is not None:
        query["min-level"] = str(args.min_level)
    if args.min_mana is not None:
        query["min-mana"] = str(args.min_mana)
    if args.max_energyshield is not None:
        query["max-energyshield"] = str(args.max_energyshield)
    if args.sort:
        query["sort"] = args.sort
    if args.sort_asc:
        query["sort-asc"] = "true"
    if target_skill and args.search_skill_filter:
        if args.delivery:
            query["skills"] = args.delivery
            query[f"linkedgems-{args.delivery}"] = target_skill
        else:
            query["skills"] = target_skill
    for raw_filter in args.filter:
        if "=" not in raw_filter:
            raise SystemExit(f"--filter must be key=value, got {raw_filter!r}")
        key, value = raw_filter.split("=", 1)
        query[key] = value
    return query


def base_target_skill(skill: str | None) -> str | None:
    if not skill:
        return None
    parts = re.split(r"\s+via\s+|\s+through\s+|\s+через\s+", skill, flags=re.IGNORECASE)
    return parts[0].strip() if parts else skill.strip()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Find poe.ninja PoE2 reference builds for build planning.")
    parser.add_argument("--league", default=DEFAULT_LEAGUE, help="poe.ninja league slug, e.g. runesofaldur.")
    parser.add_argument("--class-name", help="Class/ascendancy filter, e.g. Oracle.")
    parser.add_argument("--skill", help="Target skill, e.g. 'Grim Pillars' or 'Grim Pillars via Spell Totem'.")
    parser.add_argument("--delivery", help="Delivery/meta skill, e.g. Spell Totem.")
    parser.add_argument("--min-level", type=int, default=90)
    parser.add_argument("--min-mana", type=int)
    parser.add_argument("--max-energyshield", "--max-es", type=int)
    parser.add_argument("--sort", default="mana", help="poe.ninja sort id, e.g. mana, level, dps-Grim Pillars.")
    parser.add_argument("--sort-asc", action="store_true")
    parser.add_argument("--columns", help="Additional comma-separated poe.ninja columns.")
    parser.add_argument("--filter", action="append", default=[], help="Raw poe.ninja search filter key=value.")
    parser.add_argument("--no-search-skill-filter", dest="search_skill_filter", action="store_false")
    parser.add_argument("--limit", type=int, default=100, help="Search rows to normalize before fetching details.")
    parser.add_argument("--details", type=int, default=12, help="Character details to fetch.")
    parser.add_argument("--current-character", type=Path, help="Current character JSON for comparison.")
    parser.add_argument("--require-keystone", action="append", default=[])
    parser.add_argument("--min-frequency", type=float, default=0.5)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--decode-pob-xml", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    args = parser.parse_args()

    current = load_current(args.current_character)
    target_skill = base_target_skill(args.skill)
    if not args.class_name and current and current.get("class"):
        args.class_name = current["class"]

    snapshot = find_snapshot(args.league)
    query = build_query(args, snapshot)
    search_result = search_builds(snapshot, query)
    dictionaries = fetch_dictionaries(search_result)
    rows = normalize_search_rows(search_result, dictionaries, limit=args.limit)

    selected_rows: list[dict[str, Any]] = []
    for row in rows:
        names = list(row.get("skills") or []) + list(row.get("allskills") or [])
        if args.delivery:
            names.extend(row.get(f"linkedgems-{args.delivery}") or [])
        if target_skill and not contains_name(names, target_skill):
            # Search can miss linked gems depending on selected columns; keep rows
            # when server-side filters were already applied.
            if not args.search_skill_filter:
                continue
        selected_rows.append(row)
        if len(selected_rows) >= args.details:
            break

    details: list[dict[str, Any]] = []
    detail_errors: list[dict[str, str]] = []
    for row in selected_rows:
        if not row.get("account") or not row.get("name"):
            continue
        try:
            details.append(fetch_character(snapshot, row["account"], row["name"]))
        except Exception as exc:
            detail_errors.append({"name": str(row.get("name")), "account": str(row.get("account")), "error": str(exc)})

    summaries = [summarize_character(character) for character in details]
    for summary, row in zip(summaries, selected_rows):
        summary["searchRow"] = row
        summary["score"] = score_reference(
            summary,
            row,
            target_skill=target_skill,
            delivery_skill=args.delivery,
            current=current,
            required_keystones=args.require_keystone,
        )
    summaries.sort(key=lambda item: item.get("score", 0), reverse=True)
    patterns = compare_patterns(summaries, current, min_frequency=args.min_frequency) if summaries else {}

    artifacts = []
    if args.save_dir:
        artifacts = save_reference_artifacts(details, args.save_dir, decode_xml=args.decode_pob_xml)

    report = {
        "source": {
            "indexState": f"{BASE_URL}/poe2/api/data/index-state",
            "search": search_result.get("_url"),
            "league": args.league,
            "snapshot": snapshot,
            "query": query,
        },
        "requirements": {
            "class": args.class_name,
            "targetSkill": target_skill,
            "delivery": args.delivery,
            "minLevel": args.min_level,
            "minMana": args.min_mana,
            "maxEnergyShield": args.max_energyshield,
            "requiredKeystones": args.require_keystone,
        },
        "current": current,
        "searchTotal": search_result.get("total"),
        "rows": rows[: args.details],
        "references": summaries,
        "patterns": patterns,
        "artifacts": artifacts,
        "errors": detail_errors,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"Snapshot: {snapshot['url']} {snapshot['version']} ({snapshot['snapshotName']})")
    print(f"Search total: {search_result.get('total')} | fetched details: {len(summaries)}")
    print(f"Search URL: {search_result.get('_url')}")
    if args.out:
        print(f"Report: {args.out}")
    if artifacts:
        print(f"Artifacts: {args.save_dir}")
    print("")
    for index, ref in enumerate(summaries[: min(8, len(summaries))], 1):
        stats = ref.get("defensiveStats") or {}
        row = ref.get("searchRow") or {}
        dps_bits = []
        for key, value in row.items():
            if key.startswith("dps-") and isinstance(value, dict) and value.get("value"):
                dps_bits.append(f"{key}={value['value']}")
        dps_text = f" | {'; '.join(dps_bits)}" if dps_bits else ""
        print(
            f"{index}. {ref.get('name')} ({ref.get('account')}) "
            f"lvl {ref.get('level')} {ref.get('class')} | mana={stats.get('mana')} "
            f"ES={stats.get('energyShield')} EHP={stats.get('effectiveHealthPool')} "
            f"score={ref.get('score'):.1f}{dps_text}"
        )
        if ref.get("keystones"):
            print("   keystones: " + ", ".join(ref["keystones"][:8]))
        if ref.get("uniqueItems"):
            print("   uniques: " + ", ".join(ref["uniqueItems"][:8]))
    if patterns:
        print("")
        missing_uniques = [x for x in patterns.get("commonUniqueItems", []) if x.get("missingFromCurrent")]
        missing_keystones = [x for x in patterns.get("commonKeystones", []) if x.get("missingFromCurrent")]
        if missing_uniques:
            print("Common uniques missing from current: " + ", ".join(f"{x['name']} ({x['count']})" for x in missing_uniques[:8]))
        if missing_keystones:
            print("Common keystones missing from current: " + ", ".join(f"{x['name']} ({x['count']})" for x in missing_keystones[:8]))
        missing_nodes = patterns.get("missingCommonPassiveIds") or []
        if missing_nodes:
            print("Common passive IDs missing from current: " + ", ".join(str(x["id"]) for x in missing_nodes[:30]))


if __name__ == "__main__":
    main()
