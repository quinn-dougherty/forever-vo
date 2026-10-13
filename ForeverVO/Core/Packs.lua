local _, ns = ...
local Util = ns.Util

--[[
Voice packs are separate addons that depend on ForeverVO and call
ForeverVO.RegisterPack(pack) with a table of this shape:

  {
    name = "Forever", version = "0.1", priority = 100,
    folder = "ForeverVO_Data",          -- Interface\AddOns\<folder>\Sounds\...
    quests = {
      [questID] = { a = 5.2, p = 2.0, c = 3.1, g = "a", npc = 288,
                    cP = { { d = 1.1 }, { d = 1.4, n = true }, { d = 0.6 } } },
      -- a/p/c: duration in seconds of accept/progress/complete audio (absent = no file)
      -- g: which of a/p/c exist as m-/f- variants, because $G branches per line
      --    ("a" = only the accept text branches). `true` means all of them, as
      --    packs built before this wrote it.
      -- va/vp/vc: the voice that rendered that event's file (scourge-male-dark).
      --    The same string names the clip. Absent on packs built before it
      --    was recorded. A line played from Narrator/<voice>/ is that voice
      --    instead, since va is the main file's.
      -- wa/wp/wc: the pipeline still wants this event captured again, by a
      --    reader whose sex letter (m/f) is in the string: "f" when only a
      --    male character has read a line the client resolved a $G branch
      --    out of, "mf" when nobody knows who read it. The addon exports
      --    such a line even though it is voiced (Capture.lua, Export.lua).
      -- sa/sp/sc: { m = 2.1 } or { f = 2.1 }, the event read in the speaker's
      --    other sex, for a creature ID met as both (Peacekeepers, guards).
      --    File Sounds\Quests\Sex\<m|f>\<base>.mp3; played when the unit
      --    in the dialog is that sex. Parts are not doubled.
      -- xa/xp/xc: { [speakerKey] = { d = 2.1, v = "human-female" } | true | false },
      --    the event's other speakers, for a quest that more than one creature
      --    takes (Dokimi and Marcy Baker share the crates: #948). A record is
      --    that speaker's own file, Sounds\Quests\Speaker\<key>\<base>.mp3,
      --    played when they are in front of the player; true says the line's
      --    own file is in their voice, false that theirs is not made yet.
      --    Every speaker the pipeline knows is listed, so a capture from one
      --    that is not goes out with the next export (QuestSpeakerKnown).
      -- ha/hp/hc: Util.TextKey of the text that event was voiced from, or
      --    "<male>,<female>" when it has a $G branch. A live text that keys
      --    differently is exported although it is voiced (QuestTextMatches).
      --    Absent on packs built before it, which never count as a mismatch.
      -- npc: quest giver speaker key (creature ID, negative for game objects);
      --    absent when an item starts the quest. ender: the turn-in speaker,
      --    only when it differs from the giver. The addon uses them for a text
      --    the client leaves unattributed (an item-started or shared quest, a
      --    turn-in at a game object); packs built before ender existed carry
      --    the first speaker of any event as npc.
      -- aP/pP/cP: the parts of a line that mixes the speaker and the narrator,
      --    in reading order, each with its duration; n marks the narrator's
      --    (a <stage direction>). Files are <questID>-p<i>-<event>.mp3.
    },
    gossip = {
      [speakerKey] = {
        { f = "288-1a2b3c4d", h = "1a2b3c4d", t = "original text", d = 4.5, v = "human-male", g = true,
          n = { [1] = 4.7 },      -- duration per alternate narrator voice
          s = { f = 4.2 },        -- the speaker's other sex, as sa above, under Gossip\Sex\f
          P = { { d = 2.0 }, { d = 1.1, n = true } },   -- parts, as aP above; files <f>-p<i> with
          nP = { [1] = { [2] = 1.2 } } },               -- the speaker before the hash: 288-p2-1a2b3c4d
      },
    },
    npcs = { [speakerKey] = "Name" },
    models = { [speakerKey] = 119376 },
      -- the model file a speaker's voice was cast from, for speakers the
      -- pipeline knows only by model (Forever's own NPCs have no display ID);
      -- a player who sees another model exports the NPC record (Capture.lua)
    sexes = { [speakerKey] = "mf" },
      -- every sex the pipeline knows a speaker as ("m", "f", "mf", ""); "mf"
      -- means its lines have the other sex's file too (sa/sp/sc, gossip s).
      -- A player who meets a sex not listed exports the NPC record (Capture.lua)
    maps = { [speakerKey] = 1413 },
      -- the map each speaker of a quest with several (xa/xp/xc) was met on,
      -- which picks one when the client keeps the unit's identity secret
    books = {
      ["1a2b3c4d"] = { d = 12.3, t = "original page text", s = "original page text",
                       b = "A Letter to Morgan", p = 1, x = "5e6f7a8b", g = true,
                       n = { [1] = 12.9 } },
      -- one entry per page of readable text (a book in the bags, a plaque or
      -- lectern in the world: both open ItemTextFrame), keyed by
      -- Util.TextKey of the page. Always the narrator. t: the raw page, which
      -- FindBook matches and which still holds $g. s: the prose the talking
      -- head shows for a page that is not on screen ($ codes spoken, HTML read
      -- as sentences, $g left for ResolveGender). b: the title the client
      -- shows (ItemTextGetItem), which bounds the fuzzy fallback to that
      -- book's own pages; p: its page number; x: the key of the voiced page
      -- after it, which Play reads on to without the player turning (the
      -- client hands an addon only the page on screen); g and n as on a
      -- gossip entry. Files are Sounds\Books\<key>-page.mp3.
    },
    narratorVoices = { "skyborne-female" },
    narrator = {
      [questID] = { [1] = { a = 5.4, c = 3.3, cP = { [2] = 1.2 } }, [2] = { a = 5.1 } },
      -- the same quest read in each alternate narrator voice, indexed into
      -- narratorVoices, with that recording's own durations; aP/pP/cP hold the
      -- durations of the narrator's parts of a mixed line, by part index
    },
  }

Files live at Sounds\Quests\<questID>-accept.mp3 (with m-/f- prefix when g is
set) and Sounds\Gossip\<f>.mp3. Higher priority packs are consulted first, so a
pack of new or revised lines can sit on top of a base pack. The released packs
all sit at 100 and hold no line twice: Classic's quests (to level 40, and from
41), Classic's gossip, Forever's own quests and gossip, and books.

Lines with no speaker to clone - quests and gossip from objects and items - are
read by a narrator. Packs may carry those lines again in other voices, under
Sounds\<Quests|Gossip>\Narrator\<voice>\, and the player picks one
(ns.db.narratorVoice); a line the chosen voice has no recording for falls back
to the default narrator. Quest alternates are in the narrator table, gossip
alternates in each entry's n field, both indexed into narratorVoices.

A line that mixes the speaker and the narrator - "Hmm... <Jorgen looks up at
you.> All right." - has parts: the speaker's words in their voice and each
stage direction in the narrator's, played back to back by the queue, with the
narrator's parts swapped for the chosen voice where the pack carries them. The
whole-line file, with the stage directions left out, is still there for addons
that predate parts; a line that is only a stage direction has parts and no
whole-line file.
]]

local Packs = {
    list = {},
    byName = {},
}
ns.Packs = Packs

local FUZZY_THRESHOLD = 0.6

--- Whether a gossip entry is the live text exactly (`hash`, Util.TextKey of it
--- tokenised). An entry with a $g branch (`g`) is keyed on its raw text, which
--- never equals what the client shows, so it is also tried resolved for the
--- player's sex. Otherwise the fuzzy match took another sex's captured reading,
--- one word away, over it: Brock Stoneseeker said "her" to men (#365).
local function GossipExact(entry, hash, letter)
    if entry.h == hash then
        return true
    end
    return entry.g and letter ~= nil and type(entry.t) == "string"
        and Util.TextKey(Util.ResolveGender(entry.t, letter)) == hash or false
end
local QUEST_FIELD = { accept = "a", progress = "p", complete = "c" }

--- Packs of the layout before 2026-10-06 (Base, Base Endgame and the delta), which
--- the six that replaced them make redundant. A copy left in AddOns still
--- registers, below every other pack, so it fills only what the player has not
--- installed the new packs for, and it is named apart: the old Base Endgame was
--- "Classic Endgame" too, and the first to register would have shut out the other.
local RETIRED_FOLDERS = {
    ForeverVO_Data_Base = true,
    ForeverVO_Data_Base_Endgame = true,
    ForeverVO_Data_Forever = true,
}
local RETIRED_PRIORITY = -1000

function ns.RegisterPack(pack)
    assert(type(pack) == "table" and pack.name and pack.folder, "ForeverVO.RegisterPack: pack needs name and folder")
    if RETIRED_FOLDERS[pack.folder] then
        pack.name = pack.name .. " (retired)"
        pack.priority = RETIRED_PRIORITY
        pack.retired = true
    end
    if Packs.byName[pack.name] then
        ns.Print(format("voice pack %q registered twice, ignoring the second copy", pack.name))
        return
    end
    pack.priority = pack.priority or 0
    pack.quests = pack.quests or {}
    pack.gossip = pack.gossip or {}
    pack.npcs = pack.npcs or {}
    pack.models = pack.models or {}
    pack.sexes = pack.sexes or {}
    pack.maps = pack.maps or {}
    pack.narrator = pack.narrator or {}
    pack.narratorVoices = pack.narratorVoices or {}
    pack.books = pack.books or {}
    pack.bookTitles = {}
    for hash, entry in pairs(pack.books) do
        if entry.b then
            local pages = pack.bookTitles[entry.b] or {}
            pack.bookTitles[entry.b] = pages
            table.insert(pages, hash)
        end
    end
    pack.nameToKey = {}
    for key, name in pairs(pack.npcs) do
        pack.nameToKey[name] = pack.nameToKey[name] or key
    end
    Packs.byName[pack.name] = pack
    table.insert(Packs.list, pack)
    table.sort(Packs.list, function(a, b)
        if a.priority ~= b.priority then
            return a.priority > b.priority
        end
        return a.name < b.name
    end)
    ns.Debug("registered pack", pack.name, pack.version or "")
    if ns.Queue then
        ns.Queue:TriggerEvent("OnPacksChanged")
    end
end

--- The model file the highest-priority pack that says cast this speaker's
--- voice from, or nil when no pack was cast by model (or predates recording it).
function Packs:SpeakerModel(key)
    key = tonumber(key)
    if not key then
        return nil
    end
    for _, pack in self:Iterate() do
        local model = pack.models[key]
        if model then
            return model
        end
    end
    return nil
end

--- Every sex the packs know a speaker as ("m", "f", "mf", ""), or nil when no
--- installed pack records it (packs built before pack.sexes record none).
function Packs:SpeakerSexes(key)
    key = tonumber(key)
    if not key then
        return nil
    end
    for _, pack in self:Iterate() do
        local sexes = pack.sexes[key]
        if sexes then
            return sexes
        end
    end
    return nil
end

function Packs:Count()
    local count = 0
    for _ in self:Iterate() do count = count + 1 end
    return count
end

-- A separate entry point makes a personal pack inert on older addon versions:
-- its generated loader never calls RegisterPack unless this guard exists.
function ns.RegisterPersonalPack(pack)
    assert(type(pack.character) == "string" and pack.character ~= ""
        and type(pack.realm) == "string" and pack.realm ~= "",
        "ForeverVO.RegisterPersonalPack: character and realm are required")
    pack.personal = true
    ns.RegisterPack(pack)
end

function Packs:Iterate()
    local index = 0
    return function()
        while true do
            index = index + 1
            local pack = self.list[index]
            if not pack then return nil end
            if not pack.personal or (ns.db and ns.db.personalVoice
                and Util.Plain(UnitName("player")) == pack.character
                and Util.Plain(GetRealmName()) == pack.realm) then
                return index, pack
            end
        end
    end
end

--- The folders of retired packs still installed, to tell the player to delete them.
function Packs:Retired()
    local folders = {}
    for _, pack in self:Iterate() do
        if pack.retired then
            table.insert(folders, pack.folder)
        end
    end
    return folders
end

--- "<name> <version>" for each installed pack, highest priority first.
function Packs:Versions()
    local names = {}
    for _, pack in self:Iterate() do
        table.insert(names, pack.version and format("%s %s", pack.name, pack.version) or pack.name)
    end
    return names
end

local function SoundPath(pack, subfolder, base)
    return format("Interface\\AddOns\\%s\\Sounds\\%s\\%s.mp3", pack.folder, subfolder, base)
end

-- ---------------------------------------------------------------------------
-- Narrator voice
-- ---------------------------------------------------------------------------

local DEFAULT_NARRATOR = "narrator"
local RACE_LABELS = {
    human = "Human", dwarf = "Dwarf", nightelf = "Night elf", orc = "Orc", troll = "Troll",
    tauren = "Tauren", gnome = "Gnome", goblin = "Goblin", bloodelf = "Blood elf",
    scourge = "Undead", skyborne = "Skyborne", draenei = "Draenei",
}

Packs.defaultNarrator = DEFAULT_NARRATOR

--- The voices the player may pick for narrated quests: the default first, then
--- every alternate the installed packs carry.
function Packs:NarratorVoices()
    local voices, seen = { DEFAULT_NARRATOR }, { [DEFAULT_NARRATOR] = true }
    for _, pack in self:Iterate() do
        for _, voice in ipairs(pack.narratorVoices) do
            if not seen[voice] then
                seen[voice] = true
                table.insert(voices, voice)
            end
        end
    end
    return voices
end

--- "dwarf-male" -> "Dwarf male". Unknown races keep their own name, capitalised.
--- The default narrator is cloned from the skyborne male clip (narrator has no
--- clip of its own; [voices.fallbacks] in configs/voices.toml sends it there; it
--- was human male until 2026-10-06), so the menu names the voice a player will
--- hear rather than the role.
function Packs.NarratorVoiceLabel(voice)
    if voice == DEFAULT_NARRATOR then
        return "Skyborne male"
    end
    local race, gender = voice:match("^(.+)%-(%a+)$")
    if not race then
        return voice
    end
    return format("%s %s", RACE_LABELS[race] or (race:sub(1, 1):upper() .. race:sub(2)), gender)
end

function Packs:NarratorVoice()
    local voice = ns.db.narratorVoice or DEFAULT_NARRATOR
    for _, available in ipairs(self:NarratorVoices()) do
        if available == voice then
            return voice
        end
    end
    return DEFAULT_NARRATOR -- the pack that carried it is no longer installed
end

--- Picks the narrator voice. It lives in the settings alone: until 0.1.9 it was
--- also an addon CVar, from when this beta did not read saved variables back,
--- but the client does not keep addon CVars across a logout, and reading the
--- CVar's default back at login reset every pick to the human male (#887).
function Packs:SetNarratorVoice(voice)
    ns.db.narratorVoice = voice
end

--- Where a voice sits in this pack's narratorVoices, or nil when it has none.
local function NarratorIndex(pack, voice)
    for index, name in ipairs(pack.narratorVoices) do
        if name == voice then
            return index
        end
    end
end

--- The record a pack holds for a narrated quest in the chosen voice, or nil.
local function NarratorRecord(pack, questID, voice)
    local alternates = pack.narrator[questID]
    local index = alternates and NarratorIndex(pack, voice)
    return index and alternates[index] or nil
end

--- The files of a line that mixes the speaker and the narrator, in reading
--- order: { path, duration } per part, the narrator's parts in the chosen
--- narrator voice where `alternates` (part index -> duration) carries them.
--- Returns the list and the summed duration.
local function ResolveParts(pack, subfolder, base, parts, alternates, voice)
    local head, last = base:match("^(.*)%-([^%-]+)$")
    local resolved, total = {}, 0
    for index, part in ipairs(parts) do
        local name = format("%s-p%d-%s", head, index, last)
        local path, seconds = SoundPath(pack, subfolder, name), part.d or 0
        if part.n and alternates and alternates[index] then
            path, seconds = SoundPath(pack, subfolder .. "\\Narrator\\" .. voice, name), alternates[index]
        end
        resolved[index] = { path = path, duration = seconds }
        total = total + seconds
    end
    return resolved, total
end

--- The recording of a line in the speaker's other sex, when the pack has one
--- (`other`: { m|f = duration }) and the speaker in front of the player is
--- that sex: path, duration and the voice it was read in. The voice is the
--- recorded one's race with the other gender, which is how the pipeline picks
--- it (generate.Item.sex_alternate).
local function OtherSex(pack, subfolder, base, other, sex, recorded)
    local seconds = sex and type(other) == "table" and other[sex]
    if not seconds then
        return nil
    end
    local race = type(recorded) == "string" and recorded:match("^([^%-]+)%-") or nil
    local voice = race and format("%s-%s", race, sex == "m" and "male" or "female") or nil
    return SoundPath(pack, subfolder .. "\\Sex\\" .. sex, base), seconds, voice
end

--- The record of a quest event's other speaker (`others`: xa/xp/xc) when that
--- speaker has a file of its own: path, duration and voice.
local function OtherSpeaker(pack, base, others, speakerKey)
    local other = speakerKey and type(others) == "table" and others[speakerKey]
    if type(other) ~= "table" or not other.d then
        return nil
    end
    return SoundPath(pack, "Quests\\Speaker\\" .. speakerKey, base), other.d, other.v
end

--- Finds the audio for a quest event. Returns path, duration, pack, parts,
--- voice or nil. `voice` is the archetype that rendered the file (va/vp/vc),
--- or the narrator voice when that recording is the one playing. `parts` is
--- set for a line the queue plays as a sequence (see ResolveParts); `path`
--- then names the whole-line file where one exists, or the first part.
--- `sex` ("m"/"f", optional) is the speaker's, from the unit in the dialog: a
--- creature met as both sexes has the line in each (sa/sp/sc). `speakerKey`
--- (optional) is whoever says it: a quest handed in to more than one NPC has
--- the line in each one's voice (xa/xp/xc), a whole line even where the
--- line's own file has parts.
---@param questID number
---@param event "accept"|"progress"|"complete"
function Packs:FindQuest(questID, event, sex, speakerKey)
    local field = QUEST_FIELD[event]
    if not questID or not field then
        return nil
    end
    for _, pack in self:Iterate() do
        local entry = pack.quests[questID]
        local parts = entry and entry[field .. "P"]
        if entry and (entry[field] or parts) then
            local base = format("%d-%s", questID, event)
            -- g lists the events whose text branches on $G: a quest can branch on
            -- its greeting and not on its turn-in, and prefixing an event that was
            -- written unprefixed asks for a file that does not exist. Packs built
            -- before this carry `true`, which still means every event.
            if entry.g == true or (type(entry.g) == "string" and strfind(entry.g, field, 1, true)) then
                base = Util.PlayerGenderPrefix() .. base
            end
            local voice = self:NarratorVoice()
            local recorded = entry["v" .. field]
            local alternate = voice ~= DEFAULT_NARRATOR and NarratorRecord(pack, questID, voice) or nil
            local speakerPath, speakerSeconds, speakerVoice = OtherSpeaker(pack, base, entry["x" .. field], speakerKey)
            if speakerPath then
                return speakerPath, speakerSeconds, pack, nil, speakerVoice
            end
            if parts then
                local resolved, total = ResolveParts(pack, "Quests", base, parts, alternate and alternate[field .. "P"], voice)
                local path = entry[field] and SoundPath(pack, "Quests", base) or resolved[1].path
                return path, total, pack, resolved, recorded
            end
            if alternate and alternate[field] then
                return SoundPath(pack, "Quests\\Narrator\\" .. voice, base), alternate[field], pack, nil, voice
            end
            local otherPath, otherSeconds, otherVoice = OtherSex(pack, "Quests", base, entry["s" .. field], sex, recorded)
            if otherPath then
                return otherPath, otherSeconds, pack, nil, otherVoice
            end
            return SoundPath(pack, "Quests", base), entry[field], pack, nil, recorded
        end
    end
end

--- True when the pack that voices the quest event asks for it to be captured
--- again by a character of the player's sex (the wa/wp/wc fields): the
--- pipeline has only one gender's reading of a line the client resolves a
--- $G branch out of, or none it can trust, and this player can supply it.
---@param pack table the pack FindQuest found the event in
---@param letter string? the reader's sex ("m"/"f"), the player's by default
function Packs:QuestWanted(pack, questID, event, letter)
    local field = QUEST_FIELD[event]
    local entry = pack and field and pack.quests[questID]
    local wanted = entry and entry["w" .. field]
    letter = letter or Util.PlayerSexLetter()
    return type(wanted) == "string" and letter ~= nil and strfind(wanted, letter, 1, true) ~= nil
end

--- False when the pack records which text it voiced an event from (ha/hp/hc)
--- and the live text is not that text, so the capture is exported to replace
--- it. The key is tried with the player's class and race tokenised and left as
--- words, since a pack line may hold either: "the $c" is a class, but the
--- "skyborne" a Skyborne reads in Forever's own text is the word.
---@param pack table the pack FindQuest found the event in
---@param text string the live text, as the client rendered it
function Packs:QuestTextMatches(pack, questID, event, text)
    local field = QUEST_FIELD[event]
    local entry = pack and field and pack.quests[questID]
    local keys = entry and entry["h" .. field]
    if type(keys) ~= "string" then
        return true
    end
    local className, raceName = UnitClass("player"), UnitRace("player")
    for _, class in ipairs({ className or "", "" }) do
        for _, race in ipairs({ raceName or "", "" }) do
            local key = Util.TextKey(text, nil, class, race)
            if strfind("," .. keys .. ",", "," .. key .. ",", 1, true) then
                return true
            end
        end
    end
    return false
end

--- The speaker a pack records for a quest event: the giver, or for a
--- progress or complete text the turn-in speaker where the pack records one.
local function RecordedSpeaker(entry, event)
    local turnIn = event == "progress" or event == "complete"
    return entry and ((turnIn and entry.ender) or entry.npc)
end

--- The map a speaker was met on, from any pack (pack.maps).
function Packs:SpeakerMap(key)
    for _, pack in self:Iterate() do
        local mapID = pack.maps[key]
        if mapID then
            return mapID
        end
    end
end

--- Speaker key recorded by any pack for the quest: the giver, or for a
--- progress or complete text the turn-in speaker where the pack records one.
--- Where the event has other speakers (xa/xp/xc), the one met on the map the
--- player stands on, if only one was: the client may keep the unit in front
--- of the player secret, and Marcy Baker took Dokimi's face and voice (#948).
function Packs:QuestGiver(questID, event)
    local field = QUEST_FIELD[event]
    for _, pack in self:Iterate() do
        local entry = pack.quests[questID]
        local key = RecordedSpeaker(entry, event)
        if key then
            local others = field and entry["x" .. field]
            local mapID = type(others) == "table" and C_Map.GetBestMapForUnit("player")
            if mapID then
                local found = self:SpeakerMap(key) == mapID and key or nil
                for candidate in pairs(others) do
                    if self:SpeakerMap(candidate) == mapID then
                        if found then
                            return key -- two met here: nothing to tell them apart
                        end
                        found = candidate
                    end
                end
                return found or key
            end
            return key
        end
    end
end

--- False when the pack that voices a quest event records its speakers and
--- `speakerKey` (a creature) is not among them: the line goes out with the
--- next export although it is voiced, so the pipeline learns of the speaker
--- and reads the line in their voice too (#948).
---@param pack table the pack FindQuest found the event in
function Packs:QuestSpeakerKnown(pack, questID, event, speakerKey)
    local field = QUEST_FIELD[event]
    local entry = pack and field and pack.quests[questID]
    local recorded = RecordedSpeaker(entry, event)
    speakerKey = tonumber(speakerKey)
    if not recorded or not speakerKey or speakerKey < 0 or recorded < 0 or speakerKey == recorded then
        return true
    end
    local others = entry["x" .. field]
    return type(others) == "table" and others[speakerKey] ~= nil
end

function Packs:SpeakerName(key)
    if not key then
        return nil
    end
    for _, pack in self:Iterate() do
        local name = pack.npcs[key]
        if name then
            return name
        end
    end
end

function Packs:SpeakerKeyByName(name)
    if not name then
        return nil
    end
    for _, pack in self:Iterate() do
        local key = pack.nameToKey[name]
        if key then
            return key
        end
    end
end

--- Finds gossip/greeting audio for a speaker. Exact hash match first, then the
--- most similar text above the fuzzy threshold. Returns path, duration, pack,
--- parts, voice. `voice` is the archetype on the entry, or the narrator voice
--- when that recording is the one playing. `sex` as for FindQuest.
---@param speakerKey number
---@param text string
function Packs:FindGossip(speakerKey, text, sex)
    if not speakerKey or not text then
        return nil
    end
    -- Pack text carries the placeholders; the client has already expanded them
    -- in what we were handed. Tokenise once so both the hash and the fuzzy
    -- word sets compare like with like whoever is reading.
    local tokenized = Util.Tokenize(text)
    local hash = Util.TextKey(tokenized)
    local letter = Util.PlayerSexLetter()
    local bestEntry, bestPack, bestScore

    for _, pack in self:Iterate() do
        local entries = pack.gossip[speakerKey]
        if entries then
            for _, entry in ipairs(entries) do
                if GossipExact(entry, hash, letter) then
                    bestEntry, bestPack, bestScore = entry, pack, 1
                    break
                end
            end
            if bestScore == 1 then
                break
            end
            for _, entry in ipairs(entries) do
                local score = Util.Similarity(tokenized, entry.t or "")
                if score >= FUZZY_THRESHOLD and (not bestScore or score > bestScore) then
                    bestEntry, bestPack, bestScore = entry, pack, score
                end
            end
        end
    end

    if not bestEntry then
        return nil
    end
    local base = bestEntry.f
    if bestEntry.g then
        base = Util.PlayerGenderPrefix() .. base
    end
    ns.Debug(format("gossip match %.2f for %s", bestScore, base))
    local voice = self:NarratorVoice()
    local index = voice ~= DEFAULT_NARRATOR and NarratorIndex(bestPack, voice) or nil
    if bestEntry.P then
        local resolved, total = ResolveParts(bestPack, "Gossip", base, bestEntry.P,
            index and bestEntry.nP and bestEntry.nP[index], voice)
        local path = bestEntry.d and SoundPath(bestPack, "Gossip", base) or resolved[1].path
        return path, total, bestPack, resolved, bestEntry.v
    end
    if index and bestEntry.n then
        local seconds = bestEntry.n[index]
        if seconds then
            return SoundPath(bestPack, "Gossip\\Narrator\\" .. voice, base), seconds, bestPack, nil, voice
        end
    end
    local otherPath, otherSeconds, otherVoice = OtherSex(bestPack, "Gossip", base, bestEntry.s, sex, bestEntry.v)
    if otherPath then
        return otherPath, otherSeconds, bestPack, nil, otherVoice
    end
    return SoundPath(bestPack, "Gossip", base), bestEntry.d, bestPack, nil, bestEntry.v
end

--- Finds the narration of one page of readable text. Exact hash match in any
--- pack first, then the most similar page of the same title above the fuzzy
--- threshold (a title with no pages in a pack is skipped, so a miss never
--- walks every book). Returns path, duration, pack, nil, voice, entry, score (1 for
--- an exact match; below it, the page on screen is worded differently).
---@param title string|nil
---@param text string
function Packs:FindBook(title, text)
    if not text then
        return nil
    end
    local tokenized = Util.Tokenize(text)
    local hash = Util.TextKey(tokenized)
    local letter = Util.PlayerSexLetter()
    local bestEntry, bestHash, bestPack, bestScore

    for _, pack in self:Iterate() do
        local entry = pack.books[hash]
        if entry then
            bestEntry, bestHash, bestPack, bestScore = entry, hash, pack, 1
            break
        end
        local pages = title and pack.bookTitles[title]
        if pages then
            for _, key in ipairs(pages) do
                local page = pack.books[key]
                -- A $g page is keyed on its raw text, as gossip is (GossipExact).
                if page.g and letter and type(page.t) == "string"
                    and Util.TextKey(Util.ResolveGender(page.t, letter)) == hash then
                    bestEntry, bestHash, bestPack, bestScore = page, key, pack, 1
                    break
                end
                local score = Util.Similarity(tokenized, page.t or "")
                if score >= FUZZY_THRESHOLD and (not bestScore or score > bestScore) then
                    bestEntry, bestHash, bestPack, bestScore = page, key, pack, score
                end
            end
            if bestScore == 1 then
                break
            end
        end
    end

    if not bestEntry then
        return nil
    end
    ns.Debug(format("book match %.2f for %s", bestScore, bestHash))
    local path, duration, voice = self:BookSound(bestPack, bestHash, bestEntry)
    return path, duration, bestPack, nil, voice, bestEntry, bestScore
end

--- The file for one page of a pack, in the chosen narrator voice where the pack
--- has it: path, duration, voice.
function Packs:BookSound(pack, hash, entry)
    local base = hash .. "-page"
    if entry.g then
        base = Util.PlayerGenderPrefix() .. base
    end
    local voice = self:NarratorVoice()
    local index = voice ~= DEFAULT_NARRATOR and NarratorIndex(pack, voice) or nil
    if index and entry.n then
        local seconds = entry.n[index]
        if seconds then
            return SoundPath(pack, "Books\\Narrator\\" .. voice, base), seconds, voice
        end
    end
    return SoundPath(pack, "Books", base), entry.d, DEFAULT_NARRATOR
end

--- The page a pack lists after this one (its x), looked up by key in every pack
--- in priority order: key, entry, pack, or nil where the voiced chain ends.
function Packs:NextBookPage(entry)
    local hash = entry and entry.x
    if not hash then
        return nil
    end
    for _, pack in self:Iterate() do
        local page = pack.books[hash]
        if page then
            return hash, page, pack
        end
    end
    return nil
end

--- The file base, the voice the queue plays, and the archetype recorded on the
--- entry (va/vp/vc). The last two differ when a narrator recording is what
--- plays. `base` is still returned for a quest the packs do not have.
function Packs:DebugQuest(questID, event)
    local field = QUEST_FIELD[event]
    if not questID or not field then
        return nil
    end
    local base = format("%d-%s", questID, event)
    local _, _, _, _, playing = self:FindQuest(questID, event)
    local recorded
    for _, pack in self:Iterate() do
        local entry = pack.quests and pack.quests[questID]
        if entry and (entry[field] or entry[field .. "P"]) then
            if entry.g == true or (type(entry.g) == "string" and strfind(entry.g, field, 1, true)) then
                base = Util.PlayerGenderPrefix() .. base
            end
            recorded = entry["v" .. field]
            break
        end
    end
    return base, playing, recorded
end

--- Playing voice and the archetype on the matched gossip entry. Same as
--- FindGossip's voice except when the narrator recording replaced it.
function Packs:DebugGossip(speakerKey, text)
    if not speakerKey or not text or text == "" then
        return nil, nil
    end
    local _, _, pack, _, playing = self:FindGossip(speakerKey, text)
    local recorded = playing
    if playing == self:NarratorVoice() and pack and pack.gossip and pack.gossip[speakerKey] then
        local tokenized = Util.Tokenize(text)
        local hash = Util.TextKey(tokenized)
        local best, bestScore
        local letter = Util.PlayerSexLetter()
        for _, entry in ipairs(pack.gossip[speakerKey]) do
            if GossipExact(entry, hash, letter) then
                return playing, entry.v
            end
            local score = Util.Similarity(tokenized, entry.t or "")
            if score >= FUZZY_THRESHOLD and (not bestScore or score > bestScore) then
                best, bestScore = entry, score
            end
        end
        if best then
            recorded = best.v
        end
    end
    return playing, recorded
end
