# Changelog

## 0.1.11

- We're no longer working on Forever Voiceover. Please download
  [Spoken](https://www.curseforge.com/wow/addons/spoken) instead. The login
  window says so and carries the link, which is also under **Switch to
  Spoken** on the addon's page in Options; **Don't Show This Again** stops
  the window. It no longer asks you to send lines or calls for volunteers,
  and `/fvo export` still works.
- The queue list and the debug panel keep clear of each other and of the
  screen edge, and the queue count sits on the queue button.

## 0.1.10

- Forever Voiceover is looking for volunteers. The login window links to the
  call on GitHub, for anyone comfortable with git who'd like to help tune
  voices for the voice packs or look after the addon. It shows once with
  this version even with nothing to send, and the link is also under
  **Call for Volunteers** on the addon's page in Options.
- The talking head's portrait stands a little further back from the face.
  A speaker whose talk animation leans into the camera (skyborne women)
  pushed her face through it, and the portrait showed the inside of her
  head.
- A quest that more than one NPC takes in speaks in the voice of the one in
  front of you. The crates of "A Sealed Crate" go to Dokimi in the Barrens
  and to Marcy Baker in Redridge, and both were read in whichever voice the
  last pack happened to pick; Owen Thadd and Garion Wendell share their
  turn-ins the same way. Turning such a quest in to an NPC the voice packs
  don't know for it yet saves the line for your next export.
- Only an English game client captures and sends lines. The voice packs are
  English, and a line read on a client in another language was sent as a
  change to the English one and replaced it.
- `/fvo export` counts lines as sent when you copy the text with Ctrl+C,
  not only through **Copy Link**, and closing the window without copying
  says in chat how many lines it kept for your next export. Opening the
  window alone has not cleared them since 0.1.8, so a window closed by
  mistake loses nothing, but nothing said so.

## 0.1.9

- Voice lines play at an even volume. They used to range from quite quiet to
  fairly loud, line to line; every voice pack file is now levelled to the
  same loudness, a little louder than most were. The packs are re-released
  once for this.
- NPCs that come as men and women under one name (Ravenholdt Assassins,
  city guards, grunts, Commendation Officers, holiday revelers) speak in the
  voice of the one in front of you. The women among them were read in a
  man's voice. Meeting one the voice packs don't have in your sex yet saves
  that NPC for your next export, so the next pack adds the other voice.
- The voice packs are reorganised into six: **Classic Quests** (to level
  40), **Classic Endgame** (from 41), **Classic Gossip**, **Forever
  Quests**, **Forever Gossip** and **Books**. Install the ones you want
  from CurseForge. The old Base, Base Endgame and Data: Forever packs still
  work, but only for lines the new packs don't have, and the addon tells you
  at login that you can delete them.
- Books, letters and plaques can be read aloud by the narrator, whether the
  book is in your bags or out in the world: press **Play** under the book
  and it reads on through the rest of the book, as far as the voice pack has
  it, without you turning a page. It keeps reading when you turn the page or
  close the book, and a page you turn to beyond what was queued is read after
  it; **Stop** ends the reading. A page no voice pack
  has yet is saved for your next export, like unvoiced quests and gossip.
- The default narrator is now a skyborne male voice instead of the human
  male one, and the narrator menu says so. Skyborne female is still the
  other choice. Lines switch over as the voice packs are regenerated, so
  you will hear both for a while.
- The count of quests to send (login window, Options, minimap button) leaves
  out lines a voice pack you installed since has voiced or stopped asking
  for. It used to keep counting them until your next export.
- With greetings or gossip set to play once, a line only counts as heard
  once it starts playing. One queued behind another line and dropped when
  the window closed (a flight master's, when the map opens) was marked heard
  and never played for that NPC again.
- "Once" in **Repeat greetings and gossip** lasts until you log out,
  not for good. An NPC you heard yesterday speaks again today.
- Gossip that says "his" or "her" depending on your character plays the
  version for your character. When only a player of the other sex had sent
  the line in, you could hear theirs: Brock Stoneseeker told men about "her
  pick".
- The **Narrator voice** you pick, and the developer overlay, stay as you set
  them after logging out. Both went back to the default at every login.
- The login window asking you to send quests shows once each time you start
  the game, not on every `/reload` and every character you log in.

## 0.1.8

- The second **Narrator voice** is Skyborne female instead of Orc male, so
  the menu has a male and a female voice. If you picked Orc male, narrated
  lines go back to the default narrator until you pick again.
- The Forever Voiceover page in Options has **Send Quests to Project** (the
  same as `/fvo export`, and showing how many lines you have to send) and
  **Report Bug** buttons, and lists the addon's
  version and, under **Voice packs**, every installed pack by its full name
  and version, for bug reports. The minimap button's tooltip shows the addon
  version and how many quests you have to send, and its menu's Send entry
  the count too.
- The login window's offer is shorter: how many quests we need, and that
  sending needs a free GitHub account.
- **Don't Show This Again** in the login window holds across logins, not
  only across `/reload`: after a client restart the window came back as if
  for a first login and ignored the box.
- **Copy Link** in the export window says thank you in chat. Send with
  nothing to send leaves Options open and only says so in a popup.
- **Report Bug** opens the report window for a
  problem that is not about one line. Pick what went wrong (UI, playback, a
  Lua error, something else); the GitHub form opens titled with that and the
  first line you write, with your addon and voice pack versions filled in.
- **Repeat greetings and gossip** replaces both **Repeat gossip** and **First
  gossip only**, which overlapped: *Every time*, *Gossip once, greetings
  always* (the default), *Both once per NPC*, or *Once for quest givers*. A
  greeting is the text an NPC with more than one quest opens with; gossip is
  what an NPC says above the conversation options. Your earlier choice
  carries over, and "Never" becomes the Greetings and Gossip boxes cleared.
  "Once" is remembered on each character, not just for the session.
- The **Voice packs** options page is now **Voice Pack Debug**.
- The minimap button and the addon list show the gold microphone from the
  project's art in place of the borrowed spell icon.
- `/fvo export` fits every export into links. A GitHub link only holds about
  25 lines, and a longer export used to leave its string out of the link and
  say so in a message that ran off the window. Now it comes in parts: **Next**
  and **Previous** step through them, each part's Copy Link opens its own
  issue, and a note you type carries over to the next part. Lines only count
  as sent once their part's link is copied, so closing the window halfway (or
  opening it and not sending) leaves the rest for your next export. Errors,
  such as a note too long for the link or nothing to export, show in a popup
  instead of chat. **All at Once** sends a long export as one issue instead:
  the link opens the form, and you copy the export string into it from the
  box below the link (up to about 250 lines per issue).
- The talking head no longer cuts long lines off with "...". Pages are sized
  to the text box, so each shows in full, and there is room for a fourth line.
  Pages now keep time with the voice through a pause (the page holds),
  Resume and Replay (back to the first page), and lines read in several parts.
- Pause, Skip and Queue are small gold buttons under the close button, in
  place of the red button row. "N more queued" moved to the title line. The
  buttons fade out before the panel does.

## 0.1.7

- The login window now offers to send what you have seen. After the first
  login it comes back only when you have quests or NPC lines the voice pack
  does not have yet, and says how many (sending them needs a free
  GitHub account). **Send Quests to Project** opens the
  `/fvo export` window; **Later** closes it. Tick "Don't Show This Again" to stop it
  appearing; clear **Opt out of crowdsourcing** on the
  Forever Voiceover page in Options to be asked again.
- **First gossip only**, to the right of **Gossip** in What to voice and on
  by default: an NPC's gossip is read the first time you talk to them, and
  not every time after. Greetings and quest text are read as before. It greys
  out when Gossip is off.
- The first line of a conversation waits a moment before it plays, so the
  NPC's own greeting can finish instead of being cut off. Later lines in the
  same window, quests started from an item, and the quest log's Play button
  do not wait.
- A quest line the voice pack reads from other text than the one you saw is
  saved and goes out with `/fvo export`, like an unvoiced line. Some of the
  Classic text the packs were made from is cut short or was reworded for
  Forever (Greatmother Hawkwind's thanks in "A Humble Task" stops after two
  sentences); your reading replaces it. Packs made before this say nothing
  about their text, so the check starts with the next voice pack.
- An NPC's looks are read more carefully. Talking to one NPC right after
  another could record the first one's model for the second, and the voice
  packs pick a Forever NPC's voice from it: Fizzlefuse, a goblin, was voiced
  as an orc. When the NPC you see is not the one the pack cast the voice
  from, `/fvo export` sends that NPC along, so the pack can fix the voice.
- NPCs that share one name but come as men and women, such as the
  Peacekeepers on Zephras Isle or city guards, speak in the voice of the one
  in front of you. The voice packs carry both once someone has met both
  (starting with the next voice pack), and the addon remembers every sex it
  has seen an NPC as, so `/fvo export` fills in the rest.
- `/fvo export` now includes your character's class and race with each line,
  so the pack can tell where a quest says your class ("Greetings, mage")
  from where it means the word. Your character name is still removed.
- NPC greetings no longer go quiet after a `/reload` or logout in the middle
  of a voiced line. The addon turns the game's dialog sound down while a line
  plays, and a reload at that moment left it off for good, since the client
  saves that setting; it is now turned back on at the next login. Pausing
  the queue also lets the game's own dialog through again.
- **Developer overlay**, under Voice packs in Options and off by default:
  a small window beside the quest or gossip frame with the sound file, the
  voice that plays, and the NPC's display and model IDs. It is meant for
  people working on the voice packs, and useful in a report about a wrong
  voice.

## 0.1.6

- The welcome no longer says a voice pack is coming and that you will hear
  nothing; the packs have been on CurseForge since September 22.
- `/fvo export` opens the contribution as text, the same way a report does.
  Edit the note, then Copy Link, and paste the link into a browser. The
  Contribute captured lines form is filled in, and the export string is under
  its own heading. A string too long for the link is left for you to paste
  into the form. Your character name is removed.
- The bug icon on the talking head, and `/fvo report`, opens the text of a GitHub
  issue about the line that is playing. Edit it, then Copy Link, and paste
  the link into a browser. The title and the description are already filled
  in. It names the voice that read the line. The character name is removed
  from the text. You still pick what is wrong on the form, and submit.
- `/fvo export` packs only what you have heard since your last export, and
  says so; `/fvo export all` packs everything again, for a string that was
  copied but never pasted. Until now every export carried the whole capture,
  and the chat notice on each unvoiced line asked for an export, so exporting
  after every quest, as it seemed to suggest, sent the same hundred lines a
  hundred times over. The per-line notice now only says the line is saved,
  and the welcome text and the export window both say you do not have to do
  this for each quest.
- Right-click on the minimap button (or the addon compartment entry) has
  "Send quests to project": the same as `/fvo export`, without typing it.
- The reminders about lines worth contributing now say what this session
  added and what was already waiting from earlier ones. The beta client keeps
  saved variables between logins again, so the capture accumulates across
  sessions and characters, and the old logout message counted all of it as
  "this session" ("141 lines" after a fishing trip). Nothing is forgotten by
  logging out any more; `/fvo export` packs everything whenever you like.
- The narrator menu names the voices you will hear, "Human male" and "Orc
  male", instead of "Narrator" for the first. The default narrator is cloned
  from the human male recordings, so that is what it is.

## 0.1.5

- Skyborne characters no longer leave "skyborne" in the lines they capture.
  The client reports the race as "Windshaper Skyborne" but reads `$r` as
  "skyborne" alone, so the word was never put back and Zamja's greeting was
  voiced "Can Zamja help you, skyborne?" for everyone. The last word of a
  multi-word race now counts. Lines captured by a Skyborne on an earlier
  release are fixed from the original text where the pipeline has it, and
  asked for again where it does not, so playing through them once on this
  release settles them.
- The first-login welcome no longer freezes the client when the Gamepad
  (Alpha) UI is on (#44). It was a standard Blizzard popup, and in gamepad mode
  the client hands every such popup to a path that addons are not allowed to
  trigger, then blames the addon and shows the same kind of popup again. The
  welcome is now a plain window of the addon's own, which that path never sees.
  Thanks to BrandtChristian for tracing it through Blizzard's code.
- No more "secret string value" error at NPCs whose identity the client hides
  from addons (the Disciple of Naralex was the first). The client hands over a
  sealed value in place of such a unit's name and GUID, and the addon now
  treats it as unknown: the speaker comes from the voice pack, or the line is
  skipped, instead of the gossip handler failing.

## 0.1.4

- The talking head shows the speaker you are actually talking to. It used to
  ask the client for the model by creature ID, which on this client comes back
  empty for some NPCs (Varimathras, for one) and left the previous speaker's
  face in the portrait. Now it takes the model from the NPC on screen, and a
  speaker the client cannot draw gets the narrator's book instead of a
  stranger.
- The export window now points to a "Contribute captured lines" issue form,
  one issue per export, instead of the shared inbox thread, which had grown
  past fifty comments. The bot decodes it, reacts with 🚀 and closes the
  issue. Comments on the old inbox still work.
- "Show the talking head" now hides just that: the speaker's portrait goes
  and the parchment, name and text stay, moved over to the left edge. Hiding
  the whole frame is the new "Show the panel" option (`/fvo panel`), for
  audio only.
- Quest lines that address you by gender ("lad" or "lass", "sir" or "madam")
  are no longer voiced with whichever word the first contributor happened to
  hear. The client resolves that choice before the addon sees the text, and
  unlike your name, class and race the other word is simply gone, so the
  pipeline now puts the branch back by comparing a male and a female reading
  of the same line. To make that possible the capture and `/fvo export` record
  your character's sex (one letter, nothing else new leaves the client), and a
  voice pack can ask for a line to be heard again: when the pack says it still
  lacks your sex's reading of a quest, that quest goes into your export even
  though it played. The same hook lets a future pack ask for any line whose
  capture has gone stale. Where the raw text is known (Classic, the beta quest
  cache) a single reading that matches it is fixed at once; thanks to
  jhaubrich for that part and for raising the problem (#28, #29).
- The export and logout messages count lines worth contributing, not only
  lines without audio.
- Captures now say which addon version heard the line and when. Every release
  so far has fixed something an earlier one recorded wrongly, and the pipeline
  can now prefer a line heard by a fixed release over one heard by an older
  one, whatever order they arrive in, instead of whichever was posted last.
  Until a quest line has been heard by this release or later, the pack asks
  everyone for it, so it goes into your export even though it played; the
  flawed recordings get replaced as people simply keep playing.

## 0.1.3

- Stage directions are read by the narrator. Lines like "Hmm... <Jorgen looks
  up at you through squinted eyes.> All right, I'll help ya" used to skip the
  part in angle brackets; now the narrator says it between the NPC's words, in
  whichever narrator voice you picked. Lines that were nothing but a stage
  direction, silent until now, are voiced too. Needs a voice pack built after
  this change; older packs play as before.
- The "no voice pack found" messages name the CurseForge packs to install.
- A quest read from an item, or turned in at a game object, is no longer
  credited to the last NPC you spoke to. The client's "npc" unit outlives its
  dialog, so Admiral Proudmoore's orders were captured and voiced as Gar'Thok
  and the Corpse Laden Boat's turn-in text as High Executor Hadrec, face and
  all. Quest events now trust the quest giver unit the way Blizzard's own frame
  does, an item-started quest is named after its item, and the book shows for
  it instead of the turn-in NPC. Packs built after this record the giver and
  the turn-in speaker separately, so a turn-in at an object shows the object
  even when the client does not say who is speaking.
- The Classic voice pack now comes as two downloads, Base (quests to level 40
  and all gossip) and Base Endgame (quests from 41), because CurseForge caps a
  file at 1 GB. The "no voice pack found" message names both.

## 0.1.2

- A character whose name is an ordinary word ("It") no longer has that word
  eaten out of every line it hears. The client always writes a character's
  name capitalised, so the name is now matched only in its own case; class and
  race still match in either case, because the server writes both.
- Names beginning with a non-ASCII letter ("Ösel", "Élodie") are redacted
  again. The whole-word check in 0.1.1 could not fire next to such a letter,
  so those names were left in captured text and in `/fvo export`. Thanks to
  jhaubrich for the report and the fix.
- Quests whose greeting branches on your gender but whose turn-in does not now
  play the turn-in. The pack recorded one flag for the whole quest, so the
  addon asked for a gendered file that was never made and played silence. New
  packs record which events branch; packs built before this still work. Thanks
  to joergensentroels for the fix.
- The pipeline now reads the addon version an export carries, so it can tell
  which repairs a submission needs.

## 0.1.1

- Lines no longer call you by the wrong class. The client fills in $n, $c and $r
  before an addon can read the text, so a quest first heard on a rogue was
  recorded saying "rogue" and then said that to everyone. Captures now store the
  placeholders, and the narrator says "adventurer" instead.
- `/fvo export` no longer carries your character's name, class or race: the
  placeholders go back in as the line is captured, so a submission says what the
  NPC said and nothing about who heard it.
- Gossip matches whoever is reading it. A greeting recorded on one class used to
  hash differently for every other class, so it often fell back to fuzzy matching
  or went silent.
- The narrator's voice is now yours to pick. Quests and gossip from objects and
  items have no speaker, so a narrator reads them; voice packs can carry those
  lines in several voices, and Options > Audio > Narrator voice chooses one
  (`/fvo narrator` cycles). Lines the chosen voice has no recording for keep
  the default narrator.
- The talking head now uses your faction's parchment by default; clear
  Options > Talking head > Faction parchment style for the dark panel.
- Fixed the queue panel's "nothing else is waiting to play" line hanging off
  the left edge of the panel.
- The play buttons on the quest list rows are gone: they covered the status
  icon the quest log draws there ("..." for in progress, "?" for ready to turn
  in). Open a quest to read it and the Play button beside Back does the same
  job, next to the text it reads.

## 0.1.0

First release: the player addon, without a voice pack. This version collects
the lines players see so the pack can be generated from them.

- Talking head styled after the client's own, with the speaker's model, name,
  quest title and the text paged in time with the audio.
- Queue with pause, skip, clear and reorder; a queue panel; play buttons in the
  quest log list and next to Back in the quest details.
- Options under Escape > Options > AddOns, an addon compartment entry with a
  playback menu, `/fvo` commands.
- Capture of every quest and gossip line seen, `/fvo export` to contribute
  them, a welcome note and chat reminders while no pack is installed.
- Voice packs register through `ForeverVO.RegisterPack`; quests are keyed by
  ID and gossip by speaker and text hash, with a fuzzy fallback.
