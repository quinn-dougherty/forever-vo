local _, ns = ...
local Util, Packs = ns.Util, ns.Packs

--[[
Developer overlay: a small window attached to the quest or gossip frame,
showing the quest file base, the voice that plays, and the speaker's
display and model ids, and a second one attached to the talking head while
a line plays: the quest window is usually closed by the time a line is heard
and found wanting, and the talking head is what is up then. Off by default.
The choice is the devOverlay setting. It was also the ForeverVO_devOverlay
CVar until 0.1.9, which the client does not keep across a logout, so reading it
back turned the overlay off at every login (#887).
]]

local Debug = {}
ns.UI.Debug = Debug

local probe
local panel     -- beside the quest or gossip frame
local current   -- { host, head } while a dialog is up
local headPanel -- beside the talking head
local headItem  -- the queue item it describes while a line plays

local function Enabled()
    return ns.db and ns.db.devOverlay
end

local function VoiceLine(playing, recorded)
    if not playing or playing == "" then
        if recorded and recorded ~= "" then
            return "voice none · recorded " .. recorded
        end
        return "voice none"
    end
    if recorded and recorded ~= "" and recorded ~= playing then
        return format("voice %s · recorded %s", playing, recorded)
    end
    return "voice " .. playing
end

local function ModelLines()
    local display = (probe and probe.displayID and probe.displayID ~= 0) and tostring(probe.displayID) or "none"
    local model = (probe and probe.modelFileID and probe.modelFileID ~= 0) and tostring(probe.modelFileID) or "none"
    return "display " .. display, "model " .. model
end

--- A panel whose text can be copied: a font string cannot be selected, so the
--- lines sit in a read-only edit box. A click selects them all, ready for
--- Ctrl+C; typing puts them back; Escape lets go of the keyboard. The font
--- string stays, hidden, to measure the lines by.
local function NewPanel()
    local frame = CreateFrame("Frame", nil, UIParent, "TooltipBackdropTemplate")
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame.text = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    frame.text:SetPoint("TOPLEFT", 12, -10)
    frame.text:SetJustifyH("LEFT")
    frame.text:SetSpacing(2)
    frame.text:SetAlpha(0)
    local edit = CreateFrame("EditBox", nil, frame)
    frame.edit = edit
    edit:SetPoint("TOPLEFT", 12, -10)
    edit:SetMultiLine(true)
    edit:SetAutoFocus(false)
    edit:SetFontObject(GameFontHighlightSmall)
    edit:SetSpacing(2)
    edit:EnableMouse(true)
    edit:SetScript("OnEditFocusGained", function(self)
        self:HighlightText()
    end)
    edit:SetScript("OnMouseUp", function(self)
        self:HighlightText()
    end)
    edit:SetScript("OnEscapePressed", function(self)
        self:ClearFocus()
    end)
    edit:SetScript("OnTextChanged", function(self, userInput)
        if userInput then
            self:SetText(self.lines or "")
            self:HighlightText()
        end
    end)
    frame.watched = {}
    frame:Hide()
    return frame
end

--- Its own frame, stuck to the host's right edge, so the quest parchment
--- never has to make room for it.
local function Panel()
    if not panel then
        panel = NewPanel()
    end
    return panel
end

--- Sizes a panel to its lines, at most 400 wide, and puts them in its edit box.
local function Fill(frame, lines)
    local text = table.concat(lines, "\n")
    local body = frame.text
    body:SetWidth(400)
    body:SetText(text)
    local width = math.min(body:GetStringWidth(), 400)
    width = math.max(width, 96)
    body:SetWidth(width)
    local height = body:GetStringHeight()
    frame.edit.lines = text
    frame.edit:SetSize(width + 4, height)
    frame.edit:SetText(text)
    frame.edit:SetCursorPosition(0)
    frame:SetSize(width + 28, height + 20)
end

local function Watch(host)
    local frame = Panel()
    if frame.watched[host] then
        return
    end
    frame.watched[host] = true
    host:HookScript("OnHide", function()
        if frame.host == host then
            frame:Hide()
            current = nil
        end
    end)
end

local Paint

local function Show(host, lines)
    if not Enabled() or not host then
        Debug:Hide()
        return
    end
    local frame = Panel()
    Watch(host)
    frame.host = host
    frame:ClearAllPoints()
    frame:SetPoint("TOPLEFT", host, "TOPRIGHT", 2, 0)
    current = { host = host, head = lines }
    Paint()
    frame:Show()
end

function Paint()
    if not current then
        return
    end
    local lines = {}
    for index, line in ipairs(current.head) do
        lines[index] = line
    end
    local display, model = ModelLines()
    lines[#lines + 1] = display
    lines[#lines + 1] = model
    Fill(panel, lines)
end

function Debug:HideClosed()
    if not current then
        return
    end
    if not current.host:IsShown() then
        self:Hide()
    end
end

function Debug:Hide()
    current = nil
    if panel then
        panel:Hide()
    end
end

local function RepaintModel()
    Paint()
end

local function Probe()
    if not probe then
        probe = CreateFrame("PlayerModel", nil, UIParent)
        probe:SetSize(1, 1)
        probe:SetPoint("TOPLEFT")
        probe:SetAlpha(0)
        probe:EnableMouse(false)
        probe:SetScript("OnModelLoaded", function(self)
            if not self.pending then
                return
            end
            local ok, displayID = pcall(self.GetDisplayInfo, self)
            if ok and displayID and displayID ~= 0 then
                self.displayID = displayID
            end
            local okFile, fileID = pcall(self.GetModelFileID, self)
            if okFile and fileID and fileID ~= 0 then
                self.modelFileID = fileID
            end
            RepaintModel()
        end)
    end
    return probe
end

local function ReadModel()
    local frame = Probe()
    frame.pending = false
    frame.displayID, frame.modelFileID = nil, nil
    local unit = Util.DialogUnit()
    if not unit then
        return
    end
    frame.pending = true
    pcall(function()
        frame:SetUnit(unit)
        local displayID = frame:GetDisplayInfo()
        if displayID and displayID ~= 0 then
            frame.displayID = displayID
        end
        local fileID = frame:GetModelFileID()
        if fileID and fileID ~= 0 then
            frame.modelFileID = fileID
        end
    end)
end

local function SpeakerKey()
    local unit = Util.DialogUnit()
    local guid = unit and Util.Plain(UnitGUID(unit))
    local key = Util.SpeakerKeyFromGUID(guid)
    if not key then
        local name = unit and Util.Plain(UnitName(unit))
        key = Packs:SpeakerKeyByName(name)
    end
    return key
end

function Debug:ShowQuest(event)
    local questID = GetQuestID()
    local base, playing, recorded
    if questID and questID ~= 0 then
        base, playing, recorded = Packs:DebugQuest(questID, event)
    end
    ReadModel()
    Show(QuestFrame, {
        base or format("quest %s", questID or "?"),
        VoiceLine(playing, recorded),
    })
end

function Debug:ShowGossip(text, onGossipFrame)
    text = Util.Plain(text)
    local playing, recorded = Packs:DebugGossip(SpeakerKey(), text)
    ReadModel()
    local parent
    if onGossipFrame and GossipFrame then
        parent = GossipFrame
    elseif GossipFrame and GossipFrame:IsShown() then
        parent = GossipFrame
    else
        parent = QuestFrame
    end
    Show(parent, {
        VoiceLine(playing, recorded),
    })
end

--- The talking head's portrait is the one model that is surely the speaker's
--- once the dialog is gone: read its ids from it. GetModelFileID is the only
--- model query that answers on this client; the display id stays 0.
local function HeadModelLines()
    local head = ns.UI.TalkingHead
    local model = head and head.frame and head.frame.Model
    local display, file
    if model and model:IsShown() then
        local ok, value = pcall(model.GetDisplayInfo, model)
        display = ok and value and value ~= 0 and value or nil
        local okFile, fileID = pcall(model.GetModelFileID, model)
        file = okFile and fileID and fileID ~= 0 and fileID or nil
    end
    return "display " .. (display and tostring(display) or "none"),
        "model " .. (file and tostring(file) or (model and model:IsShown() and "loading" or "none (book)"))
end

--- What plays now, as the pack sees it: the file under Sounds\, the voice playing
--- and the one recorded for the line, the speaker and the pack.
local function PaintHead()
    if not headItem or not headPanel then
        return
    end
    local item = headItem
    local recorded
    if item.kind == "quest" and item.questID then
        local _, _, rec = Packs:DebugQuest(item.questID, item.event)
        recorded = rec
    elseif item.kind == "gossip" then
        local _, rec = Packs:DebugGossip(item.speakerKey, item.text)
        recorded = rec
    end
    local file = item.path and (item.path:match("\\Sounds\\(.+)%.mp3$") or item.path) or "no file"
    local lines = {
        file,
        VoiceLine(item.voice, recorded),
        format("speaker %s%s", tostring(item.speakerKey or "?"), item.pack and (" · pack " .. (item.pack.name or "?")) or ""),
    }
    if item.kind == "quest" and item.questID then
        lines[#lines + 1] = format("quest %d %s", item.questID, item.event or "?")
    end
    local display, model = HeadModelLines()
    lines[#lines + 1] = display
    lines[#lines + 1] = model
    Fill(headPanel, lines)
end

local GAP = 4   -- between the head and the panel

--- Beside the head, to its left, its top level with the parchment's (the
--- head's rect runs above its art, artTop), or to its right when the room on the
--- left is narrower than the panel (the head dragged to the left edge of
--- the screen) and the right is not. It follows the head as it is dragged.
--- Sized before it is placed: PaintHead runs first.
local function AnchorHead(host)
    if not host.debugHooked then
        host.debugHooked = true
        host:HookScript("OnDragStop", function()
            if headPanel and headPanel:IsShown() then
                AnchorHead(host)
            end
        end)
    end
    local room = host:Room()
    local need = (headPanel:GetWidth() + GAP) * headPanel:GetEffectiveScale()
    local left = not room or room.left >= need or room.left >= room.right
    headPanel:ClearAllPoints()
    if left then
        headPanel:SetPoint("TOPRIGHT", host, "TOPLEFT", -GAP, -host.artTop)
    else
        headPanel:SetPoint("TOPLEFT", host, "TOPRIGHT", GAP, -host.artTop)
    end
end

--- Follows the talking head: shown beside it while a line plays, gone with it.
function Debug:ShowHead(item)
    local head = ns.UI.TalkingHead
    local host = head and head.frame
    if not Enabled() or not item or not host then
        self:HideHead()
        return
    end
    if not headPanel then
        headPanel = NewPanel()
        host:HookScript("OnHide", function()
            Debug:HideHead()
        end)
        -- the portrait's model arrives after the line starts; its ids follow
        host.Model:HookScript("OnModelLoaded", function()
            if headItem then
                PaintHead()
                AnchorHead(host)
            end
        end)
    end
    headItem = item
    PaintHead()
    AnchorHead(host)
    headPanel:Show()
end

function Debug:HideHead()
    headItem = nil
    if headPanel then
        headPanel:Hide()
    end
end

--- Which quest panel is up, so turning the option on mid-dialog can redraw.
local function OpenQuestEvent()
    if QuestFrameDetailPanel and QuestFrameDetailPanel:IsShown() then
        return "accept"
    elseif QuestFrameProgressPanel and QuestFrameProgressPanel:IsShown() then
        return "progress"
    elseif QuestFrameRewardPanel and QuestFrameRewardPanel:IsShown() then
        return "complete"
    end
end

function Debug:Apply()
    if not Enabled() then
        self:Hide()
        self:HideHead()
        return
    end
    local head = ns.UI.TalkingHead
    if head and head.displayed then
        self:ShowHead(head.displayed)
    end
    local event = OpenQuestEvent()
    if event then
        self:ShowQuest(event)
    elseif GossipFrame and GossipFrame:IsShown() then
        self:ShowGossip(C_GossipInfo.GetText(), true)
    else
        self:Hide()
    end
end

ns.OnInit(function()
    local frame = CreateFrame("Frame")
    local handlers = {
        QUEST_DETAIL = function()
            Debug:ShowQuest("accept")
        end,
        QUEST_PROGRESS = function()
            Debug:ShowQuest("progress")
        end,
        QUEST_COMPLETE = function()
            Debug:ShowQuest("complete")
        end,
        QUEST_FINISHED = function()
            Debug:HideClosed()
        end,
        QUEST_GREETING = function()
            Debug:ShowGossip(GetGreetingText())
        end,
        GOSSIP_SHOW = function()
            Debug:ShowGossip(C_GossipInfo.GetText(), true)
        end,
        GOSSIP_CLOSED = function()
            Debug:HideClosed()
        end,
    }
    for event in pairs(handlers) do
        frame:RegisterEvent(event)
    end

    -- the talking head: a new line presented, or the frame closing
    local head = ns.UI.TalkingHead
    hooksecurefunc(head, "Present", function(_, item)
        if Enabled() then
            local ok, err = pcall(Debug.ShowHead, Debug, item)
            if not ok then
                ns.Print("|cffff4040error in developer overlay (talking head):|r", err)
            end
        end
    end)
    hooksecurefunc(head, "CloseFrame", function()
        Debug:HideHead()
    end)
    frame:SetScript("OnEvent", function(_, event)
        if not Enabled() then
            return
        end
        local ok, err = pcall(handlers[event])
        if not ok then
            ns.Print("|cffff4040error in developer overlay " .. event .. ":|r", err)
        end
    end)
end)
