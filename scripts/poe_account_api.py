#!/usr/bin/env python3
"""Use Path of Building 2 OAuth credentials for PoE2 account/trade API calls.

This helper intentionally never prints access or refresh tokens. When a refresh
token is used, the rotated credentials are written back to PoB's Settings.xml
after creating a timestamped backup next to the original file.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


DEFAULT_POB_PATH = Path(r"D:\Soft\PathOfBuilding-PoE2")
WWW_BASE = "https://www.pathofexile.com"
API_BASE = "https://api.pathofexile.com"
USER_AGENT = "PathOfBuilding-PoE2 OAuth helper"
SCOPES = ["account:profile", "account:leagues", "account:characters", "account:trade"]


class ApiError(RuntimeError):
    pass


def utc_now() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


def iso_from_epoch(value: int | None) -> str | None:
    if not value:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def settings_path_from_args(args: argparse.Namespace) -> Path:
    if args.settings:
        return Path(args.settings)
    pob_path = Path(args.pob_path or os.environ.get("POB2_PATH") or DEFAULT_POB_PATH)
    return pob_path / "Settings.xml"


def load_accounts(settings_path: Path) -> dict[str, Any]:
    if not settings_path.exists():
        raise ApiError(f"Settings.xml not found: {settings_path}")
    root = ET.parse(settings_path).getroot()
    accounts = root.find(".//Accounts")
    if accounts is None:
        raise ApiError(f"Accounts node not found in {settings_path}")
    expiry_text = accounts.attrib.get("tokenExpiry") or "0"
    try:
        token_expiry = int(float(expiry_text))
    except ValueError:
        token_expiry = 0
    return {
        "lastToken": accounts.attrib.get("lastToken") or "",
        "lastRefreshToken": accounts.attrib.get("lastRefreshToken") or "",
        "tokenExpiry": token_expiry,
        "lastAccountName": accounts.attrib.get("lastAccountName") or "",
        "lastRealm": accounts.attrib.get("lastRealm") or "",
        "accountCount": len(list(accounts.findall("Account"))),
    }


def xml_attr_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def replace_or_add_attr(tag_text: str, attr: str, value: str) -> str:
    escaped = xml_attr_escape(value)
    pattern = re.compile(rf'(\s{re.escape(attr)}=")[^"]*(")')
    if pattern.search(tag_text):
        return pattern.sub(rf"\g<1>{escaped}\2", tag_text, count=1)
    if tag_text.endswith("/>"):
        return tag_text[:-2] + f' {attr}="{escaped}"/>'
    return tag_text[:-1] + f' {attr}="{escaped}">'


def update_settings_tokens(settings_path: Path, token_data: dict[str, Any]) -> Path:
    text = settings_path.read_text(encoding="utf-8")
    match = re.search(r"<Accounts\b[^>]*>", text)
    if not match:
        raise ApiError(f"Accounts start tag not found in {settings_path}")

    expires_at = str(utc_now() + int(token_data.get("expires_in") or 0))
    tag = match.group(0)
    tag = replace_or_add_attr(tag, "lastToken", token_data["access_token"])
    tag = replace_or_add_attr(tag, "lastRefreshToken", token_data["refresh_token"])
    tag = replace_or_add_attr(tag, "tokenExpiry", expires_at)
    new_text = text[: match.start()] + tag + text[match.end() :]

    backup = settings_path.with_name(
        f"{settings_path.name}.codex-oauth-backup-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(settings_path, backup)
    fd, tmp_name = tempfile.mkstemp(prefix=settings_path.name + ".", suffix=".tmp", dir=str(settings_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp:
            tmp.write(new_text)
        os.replace(tmp_name, settings_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    return backup


def request_text(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None) -> str:
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8-sig", errors="replace")
        if raw.lstrip().startswith("<"):
            raise ApiError(f"HTTP {exc.code}: HTML response from {urllib.parse.urlparse(url).netloc}") from exc
        raise ApiError(f"HTTP {exc.code}: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Network error: {exc.reason}") from exc


def request_json(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, payload: Any = None) -> Any:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
        req_headers.setdefault("Accept", "application/json")
    text = request_text(url, method=method, headers=req_headers, body=body)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if text.lstrip().startswith("<"):
            raise ApiError(f"Non-JSON HTML response from {urllib.parse.urlparse(url).netloc}") from exc
        raise ApiError(f"Non-JSON response: {text[:500]}") from exc


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def refresh_token(settings_path: Path, refresh_token_value: str) -> dict[str, Any]:
    if not refresh_token_value:
        raise ApiError("No refresh token in PoB Settings.xml. Authorize in PoB first.")
    form = urllib.parse.urlencode(
        {
            "client_id": "pob",
            "grant_type": "refresh_token",
            "refresh_token": refresh_token_value,
        }
    ).encode("utf-8")
    text = request_text(
        f"{WWW_BASE}/oauth/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body=form,
    )
    data = json.loads(text)
    if "access_token" not in data or "refresh_token" not in data:
        raise ApiError("Refresh response did not contain OAuth tokens")
    backup = update_settings_tokens(settings_path, data)
    data["_settings_backup"] = str(backup)
    return data


class OAuthResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.error_description: str | None = None


def bind_oauth_server(result: OAuthResult) -> tuple[http.server.HTTPServer, int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            result.code = (params.get("code") or [None])[0]
            result.state = (params.get("state") or [None])[0]
            result.error = (params.get("error") or [None])[0]
            result.error_description = (params.get("error_description") or [None])[0]
            ok = result.code is not None and result.error is None
            title = "PoE OAuth complete" if ok else "PoE OAuth failed"
            body = "Authorization complete. You can return to Codex." if ok else "Authorization failed. Return to Codex."
            html = f"<!doctype html><meta charset='utf-8'><title>{title}</title><body><h1>{title}</h1><p>{body}</p></body>"
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    last_error: Exception | None = None
    for port in (49082, 49083, 49084):
        try:
            server = http.server.HTTPServer(("localhost", port), Handler)
            return server, port
        except OSError as exc:
            last_error = exc
    raise ApiError(f"Could not bind OAuth redirect server on localhost:49082-49084: {last_error}")


def exchange_authorization_code(code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
    form = urllib.parse.urlencode(
        {
            "client_id": "pob",
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    text = request_text(
        f"{WWW_BASE}/oauth/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body=form,
    )
    data = json.loads(text)
    if "access_token" not in data or "refresh_token" not in data:
        raise ApiError("Authorization response did not contain OAuth tokens")
    return data


def ensure_token(settings_path: Path) -> tuple[str, dict[str, Any]]:
    accounts = load_accounts(settings_path)
    token = accounts["lastToken"]
    expiry = accounts["tokenExpiry"]
    if token and expiry > utc_now() + 60:
        return token, {"refreshed": False, "tokenExpiry": expiry}
    refreshed = refresh_token(settings_path, accounts["lastRefreshToken"])
    return refreshed["access_token"], {
        "refreshed": True,
        "tokenExpiry": utc_now() + int(refreshed.get("expires_in") or 0),
        "settingsBackup": refreshed.get("_settings_backup"),
    }


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def clean_mod_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("description") or value.get("text") or ""
    text = str(value)
    text = re.sub(r"\[[^|\]]*\|([^\]]+)\]", r"\1", text)
    text = text.replace("[", "").replace("]", "")
    return text.strip()


def trade_item_to_raw(entry: dict[str, Any]) -> str:
    item = entry.get("item") or {}
    listing = entry.get("listing") or {}
    price = listing.get("price") or {}
    properties = item.get("properties") or []
    item_class = "Unknown"
    if properties and isinstance(properties[0], dict) and properties[0].get("name"):
        item_class = str(properties[0]["name"])
        if not item_class.endswith("s"):
            item_class += "s"
    rarity = item.get("rarity") or item.get("frameTypeId") or "Unknown"
    name = item.get("name") or ""
    type_line = item.get("typeLine") or item.get("baseType") or ""

    lines = [f"Item Class: {item_class}", f"Rarity: {rarity}"]
    if name:
        lines.append(str(name))
    if type_line:
        lines.append(str(type_line))
    if item.get("ilvl"):
        lines.extend(["--------", f"Item Level: {item['ilvl']}"])

    for key in ("implicitMods", "explicitMods", "craftedMods", "runeMods", "desecratedMods", "enchantMods", "fracturedMods"):
        mods = item.get(key) or []
        if mods:
            lines.append("--------")
            lines.extend(clean_mod_text(mod) for mod in mods)
    if item.get("corrupted"):
        lines.extend(["--------", "Corrupted"])
    if price.get("amount") and price.get("currency"):
        lines.extend(["--------", f"Price: {price['amount']} {price['currency']}"])
    return "\n".join(line for line in lines if line)


def write_trade_items_text(data: dict[str, Any], out_path: str | None) -> str | None:
    if not out_path:
        return None
    entries = data.get("result") or []
    chunks = [trade_item_to_raw(entry) for entry in entries if isinstance(entry, dict)]
    Path(out_path).write_text("\n\n".join(chunks) + ("\n" if chunks else ""), encoding="utf-8")
    return out_path


def command_status(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    accounts = load_accounts(settings_path)
    print_json(
        {
            "settings": str(settings_path),
            "hasAccessToken": bool(accounts["lastToken"]),
            "hasRefreshToken": bool(accounts["lastRefreshToken"]),
            "tokenExpiryUtc": iso_from_epoch(accounts["tokenExpiry"]),
            "tokenExpired": accounts["tokenExpiry"] <= utc_now() + 60,
            "lastAccountName": accounts["lastAccountName"] or None,
            "lastRealm": accounts["lastRealm"] or None,
            "accountCount": accounts["accountCount"],
        }
    )
    return 0


def command_authorize(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    if not settings_path.exists():
        raise ApiError(f"Settings.xml not found: {settings_path}")

    result = OAuthResult()
    server, port = bind_oauth_server(result)
    redirect_uri = f"http://localhost:{port}"
    code_verifier = b64url(secrets.token_bytes(48))
    code_challenge = b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    state = secrets.token_hex(8)
    query = urllib.parse.urlencode(
        {
            "client_id": "pob",
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
        },
        quote_via=urllib.parse.quote,
    )
    auth_url = f"{WWW_BASE}/oauth/authorize?{query}"
    opened = webbrowser.open(auth_url)
    print_json(
        {
            "authorizationStarted": True,
            "browserOpened": opened,
            "redirectUri": redirect_uri,
            "timeoutSeconds": args.timeout,
            "message": "Complete Path of Exile authorization in the opened browser.",
        }
    )
    deadline = time.time() + args.timeout
    server.timeout = 1
    try:
        while time.time() < deadline and not (result.code or result.error):
            server.handle_request()
    finally:
        server.server_close()

    if result.error:
        raise ApiError(f"OAuth failed: {result.error} {result.error_description or ''}".strip())
    if not result.code:
        raise ApiError("OAuth timed out before the redirect was received")
    if result.state != state:
        raise ApiError("OAuth state mismatch")

    token_data = exchange_authorization_code(result.code, redirect_uri, code_verifier)
    backup = update_settings_tokens(settings_path, token_data)
    print_json(
        {
            "authorized": True,
            "settings": str(settings_path),
            "settingsBackup": str(backup),
            "tokenExpiryUtc": iso_from_epoch(utc_now() + int(token_data.get("expires_in") or 0)),
        }
    )
    return 0


def command_characters(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    token, auth_info = ensure_token(settings_path)
    realm = args.realm
    url = f"{API_BASE}/character" + ("" if realm == "pc" else f"/{urllib.parse.quote(realm, safe='')}")
    data = request_json(url, headers=auth_headers(token))
    chars = data.get("characters", data if isinstance(data, list) else [])
    summary = []
    for char in chars:
        if isinstance(char, dict):
            summary.append(
                {
                    "name": char.get("name"),
                    "league": char.get("league"),
                    "class": char.get("class"),
                    "level": char.get("level"),
                }
            )
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print_json(
        {
            "auth": auth_info,
            "realm": realm,
            "count": len(summary),
            "characters": summary,
            "out": str(Path(args.out)) if args.out else None,
        }
    )
    return 0


def command_character(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    token, auth_info = ensure_token(settings_path)
    realm = args.realm
    name = urllib.parse.quote(args.name, safe="")
    url = f"{API_BASE}/character" + ("" if realm == "pc" else f"/{urllib.parse.quote(realm, safe='')}") + f"/{name}"
    data = request_json(url, headers=auth_headers(token))
    char = data.get("character", data)
    equipment = char.get("equipment") or char.get("items") or []
    passives = char.get("passives") or []
    if isinstance(passives, dict):
        passive_count = len(passives.get("hashes") or [])
    else:
        passive_count = len(passives)
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print_json(
        {
            "auth": auth_info,
            "name": char.get("name"),
            "league": char.get("league"),
            "class": char.get("class"),
            "level": char.get("level"),
            "equipment": len(equipment),
            "skills": len(char.get("skills") or []),
            "passives": passive_count,
            "out": str(Path(args.out)) if args.out else None,
        }
    )
    return 0


def command_trade_search(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    token, auth_info = ensure_token(settings_path)
    query = json.loads(Path(args.query).read_text(encoding="utf-8-sig"))
    realm = urllib.parse.quote(args.realm, safe="")
    league = urllib.parse.quote(args.league, safe="")
    url = f"{WWW_BASE}/api/trade2/search/{realm}/{league}"
    data = request_json(url, method="POST", headers=auth_headers(token), payload=query)
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print_json(
        {
            "auth": auth_info,
            "id": data.get("id"),
            "total": data.get("total"),
            "resultCount": len(data.get("result") or []),
            "out": str(Path(args.out)) if args.out else None,
        }
    )
    return 0


def command_trade_fetch(args: argparse.Namespace) -> int:
    settings_path = settings_path_from_args(args)
    token, auth_info = ensure_token(settings_path)
    ids = ",".join(args.ids)
    query = urllib.parse.quote(args.query_id, safe="")
    url = f"{WWW_BASE}/api/trade2/fetch/{ids}?query={query}"
    data = request_json(url, headers=auth_headers(token))
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    items_out = write_trade_items_text(data, args.items_out)
    print_json(
        {
            "auth": auth_info,
            "resultCount": len(data.get("result") or []),
            "out": str(Path(args.out)) if args.out else None,
            "itemsOut": str(Path(items_out)) if items_out else None,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PoB OAuth-backed Path of Exile 2 API helper")
    parser.add_argument("--pob-path", default=None, help="Path to Path of Building-PoE2 install")
    parser.add_argument("--settings", default=None, help="Explicit PoB Settings.xml path")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show token presence and expiry without network calls")
    status.set_defaults(func=command_status)

    authorize = sub.add_parser("authorize", help="Run PoE OAuth login and save tokens into PoB Settings.xml")
    authorize.add_argument("--timeout", type=int, default=180)
    authorize.set_defaults(func=command_authorize)

    chars = sub.add_parser("characters", help="Fetch account character list")
    chars.add_argument("--realm", default="poe2")
    chars.add_argument("--out")
    chars.set_defaults(func=command_characters)

    char = sub.add_parser("character", help="Fetch one character")
    char.add_argument("--realm", default="poe2")
    char.add_argument("--name", required=True)
    char.add_argument("--out")
    char.set_defaults(func=command_character)

    search = sub.add_parser("trade-search", help="POST an authenticated trade2 search query")
    search.add_argument("--realm", default="poe2")
    search.add_argument("--league", required=True)
    search.add_argument("--query", required=True, help="JSON query file")
    search.add_argument("--out")
    search.set_defaults(func=command_trade_search)

    fetch = sub.add_parser("trade-fetch", help="Fetch authenticated trade2 item results")
    fetch.add_argument("--query-id", required=True)
    fetch.add_argument("--ids", nargs="+", required=True)
    fetch.add_argument("--out")
    fetch.add_argument("--items-out", help="Write fetched items as PoE-style copied item text")
    fetch.set_defaults(func=command_trade_fetch)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ApiError as exc:
        print_json({"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
