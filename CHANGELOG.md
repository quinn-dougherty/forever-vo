# Changelog

## 0.1.2

- Names beginning with a non-ASCII letter ("Ösel", "Élodie") are redacted
  again. The whole-word check in 0.1.1 could not fire next to such a letter,
  so those names were left in captured text and in `/fvo export`. Thanks to
  jhaubrich for the report and the fix.
- Quests whose greeting branches on your gender but whose turn-in does not now
  play the turn-in. The pack recorded one flag for the whole quest, so the
  addon asked for a gendered file that was never made and played silence. New
  packs record which events branch; packs built before this still work. Thanks
  to joergensentroels for the fix.

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
