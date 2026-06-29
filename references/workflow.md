# PoE2 Item Evaluation Workflow

## What "exact" means

An exact answer means PoB2 calculated the item in the active build, with the correct item set, selected target skill, passive tree, config set, and trade weights. A script-only score is a triage estimate.

When available, the local headless PoB calculator is an exact PoB path, not a heuristic, because it loads the current PoB XML/export and reads PoB calculation output. Use it only after verifying that the baseline selected-skill DPS matches the current saved/live baseline. If the baseline differs, the XML, target skill, or config is stale/wrong and the result must be discarded.

The skill does not vendor the full PoB2 runtime. Use `scripts/bootstrap_pob2.py --json` to locate a runtime, `scripts/bootstrap_pob2.py --prepare-headless --json` to add the bundled headless adapter to an existing runtime, or `scripts/bootstrap_pob2.py --install --json` to download PathOfBuildingCommunity/PathOfBuilding-PoE2 into the local cache and prepare it.

## Target skill selection

When damage matters, pick a target skill before searching or ranking:

1. Fetch or refresh the current character JSON with `poe_account_api.py character`.
2. Run `scripts/list_character_skills.py --character <character>.json`.
3. Copy the numbered menu into the assistant message and ask the user to choose a number unless the user already named a skill. Terminal/tool output is not a substitute for showing the list to the user.
4. If the chosen entry is nested, keep the full context in later output, for example `Grim Pillars via Spell Totem` or a trigger/autocast support entry.
5. If no exact PoB check is performed, do not report a target-skill DPS percentage. Say it is heuristic triage.

## Resistance scoring

Default resistance utility is capped at the character's effective cap, usually 75%:

- Count missing resistance until the cap is reached.
- Do not add score for resistance above cap.
- Do not penalize overcap unless the user explicitly asks to avoid wasted suffixes, minimize overcap, or preserve suffix space.
- If the user wants curse/exposure/map-mod safety, treat the requested overcap amount as the temporary cap and state that assumption.

## Single item

1. Collect current context with `collect_context.py`.
2. Select the target skill if the item can affect damage.
3. Parse the copied item with `rank_items.py` to identify likely upside/downside.
4. Open PoB2 and paste the item into the Items tab.
5. Compare against the matching equipped slot. For rings, test both ring slots. For weapons, test the active weapon set.
6. Report the deltas that matter to the current build:
   - damage: selected target skill DPS or named PoB damage field, Full DPS, cast speed, crit, +gem levels, extra damage
   - survival: Effective Hit Pool, max hit by element, life, ES, mana, spirit
   - constraints: attributes, resist caps, reservation/spirit, socket or unique restrictions
7. Say clearly whether the result is exact PoB or heuristic triage.

For ring pairs or other simple equipped-item swaps, prefer the headless PoB path before manual UI:

1. Decode the current `pathOfBuildingExport` to XML.
2. Generate PoB-compatible copied item text for each candidate. If the source is trade2 JSON, use displayed final mod values and do not double-apply catalyst/quality scaling.
3. Resolve the PoB2 runtime with `scripts/bootstrap_pob2.py --json`; prepare it with `--prepare-headless` or `--install` if `headlessReady` is false or no runtime exists.
4. Run the resolved `pobExe` with `POB_HEADLESS_CALC_CONFIG` and `POB_HEADLESS_CALC_OUT`.
5. Confirm baseline target DPS equals the live/saved target DPS.
6. Report `new / old - 1` for the target skill damage and the old/new values for the requested stats.

## Candidate ranking

1. Preserve the user's scoring rule.
2. Select or confirm the target skill if any candidate can affect damage.
3. If the candidates came from trade search and can be pasted as item text, rank them with `rank_items.py`.
4. Pick a small shortlist for exact PoB checks. Usually 3-5 items is enough unless the scores are close.
5. For the final answer, separate "PoB-confirmed" from "script-ranked".

## Analog search

1. Prefer PoB2 `Trade for these items`.
2. Select or confirm the target skill before building offensive weights.
3. Use the current build's active item set and stat weights.
4. For offensive searches, include the selected target skill's DPS or closest PoB damage field plus build-specific stats. For defensive searches, raise Effective Hit Pool and missing max-hit/resist stats.
5. Use instant-buyout-only listed status by default. In trade JSON this is `query.status.option = "securable"`; in PoB popups it is the `Instant Buyout` listed dropdown. Broaden to `available`, `online`, `onlineleague`, or `any` only if the user explicitly asks for non-instant-buyout listings, and label that result clearly.
6. If shell access to trade2 is needed, try `scripts/poe_account_api.py trade-search` with a saved query JSON. It reuses PoB OAuth credentials, forces `--listed securable` by default, and writes rotated refresh tokens back to PoB settings.
   Use `trade-fetch --items-out <items.txt>` to create copied-item text from fetched trade results before running `rank_items.py`.
7. If PoB is not authenticated, use the generated weighted trade URL and ask the user for candidate text or saved results.
8. If authenticated, fetch in PoB Trader and sort by value/price only after PoB's build weight is known.

## poe.ninja build discovery and respec planning

Use this path when the user wants to find builds matching requirements, compare them to the current character, or plan a passive-tree/gear respec.

1. Refresh the user's current context first. Prefer a live account character JSON for current gear and skills; use local summaries only as hints if the live snapshot is unavailable.
2. Infer filters from the request and current snapshot:
   - league slug, usually `runesofaldur`
   - class/ascendancy, for example `Oracle`
   - target skill and delivery context, for example `Grim Pillars via Spell Totem`
   - hard constraints such as `--max-energyshield 0`, minimum mana, required keystones, must-keep uniques, budget, or resist caps
3. If the target skill is unclear and damage matters, list skills with `list_character_skills.py` and ask the user to choose. Do not rank damage patterns against an unspecified skill.
4. Run `scripts/poe_ninja_build_finder.py` to fetch the current poe.ninja snapshot, decode the binary search payload, fetch top matching character details, and produce a JSON report:

```powershell
python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_ninja_build_finder.py" --league runesofaldur --class-name Oracle --skill "Grim Pillars" --delivery "Spell Totem" --max-energyshield 0 --min-level 90 --sort mana --details 12 --current-character "<current>.json" --out "<report>.json" --save-dir "<refs-dir>" --decode-pob-xml
```

5. Inspect the report before recommending:
   - `references`: strongest matching builds with mana, ES, EHP, skills, uniques, keystones, passive IDs, and poe.ninja source URLs
   - `patterns.commonUniqueItems`: frequent gear changes; recommend only if the unique is missing, build-enabling, and compatible with the user's current constraints
   - `patterns.commonKeystones`: likely build-defining keystones, such as Mind Over Matter or Eldritch Battery for mana builds
   - `patterns.missingCommonPassiveIds`: candidate passive IDs to test, not final advice by themselves
   - saved `.xml` files when `--decode-pob-xml` is used
6. Build a small PoB validation queue. Usually test:
   - current XML baseline
   - current gear plus the most common missing mana keystones
   - a conservative reference-tree variant that keeps required attributes/resists/spirit
   - one or two higher-risk variants if the reference cluster clearly uses different uniques
7. Validate through PoB2 before final recommendations. The headless adapter accepts `xmlVariants`:

```json
{
  "target": "Grim Pillars via Spell Totem",
  "xmlPath": "current.xml",
  "xmlVariants": [
    {"name": "current + EB/MoM core", "xmlPath": "variant-eb-mom.xml"},
    {"name": "poe.ninja reference tree", "xmlPath": "reference-tree.xml"}
  ]
}
```

Use `bootstrap_pob2.py --json` first to confirm `headlessReady`. Run PoB with `POB_HEADLESS_CALC_CONFIG` and `POB_HEADLESS_CALC_OUT`, then accept only variants whose baseline target skill and config match the current build.
8. Final recommendations must separate evidence levels:
   - `poe.ninja pattern`: common among matching builds, not exact for the user
   - `PoB2-confirmed`: exact target-skill DPS/stat result in the user's build
   - `needs user decision`: expensive unique swaps, lost resists/attributes/spirit, or alternate target-skill assumptions

## Required result format for damage-affecting recommendations

Include:

- Selected target: `<skill>` plus delivery context such as self-cast, totem, trigger/autocast.
- Validation type: exact PoB or heuristic triage.
- Target damage: always show this as its own visible block. For exact PoB results, include baseline, after, and `% change`. For script-only triage, write `Exact PoB DPS: not calculated`, omit any DPS percentage, and optionally include a clearly labelled heuristic/offensive-affix proxy that is not presented as DPS.
- Character stats: baseline, after, and change for mana, life, ES, spirit, EHP, rarity, attributes, and resistances when available. For mana, include flat mana and `% increased maximum Mana` as inputs plus the final estimated or PoB-confirmed total; do not leave percentage mana only as a raw mod. For every listed stat, subtract the replaced item's contribution before adding the candidate item contribution, and label non-PoB results as estimates when global scaling may apply.
- Trade links and price for each recommended item. Buy-ready links must be instant-buyout-only (`status.option = "securable"`) unless the user explicitly asked otherwise.

## Account-backed current data

Use `scripts/poe_account_api.py` when the user asks for current account character data rather than a public poe.ninja snapshot:

```powershell
python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" status
python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" authorize
python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" characters --realm poe2
python "C:\Users\Hatzy\.codex\skills\poe2-item-evaluator\scripts\poe_account_api.py" character --realm poe2 --name "<character>" --out "<file>.json"
```

Do not print tokens. If the helper refreshes or authorizes a token, mention only that it refreshed/authorized and created a backup path. If refresh returns `invalid_grant`, run `authorize` and ask the user to complete the browser login.

## Current build hygiene

- Do not reuse old `current_character_summary.json` as current truth if the user says their character changed.
- Check the real BuildPlanner folder when a build file matters.
- Use poe.ninja/live profile data when the user asks for current build state and the local snapshot is stale.
- Keep old workspace reports as references, not proof of current state.
