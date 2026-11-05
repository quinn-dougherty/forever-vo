local _, ns = ...
local Queue = ns.Queue

--[[
Slash commands and the addon compartment entry (the addon list button next to
the minimap in modern clients).
]]

ns.PACKS_TO_INSTALL = "Install the Forever Voiceover packs from CurseForge next to ForeverVO: Classic Quests, "
    .. "Classic Endgame, Classic Gossip, Forever Quests, Forever Gossip and Books."

local function Status()
    local packs = ns.Packs:Count()
    local quests, questsMissing, gossip, gossipMissing, sessionSeen, _, books, booksMissing = ns.Capture:Summary()
    ns.Print(format("%d voice %s loaded. Captured: %d quest %s (%d to export), %d gossip %s (%d to export), %d book %s (%d to export); %d %s this session.",
        packs, ns.Util.Plural(packs, "pack"), quests, ns.Util.Plural(quests, "text"), questsMissing,
        gossip, ns.Util.Plural(gossip, "text"), gossipMissing, books, ns.Util.Plural(books, "page"), booksMissing,
        sessionSeen, ns.Util.Plural(sessionSeen, "line")))
    if packs == 0 then
        ns.Print("No voice pack found. " .. ns.PACKS_TO_INSTALL)
    end
end

--- Cycles through the narrator voices the installed packs carry. The pick is
--- kept in a CVar as well as the settings (from when this beta did not read
--- saved variables back).
local function NextNarratorVoice()
    local voices = ns.Packs:NarratorVoices()
    if #voices < 2 then
        ns.Print("no alternate narrator voices are installed; the voice pack has the default narrator only.")
        return
    end
    local current, index = ns.Packs:NarratorVoice(), 1
    for i, voice in ipairs(voices) do
        if voice == current then
            index = i
        end
    end
    local chosen = voices[index % #voices + 1]
    ns.Packs:SetNarratorVoice(chosen)
    ns.Print(format("narrator voice: |cffffd100%s|r (read the options tooltip for what the narrator covers)",
        ns.Packs.NarratorVoiceLabel(chosen)))
end

local commands = {
    pause  = { "Pause playback", function() Queue:Pause() end },
    resume = { "Resume playback", function() Queue:Resume() end },
    skip   = { "Skip the current line", function() Queue:Skip() end },
    clear  = { "Clear the queue", function() Queue:Clear() end },
    replay = { "Replay the current line", function() Queue:Replay() end },
    queue  = { "Toggle the queue panel", function() ns.UI.QueueList:Toggle() end },
    panel  = { "Toggle the panel (audio only when off)", function()
        ns.db.showPanel = not ns.db.showPanel
        ns.UI.TalkingHead:ApplySettings()
    end },
    head   = { "Toggle the speaker's portrait in the panel", function()
        ns.db.showHead = not ns.db.showHead
        ns.UI.TalkingHead:ApplySettings()
    end },
    reset  = { "Reset the talking head position", function()
        ns.UI.TalkingHead.frame:ResetPosition()
    end },
    narrator = { "Switch to the next narrator voice", NextNarratorVoice },
    status = { "Show loaded packs and capture counts", Status },
    export = { "Copy a link that opens a GitHub issue with the unvoiced lines heard since your last export", function() ns.Export:Show() end },
    ["export all"] = { "The same, with every captured unvoiced line, exported before or not", function() ns.Export:Show(true) end },
    report = { "Copy a link that opens a GitHub issue about the line that is playing", function()
        ns.Report:Show(ns.Report:Current())
    end },
    welcome = { "Show the welcome message again", function() ns.Welcome:Show() end },
    debug  = { "Toggle debug messages", function()
        ns.db.debug = not ns.db.debug
        ns.Print("debug", ns.db.debug and "on" or "off")
    end },
}

local function Usage()
    ns.Print("/fvo opens the options. Commands:")
    for name, command in pairs(commands) do
        ns.Print(format("  |cffffd100/fvo %s|r  %s", name, command[1]))
    end
end

SLASH_FOREVERVO1 = "/fvo"
SLASH_FOREVERVO2 = "/forevervo"
SlashCmdList["FOREVERVO"] = function(input)
    local word = strtrim(input or ""):lower()
    if word == "" then
        ns.SettingsPanel:Open()
    elseif commands[word] then
        commands[word][2]()
    else
        Usage()
    end
end

-- Addon compartment ---------------------------------------------------------

function ForeverVO_OnCompartmentClick(_, buttonName, menuButtonFrame)
    if buttonName == "RightButton" and MenuUtil and MenuUtil.CreateContextMenu then
        MenuUtil.CreateContextMenu(menuButtonFrame or UIParent, function(_, root)
            root:CreateTitle("Forever Voiceover")
            root:CreateButton(Queue:IsPaused() and "Resume" or "Pause", function() Queue:TogglePause() end)
            root:CreateButton("Skip current line", function() Queue:Skip() end)
            local current = Queue:Current()
            if current then
                root:CreateButton("Report this line", function() ns.Report:Show(current) end)
            end
            root:CreateButton("Clear queue", function() Queue:Clear() end)
            root:CreateCheckbox("Show the panel", function() return ns.db.showPanel end, function()
                ns.db.showPanel = not ns.db.showPanel
                ns.UI.TalkingHead:ApplySettings()
            end)
            root:CreateCheckbox("Show the talking head", function() return ns.db.showHead end, function()
                ns.db.showHead = not ns.db.showHead
                ns.UI.TalkingHead:ApplySettings()
            end)
            -- In blue when there is something to send, so it stands out of the menu
            local send = ns.Export:SendLabel()
            if ns.Export:PendingCount() > 0 then
                send = "|cff66bbff" .. send .. "|r"
            end
            root:CreateButton(send, function() ns.Export:Show() end)
            root:CreateButton("Options", function() ns.SettingsPanel:Open() end)
        end)
    else
        ns.SettingsPanel:Open()
    end
end

function ForeverVO_OnCompartmentEnter(_, menuButtonFrame)
    GameTooltip:SetOwner(menuButtonFrame, "ANCHOR_LEFT")
    GameTooltip:SetText("Forever Voiceover")
    GameTooltip:AddLine("Version " .. (ns.version or "dev"), 0.6, 0.6, 0.6)
    local size = Queue:Size()
    if size > 0 then
        GameTooltip:AddLine(format("%d %s queued%s", size, ns.Util.Plural(size, "line"), Queue:IsPaused() and " (paused)" or ""), 1, 1, 1)
    end
    local pending = ns.Export:PendingCount()
    if pending > 0 then
        GameTooltip:AddLine(format("You have %d %s that we need!", pending, ns.Util.Plural(pending, "quest")), 1, 1, 1)
    end
    GameTooltip:AddLine("Left-click: options. Right-click: playback menu.", 0.6, 0.6, 0.6)
    GameTooltip:Show()
end

function ForeverVO_OnCompartmentLeave()
    GameTooltip:Hide()
end

ns.OnLogin(function()
    local retired = ns.Packs:Retired()
    if #retired > 0 then
        ns.Print(format("%s %s from an older layout and can be deleted from AddOns once you have the new packs. %s",
            table.concat(retired, ", "), #retired == 1 and "is" or "are", ns.PACKS_TO_INSTALL))
    end
    if ns.Packs:Count() == 0 then
        ns.Print("no voice pack found. " .. ns.PACKS_TO_INSTALL .. " Or generate one with the tools in the repository.")
    end
end)
