local _, ns = ...
local Queue = ns.Queue

--[[
A panel under the talking head listing what is waiting to play. Click a row
to play it now, its X to drop it, or clear the whole queue.
]]

local ROW_HEIGHT = 22
local MAX_ROWS = 8
local PANEL_WIDTH = 340
local GAP = 4   -- between the head and the list

local QueueList = {}
ns.UI.QueueList = QueueList

function QueueList:GetFrame()
    if self.frame then
        return self.frame
    end
    local head = ns.UI.TalkingHead.frame
    local frame = CreateFrame("Frame", "ForeverVOQueueList", head, "TooltipBackdropTemplate")
    self.frame = frame
    frame:SetWidth(PANEL_WIDTH)
    frame:SetFrameLevel(head:GetFrameLevel() + 5)
    frame:Hide()
    head:HookScript("OnDragStop", function()
        self:Update()
    end)

    frame.Header = frame:CreateFontString(nil, "ARTWORK")
    frame.Header:SetFontObject("GameFontNormal")
    frame.Header:SetPoint("TOPLEFT", 12, -10)
    frame.Header:SetText("Up next")

    frame.ClearButton = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    frame.ClearButton:SetSize(70, 20)
    frame.ClearButton:SetPoint("TOPRIGHT", -10, -7)
    frame.ClearButton:SetText("Clear all")
    frame.ClearButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_OFF)
        Queue:Clear()
    end)

    frame.Empty = frame:CreateFontString(nil, "ARTWORK")
    frame.Empty:SetFontObject("GameFontDisableSmall")
    frame.Empty:SetPoint("TOPLEFT", frame, "TOPLEFT", 12, -34)
    frame.Empty:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -12, -34)
    frame.Empty:SetText("Nothing else is waiting to play.")

    frame.More = frame:CreateFontString(nil, "ARTWORK")
    frame.More:SetFontObject("GameFontDisableSmall")
    frame.More:SetJustifyH("LEFT")

    self.rowPool = CreateFramePool("Button", frame, nil, function(_, row)
        row:Hide()
        row:ClearAllPoints()
        row.item = nil
    end)
    return frame
end

local function InitRow(row)
    if row.Text then
        return
    end
    row:SetHeight(ROW_HEIGHT)
    row:SetHighlightTexture("Interface\\QuestFrame\\UI-QuestTitleHighlight", "ADD")

    row.Icon = row:CreateTexture(nil, "ARTWORK")
    row.Icon:SetSize(14, 14)
    row.Icon:SetPoint("LEFT", 4, 0)

    row.Remove = CreateFrame("Button", nil, row)
    row.Remove:SetSize(16, 16)
    row.Remove:SetPoint("RIGHT", -2, 0)
    row.Remove:SetNormalTexture(ns.mediaPath .. "BulletDelete")
    row.Remove:SetHighlightTexture("Interface\\BUTTONS\\UI-Panel-MinimizeButton-Highlight", "ADD")
    row.Remove:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_OFF)
        if row.item then
            Queue:Remove(row.item)
        end
    end)

    row.Text = row:CreateFontString(nil, "ARTWORK")
    row.Text:SetFontObject("GameFontHighlightSmall")
    row.Text:SetJustifyH("LEFT")
    row.Text:SetWordWrap(false)
    row.Text:SetPoint("LEFT", row.Icon, "RIGHT", 6, 0)
    row.Text:SetPoint("RIGHT", row.Remove, "LEFT", -6, 0)

    row:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        if row.item then
            Queue:MoveToFront(row.item)
        end
    end)
    row:SetScript("OnEnter", function(self)
        if not self.item then return end
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:SetText(self.item.title or self.item.name or "")
        if self.item.name and self.item.title then
            GameTooltip:AddLine(self.item.name, 1, 1, 1)
        end
        GameTooltip:AddLine("Click to play now", 0.5, 0.5, 0.5)
        GameTooltip:Show()
    end)
    row:SetScript("OnLeave", GameTooltip_Hide)
end

function QueueList:Toggle()
    ns.db.showQueuePanel = not ns.db.showQueuePanel
    self:Update()
end

--- Below the head, or above it when the room below is shorter than the
--- list (the head dragged to the bottom of the screen) and the room above
--- is not. Sized before it is placed: Update sets the height first.
function QueueList:Anchor()
    local head = ns.UI.TalkingHead.frame
    local frame = self:GetFrame()
    local room = head:Room()
    local need = (frame:GetHeight() + GAP) * frame:GetEffectiveScale()
    local below = not room or room.bottom >= need or room.bottom >= room.top
    frame:ClearAllPoints()
    if below then
        frame:SetPoint("TOP", head, "BOTTOM", 0, -GAP)
    else
        frame:SetPoint("BOTTOM", head, "TOP", 0, GAP)
    end
end

function QueueList:Update()
    local head = ns.UI.TalkingHead.frame
    if not head then
        return
    end
    local frame = self:GetFrame()
    local visible = ns.db.showQueuePanel and head:IsShown() and not head.isClosing
    if not visible then
        frame:Hide()
        return
    end

    self.rowPool:ReleaseAll()
    local shown = 0
    local total = Queue:Size()
    for index = 2, total do
        if shown >= MAX_ROWS then
            break
        end
        local item = Queue:Get(index)
        shown = shown + 1
        local row = self.rowPool:Acquire()
        InitRow(row)
        row.item = item
        row.Icon:SetTexture(ns.UI.TalkingHead.EventIcon(item))
        local label = item.title or ""
        if item.name then
            label = label ~= "" and format("%s  %s%s|r", label, GRAY_FONT_COLOR_CODE, item.name) or item.name
        end
        row.Text:SetText(label)
        row:SetPoint("TOPLEFT", frame, "TOPLEFT", 8, -30 - (shown - 1) * ROW_HEIGHT)
        row:SetPoint("RIGHT", frame, "RIGHT", -8, 0)
        row:Show()
    end

    local extra = total - 1 - shown
    frame.More:ClearAllPoints()
    if extra > 0 then
        frame.More:SetPoint("TOPLEFT", 14, -30 - shown * ROW_HEIGHT - 2)
        frame.More:SetText(format("and %d more", extra))
        frame.More:Show()
    else
        frame.More:Hide()
    end

    frame.Empty:SetShown(shown == 0)
    frame.ClearButton:SetEnabled(total > 0)
    frame:SetHeight(40 + math.max(shown, 1) * ROW_HEIGHT + (extra > 0 and 16 or 0))
    self:Anchor()
    frame:Show()
end
