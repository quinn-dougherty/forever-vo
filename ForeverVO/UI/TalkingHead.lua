local _, ns = ...
local Util, Queue = ns.Util, ns.Queue

--[[
The talking head: a frame that mirrors Blizzard's own TalkingHeadFrame
(Blizzard_FrameXML/TalkingHeadUI.xml) in size, atlases, anchors and fade
animations, so it reads as part of the client. Shows the current queue item
with the speaker's model, name, title, and the spoken text in pages sized to
the text box, turned in time with the audio. Right-click skips, the X clears the queue. The bug icon opens a
GitHub issue about the line.
]]

local FRAME_WIDTH, FRAME_HEIGHT = 570, 155
local MODEL_SIZE = 115
local TEXT_INSET = 28   -- left margin of the name and text when the portrait is hidden
local TALK_ANIMATION = 60
-- How far the portrait camera stands from the head, as a multiple of the
-- model's own head camera. At 1 the camera sits where the model's portrait
-- camera puts it, and a talk animation that leans forward (the skyborne
-- female's) pushes the face through it and shows the inside of the head
-- (#1945). Backed off, the lean stays in front of the lens
local PORTRAIT_DISTANCE = 1.5
local MODEL_SETTLE = 0.5    -- seconds a model load gets before the book stands in
local UNIT_SETTLE = 2       -- further seconds a unit's load gets before the creature cache
local CLOSE_INSET = 12       -- the close button's offset from the top right corner
local CONTROL_SIZE = 26
local CONTROLS_OUT = 0.3    -- seconds the buttons take to fade before the panel does
local READ_RATE = 15        -- characters a second, to page a line that has no duration
local FALLBACK_LINES = 3    -- the box's height in lines, while its layout is unresolved
local FALLBACK_CHARS = 75   -- characters a line, while the font cannot be measured

local TEXTURE_KIT_FORMATS = {
    TextBackground = "%s-TextBackground",
    Portrait = "%s-PortraitFrame",
    PortraitBg = "%s-PortraitBg",
}
local DEFAULT_ATLASES = {
    TextBackground = "TalkingHeads-TextBackground",
    Portrait = "TalkingHeads-Alliance-PortraitFrame",
    PortraitBg = "TalkingHeads-PortraitBg",
}
-- Name / Title / Text colors per texture kit. Blizzard's values for Name and
-- Text; Title is ours and must read on both the dark panel and the parchment.
local FONT_COLORS = {
    ["TalkingHeads-Horde"]    = { Name = CreateColor(0.28, 0.02, 0.02), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["TalkingHeads-Alliance"] = { Name = CreateColor(0.02, 0.17, 0.33), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["TalkingHeads-Neutral"]  = { Name = CreateColor(0.33, 0.16, 0.02), Title = CreateColor(0.25, 0.15, 0.05), Text = CreateColor(0, 0, 0), Shadow = CreateColor(0, 0, 0, 0) },
    ["Normal"]                = { Name = CreateColor(1, 0.82, 0.02),    Title = CreateColor(0.85, 0.85, 0.85), Text = CreateColor(1, 1, 1), Shadow = CreateColor(0, 0, 0, 1) },
}

local TalkingHead = {
    displayed = nil,
    pages = { "" },     -- the displayed item's pages (pageItem), and where each
    starts = { 0 },     -- starts as a share of the text
    heldAt = 0,         -- where the text is while its audio is not playing
}
ns.UI.TalkingHead = TalkingHead

local function AtlasExists(atlas)
    return atlas ~= nil and C_Texture.GetAtlasExists(atlas)
end

local function CurrentTextureKit()
    if not ns.db.factionHead then
        return "Normal" -- Blizzard's default dark talking head
    end
    local faction = UnitFactionGroup("player")
    local kit = faction and ("TalkingHeads-" .. faction) or "TalkingHeads-Neutral"
    if AtlasExists(format(TEXTURE_KIT_FORMATS.TextBackground, kit)) then
        return kit
    end
    return "Normal"
end

local function Alpha(group, target, fromAlpha, toAlpha, duration, delay)
    local anim = group:CreateAnimation("Alpha")
    anim:SetTarget(target)
    anim:SetFromAlpha(fromAlpha)
    anim:SetToAlpha(toAlpha)
    anim:SetDuration(duration)
    anim:SetStartDelay(delay or 0)
    anim:SetOrder(1)
end

local function Scale(group, target, fromX, fromY, toX, toY, duration, delay, origin)
    local anim = group:CreateAnimation("Scale")
    anim:SetTarget(target)
    anim:SetScaleFrom(fromX, fromY)
    anim:SetScaleTo(toX, toY)
    anim:SetDuration(duration)
    anim:SetStartDelay(delay or 0)
    anim:SetOrder(1)
    if origin then
        anim:SetOrigin(origin, 0, 0)
    end
end

function TalkingHead.EventIcon(item)
    local event = item.event
    if event == "accept" then
        return ns.mediaPath .. "BulletAccept"
    elseif event == "progress" then
        return ns.mediaPath .. "BulletProgress"
    elseif event == "complete" then
        return ns.mediaPath .. "BulletComplete"
    end
    return ns.mediaPath .. "BulletGossip"
end

-- ---------------------------------------------------------------------------
-- Construction
-- ---------------------------------------------------------------------------

function TalkingHead:Init()
    self:CreateFrame()
    self:CreatePortrait()
    self:CreateText()
    self:CreateControls()
    self:CreateAnimations()
    self:ApplyTextureKit()
    self:ApplySettings()

    Queue:RegisterCallback("OnChanged", self.Update, self)
    Queue:RegisterCallback("OnPause", self.UpdatePause, self)
end

--- Rebuilds the pages when the box or the font's pixel size may have
--- changed, keeping the place in the line.
function TalkingHead:Repaginate()
    if self.displayed and self.pageItem == self.displayed then
        self:Paginate(self.displayed)
        self:RenderPage()
    end
end

function TalkingHead:CreateFrame()
    local frame = CreateFrame("Button", "ForeverVOTalkingHead", UIParent)
    self.frame = frame
    frame:SetSize(FRAME_WIDTH, FRAME_HEIGHT)
    frame:SetFrameStrata("HIGH")
    frame:SetClampedToScreen(true)
    frame:SetMovable(true)
    frame:RegisterForClicks("RightButtonUp")
    frame:RegisterForDrag("LeftButton")
    frame:Hide()

    function frame:ResetPosition()
        self:ClearAllPoints()
        self:SetPoint("BOTTOM", UIParent, "BOTTOM", 0, 96) -- where Blizzard puts TalkingHeadFrame
    end
    frame:ResetPosition()
    frame:SetUserPlaced(true)

    --- Where the parchment's top edge sits below the frame's top. The
    --- TextBackground atlases are 570x155, the whole frame, with the art
    --- inset 14 px on every side (half alpha at row 14, opaque from 16, in
    --- all three kits). A panel level with the head lines up with this,
    --- not with the rect.
    frame.artTop = 14

    --- How much room, in screen pixels, a panel beside the head has on each
    --- side: the distance from each of the head's edges to the screen's. The
    --- queue list and the debug panel pick their side by it. Nil before the
    --- head is laid out.
    function frame:Room()
        local left, bottom, width, height = self:GetRect()
        local screenWidth, screenHeight = UIParent:GetRight(), UIParent:GetTop()
        if not left or not screenWidth then
            return nil
        end
        local scale, screenScale = self:GetEffectiveScale(), UIParent:GetEffectiveScale()
        return {
            left = left * scale,
            right = screenWidth * screenScale - (left + width) * scale,
            bottom = bottom * scale,
            top = screenHeight * screenScale - (bottom + height) * scale,
        }
    end

    frame:SetScript("OnClick", function(_, button)
        if button == "RightButton" then
            Queue:Skip()
        end
    end)
    frame:SetScript("OnDragStart", function(self)
        if not ns.db.lockHead then
            self:StartMoving()
        end
    end)
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)

    local sinceRender = 0
    frame:SetScript("OnUpdate", function(_, elapsed)
        sinceRender = sinceRender + elapsed
        if sinceRender >= 0.1 and #self.pages > 1 then
            sinceRender = 0
            self:RenderPage()
        end
    end)
    frame:RegisterEvent("UI_SCALE_CHANGED")
    frame:RegisterEvent("DISPLAY_SIZE_CHANGED")
    frame:SetScript("OnEvent", function()
        self:Repaginate()
    end)

    frame.TextBackground = frame:CreateTexture(nil, "BACKGROUND")
    frame.TextBackground:SetPoint("CENTER")
    frame.TextBackground:SetAlpha(0.01)

    frame.Portrait = frame:CreateTexture(nil, "OVERLAY")
    frame.Portrait:SetPoint("TOPLEFT", 5, -6)
    frame.Portrait:SetAlpha(0.01)

    local function Glow(atlas, subLevel)
        local tex = frame:CreateTexture(nil, "OVERLAY", nil, subLevel or 0)
        tex:SetAtlas(atlas, true)
        tex:SetBlendMode("ADD")
        tex:SetAlpha(0.01)
        return tex
    end
    frame.Sheen = Glow("TalkingHeads-Glow-Sheen")
    frame.TextSheen = Glow("TalkingHeads-Glow-TextSheen")
    frame.GlowTop = Glow("TalkingHeads-Glow-TopBarGlow", 1)
    frame.GlowTop:SetPoint("CENTER", frame.Portrait, "TOP", 0, -11)
    frame.GlowLeft = Glow("TalkingHeads-Glow-SideBarGlow", 1)
    frame.GlowLeft:SetPoint("CENTER", frame.Portrait, "LEFT", 11, 25)
    frame.GlowRight = Glow("TalkingHeads-Glow-SideBarGlow", 1)
    frame.GlowRight:SetPoint("CENTER", frame.Portrait, "RIGHT", -11, 25)

    frame.CloseButton = CreateFrame("Button", nil, frame, "UIPanelCloseButtonNoScripts")
    frame.CloseButton:SetPoint("TOPRIGHT", -CLOSE_INSET, -CLOSE_INSET)
    frame.CloseButton:SetAlpha(0.01)
    frame.CloseButton:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_CLOSE)
        Queue:Clear()
    end)

    -- Blizzard's report-bug icon (HelpIcon-Bug), not the 64px UIPanelBugButton
    -- it usually sits in. It belongs on the left of the portrait.
    local bug = CreateFrame("Button", nil, frame)
    bug:SetSize(22, 22)
    bug:SetPoint("CENTER", frame, "TOPLEFT", 16, -42)
    bug:SetFrameLevel(frame:GetFrameLevel() + 30)
    bug:SetAlpha(0.01)
    local icon = bug:CreateTexture(nil, "ARTWORK")
    icon:SetSize(22, 22)
    icon:SetPoint("CENTER")
    icon:SetTexture("Interface\\HelpFrame\\HelpIcon-Bug")
    bug.Icon = icon
    bug:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight", "ADD")
    bug:GetHighlightTexture():SetSize(22, 22)
    bug:SetScript("OnMouseDown", function()
        icon:ClearAllPoints()
        icon:SetPoint("CENTER", 1, -1)
    end)
    bug:SetScript("OnMouseUp", function()
        icon:ClearAllPoints()
        icon:SetPoint("CENTER")
    end)
    bug:SetScript("OnClick", function()
        PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
        ns.Report:Show(self.displayed or Queue:Current())
    end)
    bug:SetScript("OnEnter", function(button)
        GameTooltip:SetOwner(button, "ANCHOR_RIGHT")
        GameTooltip:SetText("Report this line")
        GameTooltip:AddLine("Copy a link that opens a GitHub issue about this line. Your character name is removed.", 1, 0.82, 0, true)
        GameTooltip:Show()
    end)
    bug:SetScript("OnLeave", GameTooltip_Hide)
    frame.ReportButton = bug
end

function TalkingHead:CreatePortrait()
    local frame = self.frame
    local model = CreateFrame("PlayerModel", nil, frame)
    frame.Model = model
    model:SetSize(MODEL_SIZE, MODEL_SIZE)
    model:SetPoint("TOPLEFT", 21, -21)
    model:SetAlpha(0.01)

    model.PortraitBg = model:CreateTexture(nil, "BACKGROUND")
    model.PortraitBg:SetPoint("TOPLEFT")
    model.PortraitBg:SetAlpha(0.01)

    frame.Book = frame:CreateTexture(nil, "ARTWORK")
    frame.Book:SetTexture(ns.mediaPath .. "Book")
    frame.Book:SetSize(MODEL_SIZE - 20, MODEL_SIZE - 20)
    frame.Book:SetPoint("CENTER", model, "CENTER")
    frame.Book:Hide()

    model:SetScript("OnModelLoaded", function(self)
        pcall(self.SetPortraitZoom, self, 1)
        pcall(self.SetCamDistanceScale, self, PORTRAIT_DISTANCE)
        pcall(self.SetFacing, self, 0)
        if not self.revealing then
            self:Reveal()
        end
        if self.talking then
            self:SetAnimation(TALK_ANIMATION)
        end
    end)

    --- GetModelFileID is the one model query that answers on this client.
    local function HasModel(self)
        local ok, fileID = pcall(self.GetModelFileID, self)
        return ok and fileID ~= nil and fileID ~= 0
    end

    local function Settle(self)
        self.settle = nil
        local hasModel = HasModel(self)
        -- A unit the client drew no model from (Zeroth, who is dead, #1286) gets
        -- one more try from the creature cache. Its load may only be slow, and
        -- SetCreature or hiding the model would end it, so the book stands over
        -- the empty model while it goes on; OnModelLoaded cancels the retry
        if not hasModel and self.retryCreature then
            local creatureID = self.retryCreature
            self.retryCreature = nil
            frame.Book:Show()
            self.settle = C_Timer.NewTimer(UNIT_SETTLE, function()
                self.settle = nil
                if not HasModel(self) then
                    self:SetCreature(creatureID)
                    self:Reveal()
                end
            end)
            return
        end
        if hasModel then
            self.retryCreature = nil
        end
        -- A hidden PlayerModel drops its model, so it is only hidden once the
        -- load has had its chance, and shown again before the next load.
        -- Showing it fires OnModelLoaded again, even when it was already
        -- shown, so only touch what changes and ignore that callback meanwhile
        self.revealing = true
        if self:IsShown() ~= hasModel then
            self:SetShown(hasModel)
        end
        self.revealing = nil
        frame.Book:SetShown(not hasModel)
    end

    --- Shows the model once one is loaded, else the narrator's book: a speaker
    --- the client has no model for gets the book, never a leftover face. A
    --- load that has not answered yet is given a moment, since a failed one
    --- never fires OnModelLoaded.
    function model:Reveal()
        self:CancelSettle()
        if HasModel(self) then
            Settle(self)
        else
            self.settle = C_Timer.NewTimer(MODEL_SETTLE, function()
                Settle(self)
            end)
        end
    end

    function model:CancelSettle()
        if self.settle then
            self.settle:Cancel()
            self.settle = nil
        end
    end
    model:SetScript("OnAnimFinished", function(self)
        self:SetAnimation(self.talking and TALK_ANIMATION or 0)
    end)

    --- Loads the speaker's model. While the speaker is the unit on screen the
    --- model comes from that unit, the way the capture reads it: the client is
    --- drawing them, so it always has the model. SetCreature only knows what the
    --- creature cache holds, and on this client it loaded nothing for
    --- Varimathras and left the previous speaker (an orc) in the portrait. It
    --- stays as the fallback for a line that plays after the dialog is gone,
    --- cleared first so a failed load shows the book rather than the wrong
    --- face. The model may arrive later, in OnModelLoaded, which reveals it.
    function model:ShowCreature(creatureID, guid)
        self.talking = true
        local unit = guid and Util.DialogUnit()
        if unit and Util.Plain(UnitGUID(unit)) ~= guid then
            unit = nil
        end
        -- A dead speaker's unit loaded nothing new and left the previous
        -- speaker's face up (Zeroth in Blackfathom Villainy, #1286); the
        -- creature cache draws them standing, or the book shows. Settle's retry
        -- would get there too, but only after the book for MODEL_SETTLE and
        -- UNIT_SETTLE, so a speaker known to be dead goes to the cache at once
        if unit and Util.Plain(UnitIsDead(unit)) then
            unit = nil
        end
        local loaded = unit and guid or creatureID
        if self.loaded == loaded then
            self:SetAnimation(TALK_ANIMATION)
            return
        end
        self.loaded = loaded
        self:CancelSettle()
        self:Show()
        frame.Book:Hide()
        -- cleared first on either path, so a load that fails shows the book and
        -- never the face before it
        self:ClearModel()
        if unit then
            self.retryCreature = creatureID
            self:SetUnit(unit)
        else
            self.retryCreature = nil
            self:SetCreature(creatureID)
        end
        self:Reveal()
    end

    function model:StopTalking()
        self.talking = false
    end
end

function TalkingHead:CreateText()
    local frame = self.frame

    frame.Name = frame:CreateFontString(nil, "ARTWORK")
    frame.Name:SetFontObject("Fancy22Font")
    frame.Name:SetJustifyH("LEFT")
    frame.Name:SetPoint("TOPLEFT", frame.Portrait, "TOPRIGHT", 2, -19)
    frame.Name:SetPoint("RIGHT", -42, 0)
    frame.Name:SetAlpha(0.01)

    frame.Title = frame:CreateFontString(nil, "ARTWORK")
    frame.Title:SetFontObject("GameFontNormal")
    frame.Title:SetJustifyH("LEFT")
    frame.Title:SetPoint("TOPLEFT", frame.Name, "BOTTOMLEFT", 1, -1)
    frame.Title:SetAlpha(0.01)

    frame.Text = frame:CreateFontString(nil, "ARTWORK")
    frame.Text:SetFontObject("GameFontHighlightLarge")
    frame.Text:SetJustifyH("LEFT")
    frame.Text:SetJustifyV("TOP")
    frame.Text:SetPoint("TOPLEFT", frame.Title, "BOTTOMLEFT", 0, -4)
    frame.Text:SetPoint("BOTTOMRIGHT", -42, 16)
    frame.Text:SetWordWrap(true)
    frame.Text:SetAlpha(0.01)

    -- Wraps exactly like Text, at alpha 0, so pages can be sized to the box
    frame.Measure = frame:CreateFontString(nil, "ARTWORK")
    frame.Measure:SetFontObject("GameFontHighlightLarge")
    frame.Measure:SetJustifyH("LEFT")
    frame.Measure:SetWordWrap(true)
    frame.Measure:SetNonSpaceWrap(false)
    frame.Measure:SetPoint("TOPLEFT")
    frame.Measure:SetAlpha(0)

    frame.Sheen:SetPoint("LEFT", frame.Name, "LEFT", -48, 0)
    frame.TextSheen:SetPoint("LEFT", frame.Text, "LEFT", -48, 16)
end

--- Pause, Skip and Queue are buttons in the gutter under the close button,
--- where Blizzard's own talking head is empty, so the text gets the frame's
--- full height. One look for all three: the chat frame's button square with
--- a gold glyph from the credits screen's media controls (pause, play, fast
--- forward). The client has no list glyph in that set, so Queue's is built
--- from one of the pause glyph's bars laid on its side, with the dropdown
--- menu's gold dots as bullets.
local PAUSE_ATLAS = "creditsscreen-assets-buttons-pause"
local PLAY_ATLAS = "creditsscreen-assets-buttons-play"

--- Shows the left bar of the pause glyph turned a quarter to the left, as a
--- horizontal bar: the bar spans x 57-128, y 27-228 of the 256px glyph.
local function SetSidewaysBar(texture)
    local info = C_Texture.GetAtlasInfo(PAUSE_ATLAS)
    if not info then
        texture:SetColorTexture(1, 0.82, 0)
        return
    end
    local du = info.rightTexCoord - info.leftTexCoord
    local dv = info.bottomTexCoord - info.topTexCoord
    local u0, u1 = info.leftTexCoord + du * 57 / 256, info.leftTexCoord + du * 128 / 256
    local v0, v1 = info.topTexCoord + dv * 27 / 256, info.topTexCoord + dv * 228 / 256
    texture:SetTexture(info.file or info.filename)
    -- corners UL, LL, UR, LR: the bar's top becomes the left end
    texture:SetTexCoord(u1, v0, u0, v0, u1, v1, u0, v1)
end

function TalkingHead:CreateControls()
    local frame = self.frame

    local function ControlButton(tooltip, onClick)
        local button = CreateFrame("Button", nil, frame)
        -- the square's art is drawn inside its box; at 26 it reads the size of the X
        button:SetSize(CONTROL_SIZE, CONTROL_SIZE)
        button:SetAlpha(0.01) -- until the fade-in
        button:SetNormalAtlas("chatframe-button-up")
        button:SetPushedAtlas("chatframe-button-down")
        button:SetHighlightAtlas("chatframe-button-highlight", "ADD")
        -- the glyph rides on its own frame so it can sink with the press
        local glyph = CreateFrame("Frame", nil, button)
        glyph:SetSize(14, 14)
        glyph:SetPoint("CENTER")
        button.Glyph = glyph
        button:SetScript("OnMouseDown", function(self)
            if self:IsEnabled() then
                glyph:SetPoint("CENTER", 1, -1)
            end
        end)
        button:SetScript("OnMouseUp", function()
            glyph:SetPoint("CENTER")
        end)
        -- OnEnable/OnDisable do not reach the glyph reliably (Skip stayed
        -- dimmed while enabled), so the state and the look change together
        function button:SetUsable(usable)
            self:SetEnabled(usable)
            glyph:SetAlpha(usable and 1 or 0.35)
        end
        button:SetScript("OnClick", function()
            PlaySound(SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON)
            onClick()
        end)
        button:SetScript("OnEnter", function(self)
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:SetText(tooltip(self))
            GameTooltip:Show()
        end)
        button:SetScript("OnLeave", GameTooltip_Hide)
        return button
    end

    local function Icon(button, atlas)
        local icon = button.Glyph:CreateTexture(nil, "ARTWORK")
        icon:SetAllPoints()
        icon:SetAtlas(atlas)
        button.Icon = icon
    end

    local queue = ControlButton(function()
        local remaining = frame.QueueButton.remaining or 0
        return remaining > 0 and format("Queue: %d more queued", remaining) or "Queue"
    end, function()
        ns.UI.QueueList:Toggle()
    end)
    frame.QueueButton = queue
    -- how many wait, on the button's corner like a bag's stack count: "N more
    -- queued" fits nowhere on the parchment without covering the spoken text
    -- (the title line, the bottom strip is the border); the tooltip spells it out
    queue.Count = queue.Glyph:CreateFontString(nil, "OVERLAY")
    queue.Count:SetFontObject("NumberFontNormalSmall")
    queue.Count:SetPoint("BOTTOMRIGHT", queue, "BOTTOMRIGHT", -2, 2)
    -- centred on the close button's column, so the stack straddles the
    -- parchment's edge the way the X does
    local close = frame.CloseButton
    queue:SetPoint("BOTTOM", frame, "BOTTOMRIGHT", -CLOSE_INSET - close:GetWidth() / 2, 14)
    for row = 0, 2 do
        local y = -(row * 4.5 + 1)
        local bar = queue.Glyph:CreateTexture(nil, "ARTWORK")
        SetSidewaysBar(bar)
        bar:SetSize(10, 3.5)
        bar:SetPoint("TOPLEFT", 4.5, y)
        local dot = queue.Glyph:CreateTexture(nil, "ARTWORK")
        dot:SetAtlas("common-dropdown-icon-radialtick-yellow")
        dot:SetSize(9, 9) -- the dot is the middle third of its atlas
        dot:SetPoint("CENTER", queue.Glyph, "TOPLEFT", 1.5, y - 1.75)
    end

    frame.SkipButton = ControlButton(function() return "Skip (right-click the panel)" end, function()
        Queue:Skip()
    end)
    frame.SkipButton:SetPoint("BOTTOM", queue, "TOP", 0, 2)
    Icon(frame.SkipButton, "creditsscreen-assets-buttons-fastforward")

    frame.PauseButton = ControlButton(function() return Queue:IsPaused() and "Resume" or "Pause" end, function()
        Queue:TogglePause()
    end)
    frame.PauseButton:SetPoint("BOTTOM", frame.SkipButton, "TOP", 0, 2)
    Icon(frame.PauseButton, PAUSE_ATLAS)

    frame.Title:SetPoint("RIGHT", -42, 0)
end

function TalkingHead:SetQueueCount(remaining)
    local queue = self.frame.QueueButton
    queue.remaining = remaining
    queue.Count:SetText(remaining > 0 and tostring(remaining) or "")
end

function TalkingHead:CreateAnimations()
    local frame = self.frame

    local fadeIn = frame:CreateAnimationGroup()
    fadeIn:SetToFinalAlpha(true)
    Alpha(fadeIn, frame.Model, 0, 1, 0.75)
    Alpha(fadeIn, frame.Model.PortraitBg, 0, 1, 0.75)
    Alpha(fadeIn, frame.Portrait, 0, 1, 0.75)
    Alpha(fadeIn, frame.TextBackground, 0, 1, 0.75, 0.4)
    Alpha(fadeIn, frame.Name, 0, 1, 0.25)
    Alpha(fadeIn, frame.Title, 0, 1, 0.25)
    Alpha(fadeIn, frame.Text, 0, 1, 0.25)
    Alpha(fadeIn, frame.CloseButton, 0, 1, 0.75, 0.75)
    Alpha(fadeIn, frame.ReportButton, 0, 1, 0.75, 0.75)
    for _, button in ipairs({ frame.PauseButton, frame.SkipButton, frame.QueueButton }) do
        Alpha(fadeIn, button, 0, 1, 0.75, 0.75)
    end
    Alpha(fadeIn, frame.GlowTop, 0, 0.7, 0.25, 0.15)
    Scale(fadeIn, frame.GlowTop, 0.25, 1, 1.5, 1, 0.25, 0.15)
    Alpha(fadeIn, frame.GlowTop, 0.7, 0, 0.5, 0.4)
    Alpha(fadeIn, frame.GlowLeft, 0, 0.7, 0.25, 0.35)
    Scale(fadeIn, frame.GlowLeft, 1, 0.5, 1, 1.6, 0.7, 0.35, "TOP")
    Alpha(fadeIn, frame.GlowLeft, 0.7, 0, 0.25, 0.85)
    Alpha(fadeIn, frame.GlowRight, 0, 0.7, 0.25, 0.35)
    Scale(fadeIn, frame.GlowRight, 1, 0.5, 1, 1.6, 0.7, 0.35, "TOP")
    Alpha(fadeIn, frame.GlowRight, 0.7, 0, 0.25, 0.95)
    Alpha(fadeIn, frame.Sheen, 0, 0.7, 0.5, 0.5)
    Scale(fadeIn, frame.Sheen, 0.25, 1, 1, 1, 0.25, 0.5, "LEFT")
    Alpha(fadeIn, frame.Sheen, 0.7, 0, 0.5, 1)
    Alpha(fadeIn, frame.TextSheen, 0, 0.7, 0.5, 0.75)
    Scale(fadeIn, frame.TextSheen, 0.25, 1, 1, 1, 0.25, 0.75, "LEFT")
    Alpha(fadeIn, frame.TextSheen, 0.7, 0, 0.5, 1.25)
    frame.FadeIn = fadeIn

    local newLine = frame:CreateAnimationGroup()
    newLine:SetToFinalAlpha(true)
    Alpha(newLine, frame.Sheen, 0, 0.7, 0.5, 0.2)
    Scale(newLine, frame.Sheen, 0.25, 1, 1, 1, 0.25, 0.2, "LEFT")
    Alpha(newLine, frame.Sheen, 0.7, 0, 0.5, 0.7)
    Alpha(newLine, frame.TextSheen, 0, 0.7, 0.5, 0.45)
    Scale(newLine, frame.TextSheen, 0.25, 1, 1, 1, 0.25, 0.45, "LEFT")
    Alpha(newLine, frame.TextSheen, 0.7, 0, 0.5, 0.95)
    frame.NewLine = newLine

    local textOut = frame:CreateAnimationGroup()
    textOut:SetToFinalAlpha(true)
    Alpha(textOut, frame.Name, 1, 0, 0.25)
    Alpha(textOut, frame.Title, 1, 0, 0.25)
    Alpha(textOut, frame.Text, 1, 0, 0.25)
    frame.TextOut = textOut

    local textIn = frame:CreateAnimationGroup()
    textIn:SetToFinalAlpha(true)
    Alpha(textIn, frame.Name, 0, 1, 0.25)
    Alpha(textIn, frame.Title, 0, 1, 0.25)
    Alpha(textIn, frame.Text, 0, 1, 0.25)
    frame.TextIn = textIn

    -- The controls go first, the way they came in last
    local close = frame:CreateAnimationGroup()
    close:SetToFinalAlpha(true)
    Alpha(close, frame.CloseButton, 1, 0, CONTROLS_OUT)
    Alpha(close, frame.ReportButton, 1, 0, CONTROLS_OUT)
    for _, button in ipairs({ frame.PauseButton, frame.SkipButton, frame.QueueButton }) do
        Alpha(close, button, 1, 0, CONTROLS_OUT)
    end
    for _, region in ipairs({ frame.Model, frame.Model.PortraitBg, frame.Portrait, frame.TextBackground, frame.Name, frame.Title, frame.Text }) do
        Alpha(close, region, 1, 0, 1, CONTROLS_OUT)
    end
    close:SetScript("OnFinished", function()
        frame:Hide()
        frame.isClosing = nil
        ns.UI.QueueList:Update()
    end)
    frame.Close = close
end

-- ---------------------------------------------------------------------------
-- Styling and settings
-- ---------------------------------------------------------------------------

function TalkingHead:ApplyTextureKit()
    local frame = self.frame
    local kit = CurrentTextureKit()
    local regions = {
        TextBackground = frame.TextBackground,
        Portrait = frame.Portrait,
        PortraitBg = frame.Model.PortraitBg,
    }
    for key, region in pairs(regions) do
        local atlas = kit ~= "Normal" and format(TEXTURE_KIT_FORMATS[key], kit) or nil
        if not AtlasExists(atlas) then
            atlas = DEFAULT_ATLASES[key]
        end
        region:SetAtlas(atlas, true)
    end
    local colors = FONT_COLORS[kit] or FONT_COLORS["Normal"]
    frame.Name:SetTextColor(colors.Name:GetRGB())
    frame.Name:SetShadowColor(colors.Shadow:GetRGBA())
    frame.Text:SetTextColor(colors.Text:GetRGB())
    frame.Text:SetShadowColor(colors.Shadow:GetRGBA())
    frame.Title:SetTextColor(colors.Title:GetRGB())
    frame.Title:SetShadowColor(colors.Shadow:GetRGBA())
end

--- Shows or hides the portrait (model, ring, glows, book) and moves the name,
--- title and text to the frame's left edge while it is hidden. "Show the
--- talking head" is this; "Show the panel" is the whole frame.
function TalkingHead:LayoutPortrait()
    local frame = self.frame
    local shown = ns.db.showHead ~= false
    for _, region in ipairs({ frame.Portrait, frame.GlowTop, frame.GlowLeft, frame.GlowRight }) do
        region:SetShown(shown)
    end
    frame.Name:ClearAllPoints()
    if shown then
        frame.Name:SetPoint("TOPLEFT", frame.Portrait, "TOPRIGHT", 2, -19)
    else
        frame.Name:SetPoint("TOPLEFT", TEXT_INSET, -19)
    end
    frame.Name:SetPoint("RIGHT", -42, 0)
    self:Repaginate()
    if self.displayed then
        self:SetPortrait(self.displayed)
        if shown and frame:IsShown() and not frame.isClosing then
            -- the fade-in already ran while these were hidden
            frame.Model:SetAlpha(1)
            frame.Model.PortraitBg:SetAlpha(1)
            frame.Portrait:SetAlpha(1)
        end
    end
end

function TalkingHead:ApplySettings()
    local frame = self.frame
    frame:SetScale(ns.db.headScale or 1)
    self:ApplyTextureKit()
    frame.Text:SetShown(ns.db.showText ~= false)
    self:LayoutPortrait()
    if not ns.db.showPanel then
        self:CloseFrame()
    end
    self:Update()
end

-- ---------------------------------------------------------------------------
-- Display
-- ---------------------------------------------------------------------------

--- The text box in lines and a way to measure a string against it, from
--- the hidden Measure string at the box's width. The box's own size is only
--- known once the frame is laid out; until then it is estimated, and the
--- last return says so.
function TalkingHead:TextBox()
    local frame = self.frame
    local text, measure = frame.Text, frame.Measure
    local width, height = text:GetWidth(), text:GetHeight()
    local estimated = width <= 1 or height <= 1
    if estimated then
        ns.Debug("talking head text box not laid out yet, sizing pages by estimate")
        local left = TEXT_INSET + 1 -- Title's offset from Name
        if ns.db.showHead ~= false then
            left = 5 + frame.Portrait:GetWidth() + 3 -- the portrait's right edge, then Name and Title's offsets
        end
        width, height = FRAME_WIDTH - 42 - left, 0
    end
    measure:SetWidth(width)
    measure:SetText("Mg")
    local one = measure:GetStringHeight()
    measure:SetText("Mg\nMg")
    local step = measure:GetStringHeight() - one -- a line and its spacing
    if one <= 0 or step <= 0 then
        ns.Debug("talking head font not measurable, sizing pages by characters")
        return FALLBACK_LINES,
            function(str) return #str <= FALLBACK_LINES * FALLBACK_CHARS end,
            function(str) return math.ceil(#str / FALLBACK_CHARS) end,
            true
    end
    local lines = FALLBACK_LINES
    if height > 1 then
        lines = math.max(1, math.floor((height - one) / step + 1.01))
    end
    local limit = one + (lines - 1) * step + 0.5
    local function Height(str)
        measure:SetText(str)
        return measure:GetStringHeight()
    end
    return lines,
        function(str) return Height(str) <= limit end,
        function(str) return math.floor((Height(str) - one) / step + 1.5) end,
        estimated
end

--- Splits the item's text into pages that fit the box.
function TalkingHead:Paginate(item)
    local frame = self.frame
    if item ~= self.pageItem then
        self.heldAt = 0
    end
    self.pageItem, self.page = item, nil
    if not frame.Text:IsShown() then
        self.pages, self.starts = { "" }, { 0 }
        return
    end
    local lines, fits, count, estimated = self:TextBox()
    if frame.Text.SetMaxLines then
        frame.Text:SetMaxLines(lines)
    end
    self.pages, self.starts = Util.Paginate(item.text, fits, count, lines)
    self.chars = Util.CharCount(item.text or "")
    if estimated and not self.remeasuring then
        -- measure again once the frame has been laid out, keeping the place
        self.remeasuring = true
        C_Timer.After(0, function()
            self.remeasuring = nil
            self:Repaginate()
        end)
    end
end

--- Shows the page the audio has reached: its share of the play time against
--- each page's share of the text. While the audio is not playing (paused, in
--- the lead-in, queued while paused) the text stays where it was; playback
--- always starts from the top, so the text does too.
function TalkingHead:RenderPage()
    local item = self.displayed
    if not item or item ~= self.pageItem then
        return
    end
    local at = self.heldAt
    if item.startedAt then
        local elapsed = GetTime() - item.startedAt
        local length = item.playLength
        if length and length > 0 then
            at = elapsed / length
        else
            at = elapsed * READ_RATE / math.max(1, self.chars or 0)
        end
    end
    local page = 1
    for i = 2, #self.pages do
        if self.starts[i] <= at then
            page = i
        end
    end
    self.heldAt = self.starts[page]
    if page ~= self.page then
        self.page = page
        local text = self.frame.Text
        text:SetText(self.pages[page])
        if text.IsTruncated and text:IsTruncated() then
            ns.Debug("talking head page", page, "truncated:", self.pages[page])
        end
    end
end

function TalkingHead:ShowPagedText(item)
    self:Paginate(item)
    self:RenderPage()
end

function TalkingHead:SetPortrait(item)
    local frame = self.frame
    local model = frame.Model
    local shown = ns.db.showHead ~= false
    local creature = item.speakerKey and Util.IsCreatureKey(item.speakerKey)
    if shown and creature then
        model:ShowCreature(item.speakerKey, item.guid)
    else
        model:StopTalking()
        model:CancelSettle()
        model:ClearModel()
        model.loaded = nil
        model.retryCreature = nil
        model:Hide()
        frame.Book:SetShown(shown)
    end
end

function TalkingHead:Present(item)
    local frame = self.frame
    local wasShown = frame:IsShown() and not frame.isClosing
    local sameSpeaker = self.displayed and self.displayed.name == item.name

    frame.Close:Stop()
    frame.isClosing = nil
    self.displayed = item
    self:SetPortrait(item)

    local name = item.name or ""
    local title = item.title or ""
    if not wasShown then
        frame.Name:SetText(name)
        frame.Title:SetText(title)
        frame:Show() -- first, so the text box is laid out when the pages are sized
        self:ShowPagedText(item)
        frame.FadeIn:Play()
    else
        frame.TextOut:Play()
        C_Timer.After(0.25, function()
            if self.displayed ~= item then return end
            frame.Name:SetText(name)
            frame.Title:SetText(title)
            self:ShowPagedText(item)
            frame.TextIn:Play()
            if not sameSpeaker then
                frame.NewLine:Play()
            end
        end)
    end
end

function TalkingHead:CloseFrame()
    local frame = self.frame
    self.displayed = nil
    frame.Model:StopTalking()
    if frame:IsShown() and not frame.isClosing then
        frame.isClosing = true
        frame.Close:Play()
    end
end

function TalkingHead:Update()
    local frame = self.frame
    local current = Queue:Current()

    if not current or not ns.db.showPanel then
        self:CloseFrame()
    elseif current ~= self.displayed then
        self:Present(current)
    end

    local remaining = Queue:Size() - 1
    self:SetQueueCount(remaining)
    frame.SkipButton:SetUsable(Queue:Size() > 0)
    self:UpdatePause(Queue:IsPaused())
    ns.UI.QueueList:Update()
end

function TalkingHead:UpdatePause(paused)
    local frame = self.frame
    frame.PauseButton.Icon:SetAtlas(paused and PLAY_ATLAS or PAUSE_ATLAS)
    if paused then
        frame.Model:StopTalking()
    elseif self.displayed then
        self:SetPortrait(self.displayed)
    end
end

ns.OnInit(function()
    TalkingHead:Init()
end)
