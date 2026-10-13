-- Forever Voiceover (ForeverVO): voiced quests and NPC dialogue for World of Warcraft: Forever.
--
-- Every file receives the private addon table `ns` through the vararg. Only two
-- globals are exported: `ForeverVO` (the same table, so voice packs can
-- register and users can poke at it) and the compartment click handlers that
-- the TOC requires by name.
local ADDON_NAME, ns = ...

ns.name = ADDON_NAME
ns.version = C_AddOns.GetAddOnMetadata(ADDON_NAME, "Version") or "dev"
ns.mediaPath = "Interface\\AddOns\\" .. ADDON_NAME .. "\\Media\\"
ns.iconTexture = ns.mediaPath .. "Microphone" -- the gold microphone from the project's art, as in the TOC
ns.UI = {}

ForeverVO = ns

-- ---------------------------------------------------------------------------
-- Saved variables
-- ---------------------------------------------------------------------------

ns.defaults = {
    -- Which dialogue to voice
    playAccept = true,
    playProgress = false,
    playComplete = true,
    playGreeting = true,
    playGossip = true,
    gossipRepeat = "gossipOnce",         -- always | gossipOnce | once | questGivers (Events.lua)

    -- Audio
    personalVoice = false,               -- use the installed pack for this character and realm
    soundChannel = "Master",             -- Master | Dialog | SFX | Music | Ambience
    narratorVoice = "narrator",          -- voice for quests given by objects and items (see Packs.lua)
    muteGameDialog = true,
    stopOnClose = false,

    -- Talking head
    showPanel = true,                    -- the whole frame; clear it for audio only
    showHead = true,                     -- the speaker's portrait inside it
    lockHead = false,
    headScale = 1,
    showText = true,
    showQueuePanel = false,
    factionHead = true,
    showMinimapButton = true,
    lockMinimapButton = false,
    minimapAngle = 225,

    -- Data collection for generating new voice lines
    capture = true,
    notifyUnvoiced = true,
    crowdsourceOptOut = false,           -- no login prompt to send lines (Welcome.lua)
    welcomed = false,                    -- the login prompt's introduction was shown (Welcome.lua)
    volunteersAsked = false,             -- the login prompt has shown the call for volunteers (Welcome.lua)
    debug = false,
    devOverlay = false,                  -- quest key and voice on the dialog; a CVar, see UI/Debug.lua
}

ns.charDefaults = {
    paused = false,
}

--- Addon 0.1.7 had two settings for one question, gossipFrequency (greetings
--- and gossip together, with a "never" that the Greetings and Gossip boxes
--- already cover) and the gossipOnce box beside Gossip. Fold them into
--- gossipRepeat, keeping what the player heard before.
local function MigrateGossipRepeat(db, char)
    if db.gossipFrequency ~= nil and db.gossipRepeat == nil then
        local once = db.gossipOnce ~= false
        local frequency = db.gossipFrequency
        if frequency == "oncePerNPC" then
            db.gossipRepeat = "once"
        elseif frequency == "oncePerQuestNPC" and not once then
            db.gossipRepeat = "questGivers"
        else
            db.gossipRepeat = once and "gossipOnce" or "always"
        end
        if frequency == "never" then
            db.playGreeting = false
            db.playGossip = false
        end
    end
    db.gossipFrequency = nil
    db.gossipOnce = nil
    char.seenGossip = nil -- greetings and gossip in one table; seenGreeting starts empty
    -- 0.1.8 kept these across logins; "once" lasts the session now (Events.lua)
    char.seenGossipOnce = nil
    char.seenGreeting = nil
end

local function ApplyDefaults(target, defaults)
    for key, value in pairs(defaults) do
        if target[key] == nil then
            target[key] = type(value) == "table" and CopyTable(value) or value
        end
    end
    return target
end

function ns.Print(...)
    local text = strjoin(" ", tostringall(...))
    DEFAULT_CHAT_FRAME:AddMessage("|cff6ec6ffForever Voiceover|r " .. text)
end

function ns.Debug(...)
    if ns.db and ns.db.debug then
        ns.Print("|cff888888(debug)|r", ...)
    end
end

-- ---------------------------------------------------------------------------
-- Lifecycle
-- ---------------------------------------------------------------------------
-- Modules register an initializer; they run in order once saved variables are
-- available (ADDON_LOADED for this addon), then OnLogin runs at PLAYER_LOGIN.

ns.initializers = {}
ns.loginHandlers = {}

function ns.OnInit(func)
    table.insert(ns.initializers, func)
end

function ns.OnLogin(func)
    table.insert(ns.loginHandlers, func)
end

local loader = CreateFrame("Frame")
loader:RegisterEvent("ADDON_LOADED")
loader:RegisterEvent("PLAYER_LOGIN")
loader:SetScript("OnEvent", function(self, event, arg1)
    if event == "ADDON_LOADED" and arg1 == ADDON_NAME then
        MigrateGossipRepeat(ForeverVODB or {}, ForeverVOCharDB or {})
        ForeverVODB = ApplyDefaults(ForeverVODB or {}, ns.defaults)
        ForeverVOCharDB = ApplyDefaults(ForeverVOCharDB or {}, ns.charDefaults)
        ns.db = ForeverVODB
        ns.char = ForeverVOCharDB
        for _, init in ipairs(ns.initializers) do
            local ok, err = pcall(init)
            if not ok then
                ns.Print("|cffff4040initialization error:|r", err)
            end
        end
        self:UnregisterEvent("ADDON_LOADED")
    elseif event == "PLAYER_LOGIN" then
        for _, handler in ipairs(ns.loginHandlers) do
            local ok, err = pcall(handler)
            if not ok then
                ns.Print("|cffff4040login error:|r", err)
            end
        end
    end
end)
