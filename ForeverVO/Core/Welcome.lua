local _, ns = ...

--[[
The login window. Forever Voiceover is no longer worked on (2026-11-05), so it
says so and points at Spoken. It shows at the first login of each run of the
client, to every player, until they tick Don't Show This Again
(spokenNoticeDismissed). It replaced the introduction, the offer to send lines
and the call for volunteers, which told players the opposite; /fvo export and
the Send button under Options still work.

The window is a frame of the addon's own, not a StaticPopup. With the client's
alpha gamepad UI on, every StaticPopup that opens is handed to the gamepad
frame manager, which ends in the protected SetPreferredGamepadInteractTarget;
a popup an addon opened taints that path, the call is blocked, and the
"blocked action" popup that reports it runs the same path again, freezing the
client (issue #44). The manager only watches StaticPopups, UI panels and menus,
so a plain frame shown directly never reaches it.

"Each run of the client": PLAYER_LOGIN fires on every /reload and every
character login, and the window came up on all of them. The saved
welcomeShownAt holds when the client was started (time() - GetSessionTime())
for the run that last showed it, which no /reload or relog changes and the next
start of the game does.

The link sits in a read-only edit box, selected on a click so Ctrl+C takes it:
the client cannot open a browser, and CopyToClipboard is protected. Options'
Switch to Spoken opens the window again.
]]

local Welcome = {}
ns.Welcome = Welcome

local WIDTH = 420 -- the width of a wide StaticPopup
local PADDING = 16
local BUTTON_WIDTH, BUTTON_HEIGHT = 168, 22
local CHECK_SIZE = 26
local TITLE_GAP = 10
local LINK_HEIGHT = 22
local LINK_GAP = 8

local SPOKEN_URL = "https://www.curseforge.com/wow/addons/spoken"
local NOTICE = "We're no longer working on Forever Voiceover. Please download |cffffd100Spoken|r instead. Click the link, press |cffffd100Ctrl+C|r and paste it into your browser."

--- When this run of the client started, to the second or so: time() and
--- GetSessionTime() tick separately, so two readings in one run can differ by
--- one. SAME_RUN absorbs that; no client restarts faster.
local SAME_RUN = 60

local function ClientStart()
    return time() - GetSessionTime()
end

local function ShownThisRun()
    local at = ns.db.welcomeShownAt
    return type(at) == "number" and math.abs(ClientStart() - at) < SAME_RUN
end

function Welcome:GetFrame()
    if self.frame then
        return self.frame
    end
    local frame = CreateFrame("Frame", "ForeverVOWelcome", UIParent)
    self.frame = frame
    frame:SetWidth(WIDTH)
    frame:SetPoint("TOP", UIParent, "TOP", 0, -135) -- where StaticPopup1 sits
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:Hide()
    tinsert(UISpecialFrames, "ForeverVOWelcome") -- close with Escape

    frame.Border = CreateFrame("Frame", nil, frame, "DialogBorderDarkTemplate")
    frame.Border:SetAllPoints()

    frame.Title = frame:CreateFontString(nil, "ARTWORK", "GameFontNormalLarge")
    frame.Title:SetPoint("TOP", 0, -PADDING)
    frame.Title:SetText("Forever Voiceover")

    frame.Text = frame:CreateFontString(nil, "ARTWORK")
    frame.Text:SetFontObject("GameFontHighlight")
    frame.Text:SetJustifyH("CENTER")
    frame.Text:SetWidth(WIDTH - 2 * PADDING)
    frame.Text:SetPoint("TOP", frame.Title, "BOTTOM", 0, -TITLE_GAP)
    frame.Text:SetText(NOTICE)

    -- Spoken's link, selected so Ctrl+C copies it
    local link = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
    link:SetAutoFocus(false)
    link:SetSize(WIDTH - 2 * PADDING - 8, LINK_HEIGHT) -- the template's border sits outside its left edge
    link:SetPoint("TOP", frame.Text, "BOTTOM", 4, -LINK_GAP)
    link:SetFontObject("GameFontHighlightSmall")
    link:SetText(SPOKEN_URL)
    link:SetCursorPosition(0)
    link:SetScript("OnEditFocusGained", function(editBox)
        editBox:HighlightText()
    end)
    link:SetScript("OnMouseUp", function(editBox)
        editBox:HighlightText()
    end)
    link:SetScript("OnEscapePressed", function()
        frame:Hide()
    end)
    link:SetScript("OnTextChanged", function(editBox, userInput)
        if userInput then
            editBox:SetText(SPOKEN_URL)
            editBox:HighlightText()
        end
    end)
    frame.Link = link

    local okay = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    okay:SetSize(BUTTON_WIDTH, BUTTON_HEIGHT)
    okay:SetPoint("TOP", frame.Text, "BOTTOM", 0, -(LINK_GAP + LINK_HEIGHT + PADDING))
    okay:SetText(OKAY)
    okay:SetScript("OnClick", function()
        frame:Hide()
    end)
    frame.Okay = okay

    local dismiss = CreateFrame("CheckButton", nil, frame, "UICheckButtonTemplate")
    dismiss:SetSize(CHECK_SIZE, CHECK_SIZE)
    dismiss.Text:SetFontObject("GameFontHighlightSmall")
    dismiss.Text:SetText("Don't Show This Again")
    dismiss.Text:SetPoint("LEFT", dismiss, "RIGHT", 2, 0)
    dismiss:SetScript("OnClick", function(box)
        ns.db.spokenNoticeDismissed = box:GetChecked() and true or false
    end)
    frame.Dismiss = dismiss

    return frame
end

function Welcome:Show()
    local frame = self:GetFrame()
    frame.Dismiss:SetChecked(ns.db.spokenNoticeDismissed)
    local checkTop = PADDING + frame.Title:GetStringHeight() + TITLE_GAP + frame.Text:GetStringHeight() + LINK_GAP + LINK_HEIGHT + PADDING + BUTTON_HEIGHT + PADDING / 2
    -- Centre the box and its label together under the button.
    frame.Dismiss:ClearAllPoints()
    frame.Dismiss:SetPoint("TOPLEFT", frame, "TOP", -(CHECK_SIZE + 2 + frame.Dismiss.Text:GetStringWidth()) / 2, -checkTop)
    frame:SetHeight(checkTop + CHECK_SIZE + PADDING / 2)
    frame:Show()
end

ns.OnLogin(function()
    if ns.db.spokenNoticeDismissed or ShownThisRun() then
        return
    end
    ns.db.welcomeShownAt = ClientStart()
    C_Timer.After(4, function()
        Welcome:Show()
    end)
end)
