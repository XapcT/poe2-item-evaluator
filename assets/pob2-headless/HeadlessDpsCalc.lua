-- Headless PoB DPS/stat calculator for Codex item comparison.
-- Runs only from Launch.lua when POB_HEADLESS_CALC_CONFIG is set.

local dkjson = require "dkjson"

local function readAll(path)
	local file = assert(io.open(path, "rb"))
	local body = file:read("*a")
	file:close()
	return body
end

local function writeAll(path, body)
	local file = assert(io.open(path, "wb"))
	file:write(body)
	file:close()
end

local function numberOrNil(value)
	if type(value) == "number" then
		return value
	end
	return nil
end

local statKeys = {
	"TotalDPS",
	"AverageHit",
	"AverageBurstDamage",
	"Speed",
	"TotemPlacementTime",
	"PreEffectiveCritChance",
	"CritChance",
	"CritMultiplier",
	"CombinedAvg",
	"FullDPS",
	"TotalEHP",
	"PhysicalMaximumHitTaken",
	"FireMaximumHitTaken",
	"ColdMaximumHitTaken",
	"LightningMaximumHitTaken",
	"ChaosMaximumHitTaken",
	"Life",
	"LifeUnreserved",
	"Mana",
	"ManaUnreserved",
	"Spirit",
	"SpiritUnreserved",
	"EnergyShield",
	"EnergyShieldRecoveryCap",
	"Str",
	"Dex",
	"Int",
	"FireResist",
	"FireResistOverCap",
	"ColdResist",
	"ColdResistOverCap",
	"LightningResist",
	"LightningResistOverCap",
	"ChaosResist",
	"ChaosResistOverCap",
}

local function collectStats(output)
	local stats = { }
	for _, key in ipairs(statKeys) do
		stats[key] = numberOrNil(output[key])
	end
	if output.SkillDPS then
		stats.SkillDPS = { }
		for index, skill in ipairs(output.SkillDPS) do
			stats.SkillDPS[index] = {
				name = skill.name,
				dps = skill.dps,
				count = skill.count,
				skillPart = skill.skillPart,
				source = skill.source,
				trigger = skill.trigger,
			}
		end
	end
	return stats
end

local function makeEntry(xmlText, label)
	LoadModule("Classes/CompareEntry")
	local entry = new("CompareEntry", xmlText, label)
	if not entry or not entry.calcsTab or not entry.calcsTab.mainOutput then
		error("CompareEntry did not produce calculation output")
	end
	return entry
end

local function equipRaw(entry, slotName, raw)
	local item = new("Item", raw)
	if not item.base then
		error("PoB could not parse item for " .. slotName .. ":\n" .. raw)
	end
	entry.itemsTab:AddItem(item, true)
	entry.itemsTab.slots[slotName]:SetSelItemId(item.id)
	entry.itemsTab:PopulateSlots()
	return {
		id = item.id,
		name = item.name,
		baseName = item.baseName,
		type = item.base and item.base.type,
		raw = item:BuildRaw(),
	}
end

local function calcPair(xmlText, pair)
	local entry = makeEntry(xmlText, pair.name or "candidate")
	local equipped = {
		equipRaw(entry, "Ring 1", pair.ring1Raw),
		equipRaw(entry, "Ring 2", pair.ring2Raw),
	}
	entry:Rebuild()
	local output = entry:GetOutput()
	return {
		name = pair.name,
		ring1 = pair.ring1,
		ring2 = pair.ring2,
		equipped = equipped,
		stats = collectStats(output),
	}
end

local configPath = os.getenv("POB_HEADLESS_CALC_CONFIG")
local outPath = os.getenv("POB_HEADLESS_CALC_OUT") or "headless_dps_result.json"
local configText = readAll(configPath)
local config, pos, err = dkjson.decode(configText)
if err then
	error("Could not decode config JSON: " .. tostring(err))
end

local xmlText = readAll(config.xmlPath)
local baselineEntry = makeEntry(xmlText, "baseline")
local result = {
	target = config.target,
	xmlPath = config.xmlPath,
	baseline = collectStats(baselineEntry:GetOutput()),
	pairs = { },
}

for index, pair in ipairs(config.pairs or { }) do
	result.pairs[index] = calcPair(xmlText, pair)
end

writeAll(outPath, dkjson.encode(result, { indent = true }))

return true
