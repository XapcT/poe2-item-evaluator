#!/usr/bin/env python3
"""Price-check a marked public PoE2 stash tab through authenticated trade2.

The marker workflow is intentionally regular player trade, not Instant Buyout:
put items into a public tab, set a distinctive fixed price such as
``~price 1 mirror``, search the account plus marker price, fetch item JSON, then
filter locally by stash tab name. The script does not edit stash contents.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any


HELPER = Path(__file__).with_name("poe_account_api.py")
DEFAULT_SETTINGS = Path(r"D:\Soft\PathOfBuilding-PoE2\Settings.xml")
DEFAULT_LEAGUE = "Runes of Aldur"
DEFAULT_REALM = "poe2"
DEFAULT_STASH_NAME = "~price 1 mirror"
DEFAULT_MARKER_CURRENCY = "mirror"
DEFAULT_MARKER_AMOUNT = 1.0
DEFAULT_LISTED_STATUS = "online"
DEFAULT_DIVINE_TO_EXALTED = 105.0
DEFAULT_CHAOS_TO_EXALTED = 10.0


def load_helper():
    spec = importlib.util.spec_from_file_location("poe_account_api", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import helper: {HELPER}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


api = load_helper()


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def clean_text(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return match.group(1).split("|")[-1]

    return re.sub(r"\[([^\]]+)\]", repl, text).replace("–", "-").replace("—", "-")


def first_number(text: str) -> float:
    match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    return float(match.group(0).replace("+", ""))


def stat_id(raw_hash: str | None) -> str | None:
    if not raw_hash:
        return None
    return raw_hash.replace("stat.explicit.", "explicit.")


def item_label(item: dict[str, Any]) -> str:
    return f"{item.get('name') or ''} {item.get('typeLine') or item.get('baseType') or ''}".strip()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return slug or "pricecheck"


def progress(args: argparse.Namespace, message: str) -> None:
    if args.quiet:
        return
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: Any = None,
    args: argparse.Namespace,
) -> Any:
    while True:
        try:
            return api.request_json(url, method=method, headers=api.auth_headers(token), payload=payload)
        except Exception as exc:  # noqa: BLE001 - helper raises ApiError with rate-limit text
            wait = rate_limit_wait(str(exc))
            if wait is None:
                raise
            progress(args, f"trade2 rate limit: sleep {wait}s")
            time.sleep(wait)


def rate_limit_wait(message: str) -> int | None:
    match = re.search(r"wait (\d+) seconds", message, re.IGNORECASE)
    if match:
        return int(match.group(1)) + 5
    match = re.search(r"retry-after[^\d]*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1)) + 5
    return None


def marker_query(args: argparse.Namespace, sort: dict[str, str]) -> dict[str, Any]:
    price_filter: dict[str, Any] = {"option": args.marker_currency}
    if args.marker_amount is not None:
        price_filter["min"] = args.marker_amount
        price_filter["max"] = args.marker_amount
    return {
        "query": {
            "status": {"option": args.listed},
            "filters": {
                "trade_filters": {
                    "filters": {
                        "account": {"input": args.account},
                        "price": price_filter,
                    }
                }
            },
        },
        "sort": sort,
        "engine": "new",
    }


def trade_search(query: dict[str, Any], token: str, args: argparse.Namespace) -> dict[str, Any]:
    realm = urllib.parse.quote(args.realm, safe="")
    league = urllib.parse.quote(args.league, safe="")
    url = f"{api.WWW_BASE}/api/trade2/search/{realm}/{league}"
    return request_json(url, token, method="POST", payload=query, args=args)


def trade_fetch(query_id: str, ids: list[str], token: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for start in range(0, len(ids), args.fetch_batch_size):
        chunk = ids[start : start + args.fetch_batch_size]
        quoted_query = urllib.parse.quote(query_id, safe="")
        url = f"{api.WWW_BASE}/api/trade2/fetch/{','.join(chunk)}?query={quoted_query}"
        data = request_json(url, token, args=args)
        entries.extend(data.get("result") or [])
        time.sleep(args.fetch_sleep)
    return entries


def price_matches(price: dict[str, Any] | None, args: argparse.Namespace) -> bool:
    if not price:
        return False
    if price.get("currency") != args.marker_currency:
        return False
    if args.marker_amount is None:
        return True
    try:
        return abs(float(price.get("amount")) - float(args.marker_amount)) < 1e-9
    except (TypeError, ValueError):
        return False


def discover_marked_tab(token: str, args: argparse.Namespace) -> dict[str, Any]:
    sorts = [
        ("price_asc", {"price": "asc"}),
        ("indexed_asc", {"indexed": "asc"}),
        ("indexed_desc", {"indexed": "desc"}),
        ("price_desc", {"price": "desc"}),
    ]
    id_sources: dict[str, str] = {}
    searches: list[dict[str, Any]] = []
    for label, sort in sorts:
        data = trade_search(marker_query(args, sort), token, args)
        result_ids = list(data.get("result") or [])
        added = 0
        for item_id in result_ids:
            if item_id not in id_sources:
                id_sources[item_id] = data.get("id")
                added += 1
        searches.append(
            {
                "label": label,
                "queryId": data.get("id"),
                "total": data.get("total"),
                "resultCount": len(result_ids),
                "added": added,
            }
        )
        progress(args, f"search {label}: total={data.get('total')} result={len(result_ids)} added={added}")
        time.sleep(args.search_sleep)

    grouped: dict[str, list[str]] = {}
    for item_id, query_id in id_sources.items():
        if query_id:
            grouped.setdefault(query_id, []).append(item_id)

    fetched: list[dict[str, Any]] = []
    for query_id, ids in grouped.items():
        progress(args, f"fetch {len(ids)} ids for query {query_id}")
        fetched.extend(trade_fetch(query_id, ids, token, args))

    matched: list[dict[str, Any]] = []
    for entry in fetched:
        listing = entry.get("listing") or {}
        stash = listing.get("stash") or {}
        if args.stash_name and stash.get("name") != args.stash_name:
            continue
        if not price_matches(listing.get("price"), args):
            continue
        matched.append(entry)

    return {
        "league": args.league,
        "realm": args.realm,
        "account": args.account,
        "stashName": args.stash_name,
        "marker": {"amount": args.marker_amount, "currency": args.marker_currency},
        "listed": args.listed,
        "searches": searches,
        "uniqueIds": len(id_sources),
        "fetched": len(fetched),
        "matchedCount": len(matched),
        "result": matched,
    }


def price_to_exalted(price: dict[str, Any] | None, args: argparse.Namespace) -> float | None:
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
    if currency == "exalted":
        return amount_f
    if currency == "divine":
        return amount_f * args.divine_to_exalted
    if currency == "chaos":
        return amount_f / args.chaos_to_exalted
    if currency == "mirror":
        return amount_f * args.divine_to_exalted * 300.0
    return None


TEXT_RULES: list[tuple[str, float, str]] = [
    (r"\bSpell Damage\b", 4.0, "spell"),
    (r"\bCold Damage\b|\bFire Damage\b|\bLightning Damage\b|\bChaos Damage\b|\bElemental Damage\b", 3.4, "damage"),
    (r"Critical Spell Damage Bonus", 4.2, "crit"),
    (r"Critical Hit Chance for Spells", 4.0, "crit"),
    (r"\bCritical Hit Chance\b|\bCritical Damage Bonus\b", 2.5, "crit"),
    (r"\bCast Speed\b", 4.0, "speed"),
    (r"Damage Penetrates", 3.5, "penetration"),
    (r"Meta Skills gain", 4.2, "meta"),
    (r"Damage is taken from Mana before Life", 5.0, "mana"),
    (r"Minions deal", 4.0, "minion"),
    (r"Minions have .+Elemental Resistances", 3.2, "minion"),
    (r"Minions have .+Attack and Cast Speed", 3.8, "minion"),
    (r"Minions have .+Critical", 3.0, "minion"),
    (r"Minions have .+maximum Life", 2.1, "minion"),
    (r"maximum Energy Shield\b", 3.0, "es"),
    (r"Energy Shield from Equipped Focus", 2.8, "es"),
    (r"Ailment Threshold equal to .+Energy Shield", 3.5, "es"),
    (r"\bTotem Damage\b", 2.8, "totem"),
    (r"Totem Placement speed", 1.8, "totem"),
    (r"Presence Area of Effect", 1.5, "presence"),
    (r"\bFlammability\b|\bCurse\b", 1.5, "curse"),
    (r"Offering", 2.4, "minion"),
    (r"Skill Speed", 2.2, "speed"),
    (r"Damage while Shapeshifted", 1.5, "damage"),
]


def mod_weight(description: str, tier: str | None) -> tuple[float, set[str]]:
    weight = 0.0
    categories: set[str] = set()
    for pattern, rule_weight, category in TEXT_RULES:
        if re.search(pattern, description, re.IGNORECASE):
            weight = max(weight, rule_weight)
            categories.add(category)
    value = first_number(description)
    if weight and value >= 20:
        weight += 0.4
    elif weight and value >= 15:
        weight += 0.2
    if tier in {"P1", "S1"} and weight:
        weight += 0.2
    return weight, categories


def explicit_mods(item: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in item.get("explicitMods") or []:
        if not isinstance(raw, dict):
            continue
        desc = clean_text(raw.get("description") or "")
        sid = stat_id(raw.get("hash"))
        if not desc or not sid:
            continue
        mod_name = None
        tier = None
        if raw.get("mods") and isinstance(raw["mods"], list):
            first = raw["mods"][0] or {}
            mod_name = first.get("name")
            tier = first.get("tier")
        weight, categories = mod_weight(desc, tier)
        records.append(
            {
                "id": sid,
                "value": first_number(desc),
                "description": desc,
                "name": mod_name,
                "tier": tier,
                "weight": round(weight, 3),
                "categories": sorted(categories),
            }
        )
    return records


def score_item(mods: list[dict[str, Any]], rarity: str | None) -> float:
    useful = sorted((float(mod["weight"]) for mod in mods if mod.get("weight")), reverse=True)
    score = sum(useful[:4])
    categories = {category for mod in mods for category in mod.get("categories", [])}
    if "spell" in categories and "crit" in categories:
        score += 2.0
    if "spell" in categories and "meta" in categories:
        score += 2.0
    if "minion" in categories and "es" in categories:
        score += 2.0
    if "damage" in categories and "penetration" in categories:
        score += 1.5
    if rarity == "Rare" and len(useful) >= 3:
        score += 0.8
    return round(score, 3)


def item_summary(entry: dict[str, Any]) -> dict[str, Any]:
    item = entry.get("item") or {}
    listing = entry.get("listing") or {}
    stash = listing.get("stash") or {}
    mods = explicit_mods(item)
    x = stash.get("x")
    y = stash.get("y")
    return {
        "id": entry.get("id"),
        "label": item_label(item),
        "baseType": item.get("baseType") or item.get("typeLine"),
        "rarity": item.get("rarity"),
        "ilvl": item.get("ilvl"),
        "stash": stash,
        "x": x,
        "y": y,
        "column": x + 1 if isinstance(x, int) else None,
        "row": y + 1 if isinstance(y, int) else None,
        "sourcePrice": listing.get("price"),
        "mods": mods,
        "score": score_item(mods, item.get("rarity")),
    }


def combo_key(base_type: str, combo: list[dict[str, Any]], args: argparse.Namespace) -> str:
    payload = {
        "league": args.league,
        "listed": args.market_listed,
        "baseType": base_type,
        "mods": sorted((mod["id"], float(mod["value"])) for mod in combo),
        "fetchLimit": args.market_fetch_limit,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def combo_label(combo: list[dict[str, Any]]) -> str:
    return " + ".join(mod["description"] for mod in combo)


def unique_combos(combos: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    seen: set[tuple[tuple[str, float], ...]] = set()
    out: list[list[dict[str, Any]]] = []
    for combo in combos:
        if not combo:
            continue
        key = tuple(sorted((mod["id"], float(mod["value"])) for mod in combo))
        if key in seen:
            continue
        seen.add(key)
        out.append(combo)
    return out


def candidate_combos(item: dict[str, Any], args: argparse.Namespace) -> list[list[dict[str, Any]]]:
    mods = [mod for mod in item["mods"] if mod.get("weight", 0) > 0 and mod.get("value") is not None]
    mods.sort(key=lambda mod: (float(mod["weight"]), float(mod["value"])), reverse=True)
    combos: list[list[dict[str, Any]]] = []
    if len(mods) >= 4:
        combos.append(mods[:4])
    if len(mods) >= 3:
        combos.append(mods[:3])
    if len(mods) >= 2:
        combos.append(mods[:2])

    by_cat: dict[str, list[dict[str, Any]]] = {}
    for mod in mods:
        for category in mod.get("categories", []):
            by_cat.setdefault(category, []).append(mod)
    for cats in (
        ("spell", "damage", "meta"),
        ("spell", "crit"),
        ("damage", "penetration"),
        ("minion", "es"),
        ("minion", "speed"),
        ("mana", "minion"),
    ):
        selected: list[dict[str, Any]] = []
        for cat in cats:
            selected.extend(by_cat.get(cat, [])[:1])
        if len(selected) >= 2:
            combos.append(selected)

    if args.allow_single_mod_checks:
        combos.extend([[mod] for mod in mods[:2]])
    return unique_combos(combos)[: args.max_combos_per_item]


def market_query(base_type: str, combo: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "query": {
            "status": {"option": args.market_listed},
            "type": base_type,
            "filters": {
                "type_filters": {"filters": {"rarity": {"option": "nonunique"}}},
                "trade_filters": {"filters": {}},
            },
            "stats": [
                {
                    "type": "and",
                    "filters": [
                        {"id": mod["id"], "value": {"min": mod["value"]}}
                        for mod in combo
                    ],
                }
            ],
        },
        "sort": {"price": "asc"},
        "engine": "new",
    }


def load_cache(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def floor_from_entries(entries: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any] | None:
    floors: list[dict[str, Any]] = []
    for entry in entries:
        listing = entry.get("listing") or {}
        account = (listing.get("account") or {}).get("name")
        if args.account and account == args.account:
            continue
        price = listing.get("price")
        price_ex = price_to_exalted(price, args)
        if price_ex is None:
            continue
        item = entry.get("item") or {}
        floors.append(
            {
                "id": entry.get("id"),
                "label": item_label(item),
                "seller": account,
                "price": price,
                "priceExalted": round(price_ex, 3),
                "mods": [clean_text(mod.get("description") or "") for mod in item.get("explicitMods") or [] if isinstance(mod, dict)],
            }
        )
    floors.sort(key=lambda row: row["priceExalted"])
    return floors[0] if floors else None


def evaluate_combo(
    item: dict[str, Any],
    combo: list[dict[str, Any]],
    token: str | None,
    cache: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    base_type = item.get("baseType")
    if not base_type:
        return {"combo": combo, "comboLabel": combo_label(combo), "error": "missing base type"}
    key = combo_key(base_type, combo, args)
    if key in cache:
        cached = dict(cache[key])
        cached["cached"] = True
        return cached
    if args.max_market_queries <= 0:
        return {"combo": combo, "comboLabel": combo_label(combo), "skipped": "market query budget is zero"}
    if token is None:
        return {"combo": combo, "comboLabel": combo_label(combo), "skipped": "no token for market query"}

    args.max_market_queries -= 1
    try:
        data = trade_search(market_query(base_type, combo, args), token, args)
        ids = list(data.get("result") or [])[: args.market_fetch_limit]
        entries = trade_fetch(data.get("id"), ids, token, args) if ids and data.get("id") else []
        floor = floor_from_entries(entries, args)
        result = {
            "combo": combo,
            "comboLabel": combo_label(combo),
            "queryId": data.get("id"),
            "url": f"https://www.pathofexile.com/trade2/search/{args.realm}/{urllib.parse.quote(args.league, safe='')}/{data.get('id')}" if data.get("id") else None,
            "total": data.get("total") or 0,
            "floor": floor,
        }
    except Exception as exc:  # noqa: BLE001 - invalid stats should not stop the whole tab
        result = {"combo": combo, "comboLabel": combo_label(combo), "error": str(exc)}
    cache[key] = result
    return result


def evaluate_items(entries: list[dict[str, Any]], token: str | None, args: argparse.Namespace) -> dict[str, Any]:
    items = [item_summary(entry) for entry in entries]
    items.sort(key=lambda row: row["score"], reverse=True)
    shortlist = [item for item in items if item["score"] >= args.min_local_score][: args.candidate_limit]
    cache_path = args.cache or (args.out_dir / "poe_stash_pricecheck_cache.json")
    cache = load_cache(cache_path)
    checked = 0
    for index, item in enumerate(shortlist, 1):
        combos = candidate_combos(item, args)
        item["marketChecks"] = []
        progress(args, f"market {index}/{len(shortlist)} {item['label']} score={item['score']} combos={len(combos)}")
        for combo in combos:
            result = evaluate_combo(item, combo, token, cache, args)
            item["marketChecks"].append(result)
            checked += 1
            save_cache(cache_path, cache)
            time.sleep(args.market_sleep)
            if args.max_market_queries <= 0:
                break

    candidates: list[dict[str, Any]] = []
    cheap: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    for item in shortlist:
        floors = [check for check in item.get("marketChecks", []) if check.get("floor")]
        floors.sort(key=lambda check: check["floor"]["priceExalted"], reverse=True)
        item["bestMarketCheck"] = floors[0] if floors else None
        if item["bestMarketCheck"] and item["bestMarketCheck"]["floor"]["priceExalted"] > args.threshold_exalted:
            candidates.append(item)
        elif item["bestMarketCheck"]:
            cheap.append(item)
        else:
            uncertain.append(item)
    candidates.sort(key=lambda row: row["bestMarketCheck"]["floor"]["priceExalted"], reverse=True)

    return {
        "sourceItems": len(items),
        "shortlistCount": len(shortlist),
        "marketChecks": checked,
        "thresholdExalted": args.threshold_exalted,
        "divineToExaltedAssumption": args.divine_to_exalted,
        "chaosToExaltedAssumption": args.chaos_to_exalted,
        "items": items,
        "shortlist": shortlist,
        "candidates": candidates,
        "cheap": cheap,
        "uncertain": uncertain,
    }


def load_fetch_payload(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return {"result": data["items"], **{key: value for key, value in data.items() if key != "items"}}
    if isinstance(data, list):
        return {"result": data}
    raise ValueError(f"Unsupported fetch payload shape: {path}")


def write_report(report: dict[str, Any], args: argparse.Namespace) -> Path:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out or (args.out_dir / f"poe_stash_pricecheck_{safe_slug(args.stash_name or 'tab')}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def print_summary(report: dict[str, Any], out_path: Path, args: argparse.Namespace) -> None:
    candidates = report.get("candidates") or []
    print_json(
        {
            "out": str(out_path),
            "matchedItems": report.get("matchedCount"),
            "shortlistCount": report.get("shortlistCount"),
            "candidateCount": len(candidates),
            "candidates": [
                {
                    "label": item["label"],
                    "x": item["x"],
                    "y": item["y"],
                    "column": item["column"],
                    "row": item["row"],
                    "floorExalted": item["bestMarketCheck"]["floor"]["priceExalted"],
                    "price": item["bestMarketCheck"]["floor"]["price"],
                    "url": item["bestMarketCheck"].get("url"),
                    "mods": [mod["description"] for mod in item["mods"]],
                }
                for item in candidates[: args.print_limit]
            ],
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Price-check a marked public PoE2 stash tab via trade2")
    parser.add_argument("--account", help='PoE account name with discriminator, e.g. "XapcT#1700"')
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    parser.add_argument("--realm", default=DEFAULT_REALM)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--stash-name", default=DEFAULT_STASH_NAME)
    parser.add_argument("--marker-currency", default=DEFAULT_MARKER_CURRENCY)
    parser.add_argument("--marker-amount", type=float, default=DEFAULT_MARKER_AMOUNT)
    parser.add_argument("--listed", default=DEFAULT_LISTED_STATUS, choices=["online", "onlineleague", "available", "any"])
    parser.add_argument("--market-listed", default=DEFAULT_LISTED_STATUS, choices=["online", "onlineleague", "available", "any"])
    parser.add_argument("--input", type=Path, help="Existing full trade2 fetch JSON. If set, skip marker discovery.")
    parser.add_argument("--fetch-only", action="store_true", help="Only discover/fetch the marked tab and write JSON.")
    parser.add_argument("--out-dir", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--threshold-exalted", type=float, default=10.0)
    parser.add_argument("--divine-to-exalted", type=float, default=DEFAULT_DIVINE_TO_EXALTED)
    parser.add_argument("--chaos-to-exalted", type=float, default=DEFAULT_CHAOS_TO_EXALTED)
    parser.add_argument("--candidate-limit", type=int, default=15)
    parser.add_argument("--min-local-score", type=float, default=7.0)
    parser.add_argument("--max-combos-per-item", type=int, default=2)
    parser.add_argument("--max-market-queries", type=int, default=30)
    parser.add_argument("--market-fetch-limit", type=int, default=20)
    parser.add_argument("--allow-single-mod-checks", action="store_true")
    parser.add_argument("--fetch-batch-size", type=int, default=10)
    parser.add_argument("--fetch-sleep", type=float, default=0.55)
    parser.add_argument("--search-sleep", type=float, default=0.7)
    parser.add_argument("--market-sleep", type=float, default=0.8)
    parser.add_argument("--print-limit", type=int, default=20)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out_dir is None:
        args.out_dir = Path.cwd()
    if args.cache is not None and not args.cache.is_absolute():
        args.cache = args.out_dir / args.cache
    if args.out is not None and not args.out.is_absolute():
        args.out = args.out_dir / args.out
    if not args.input and not args.account:
        parser.error("--account is required unless --input is provided")

    token: str | None = None
    if not args.input or args.max_market_queries > 0:
        token, _ = api.ensure_token(args.settings)

    if args.input:
        fetch_payload = load_fetch_payload(args.input)
    else:
        fetch_payload = discover_marked_tab(token or "", args)
        fetched_out = args.out_dir / f"poe_stash_pricecheck_fetch_{safe_slug(args.stash_name or 'tab')}.json"
        args.out_dir.mkdir(parents=True, exist_ok=True)
        fetched_out.write_text(json.dumps(fetch_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        progress(args, f"saved fetch: {fetched_out}")

    entries = fetch_payload.get("result") or []
    report: dict[str, Any] = {
        **{key: value for key, value in fetch_payload.items() if key != "result"},
        "matchedCount": len(entries),
        "account": args.account or fetch_payload.get("account"),
        "stashName": args.stash_name or fetch_payload.get("stashName"),
        "marketListed": args.market_listed,
    }
    if not args.fetch_only:
        report.update(evaluate_items(entries, token, args))

    out_path = write_report(report, args)
    print_summary(report, out_path, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
