local _, ns = ...

--[[
Options panel built on the Settings API (Escape > Options > AddOns).
Boolean settings write straight into ns.db; dropdowns and the slider go through
proxy settings so the saved values stay readable strings/numbers.
]]

local SettingsPanel = {}
ns.SettingsPanel = SettingsPanel

-- The third field is the tooltip on that entry of the dropdown
local GOSSIP_REPEAT = {
    { "always",      "Every time",
      "Greetings and gossip are read every time you talk to an NPC." },
    { "gossipOnce",  "Gossip once, greetings always",
      "An NPC's gossip is read the first time you talk to them and not after. Greetings are read every time." },
    { "once",        "Both once per NPC",
      "An NPC's greeting and their gossip are each read the first time and not after." },
    { "questGivers", "Once for quest givers",
      "NPCs with a quest for you have their greeting and gossip read once; everyone else every time." },
}
local SOUND_CHANNELS = { "Master", "Dialog", "SFX", "Music", "Ambience" }

--- Narrator voices the installed packs offer; resolved when the dropdown opens.
local function NarratorChoices()
    local choices = {}
    for _, voice in ipairs(ns.Packs:NarratorVoices()) do
        table.insert(choices, { voice, ns.Packs.NarratorVoiceLabel(voice) })
    end
    return choices
end

local function Checkbox(category, key, name, tooltip, onChange)
    local setting = Settings.RegisterAddOnSetting(category, "FVO_" .. key, key, ns.db, Settings.VarType.Boolean, name, ns.defaults[key])
    if onChange then
        setting:SetValueChangedCallback(function(_, value)
            onChange(value)
        end)
    end
    return setting, Settings.CreateCheckbox(category, setting, tooltip)
end

local function Dropdown(category, key, name, tooltip, choices, onChange)
    -- choices: list of { value, label }, or a function returning one for choices
    -- that depend on the installed voice packs (they register after this panel
    -- is built). The stored value is choices[i][1]; the control sees the index.
    local function Choices()
        return type(choices) == "function" and choices() or choices
    end
    local function GetValue()
        for index, choice in ipairs(Choices()) do
            if choice[1] == ns.db[key] then
                return index
            end
        end
        return 1
    end
    local function SetValue(index)
        local choice = Choices()[index]
        if not choice then
            return
        end
        ns.db[key] = choice[1]
        if onChange then
            onChange(ns.db[key])
        end
    end
    local function GetOptions()
        local container = Settings.CreateControlTextContainer()
        for index, choice in ipairs(Choices()) do
            container:Add(index, choice[2], choice[3])
        end
        return container:GetData()
    end
    local default = 1
    for index, choice in ipairs(Choices()) do
        if choice[1] == ns.defaults[key] then
            default = index
        end
    end
    local setting = Settings.RegisterProxySetting(category, "FVO_" .. key, Settings.VarType.Number, name, default, GetValue, SetValue)
    Settings.CreateDropdown(category, setting, GetOptions, tooltip)
    return setting
end

local function Slider(category, key, name, tooltip, minValue, maxValue, step, formatter, onChange)
    local function GetValue()
        return ns.db[key]
    end
    local function SetValue(value)
        ns.db[key] = value
        if onChange then
            onChange(value)
        end
    end
    local setting = Settings.RegisterProxySetting(category, "FVO_" .. key, Settings.VarType.Number, name, ns.defaults[key], GetValue, SetValue)
    local options = Settings.CreateSliderOptions(minValue, maxValue, step)
    options:SetLabelFormatter(MinimalSliderWithSteppersMixin.Label.Right, formatter)
    Settings.CreateSlider(category, setting, options, tooltip)
    return setting
end

-- The Versions section is a table under a native section header: label left in
-- the settings' gold, version right in white. The labels start where every
-- settings row's label does; the versions line up a gap past the longest
-- label, because a pack's full title is too long for Blizzard's control column
-- (80 px left of the row's centre) and would run under it.
local HEADER_HEIGHT = 45     -- SettingsListSectionHeaderTemplate
local VERSION_LEFT = 37      -- SettingsListElementMixin's label inset
local VERSION_TOP = 7        -- first row as far below the title as a setting's
local VERSION_ROW = 20
local VERSION_GAP = 24
local VERSION_BOTTOM = 4

--- { label, version } for the addon, then each installed voice pack by its
--- full title. Read when the page is shown: the packs register after this panel.
local function VersionRows()
    local rows = { { "Forever Voiceover", ns.version or "dev" } }
    for _, pack in ns.Packs:Iterate() do
        local title = C_AddOns.GetAddOnMetadata(pack.folder, "Title") or pack.name
        table.insert(rows, { title, pack.version or "" })
    end
    if #rows == 1 then
        table.insert(rows, { "Voice packs", "None installed" })
    end
    return rows
end

--- The "Versions" header with the table drawn on its own frame, so the section
--- scrolls and reflows as one list row. Header frames are pooled with every
--- other header in the Settings list, so the table is hidden again whenever the
--- frame is reused for another one.
local function VersionsInitializer()
    local initializer = CreateSettingsListSectionHeaderInitializer("Versions")
    function initializer:GetExtent()
        return HEADER_HEIGHT + VERSION_TOP + #VersionRows() * VERSION_ROW + VERSION_BOTTOM
    end
    local baseInit = initializer.InitFrame
    function initializer:InitFrame(frame)
        if not frame.FVOVersions then
            frame.FVOVersions = { labels = {}, values = {} }
            hooksecurefunc(frame, "Init", function(header, other)
                local parts = header.FVOVersions
                for i = 1, #parts.labels do
                    parts.labels[i]:SetShown(other == initializer)
                    parts.values[i]:SetShown(other == initializer)
                end
            end)
        end
        baseInit(self, frame)
        local parts = frame.FVOVersions
        local rows = VersionRows()
        local widest = 0
        for i, row in ipairs(rows) do
            if not parts.labels[i] then
                local top = -(HEADER_HEIGHT + VERSION_TOP + (i - 1) * VERSION_ROW)
                local label = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
                label:SetJustifyH("LEFT")
                label:SetWordWrap(false)
                label:SetPoint("TOPLEFT", VERSION_LEFT, top)
                local value = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
                value:SetJustifyH("LEFT")
                value:SetWordWrap(false)
                parts.labels[i], parts.values[i] = label, value
            end
            parts.labels[i]:SetText(row[1])
            parts.values[i]:SetText(row[2])
            parts.labels[i]:Show()
            parts.values[i]:Show()
            widest = math.max(widest, parts.labels[i]:GetStringWidth())
        end
        local column = VERSION_LEFT + math.ceil(widest) + VERSION_GAP
        for i = 1, #parts.labels do
            local shown = i <= #rows
            parts.labels[i]:SetShown(shown)
            parts.values[i]:SetShown(shown)
            parts.values[i]:SetPoint("TOPLEFT", column, -(HEADER_HEIGHT + VERSION_TOP + (i - 1) * VERSION_ROW))
        end
    end
    return initializer
end

-- A button alone in a list row, left-aligned with the settings' labels
local BUTTON_ROW_HEIGHT = 26
local BUTTON_HEIGHT = 22
local BUTTON_PADDING = 40    -- beside the text, as Blizzard's panel buttons have
local BUTTON_MIN_WIDTH = 160

--- A list row holding only a button: Blizzard's button row puts a label left
--- and the button in the control column, and the label only repeated the
--- button. The row is a section header with no name, whose frames are pooled
--- with every other header, so the button is hidden when another one reuses it.
--- text is a function, read each time the row is shown. opens, if given, says
--- whether onClick will open a window; when it will not (an export with nothing
--- in it only prints to chat), Options stays open.
local function ButtonRow(layout, text, tooltip, onClick, opens)
    local initializer = CreateSettingsListSectionHeaderInitializer("")
    initializer.fvoButton = true
    function initializer:GetExtent()
        return BUTTON_ROW_HEIGHT
    end
    local baseInit = initializer.InitFrame
    function initializer:InitFrame(frame)
        if not frame.FVOButton then
            local button = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
            button:SetHeight(BUTTON_HEIGHT)
            button:SetPoint("LEFT", VERSION_LEFT, 0)
            button:SetScript("OnLeave", GameTooltip_Hide)
            frame.FVOButton = button
            hooksecurefunc(frame, "Init", function(header, other)
                header.FVOButton:SetShown(other.fvoButton == true)
            end)
        end
        baseInit(self, frame)
        local button = frame.FVOButton
        button:SetText(text())
        button:SetWidth(math.max(BUTTON_MIN_WIDTH, math.ceil(button:GetFontString():GetStringWidth()) + BUTTON_PADDING))
        button:SetScript("OnClick", function()
            -- the window it opens would sit behind Options
            if not opens or opens() then
                _G.SettingsPanel:Close(true)
            end
            onClick()
        end)
        button:SetScript("OnEnter", function(self)
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:SetText(self:GetText(), 1, 0.82, 0)
            GameTooltip:AddLine(tooltip, 1, 1, 1, true)
            GameTooltip:Show()
        end)
        button:Show()
    end
    layout:AddInitializer(initializer)
    return initializer
end

function SettingsPanel:Open()
    if self.category then
        Settings.OpenToCategory(self.category:GetID())
    end
end

ns.OnInit(function()
    local category, layout = Settings.RegisterVerticalLayoutCategory("Forever Voiceover")
    SettingsPanel.category = category
    local head = ns.UI.TalkingHead

    local function RefreshHead()
        head:ApplySettings()
    end

    -- The addon's own page: the ways to help the project, where a player looking
    -- to opt back in lands first, then the versions a bug report asks for
    layout:AddInitializer(CreateSettingsListSectionHeaderInitializer("Contribute"))
    local send = ButtonRow(layout, function() return ns.Export:SendLabel() end,
        "Copy a link that opens a GitHub issue with the quest and NPC lines you have heard since your last export that the voice pack does not have yet. The same as /fvo export.",
        function() ns.Export:Show(false) end,
        function() return ns.Export:HasLines(false) end)
    -- Under Send, indented and small as Blizzard draws a sub-option: it turns
    -- off the login window that offers the same export
    local _, optOut = Checkbox(category, "crowdsourceOptOut", "Opt out of crowdsourcing", "Stop the window at login that offers to send quests and NPC lines the voice pack does not have yet. Clear it to be asked again. /fvo export works either way.")
    optOut:SetParentInitializer(send)
    ButtonRow(layout, function() return "Report Bug" end,
        "Say what went wrong and copy a link that opens a GitHub issue with your addon and voice pack versions filled in.",
        function() ns.Report:ShowGeneral() end)
    ButtonRow(layout, function() return "Call for Volunteers" end,
        "Show the link to the call for volunteers on GitHub: tuning voices for the voice packs and looking after the addon.",
        function() ns.Welcome:ShowVolunteers() end)
    layout:AddInitializer(VersionsInitializer())

    -- What to voice
    local voiced = Settings.RegisterVerticalLayoutSubcategory(category, "What to voice")
    Checkbox(voiced, "playAccept", "Quest offers", "Read the quest text when a quest is offered.")
    Checkbox(voiced, "playComplete", "Quest turn-ins", "Read the reward text when handing in a quest.")
    Checkbox(voiced, "playProgress", "Quest progress", "Read the 'not yet complete' text when returning early. Usually best left off.")
    Checkbox(voiced, "playGreeting", "Greetings", "Read the greeting an NPC with more than one quest opens with, above the list of their quests.")
    Checkbox(voiced, "playGossip", "Gossip", "Read what an NPC says when you talk to them, above the conversation options.")
    Dropdown(voiced, "gossipRepeat", "Repeat greetings and gossip",
        "Whether an NPC's greeting and gossip are read again the next time you talk to them. \"Once\" lasts until you log out. Quest text is always read.",
        GOSSIP_REPEAT)

    -- Audio
    local audio = Settings.RegisterVerticalLayoutSubcategory(category, "Audio")
    Checkbox(audio, "personalVoice", "Use my character name in speech",
        "Use a locally generated personal voice pack for this character and realm. Lines it does not contain use your regular packs. Generate and install the personal pack first, then restart the game. Changing this option clears queued speech.",
        function()
            ns.Queue:Clear()
            ns.Queue:TriggerEvent("OnPacksChanged")
        end)
    local channels = {}
    for _, channel in ipairs(SOUND_CHANNELS) do
        table.insert(channels, { channel, channel })
    end
    Dropdown(audio, "soundChannel", "Sound channel", "Which volume slider controls the voiceovers.", channels)
    Dropdown(audio, "narratorVoice", "Narrator voice",
        "Some lines have no speaker to voice them: quests and chatter from objects, items and signs. A narrator reads those. Voice packs may carry them in other voices; a line the chosen voice does not have keeps the default narrator.",
        NarratorChoices, function(voice) ns.Packs:SetNarratorVoice(voice) end)
    Checkbox(audio, "muteGameDialog", "Mute the game's own NPC voices", "Turn off the Dialog channel while a voiceover plays so the two do not overlap.")
    Checkbox(audio, "stopOnClose", "Stop when the dialog closes", "Stop the current line when you close the quest or gossip window.")

    -- Talking head
    local display = Settings.RegisterVerticalLayoutSubcategory(category, "Talking head")
    Checkbox(display, "showPanel", "Show the panel", "Show the frame with the speaker, their name and the text while a line plays. Clear it for audio only.", RefreshHead)
    Checkbox(display, "showHead", "Show the talking head", "Show the speaker's portrait. Clear it to keep the parchment, the name and the text without the head.", RefreshHead)
    Checkbox(display, "showText", "Show the spoken text", "Display the words being spoken, paged in time with the audio.", RefreshHead)
    Checkbox(display, "lockHead", "Lock position", "Prevent the frame from being dragged.")
    Checkbox(display, "factionHead", "Faction parchment style", "Read quests on the light Alliance or Horde parchment. Clear it for Blizzard's dark talking head panel.", RefreshHead)
    Slider(display, "headScale", "Scale", "Size of the talking head.", 0.5, 1.5, 0.05, function(value) return format("%d%%", value * 100) end, RefreshHead)
    Checkbox(display, "showMinimapButton", "Minimap button", "Show the button on the minimap. Left-click for options, right-click for playback controls, drag to move.", function() ns.UI.MinimapButton:ApplySettings() end)
    Checkbox(display, "lockMinimapButton", "Lock minimap button", "Prevent the minimap button from being dragged.")

    -- Data
    local data = Settings.RegisterVerticalLayoutSubcategory(category, "Voice Pack Debug")
    Checkbox(data, "capture", "Record lines that have no audio", "Save every quest and gossip text you see so new voice lines can be generated from them.")
    Checkbox(data, "notifyUnvoiced", "Say when a line has no voice", "Print a chat line whenever a quest or gossip text has no audio yet. The line is saved for your next export either way.")
    Checkbox(data, "debug", "Debug messages", "Print matching details to chat.")
    Checkbox(data, "devOverlay", "Developer overlay",
        "Show a window attached to the quest or gossip frame with the quest file name, the voice, and the speaker's display and model ids. For people working on the voice pack.",
        function()
            ns.UI.Debug:Apply()
        end)

    Settings.RegisterAddOnCategory(category)
end)
