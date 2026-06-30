---
name: poe2-item-evaluator
description: Evaluate Path of Exile 2 items, find upgrade analogs, price-check marked public stash tabs, show sale-price labels on the local stash overlay, and plan respec/build changes using Path of Building 2 context, copied item text, saved trade candidates, poe.ninja build snapshots, authenticated PoE account/trade API access via local PoB OAuth credentials, and the local Windows PoB/BuildPlanner setup. Use when the user asks whether a PoE2 item is better, wants candidate item ranking, suitable analogs/upgrades, stash-tab price checking, says "сделай оценку на продажу" or asks to evaluate items for sale/pricing, wants PoB trade weights, current account character data, target-skill DPS deltas, poe.ninja build/reference discovery, similar-build analysis, mana/damage respec planning, or passive-tree/gear recommendations that must be checked through PoB2.
---

# PoE2 Item Evaluator

## Ground Rules

- Answer in Russian unless the user switches language.
- Prefer current evidence over old snapshots. Re-check local files and live context before making build-specific claims.
- Use Russian in-game names for items, gems, passives, keystones, and uniques when possible; otherwise add the English name next to a Russian explanation.
- Do not present script ranking as exact PoB math. Exact item impact must come from Path of Building 2 item comparison or Trader evaluation.
- If a request involves damage, DPS, offensive item value, or trade upgrades that affect damage, choose a target skill before searching or ranking unless the user already named one. Include socketed skills inside totems, triggers, or other meta/autocast setups as separate target choices.
- For trade searches and buy-ready trade links, search only listings with instant buyout by default: `query.status.option = "securable"` (`Instant Buyout`). Do not use `online`, `onlineleague`, `any`, or `available` unless the user explicitly asks to broaden the search; if broadened, label the result as not instant-buyout-only.
- By default, score resistances only up to the relevant cap, usually 75%. Extra overcap is neutral: do not count it as a benefit, but do not reject or penalize an otherwise strong item only because it overcaps. Penalize overcap only when the user explicitly asks to avoid it or to minimize wasted suffixes.
- Keep changes non-breaking. Do not edit or delete BuildPlanner files unless the user explicitly asks.
- Treat PoB OAuth credentials as secrets. Never print access tokens, refresh tokens, or raw `Settings.xml` account attributes.

## Local Paths

Default paths on this machine:

- Workspace: `D:\Soft\PoE2_Build`
- Working PoB2 install: `D:\Soft\PathOfBuilding-PoE2`
- Bootstrap cache for portable installs: `%LOCALAPPDATA%\Codex\poe2-item-evaluator\PathOfBuilding-PoE2`
- Authoritative BuildPlanner folder: `C:\Users\Hatzy\Documents\My Games\Path of Exile 2\BuildPlanner`
- Mirror BuildPlanner folder: `C:\Users\Hatzy\OneDrive\Документы\My Games\Path of Exile 2\BuildPlanner`

Run `scripts/collect_context.py` first for real paths, versions, and current files.
If PoB2 is missing or the headless adapter is not present, run `scripts/bootstrap_pob2.py --json` to locate PoB2, `scripts/bootstrap_pob2.py --prepare-headless --json` to patch an existing local runtime, or `scripts/bootstrap_pob2.py --install --json` to download PoB2 into the cache and apply the headless adapter. Do not vendor the full PoB2 runtime into the skill repo.

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
   - Marked stash-tab price check: user puts items into a public tab, gives an account, stash name, and fixed marker price such as `~price 1 mirror`; use trade2 fetch plus local stash filtering and market-floor checks.
   - Sale-price overlay request: if the user says `сделай оценку на продажу`, `оцени на продажу`, asks to price stash items for sale, or asks to put prices on the overlay, treat it as the marked/public stash-tab price-check workflow and produce/update `overlay_prices.json`, then print the sale-price summary and offer or run the overlay manager according to the user's wording.
   - Build/respec planning: use poe.ninja build discovery to find matching reference builds, compare their gear/passives/keystones against the user's current snapshot, then verify candidate gear/tree variants through PoB2 before final recommendations.

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
     - Resolve the runtime with `scripts/bootstrap_pob2.py --json`. If `headlessReady` is false, run `scripts/bootstrap_pob2.py --prepare-headless --json`; if no runtime exists, run `scripts/bootstrap_pob2.py --install --json`.
     - Decode the current `pathOfBuildingExport` into a temporary XML.
     - Build a JSON config with the target name, XML path, and candidate copied-item text.
     - Run the resolved `pobExe` with `POB_HEADLESS_CALC_CONFIG` and `POB_HEADLESS_CALC_OUT`.
     - Accept the result only if the baseline selected-skill DPS matches the saved/live baseline for the target skill. If the baseline differs, discard the run and use the UI workflow below.
     - For trade2 item JSON, remember that displayed explicit/implicit values are already final. Do not add catalyst scaling a second time when generating copied-item text for PoB.
   - Open `D:\Soft\PathOfBuilding-PoE2\Path of Building-PoE2.exe`.
   - Load/import the current build.
   - Select the chosen target skill in the calculation view. For totem/meta/autocast targets, select the nested skill or its closest PoB calculation entry and note the delivery context.
   - On the Items tab, paste the copied item with `Ctrl+V`.
   - Hover/equip in the matching slot and record the stat diff: Full DPS, Effective Hit Pool, max hit, life, ES, mana, spirit, resist caps, attributes, and any build-specific stat the user asks about.
   - Record the target skill baseline damage and candidate damage, then report `delta % = (new / old - 1) * 100`. If PoB only exposes a related DPS field, name the field exactly.
   - For analogs, use `Trade for these items`, adjust search weights, execute the weighted search, then rank fetched candidates by PoB's evaluation.
   - In PoB trade popups, set `Listed` to `Instant Buyout` before opening/copying a URL. The API value is `securable`.

6. For analog recommendations:
   - Prefer PoB-generated weighted trade URLs or authenticated PoB Trader results, with listed status set to instant buyout (`securable`) unless the user explicitly says otherwise.
   - Weight the search around the selected target skill's damage first, then the user's defensive/stat constraints. Do not optimize generic damage if the target skill was selected.
   - For resistance constraints, compute useful resistance as `min(final resistance, cap) - current/baseline need`; treat values above cap as no extra value unless the user explicitly wants overcap for curses, exposure, or map mods.
   - If the user gives saved trade candidates, parse/rank them with `rank_items.py`, then mark top candidates for exact PoB validation.
   - Preserve the user's scoring rule literally. If they say to ignore runes, implicits, price, corrupted mods, or a defensive stat, do that.
   - In the final answer, always include a visible `Target skill damage` block for damage-affecting recommendations. Include the selected target skill, baseline stats, post-item stats, and percentage deltas for the target skill damage plus requested attributes such as mana, life, ES, spirit, EHP, and resistances. If the result is script-only triage, write `Exact PoB DPS: not calculated` in that block, optionally include a clearly labelled heuristic/offensive-affix proxy, and do not invent or imply a target-skill DPS percentage.
   - When reporting mana changes, do not list flat mana and percent mana only as raw mods. Include baseline mana, estimated or PoB-confirmed after-swap mana, and delta. If exact PoB validation was not done but a current mana breakdown is available, calculate an estimate with the current base/inc values after removing replaced-item mana contributions, apply new flat mana first and `% increased maximum Mana` second, and label it as an estimate. If the needed breakdown is unavailable, say the exact total requires PoB and show the formula instead.
   - For every character stat used in ranking or user constraints, report the old value, new estimated or PoB-confirmed value, and delta. Include lost stats from replaced items, not just gained stats from candidates. For life, ES, spirit, attributes, rarity, and resistances, subtract the current item contribution before adding the candidate contribution. If the stat has global increased/more scaling and exact PoB validation was not done, label the result as estimated and state the scaling assumption.
   - For 2-5 recommended trade options, prefer compact per-option blocks over wide markdown tables. Each block should put the recommendation name, price, damage delta/proxy, key stat deltas, and trade links together so the user can read and act on one option without scanning across columns.
   - Trade links for rare items must use the full rare name plus base type whenever possible, for example `Hate Knuckle Mnemonic Ring`, not just `Hate Knuckle`. For buy-ready recommendations of a specific listing, include the seller account filter too: `term = "<rare name> <base type>"`, `type = "<base type>"`, `query.status.option = "securable"`, and `trade_filters.account.input = "<seller account>"`. Add key-mod minimum filters only when their stat ids are verified from the item JSON; guessed stat ids can hide the intended item. Verify the search result count and that the original item id is present. Prefer links that return exactly the intended listing; if the seller-filtered link returns 0 or many irrelevant items, include the seller/whisper text and do not present the link as buy-ready.

7. For marked public stash-tab price checks:
   - Treat phrases like `сделай оценку на продажу`, `оцени на продажу`, `выведи оценку предметов во вкладках под торговлю`, and requests to show prices on the stash overlay as this workflow. Default to the latest known account/league/marked-tab setup when the current workspace already has saved reports or overlay state; otherwise ask only for the missing account, league, public stash name/marker price, and threshold.
   - If there is no saved report, no known marker setup, or the current stash setup is unclear, pause before running trade2 and explain the setup to the user. Tell them to make the target stash tabs public, put the items for sale evaluation into those tabs, and set every item in each tab to the tab's fixed marker price. Offer exactly two marker ladders: `~price 1 mirror`, `~price 2 mirror`, `~price 3 mirror`, ... for normal use, or `~price 10 mirror`, `~price 11 mirror`, `~price 12 mirror`, ... when they want the markers far away from ordinary prices. Ask which ladder to use, which tabs map to which marker, the account, league, threshold, and market status (`online`, `any`, or `available`). Warn that these are public listings and can receive whispers at the marker prices.
   - Use this workflow when the user wants to evaluate many items from their stash without visual OCR. Ask them to make the tab public and give every item a distinctive fixed marker price, for example `~price 1 mirror`, then provide the account name such as `XapcT#1700`, league, and tab name. The tab name can be Russian or English; it is filtered locally after fetch.
   - This workflow is a deliberate exception to the instant-buyout default. Use ordinary player-trade statuses such as `online` or `any`, not `securable`, because the marker listing itself is not a buy-ready upgrade search.
   - Run the bundled script:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_pricecheck.py" --account "XapcT#1700" --league "Runes of Aldur" --stash-name "~price 1 mirror" --marker-currency mirror --marker-amount 1 --threshold-exalted 10 --out-dir "D:\Soft\PoE2_Build"
     ```
     This searches account+marker price, unions multiple sorts to work around the 100-result cap, fetches full item JSON, filters by `listing.stash.name`, builds a local shortlist from explicit stat ids and values, then runs a small number of ordinary `online` market-floor checks.
   - If a full fetch JSON already exists, avoid another account search and reuse it:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_pricecheck.py" --input "D:\Soft\PoE2_Build\poe_stash_pricecheck_fetch_price_1_mirror.json" --account "XapcT#1700" --threshold-exalted 10 --out-dir "D:\Soft\PoE2_Build"
     ```
   - Report only items with a fetched external floor above the requested threshold as confirmed. Put the stash coordinates first: `x/y` are trade2's zero-based stash coordinates; `column/row` in the JSON are one-based for in-game navigation. Include the market-floor price, trade URL, and key mods. Put items with no analogs, invalid stat filters, or exhausted query budget into an `uncertain` bucket instead of calling them expensive.
   - Explain setup to the user as: create public stash tabs, place items there, set every item in each tab to a fixed high marker such as `~price 1 mirror` / `~price 2 mirror` / `~price 3 mirror` or `~price 10 mirror` / `~price 11 mirror` / `~price 12 mirror`, tell Codex the account, league, stash names or marker-to-tab mapping, threshold, and whether to use `online`, `any`, or `available`. Warn that the items are publicly visible at the marker prices and may receive whispers.
   - After a stash evaluation, optionally show prices directly over the stash grid with the local overlay helper. It reads saved report JSON and draws click-through labels at the fetched stash coordinates:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay.py" --latest --min-price-exalted 10 --window-title "Path of Exile"
     ```
     If the final answer adjusts raw market floors manually, save a curated `overlay_prices.json` in the evaluation directory and include rows such as `{"marker":"marker2","x":2,"y":0,"text":"2div","priceExalted":210,"labelRu":"..."}`. When `overlay_prices.json` exists, `--latest` uses it instead of raw report floors. Use `--marker marker1` or `--marker marker2` when only one marked tab is open.
     To switch and hide automatically while the user changes stash tabs, keep the marker tabs visible in the tab strip and run:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay.py" --latest --auto-marker --tab-marker marker2 --tab-marker marker1
     ```
     Auto-marker mode watches the tab-strip colors in the saved profile's `tabScan*` region, treats the brighter wide gold tab as active, maps visible marker tabs left-to-right to the given `--tab-marker` order, and hides all labels when neither marked tab is active. If a marked tab is active but has no saved labels above the threshold, it shows a small empty-state label such as `1 mirror: 0 >= 10ex`; pass `--no-empty-status` to hide even that.
     Use slot guarding for sale-price overlays: the overlay can sample several pixels inside each labelled stash slot after it first sees the tab, then hide that label if those pixels change. Only compare slot pixels while the matching trade tab is actively detected by auto-marker; closing the stash, switching away, or losing tab detection must hide/switch the overlay instead of marking prices stale. Hide the overlay's own price-label canvas items before screenshot sampling, otherwise the guard can compare the green price label against itself instead of the item underneath. Treat many slot changes in one poll as an unstable frame and skip that poll instead of hiding the whole tab. This prevents stale prices from staying visible after an item is moved, removed, or replaced without breaking labels when the stash UI closes; hidden labels are persisted in `poe_stash_slot_guard_state.json` and should stay hidden until the report file changes after a new sale evaluation. The manager enables this by default through `--slot-guard`; use `--no-slot-guard` only when the screenshot-based check is too noisy.
     Prefer the manager tool for user-facing activation, calibration, stopping, and autostart:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" calibrate
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" start
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" stop
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" enable-autostart --start-now
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" disable-autostart --stop-running
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay_manager.py" status
     ```
     `calibrate` opens a non-click-through overlay with the stash grid, a visible `СЕТКА` handle for moving the grid, the blue tab-scan rectangle, detected marker-tab boxes, and on-screen buttons. Prefer mouse controls because the game can steal keyboard focus: drag the `СЕТКА` handle to move the grid, drag the blue tab-scan rectangle to move the tab detector, use `+`/`-` buttons to change cell size, `Сброс` to restore the default calibration, and `Готово` to save and close. Double `Enter` also saves and closes when the overlay has focus. Keyboard fallbacks still exist: `Tab`/`G`/`T` switch selected area, arrows move it, `Ctrl+arrows` resize tab-scan, `S` saves, and `Esc` closes. This calibration should be offered before first overlay use, after monitor/UI scaling changes, or when labels do not align.
     `start` is a one-time overlay launch. The manager is singleton: `start`, `calibrate`, and `enable-autostart --start-now` stop every existing `poe_stash_overlay.py` process before opening a new one, even when the old process has a different `--agent-id`. `enable-autostart --start-now` writes the single `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry, removes other `PoE2StashOverlay_*` Run entries first, starts the overlay, waits for the game window, follows it if it moves, and closes the overlay when the game closes. `disable-autostart --stop-running` removes all overlay Run entries and stops all running stash-overlay processes.
     After each stash evaluation that produces or updates `overlay_prices.json`, do not stop at "saved overlay file". Offer a concrete output mode and interface lifetime choice unless the user already requested one. Use a short Russian prompt like:
     - `Только текст`: print the valuable-item list with stash column/row, item name, recommended price, and key reason; do not launch overlay.
     - `Калибровка + оверлей`: run `poe_stash_overlay_manager.py calibrate` first, then after the user saves/closes calibration run `poe_stash_overlay_manager.py start`.
     - `Оверлей один раз`: run `poe_stash_overlay_manager.py start` with current saved calibration.
     - `Постоянный оверлей`: run `poe_stash_overlay_manager.py enable-autostart --start-now`; explain that this leaves only one overlay Run entry, stops any previous overlay before launch, waits for the game window, follows it, and closes with the game.
     - `Не показывать`: leave only the text report and saved JSON files.
     Also offer the closing mode for the overlay interface:
     - `Закрыть вместе с игрой` is the default for `start` and autostart because the manager passes `--exit-with-window`.
     - `Закрыть по запросу` means keep the one-time overlay running until the user asks; then run `poe_stash_overlay_manager.py stop`.
     - `Отключить полностью` means remove all overlay autostart entries and close the running overlay with `poe_stash_overlay_manager.py disable-autostart --stop-running`.
     - `Только убрать автозапуск` means run `poe_stash_overlay_manager.py disable-autostart` and leave the current manual overlay state unchanged.
     When presenting these choices, map them to the exact manager command the agent will run and briefly explain what that command changes. If the user asks to close, hide, or "погаси" the interface, prefer `stop` for one-time overlays and `disable-autostart --stop-running` only when they explicitly want the permanent mode removed too.
     If the manager is not available, the direct fallback calibration command is:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_stash_overlay.py" --latest --auto-marker --tab-marker marker2 --tab-marker marker1 --calibrate --show-grid --show-tab-scan --no-click-through --window-title "Path of Exile"
     ```
     The overlay works best in windowed or borderless fullscreen mode; exclusive fullscreen can hide normal Windows overlays.

8. For poe.ninja build discovery and respec planning:
   - Read `references/workflow.md` if the task asks to compare builds, plan a respec, copy poe.ninja patterns, or optimize passive trees.
   - Start from the user's current snapshot when available. If not, fetch the current account character with `poe_account_api.py character` or use an explicit poe.ninja character URL.
   - Infer safe filters from the request and snapshot: league, class/ascendancy, target skill, delivery skill such as `Spell Totem`, minimum level, mana/ES constraints, required keystones, and known must-keep items. If damage matters and the target skill is not clear, list skills and ask for the target before ranking.
   - Run the build finder. Example for an Oracle mana Grim Pillars totem search:
     ```powershell
     python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_ninja_build_finder.py" --league runesofaldur --class-name Oracle --skill "Grim Pillars" --delivery "Spell Totem" --max-energyshield 0 --min-level 90 --sort mana --details 12 --current-character "<current>.json" --out "<report>.json" --save-dir "<refs-dir>" --decode-pob-xml
     ```
     This fetches the current poe.ninja snapshot, decodes the binary search result, downloads top character JSON details, compares common unique items/keystones/passive IDs against the current snapshot, and saves PoB exports/XML when requested.
   - Treat the build finder as discovery and pattern extraction. It is not exact PoB optimization by itself.
   - Before final recommendations, build a small set of PoB XML variants: current tree baseline, reference tree transplant if feasible, current tree plus common missing keystones, and 2-4 conservative variants that keep required gear/attributes/resists. Use the headless PoB adapter or PoB UI to validate target skill DPS, mana, EHP, max hits, spirit, attributes, and resistances.
   - The headless adapter supports `xmlVariants` in addition to ring `pairs`. A config can include:
     ```json
     {
       "target": "Grim Pillars via Spell Totem",
       "xmlPath": "current.xml",
       "xmlVariants": [
         {"name": "poe.ninja mana core", "xmlPath": "variant.xml"}
       ]
     }
     ```
     Run it through the existing `POB_HEADLESS_CALC_CONFIG` / `POB_HEADLESS_CALC_OUT` flow after confirming `bootstrap_pob2.py --json` reports `headlessReady: true`.
   - In the final answer, separate `poe.ninja pattern` from `PoB2-confirmed recommendation`. Do not recommend a respec tree as optimal until the exact PoB run confirms the requested objective, such as maximum mana plus target-skill damage.

9. For current account data or authenticated trade access, use the PoB OAuth helper:
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
- `scripts/bootstrap_pob2.py`: locates an existing PoB2 runtime or downloads PathOfBuildingCommunity/PathOfBuilding-PoE2 into a local cache, then applies the bundled headless adapter from `assets/pob2-headless/`.
- `scripts/poe_account_api.py`: safely reuses PoB OAuth credentials for account character and trade2 API calls without printing tokens.
  `trade-search` forces `--listed securable` by default so searches are instant-buyout-only; use `--listed preserve` only when the user explicitly asks to keep another listed status. Use `trade-fetch --items-out <items.txt>` to convert fetched trade listings into copied-item text for `rank_items.py`.
- `scripts/list_character_skills.py`: reads a saved character JSON and prints a numbered target-skill menu, including non-support skills nested inside totems/meta setups and trigger-like support skills.
- `scripts/rank_items.py`: parses copied PoE2 item text and ranks items with a transparent heuristic profile. Use for triage only.
- `scripts/poe_stash_pricecheck.py`: fetches a marked public stash tab by account+marker price, filters by stash name, triages likely valuable items, and runs low-volume ordinary trade market-floor checks for a price threshold.
- `scripts/poe_stash_overlay.py`: reads saved stash price-check reports and draws a transparent, optionally click-through Windows overlay with compact price labels over the 12x12 stash grid. It also supports grid and tab-scan calibration; use the manager for normal activation.
- `scripts/poe_stash_overlay_manager.py`: singleton user-facing overlay manager for calibration, one-time start, stop, status, and Windows autostart. Prefer this over direct overlay commands after stash evaluations.
- `scripts/poe_ninja_build_finder.py`: decodes live poe.ninja PoE2 build search results, fetches matching character details, extracts common gear/keystone/passive patterns, compares them to a current character snapshot, and saves reference PoB exports/XML for exact PoB2 validation.

Read `references/workflow.md` if the task involves exact PoB validation, analog search, or explaining limitations.
