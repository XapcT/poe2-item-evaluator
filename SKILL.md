---
name: poe2-item-evaluator
description: Evaluate Path of Exile 2 items and find upgrade analogs for the user's local builds using Path of Building 2 context, copied item text, saved trade candidates, poe.ninja snapshots, authenticated PoE account/trade API access via local PoB OAuth credentials, and the local Windows PoB/BuildPlanner setup. Use when the user asks whether a PoE2 item is better, wants a ranking of candidate items, wants suitable analogs/upgrades, wants PoB trade weights, wants current account character data, asks to compare gear against their current build, or needs target-skill DPS percent deltas after choosing a character skill.
---

# PoE2 Item Evaluator

## Ground Rules

- Answer in Russian unless the user switches language.
- Prefer current evidence over old snapshots. Re-check local files and live context before making build-specific claims.
- Use Russian in-game names for items, gems, passives, keystones, and uniques when possible; otherwise add the English name next to a Russian explanation.
- Do not present script ranking as exact PoB math. Exact item impact must come from Path of Building 2 item comparison or Trader evaluation.
- If a request involves damage, DPS, offensive item value, or trade upgrades that affect damage, choose a target skill before searching or ranking unless the user already named one. Include socketed skills inside totems, triggers, or other meta/autocast setups as separate target choices.
- By default, score resistances only up to the relevant cap, usually 75%. Extra overcap is neutral: do not count it as a benefit, but do not reject or penalize an otherwise strong item only because it overcaps. Penalize overcap only when the user explicitly asks to avoid it or to minimize wasted suffixes.
- Keep changes non-breaking. Do not edit or delete BuildPlanner files unless the user explicitly asks.
- Treat PoB OAuth credentials as secrets. Never print access tokens, refresh tokens, or raw `Settings.xml` account attributes.

## Local Paths

Default paths on this machine:

- Workspace: `D:\Soft\PoE2_Build`
- Working PoB2 install: `D:\Soft\PathOfBuilding-PoE2`
- Authoritative BuildPlanner folder: `C:\Users\Hatzy\Documents\My Games\Path of Exile 2\BuildPlanner`
- Mirror BuildPlanner folder: `C:\Users\Hatzy\OneDrive\Документы\My Games\Path of Exile 2\BuildPlanner`

Run `scripts/collect_context.py` first for real paths, versions, and current files.

## Workflow

1. Collect context:
   ```powershell
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\collect_context.py" --json
   ```
   Use this to confirm PoB version, build files, current summaries, and BuildPlanner locations.

2. Identify the requested mode:
   - Single item check: user provides copied item text or a screenshot/transcription.
   - Candidate ranking: user provides multiple copied items, saved trade text/HTML-derived candidates, or JSON candidates.
   - Analog search: use PoB Trader weighted search first; use scripts to triage candidate text after fetching/saving results.

3. Choose the target skill when damage matters:
   - Fetch the current character if needed:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" character --realm poe2 --name "<character>" --out "<character>.json"
     ```
   - List selectable skills:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\list_character_skills.py" --character "<character>.json"
     ```
   - Copy the numbered list into the assistant message to the user; do not rely on terminal/tool output being visible. Ask for the number. If the user already named a skill, match it from the list and state the matched target.
   - Treat nested non-support skills under `Spell Totem`, triggers, or other meta skills as distinct targets, for example `Grim Pillars via Spell Totem`.
   - Carry the selected target name and delivery context through every search, ranking, and final recommendation.

4. For copied items, save the raw text to a temporary `.txt` file and run:
   ```powershell
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\rank_items.py" --items <items.txt> --profile oracle-thrashing-vines
   ```
   The script returns parsed mods, heuristic score, and reasons. Treat this as a filter before exact PoB checks.

5. For exact effect, use PoB2:
   - First try the local headless PoB calculator when a current `pathOfBuildingExport` XML and candidate item text are available:
     - Decode the current `pathOfBuildingExport` into a temporary XML.
     - Build a JSON config with the target name, XML path, and candidate copied-item text.
     - Run the workspace PoB copy with `POB_HEADLESS_CALC_CONFIG` and `POB_HEADLESS_CALC_OUT` against `D:\Soft\PoE2_Build\pob2_v0.21.1_extract\Path of Building-PoE2.exe`.
     - Accept the result only if the baseline selected-skill DPS matches the saved/live baseline for the target skill. If the baseline differs, discard the run and use the UI workflow below.
     - For trade2 item JSON, remember that displayed explicit/implicit values are already final. Do not add catalyst scaling a second time when generating copied-item text for PoB.
   - Open `D:\Soft\PathOfBuilding-PoE2\Path of Building-PoE2.exe`.
   - Load/import the current build.
   - Select the chosen target skill in the calculation view. For totem/meta/autocast targets, select the nested skill or its closest PoB calculation entry and note the delivery context.
   - On the Items tab, paste the copied item with `Ctrl+V`.
   - Hover/equip in the matching slot and record the stat diff: Full DPS, Effective Hit Pool, max hit, life, ES, mana, spirit, resist caps, attributes, and any build-specific stat the user asks about.
   - Record the target skill baseline damage and candidate damage, then report `delta % = (new / old - 1) * 100`. If PoB only exposes a related DPS field, name the field exactly.
   - For analogs, use `Trade for these items`, adjust search weights, execute the weighted search, then rank fetched candidates by PoB's evaluation.

6. For analog recommendations:
   - Prefer PoB-generated weighted trade URLs or authenticated PoB Trader results.
   - Weight the search around the selected target skill's damage first, then the user's defensive/stat constraints. Do not optimize generic damage if the target skill was selected.
   - For resistance constraints, compute useful resistance as `min(final resistance, cap) - current/baseline need`; treat values above cap as no extra value unless the user explicitly wants overcap for curses, exposure, or map mods.
   - If the user gives saved trade candidates, parse/rank them with `rank_items.py`, then mark top candidates for exact PoB validation.
   - Preserve the user's scoring rule literally. If they say to ignore runes, implicits, price, corrupted mods, or a defensive stat, do that.
   - In the final answer, always include a visible `Target skill damage` block for damage-affecting recommendations. Include the selected target skill, baseline stats, post-item stats, and percentage deltas for the target skill damage plus requested attributes such as mana, life, ES, spirit, EHP, and resistances. If the result is script-only triage, write `Exact PoB DPS: not calculated` in that block, optionally include a clearly labelled heuristic/offensive-affix proxy, and do not invent or imply a target-skill DPS percentage.
   - When reporting mana changes, do not list flat mana and percent mana only as raw mods. Include baseline mana, estimated or PoB-confirmed after-swap mana, and delta. If exact PoB validation was not done but a current mana breakdown is available, calculate an estimate with the current base/inc values after removing replaced-item mana contributions, apply new flat mana first and `% increased maximum Mana` second, and label it as an estimate. If the needed breakdown is unavailable, say the exact total requires PoB and show the formula instead.
   - For every character stat used in ranking or user constraints, report the old value, new estimated or PoB-confirmed value, and delta. Include lost stats from replaced items, not just gained stats from candidates. For life, ES, spirit, attributes, rarity, and resistances, subtract the current item contribution before adding the candidate contribution. If the stat has global increased/more scaling and exact PoB validation was not done, label the result as estimated and state the scaling assumption.
   - For 2-5 recommended trade options, prefer compact per-option blocks over wide markdown tables. Each block should put the recommendation name, price, damage delta/proxy, key stat deltas, and trade links together so the user can read and act on one option without scanning across columns.
   - Trade links for rare items must use the full rare name plus base type whenever possible, for example `Hate Knuckle Mnemonic Ring`, not just `Hate Knuckle`. For buy-ready recommendations of a specific listing, include the seller account filter too: `term = "<rare name> <base type>"`, `type = "<base type>"`, and `trade_filters.account.input = "<seller account>"`. Add key-mod minimum filters only when their stat ids are verified from the item JSON; guessed stat ids can hide the intended item. Verify the search result count and that the original item id is present. Prefer links that return exactly the intended listing; if the seller-filtered link returns 0 or many irrelevant items, include the seller/whisper text and do not present the link as buy-ready.

7. For current account data or authenticated trade access, use the PoB OAuth helper:
   ```powershell
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" status
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" authorize
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" characters --realm poe2
   python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" character --realm poe2 --name "<character>" --out "<file>.json"
   ```
   This reads OAuth tokens from `D:\Soft\PathOfBuilding-PoE2\Settings.xml`, can run the same PoB OAuth login flow when refresh tokens are expired, refreshes expired tokens when possible, writes rotated tokens back after creating a timestamped backup, and never prints token values.

## Useful PoB Details

- PoB2's default Trader weights are `Full DPS = 1.0` and `Effective Hit Pool = 0.5`.
- The built-in local server in `LaunchServer.lua` is for OAuth redirect, not a calculation API.
- `TradeQueryGenerator.lua` creates weighted trade filters by testing mods against the current build; use it through PoB UI for exact search weights.
- `TradeQuery.lua` evaluates fetched trade results by comparing the candidate item output against the base build output.
- `poe_account_api.py` reuses PoB's OAuth credentials for `api.pathofexile.com/character/poe2` and authenticated `trade2` calls. Use it for account-backed current character fetches and trade query probes.

## Scripts

- `scripts/collect_context.py`: reports PoB install/version, BuildPlanner files, workspace summaries, candidate files, and current character snapshot availability.
- `scripts/poe_account_api.py`: safely reuses PoB OAuth credentials for account character and trade2 API calls without printing tokens.
  Use `trade-fetch --items-out <items.txt>` to convert fetched trade listings into copied-item text for `rank_items.py`.
- `scripts/list_character_skills.py`: reads a saved character JSON and prints a numbered target-skill menu, including non-support skills nested inside totems/meta setups and trigger-like support skills.
- `scripts/rank_items.py`: parses copied PoE2 item text and ranks items with a transparent heuristic profile. Use for triage only.

Read `references/workflow.md` if the task involves exact PoB validation, analog search, or explaining limitations.
