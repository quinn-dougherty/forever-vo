-- Standalone Lua 5.1 regression harness: real pack resolution, stubbed client.
format, strfind = string.format, string.find
local name, realm, secret = "Arlen", "Realm", false
function UnitName() return name end
function GetRealmName() return realm end
local ns = { db = {}, Print = function() end, Debug = function() end }
ns.Util = {
    Plain = function(value) if not secret then return value end end,
    PlayerGenderPrefix = function() return "m-" end,
    Tokenize = function(text) return text end,
    TextKey = function() return "hello" end,
    PlayerSexLetter = function() return "m" end,
}
assert(loadfile("ForeverVO/Core/Packs.lua"))("ForeverVO", ns)
local base = { name = "Shared", folder = "Shared", priority = 100,
    quests = { [1] = { a = 2 }, [2] = { a = 3 } },
    gossip = { [1] = { { f = "1-hello", h = "hello", d = 4 } } } }
local own = { name = "Own", folder = "Own", priority = 1000,
    character = "Arlen", realm = "Realm", quests = { [1] = { a = 5 } },
    narratorVoices = { "human-female" },
    gossip = { [1] = { { f = "1-hello", h = "hello", d = 6 } } } }
ns.RegisterPack(base)
ns.RegisterPersonalPack(own)
local function quest(id, folder, duration)
    local path, seconds = ns.Packs:FindQuest(id, "accept")
    assert(path == "Interface\\AddOns\\" .. folder .. "\\Sounds\\Quests\\" .. id .. "-accept.mp3", path)
    assert(seconds == duration)
end
quest(1, "Shared", 2) -- opt-in defaults to off
ns.db.personalVoice = true
quest(1, "Own", 5)
quest(2, "Shared", 3) -- incomplete/missing lines fall back
local path, seconds = ns.Packs:FindGossip(1, "Hello")
assert(path == "Interface\\AddOns\\Own\\Sounds\\Gossip\\1-hello.mp3" and seconds == 6)
assert(#ns.Packs:NarratorVoices() == 2)
name = "Someone Else"
quest(1, "Shared", 2)
name, realm = "Arlen", "Other Realm"
quest(1, "Shared", 2)
realm, secret = "Realm", true
quest(1, "Shared", 2) -- restricted identity must never unlock personal audio
secret = false
ns.db.personalVoice = false
quest(1, "Shared", 2)
assert(#ns.Packs:NarratorVoices() == 1) -- no stale menu cache after toggling
print("Personal pack isolation and fallback passed")
