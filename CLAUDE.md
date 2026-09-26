# CLAUDE.md — Forever Voiceover

Notes for agents working in this repo. Read this before touching anything.

## What this is

Voiced quests and NPC dialogue for **World of Warcraft: Forever** (the
Classic-plus client, codename Camelot). Two addons plus a Python pipeline:

- `ForeverVO/` — the player addon. Lua, Retail-engine APIs only, no libraries.
- `ForeverVO_Data/` — the voice pack (generated Lua tables + mp3s). Only the
  tables are in git; the audio lives on the maintainer's machine and ships as
  a separate zip.
- `tools/` — capture ingestion, bulk text sources, voice reference building,
  Chatterbox TTS generation, pack table writer, packaging helpers.
- `captures/` — community `/fvo export` submissions, committed by a GitHub
  Action from comments on the pinned issue #1.

Owner: Quinn Dougherty (quinn@for-all.dev). CurseForge projects: addon 1705010,
delta pack 1705094, base pack 1705100 (IDs under `[release]` in `forever-vo.toml`; API key only in the gitignored .env). Addon
slug `forever-vo`, display name "Forever Voiceover". License MIT; voice packs
are non-commercial fan content (Blizzard's text, voices cloned from the
game's own recordings).

## The client, and why so much is unusual

- Product `wow_classic_beta`, version 1.60.1, **Interface 16001**, TOC suffix
  `_Camelot`. It runs the **Retail engine** (`WOW_PROJECT_ID ==
  WOW_PROJECT_MAINLINE`) with Classic data. Port from Retail code paths, never
  from Classic ones.
- Blizzard's UI source for this exact build is in the Gethe mirror, branch
  `forever` (`github.com/Gethe/wow-ui-source`). A sparse checkout was used
  at `~/Projects/wow-ui-source`. Check there before assuming an API or frame
  exists. Camelot-specific overrides live in `*/Camelot/` folders.
- `docs/forever_api.json` is a captured list of the client's global
  functions, frames and `C_*` namespaces. `./tools/run.sh tools/apicheck.py`
  diffs the addon against it. Run it after any Lua change.
- Removed globals that bite: `MouseIsOver` (use `frame:IsMouseOver()`),
  `SetDesaturation` (use `texture:SetDesaturated()`),
  `InterfaceOptions_AddCategory` (use the Settings API),
  `GetGossipText` (use `C_GossipInfo.GetText()`). `GameTooltip:SetText`
  rejects the old 6-argument form.
- **Unit identity can be secret.** `UnitName`, `UnitGUID`, `UnitSex`,
  `UnitRace` and `UnitClass` return a *secret value* while the unit's identity
  is restricted (`ShouldUnitIdentityBeSecret`); it displays through `SetText`
  but errors in any string operation, comparison or table key ("secret string
  value", first seen at the Disciple of Naralex, 2026-09-25). Every read of a
  unit's identity or of dialog text goes through `Util.Plain`, which turns a
  secret into nil so the speaker falls back to the pack or the line is skipped.
  Keep new reads behind it; `issecretvalue` is in `docs/forever_api.json`.
- **Saved variables persist again since about 2026-09-24.** Until then this
  beta wrote them on logout/reload but never read them back, so every session
  started from defaults; the owner and others confirmed the fix on 2026-09-25,
  and the capture DB on disk spans two days and four characters. Leftovers of
  the old state, all harmless: the unvoiced-line reminders default on, the
  welcome popup's "seen" flag and the narrator pick live in addon-registered
  CVars as well as the settings, and ingest treats every write of the file as a
  merge (it always did, and must keep doing so: the DB now accumulates across
  sessions and characters, so one write carries days of play). The reminders
  count what this session recorded apart from what was already waiting
  (`Capture:Summary`'s last two returns) since 0.1.6; before that the logout
  reminder said "141 lines this session" for two days of captures.
- The addon compartment exists in the code but does not show on the Camelot
  minimap skin, hence our own minimap button.
- Quest and gossip **text is not in the client files**. The server sends it.
  Client tables on wago.tools (`QuestV2`, `BroadcastText`) carry no usable
  text. Text comes only from: in-game capture, the client's own quest cache
  (`Cache/WDB/enUS/questcache.wdb`, offers only), and the open Classic
  database snapshot (VMaNGOS, for unchanged Classic content).

## Addon conventions

- Every file starts `local _, ns = ...`; only `ForeverVO` (the namespace) and
  the three compartment handlers are globals. Modules register with
  `ns.OnInit`/`ns.OnLogin`.
- UI follows Blizzard's own frames: the talking head is a rebuild of
  `TalkingHeadFrame` (same atlases, anchors, animations); buttons use
  `UIPanelButtonTemplate`; options use `Settings.RegisterAddOnSetting` and
  friends; the queue is a `CallbackRegistryMixin`; rows use frame pools.
  Keep that discipline: no embedded libraries, native look.
- Voice pack format is documented at the top of `ForeverVO/Core/Packs.lua`.
  Quests are keyed by ID and event with durations; gossip by speaker key
  (creature ID, negative for game objects) plus a text hash, with a Jaccard
  fuzzy fallback.
- **The text hash must stay identical in Lua and Python**:
  `Util.Tokenize`/`Util.NormalizeText`/`Util.HashText` in `Core/Util.lua` and
  `tools/textkey.py`. If you touch one, touch the other and re-test with
  `./tools/run.sh tools/textkey_parity.py` (the dev shell supplies lua 5.1),
  which runs both over the real captures, the tokenised quest cache and edge
  cases.
- **The client expands `$n`, `$c` and `$r` before any addon sees the text.** A
  line first heard on a rogue would otherwise be recorded saying "rogue" and
  voiced that way for everyone, and its gossip hash would only match other
  rogues. `Util.Tokenize` puts the placeholders back at capture time (capture
  version 3 also records the reader's class and race), `FindGossip` tokenises
  the live text before hashing, and `NormalizeText` drops the placeholders on
  both sides so one recording matches every class. `[readers.legacy]` in
  `forever-vo.toml` covers captures made before version 3; `ingest.py` re-runs
  the reversal on every ingest, which is idempotent. Since addon 0.1.2 the
  name is matched case-sensitively (the client always renders it capitalised,
  so a lowercase match is the word, not the name) while class and race still
  fold case (`$c` renders "rogue", `$C` "Rogue"); both sides are ASCII-only
  on purpose, since Lua patterns cannot fold Unicode.
- **Ingest repairs two ways Tokenize goes wrong**, in the same idempotent pass
  (`repair_entry`: tokenize, un-glue, reconcile). Addon releases before the
  whole-word fix tokenised inside words (`w$nh`, `$Cs`), and a placeholder
  touching a letter or digit is treated as corruption: the literal word is put
  back when the reader is known, otherwise the entry is dropped so the line
  gets re-captured. Community exports carry no name, class or race, so
  `[readers.community]` in `forever-vo.toml` maps an export's `origin` (the
  issue comment id, stamped on each entry at ingest) to what the poster said;
  `restore_name` marks a name that is an ordinary word ("It"), whose every `$n`
  is put back; for exports from addon 0.1.2 on (`addon` in the decoded file,
  stamped on each entry like `origin`) only `$N` is put back, since that addon
  can only have written the name capitalised. The glued check skips the `B`
  of a `$B` line break, or raw text (`$B$B$n`) would read as corruption.
  Tokenize also cannot tell a Mage's expanded `$c` from a literal
  "mage", so where the raw text is known (`bulk/questcache.json` by quest key,
  `bulk/classic.json` gossip by speaker, closest line) a capture placeholder
  that aligns to a plain word there is restored to that word, and (since
  2026-09-25) a plain word that aligns to a placeholder there becomes that
  placeholder, whatever got it past Tokenize; the alignment
  must score 0.9 or better, so lines Forever rewrote are left alone. Only the
  placeholders are reconciled; the capture's text otherwise wins. Two readers
  of different class or race who capture the same quest also settle it in
  `merge_entry` (gossip keys differ by hash, so that only helps quests), and
  there only the placeholder side is corrected, since between two captures the
  placeholder is the suspect (a Mage's "$c of Dalaran").
- **A multi-word race renders `$r` as its last word alone**: UnitRace says
  "Windshaper Skyborne" and Zamja's `$r` came out "skyborne", so until 0.1.5
  no Skyborne capture ever had `$r` put back (2026-09-25; Mebok Mizzyrix's
  "the orc I'm looking for" was the same shape on a legacy Orc reader whose
  `[readers.legacy]` entry named only the class). Both tokenizers now also
  match the race's last word, but ingest applies that rule only to captures
  from 0.1.5 on (`SHORT_RACE_SINCE`, `short_race` on `tokenize`/`text_key`):
  Forever's own text says "skyborne" literally all over Zephras Isle, and
  re-tokenising an old capture would turn every one of those into `$r`. Older
  captures are repaired from the raw text where it exists and re-asked where
  it does not (see `KNOWN_FLAWS`, next item). Do not give a Skyborne legacy
  reader a race in the TOML for the same reason.
- **The client resolves `$g lad:lass;` too, and one reader cannot reverse it**:
  the other branch is gone. Since addon 0.1.4 (capture version 4, export field
  `g`) the capture records the reader's sex, and ingest puts the branch back
  two ways, both idempotent: `restore_gender` hands the raw source text back
  when a capture is that text read as one sex (jhaubrich's #28), and
  `merge_gender` rebuilds `$g his:hers;` from a male and a female reading that
  differ only in short aligned runs (`rebuild_gender`: at most three branches
  of four words, alignment 0.6, no inserts). Identical readings settle the
  line with `sex: "mf"`; a later reading that no longer matches (Forever
  reworded the quest) outvotes a settled entry, so it is asked for again.
  `needs_of` records on each quest entry which readers are still wanted
  (`needs`: `f` after a male reading, `mf` when the reader is unknown, none
  when the text matches raw source text without a branch), `rebuild_tables`
  writes it as `wa`/`wp`/`wc` on the quest record, and the addon
  (`Packs:QuestWanted`, `Capture.Contributes`) captures and exports such a
  line even though it is voiced, with `wanted` set. That is the general hook
  for any later change in what a capture must carry: make `needs_of` ask and
  players re-supply the line; no hand-built exports, no sharing of
  `questcache.wdb` (the owner declined both on #29). Gossip stays resolved:
  it is keyed by a hash of the live text. Note that `release_pack.py
  --if-changed` fingerprints sound files only, so new `w*` markers reach
  players with the next delta that carries a new file. `./tools/run.sh
  tools/gender_check.py` runs fixed cases and Classic's quest 233 through all
  of it; run it after touching any of those functions.
- **Self-repair is a development principle**: every addon release so far has
  recorded something wrong that a later one fixes, and the fix must reach
  lines already captured through ongoing play, never by hand. Since 0.1.4
  every capture carries the addon version (`addon`, on owner entries too) and
  exports carry per-line time (`d`), and `merge_entry` ranks readings by
  `capture_rank`: newer addon first, then later reading, unknown addon lowest.
  `trusted_since` under `[readers]` in `forever-vo.toml` (0.1.4) names the first release
  believed at face value: `needs_of` answers `mf` for any quest entry from
  before it, so the pack asks everyone for the line until a trusted capture
  wins it (unless, since 2026-09-25, the repaired text is Classic's own with no
  branch and Classic names the same speaker: a re-read could show nothing the
  raw text does not, and 179 of 510 legacy lines were being asked for on that
  basis, Spron's two Sten Stoutarm lines among them), and `superseded_gossip` in `backfill` drops an untrusted gossip
  line once a trusted one from the same speaker aligns at 0.9 (gossip keys by
  hash, so the flawed reading would otherwise sit beside its correction for
  good and the addon's fuzzy match could play it). Raise the constant when a
  release fixes what its predecessor recorded *and the blast radius is
  everything*; when it is narrower, add the flaw to `KNOWN_FLAWS` in
  `ingest.py` instead (since 2026-09-25): a `(fixed_in, predicate)` pair, and
  `trusted()` refuses an entry from an earlier addon that the predicate
  matches, so `needs_of` re-asks exactly those lines and `superseded_gossip`
  lets a fixed capture replace them, while the same reader's other lines stay
  believed. The predicate tests the repaired text, so a line the raw source
  already fixed drops out on its own; the first one is a pre-0.1.5 capture by
  a multi-word-race reader whose text still holds that race's last word (7
  lines on 2026-09-25, all Zephras Isle content with no raw text).
  `superseded_gossip` also compares a flawed line as the fixed addon would
  tokenise it, since a short line ("Help you, skyborne?") never reaches the
  0.9 alignment on one changed word. When designing any capture
  change ask how a line the previous version captured gets replaced. The
  cost is regeneration, which only happens when the spoken text or voice
  actually changes; the owner accepts it. Checked by `gender_check.py` and
  `tests/test_ingest.py`.
- `luac -p` every changed Lua file (`./tools/run.sh luac -p <file>`; the dev
  shell has lua 5.1).
  There is no in-game test harness; the owner tests by `/reload`.

## Pipeline (tools/)

`tools/` is the Python package of the root `pyproject.toml` (flat: no `src/`,
`tools/__init__.py`, modules import each other as `from tools.config import
...`). Since issue #31 (2026-09-24) the toolchain is pinned three ways:
`.python-version` and `uv.lock` for the interpreter and every package,
`flake.nix`/`flake.lock` for what is not Python (uv, ffmpeg, lua 5.1 and the
shared libraries the CUDA wheels need, on the library path from the shell
hook). **Python is only ever invoked through uv**: the dev shell sets
`UV_PYTHON_PREFERENCE=only-managed`, uv fetches the interpreter itself (it
runs on NixOS through nix-ld, which the host has), and there is no system
Python fallback. `tools/run.sh` is `nix develop -c uv run "$@"` (plain
`uv run` off NixOS), so `./tools/run.sh tools/<x>.py`, `./tools/run.sh
fvo-<x>` (the console scripts in `pyproject.toml`), `./tools/run.sh python -c
...` and `./tools/run.sh luac -p ...` all run against the same pins. The GPU
stack (`chatterbox-tts`, pinned to the release every voice was generated on,
and `setuptools<81` for perth) is the `tts` dependency group, on by default;
`--no-group tts` skips the ~3 GB of torch for a checkout that only ingests or
releases. `tts-rocm` is the same Chatterbox pin on the ROCm 6.2.4 wheel
(`torch==2.6.0+rocm6.2.4`, the matching `torchaudio`, `pytorch-triton-rocm==3.2.0`),
for audition on an AMD GPU. The groups conflict, so the lock holds both and the
default stays `tts` — do not point the nightly at the ROCm index. Setup is in
the README: the wheel bundles the ROCm userspace (~17 GB unpacked, so
`.venv-rocm` needs a real disk, not a tmpfs), the machine needs `/dev/kfd`,
and the run is
`UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm audition`.
Checked on gfx1030, where hipBLASLt falls back to hipblas and the line still completes.
`Synth` loads that build only when audition asks (`allow_hip`); `fvo-generate`
refuses it, and audition's "Write to pack" returns 400, because the sound index
records text and tuning, not which GPU rendered the file. The GitHub ingest
workflow runs `exportfile.py` (stdlib only) with `uv run --no-project`. Bump a
pin with `uv lock --upgrade-package <name>` or `nix flake update`, and commit
the lock; the sound index only regenerates a file when its text or voice
changes, so a library bump changes no audio on its own.

**Configuration is `forever-vo.toml` at the root**, validated by the pydantic
models in `tools/config.py` (`Voices`, `Tts`, `Pronunciations`, `Readers`,
`Release`, together `Config`; an unknown key is an error). The rule for what
goes there: if someone edits it, it is TOML; if nobody does (paths, the
client's race IDs), it stays a Python constant. `load_config()` reads the file
once, and a function that needs a section takes that model by type hint:
`generate.load_items(..., catalog)` and `rebuild_tables(..., config)`, every
ingest repair takes `readers: Readers`, `release_pack` takes `Release`. The two
leaf helpers called from everywhere, `textclean.clean()` and
`wowdata.voice_for_npc()`, accept their model as a keyword and default to the
repository's file, so one-liners keep working. There is deliberately no
per-line table (`[lines."91741-accept"]`): the file would grow without bound.
`./tools/run.sh pytest` runs `tests/`, which loads the real TOML and checks
the tuning resolution; `ruff check` and `ty check` are both clean and should
stay so.

**`uv run audition`** (`tools/audition/`, a FastAPI app and one `index.html`,
no front-end framework, on port 8765) is the page for ear tests: pick a line
from the corpus or type one, a voice, optionally a clip to clone from, a grid
of exaggeration and cfg_weight values and a number of takes, and get a player
per take with the resolved recipe beside it. "Keep these settings" writes
`[tts.voices.<voice>]` (or a `[pronunciations]` entry from the sidebar) into
`forever-vo.toml` through tomlkit, so the comments survive, validated by the
models before the file is replaced. "Write to pack" regenerates one line's
pack file under the *saved* configuration only and records the fingerprint
`generate.py` would compute, so the nightly run neither redoes nor misses it;
it is disabled until the row's recipe is the saved one. On the ROCm build
that button is refused; keeping a voice's settings in the TOML still works,
and the CUDA generator restages the voice from them. Takes go to
`tools/data/audition/<session>/` (gitignored). One model instance, loaded on
the first take; `--config` points it at another TOML for experiments, `--cpu`
allows a GPU-less machine. It was chosen over gradio on purpose: the widgets
we need are plain HTML, and the addon's own rule of no libraries and a native
look carries over. Parts (`-p1-`) and alternate narrator files are not
reachable from it yet.

Data flow (all JSON is the source of truth; `ForeverVO_Data/Data/*.lua` is a
build artifact, never hand-edited):

1. `ingest.py` merges `WTF/Account/*/SavedVariables/ForeverVO.lua` and
   `captures/*.json` into `tools/data/capture.json`.
2. `classicdb.py` exports the VMaNGOS SQLite snapshot to
   `tools/data/bulk/classic.json` (ignored, 7 MB, regenerable).
   `wdbcache.py` decodes the beta quest cache to `bulk/questcache.json`
   (versioned). Precedence when merging: capture > questcache > classic.
3. `generate.py` picks a voice per speaker, synthesises with Chatterbox on the
   GPU, writes mp3s under `ForeverVO_Data/Sounds/`, rebuilds the tables every
   25 files, and records the voice (`v`) and a hash of the spoken text (`t`)
   per file in `sound_index.json`, so a file is regenerated when its resolved
   voice changes (e.g. a guessed Skyborne male giver turns out female once
   captured) or when the text itself is corrected — a quest file keeps its
   `<questID>-<event>` name, so nothing else would notice. Entries written
   before `t` existed have none and are left alone rather than all regenerated.
   Narrator lines (quests and gossip from objects, items and speakers with no
   gender) are generated once per voice in `[voices]` of `forever-vo.toml`: the
   `narrator` is the default and keeps the plain path and `sound_index` key, the
   `narrator_alternates`
   rest go to `Sounds/<Quests|Gossip>/Narrator/<voice>/` with
   `Narrator/<voice>/<base>` as their index key. The addon needs each
   alternate's own duration, so quests get `Data/Narrator.lua`
   (`pack.narrator`, indexed into `pack.narratorVoices`) and gossip entries get
   an `n` field indexed the same
   way. Alternates sort last in the todo list, so a time-boxed run still spends
   its GPU on unvoiced lines; `--narrator-only` / `--narrator-voices` control a
   dedicated pass.
4. `build_voice_references.py` makes cloning clips under `tools/voices/`
   from the client's own audio via wago.tools. **Only the first 6 s (t3) and
   10 s (s3gen) of a reference reach the model**, which continues it, so a clip
   is composed rather than pooled: a head of the race's spoken emote lines
   (`EmotesTextSound`, JOKE and FLIRT - the rest are wordless), then that
   speaker's own greetings starting inside the 10 s window. Each voice gets a
   different slice of the shared emote pool, or archetypes of one race come out
   byte-identical. Skyborne have no spoken emotes, so `VocalUISounds` is their
   head. `--named` builds `npc-<displayID>.wav` for kits used by 3 or fewer
   models (Varimathras, Thrall, Sylvanas, ...).
   `wowdata.voice_for_npc` prefers `npc-<displayID>.wav`, then the archetype the
   display is cast with (`CreatureDisplayInfo.NPCSoundID` ->
   `<race>-<gender>-s<set>.wav`, at most 3 per race), then race+gender
   from `CreatureDisplayInfoExtra` via the display ID, then the same via the
   captured `modelFileID` (`CreatureModelData` -> the display rows using that
   model, majority vote narrowed by UnitSex and the zone hint), then zone
   hints, then narrator. The archetype step is skipped when its clip would be
   identical to the race's, and when the set belongs to another race - 26 sets
   span more than one, and trusting the name once had night elves read by a
   blood elf recording.

Voice quality notes: Chatterbox on an RTX 3080 does ~6 s of audio in ~5 s
with the game closed, roughly 3x slower with it open. Perth (the watermarker)
needs `setuptools<81`. Text cleaning rules mirror the original VoiceOver
tool (`$B` newlines, `$N`/`$C`/`$R` substitutions, `$G` gender branches as
m-/f- file variants). Angle-bracket stage directions are the narrator's: a
speaker's whole-line file leaves them out, and the line also gets *parts*
(`textclean.segments`, `Item.variants().parts`), one file each in reading
order, `<questID>-p<i>-<event>` / `<speaker>-p<i>-<hash>`, the speaker's in
their voice and the stage directions in the narrator's (plus every alternate
narrator voice, under `Narrator/<voice>/`). The tables record them as
`aP`/`pP`/`cP` on the quest record, `P`/`nP` on a gossip entry and
`<letter>P` in the narrator table; the addon plays them back to back and
swaps in the chosen narrator. The whole-line file stays for older addons; a
line that is only a stage direction has parts and no whole-line file. The
part number sits *before* the last name segment on purpose: `sound_folder`
and every older tool tell quests from gossip by that segment, and an older
generator still running probes any file it finds under `Sounds/`.

## Automation on the owner's machine (NixOS, systemd user units)

Installed by `tools/install-timer.sh`:

- `forever-vo-ingest.path` — fires on every write of the saved-variables
  file; runs `tools/ingest.sh` = pull, ingest, push `capture.json`.
- `forever-vo-daily.timer` — 04:00 nightly: sync, voice captured lines,
  work the bulk backlog for 2 h, rebuild tables, commit and push.
- `forever-vo-bulk.service` — the long bulk run, `Restart=on-failure` so a
  CUDA context lost to suspend just resumes (existing files are skipped).
  `WantedBy=default.target` (since 2026-09-21), so a reboot resumes it on its
  own; `Restart=on-failure` only covers a crash while running, not a reboot.
  Because it is then normally up at 04:00, `daily.sh` stops it for the duration
  of the nightly run and restarts it from an `EXIT` trap — it used to just
  bail out, which would have skipped the captured pass, the table rebuild and
  the delta upload for as long as bulk stayed up. The service exits 0 once its
  list is empty and nothing but a login starts it again, so since 2026-09-25
  the trap also starts it when a `--dry-run` at the end of the nightly run
  still counts files to generate: work that appears later (a rebuilt clip, a
  voice change, a merged branch) gets the whole GPU, not two hours a night.
  It runs `tools/bulk.sh`,
  which starts `FOREVER_VO_WORKERS` (default 1 since 2026-09-24; 2 until the
  Classic backlog finished on 2026-09-23) `generate.py --shard i/N`
  processes: one autoregressive stream leaves the GPU about 60% idle, and on
  the 3080 two together measured 2.59x realtime against 1.68x for one, while
  three were no better than two and crowd the 16 GB. One worker is the
  default now so a run that starts while the owner plays does not fight the
  client for the GPU; set `FOREVER_VO_WORKERS=2` for a big run with the game
  closed.

Do not add a periodic pull timer; the owner declined it. Parallel *shards* are
fine — `save_sound_index` merges only the keys a process wrote since its last
save (`dirty`) into the file on disk, under an `flock`, and an entry never
replaces one of higher `index_rank` (a duration probed by a table rebuild is
rank 0, a generator's record with voice and fingerprint rank 3); the pack
tables and the index are written through pid-named temporary files and
renamed, and each mp3 is encoded to a `.part` file and renamed. Before
2026-09-22 the merge let each worker's whole in-memory copy win, so the
placeholders one worker probed for the other's fresh files erased the other's
voice and fingerprint: 2,790 entries lost them in two days of two-worker runs
(repaired from the journal, see `tools/repair_sound_index.py`). Two
*unsharded* generators are still wrong: they would walk the same todo list and
race for the same files.

## Releases

Three CurseForge projects, three release paths:

- **Addon** (1705010, slug `forever-vo`): `.pkgmeta` at the root uses
  `move-folders` so only `ForeverVO/` ships. Push a `v*` tag: the GitHub
  workflow builds a release with the BigWigs packager, publishes it on
  GitHub Releases and uploads it to CurseForge with the `CF_API_KEY` GitHub
  *secret* (set 2026-09-22; CurseForge's own source-linked packager never
  picked the tags up, so v0.1.2 was re-run with the secret and the log shows
  the upload succeed). The same name in the local `.env` is a different
  thing and is also set: that one is for the voice packs, below. Anything at
  the repo root not in `.pkgmeta`'s ignore list ships in the zip as a stray
  `forever-vo/` folder (CLAUDE.md did in v0.1.2), so add new root files there.
  `CHANGELOG.md` is the release notes. The packager's own dry run
  (`release.sh -d -g 1.60.1`, needs zip, unzip, pandoc) is no longer practical
  here: it walks the whole working tree, and `ForeverVO_Data/Sounds/` now holds
  thousands of mp3s, so it hangs in `find` for many minutes. CI never hits this
  because the mp3s are gitignored and the checkout has no audio. The TOC carries
  `## X-Curse-Project-ID`; the packager maps Interface 16001 to game version
  "1.60.1" itself, there is no version field to fill.
- **Delta pack** "Forever Voiceover Data: Forever" (1705094): lines whose
  source is not `classic` (captures, community, beta cache), priority 200.
  `tools/release_pack.py delta --upload --if-changed` runs at the end of the
  nightly job and uploads a dated beta when the file set changed.
- **Base packs** "Forever Voiceover Data: Base" (1705100, installs as
  `ForeverVO_Data_Base`) and "Forever Voiceover Data: Base Endgame" (project
  created 2026-09-24, ID under `[release.curseforge_projects]`, installs as
  `ForeverVO_Data_Base_Endgame`; the owner's working folder `ForeverVO_Data`
  is never shipped): the Classic-sourced lines, priority 100, released by
  hand and rarely. The complete Classic set with the five alternate narrators
  it had until 2026-09-25 was 1.36 GB at 32 kbps (with orc-male alone about
  1.08 GB, so the split stays for now), and **the CurseForge website caps a file at 1 GB**
  (learned 2026-09-24 when the 1,378 MB zip was refused; the API's cap is
  lower still, `413 Payload Too Large` at 887 MB on 2026-09-22 and at 574 MB
  on 2026-09-24, while the 30
  to 70 MB delta goes through). So the set is split by quest level in
  `release_pack.py` (`base_split_level` under `[release]`): Base is quests to level 40 with all
  gossip (~800 MB), Base Endgame quests from 41 (~570 MB), each with its
  alternates, since the addon looks a quest's alternates up in the pack that
  had the quest. Cutting at 50 would put Base back over the cap; a sixth
  narrator voice costs ~65 MB per pack. Build both with `release_pack.py base`
  then `release_pack.py base_endgame` (each re-encodes its whole set, ~45 min
  together) and **upload through the website**, as "release" files for game
  version 1.60.1 (the nightly delta is a "release" file too since 2026-09-24: the CurseForge app hides
  beta files unless the user opts in). The script records
  `tools/data/release_state.json` itself even without `--upload`. The first
  Base went up 2026-09-22 before the bulk run finished, to get through
  moderation early; the split versions are dated 2026-09-24.

`release_pack.py` builds from the single working folder `ForeverVO_Data`
(which holds everything on the owner's machine and is what the client loads
locally), re-encodes to mono 32 kbps mp3 at 22.05 kHz with no Xing/Info
header frame under
`tools/data/release/` (48 kbps until 2026-09-22; the header frame was there
until 2026-09-25, see the gotcha below; the originals in
`ForeverVO_Data/Sounds/` stay at the generator's full quality, so the
release bitrate can be raised again on any later build), streams the upload
from disk (`requests-toolbelt`), writes
a fresh manifest per pack (`<Folder>Pack` global, `Register.lua`), and
uploads through the CurseForge upload API (`wow.curseforge.com/api`). Pack
versions are date based (`2026.09.20`, `.2` on the same day) and tracked in
`tools/data/release_state.json`. Project IDs, the split level, the bitrate and
the transcode parallelism are `[release]` in `forever-vo.toml`; the API key is `CF_API_KEY` in the
gitignored `.env`, read by `load_dotenv()` in `release_pack.py`
(`CURSEFORGE_API_KEY` is still accepted; it was renamed 2026-09-21 to match
the packager's name). This is the local file, not the GitHub secret of the
same name, which stays unset — see the addon entry above.

CurseForge moderation holds new projects and their first files for a day or
so; nothing needs doing meanwhile. The project logo must be original art
(`docs/logo.png`, a 1408x768 banner since 2026-09-22; the earlier square SVG
icon is gone); Blizzard icons are fine inside the client but rejected as a
storefront logo.

## Crowdsourcing

`/fvo export` packs a session's unvoiced lines (character name replaced by
`$n`), plus voiced lines the pack asked to hear again from a reader of the
player's sex (`wanted`), via `C_EncodingUtil` into an `FVO1:` string. Players
paste it into the "Contribute captured lines" issue form
(`.github/ISSUE_TEMPLATE/capture.yml`, label `capture`, one issue per export,
since 2026-09-24 when the inbox thread passed 50 comments) or, the older way,
as a comment on the pinned inbox issue #1 (label `capture-inbox`; the owner
keeps it open for anyone following a stale note).
`.github/workflows/ingest-captures.yml` handles both: it decodes the text with
`tools/exportfile.py` (stdlib only) into `captures/`, reacts with a rocket, and
closes a form issue as completed (a comment on #1 just gets the reaction). The
owner's machine picks the files up on the next sync.

## Gotchas already paid for

- A bash heredoc inside a Python heredoc terminates at the inner `EOF`. Use
  different terminators or the Write tool.
- `pkill -f 'tools/generate.py'` matches the shell that runs it; use
  `pgrep -f` to look and `systemctl --user stop forever-vo-bulk` to stop.
- A suspend can kill the GPU outright, not just the CUDA context: on
  2026-09-21 the resume logged `Xid 31` then `Xid 154, GPU recovery action
  changed to 0x2 (Node Reboot Required)`, and every later process got "CUDA
  unknown error" from `torch.cuda.is_available()` until a reboot. `nvidia-smi`
  still answers in that state, so it is not a good health check; the kernel log
  (`journalctl -k | grep Xid`) is. `generate.py` now refuses to run on the CPU
  unless `--cpu` is given, because the silent fallback is ~18x slower than real
  time and looks like a working run. The bulk unit holds a
  `systemd-inhibit --what=idle` lock, but that only stops logind's own idle
  action: GNOME's power plugin suspends on its own input-idle timer and never
  consults logind inhibitors, and it did exactly that on 2026-09-22 at 00:53
  (two hours after the last keypress, `sleep-inactive-ac-timeout` 7200) with
  the lock held, killing the GPU again. The fix is on the desktop side:
  `gsettings set org.gnome.settings-daemon.plugins.power
  sleep-inactive-ac-type 'nothing'` (set 2026-09-22; battery left at
  `suspend`). If a run dies at a round two-hour mark, check that setting first.
- **Release mp3s carry no Xing/Info header frame** (`-write_xing 0` in
  `release_pack.transcode`). LAME puts one in front of a CBR stream, sized for
  the tag rather than the stream (56 kbps on a 32 kbps mono file), and the
  client does not recognise the CBR `Info` variant: it takes that first frame's
  bitrate as the file's and computes the length from the byte count, so until
  2026-09-25 every released line stopped at 32/56 of its length (958-accept,
  31 s, stopped at 16 s; reported on all three CurseForge packs). The owner
  never heard it because the working folder's files are the generator's VBR
  originals, whose `Xing` frame the client does read, and the client runs the
  same Windows binary through Faugus, so it was never a Linux/Windows thing.
  Found by timing the same line in game as CBR with and without the header at
  22.05 and 44.1 kHz: the header was the whole story, the sample rate nothing
  (a first guess at MPEG-2 framing was wrong). The fix costs no bytes, so the
  1 GB cap is untouched; 64 kbps, as one commenter suggested, would have
  doubled every pack. The release state records the encoding so
  `--if-changed` re-releases on an encoding change. New files under `AddOns`
  need a client restart, not a `/reload`, before `PlaySoundFile` finds them.
- The wago.tools CSV export is complete for client tables, but the beta's
  `BroadcastText` really is 12 rows; gossip is server-pushed on this engine.
- `questcache.wdb` records have a variable fixed part; `wdbcache.py` scans
  every offset and prefers the candidate with no objectives block. It parses
  all 258 records of the owner's cache and was validated against Classic
  titles.
- `PlayerModel:GetDisplayInfo()` returns 0 until the model loads; the capture
  reads it in `OnModelLoaded`, and the merge ignores zero display IDs. On the
  Forever client it never yields anything at all (0 of 146 captured NPCs), only
  `GetModelFileID()` does; the Classic export supplies display IDs for
  unchanged NPCs and the `modelFileID` fallback covers Forever-only ones. Several
  `CreatureModelData` rows can share one file (Jornah's 949470 has two), so the
  reverse index is keyed by model ID, not by file.
- `wowdata.voice_for_npc` once silently used an old field name
  (`isObjectOrItem`) and a patch whose anchor text had drifted never applied.
  After editing with search-and-replace, grep for the new text; do not trust
  "patched".
- Sound file names: quests are `<questID>-<event>`, gossip `<speaker>-<hash>`.
  Tell them apart by the last segment (`generate.sound_folder`), not by
  whether the first segment is numeric, since speaker keys are numeric too.
- Alternate narrator files keep the same base name and are told apart by their
  folder (`Quests/Narrator/<voice>/`, `Gossip/Narrator/<voice>/`), so anything
  that walks sounds by `glob("*/*.mp3")` (the two-level scan in
  `rebuild_tables`) misses them by design; they have their own scan and their
  own set in the stats (`narratorFiles`, paths relative to `Sounds/`).
- A line that stops being narrated (a capture names the giver, a species clip
  appears) leaves its whole-line alternate narrator files under
  `Narrator/<voice>/`, and the addon plays an alternate whenever the table has
  one for the quest and the player picked that voice. Since 2026-09-23
  `rebuild_tables` lists whole-line alternates only for `is_narrator` items
  and the generator deletes the leftovers (and their index entries: a dirty
  key absent from memory is removed on save). Six quests (Tarindrella, Billy
  Maclure) were already in that state.
- The bulk generator's `sound_index.json` is written every 25 files; the
  release script and the nightly table rebuild reload sources so files made
  by another run are still indexed. An entry with `v: null` and no `t` is a
  probed placeholder, not a generator record: the voice-change and
  text-change checks are blind for it. `generate.py --reindex` restamps `t`;
  the voice is only in the journal (`[voice]` on each generated line).
- `./tools/run.sh python -c 'from tools.wowdata import voice_for_npc; ...'`
  is the way to poke at the tools from a one-liner: the project is installed
  in the environment, so `tools.*` imports work from anywhere in the repo.
- CurseForge's public web API (`curseforge.com/api/v1/...`) returns HTML to
  scripts; the authenticated `wow.curseforge.com/api` is what works.
- The Forever client picks `_Camelot.toc` when present and `.toc` otherwise;
  this addon uses the plain `.toc` with Interface 16001.

## Voices

**Accent and delivery come from the source clips in the reference audio.
`exaggeration` and `cfg_weight` are for colour and variety.** This is settled;
do not propose the knobs as the fix for a wrong accent, a flat delivery or a
voice that sounds like the wrong race. The owner has been told otherwise more
than once and it was wrong each time.

What the evidence is. goblin-male read as a gnome: four settings pairs from
0.45/0.5 to 1.0/0.2, two takes each on two references, moved the accent not at
all, while changing which clips fed the reference took the same voice from
"bad delivery and accent" to level with the shipped pack. Fifteen different
head clips produced British, Southern and General American in turn - the
reference decided it every time, stably across takes, and no setting did.
Where a knob has worked it was alongside a reference of connected speech, never
instead of one (#18 dwarves: "it takes both changes together"). A strong
regional accent may not survive cloning at all whatever you feed it - retail
goblin's New York never did - so the honest answer is sometimes "this voice
cannot have that accent", not "try 0.75/0.3".

Order of attack for a voice that sounds wrong: the clips in the first 6 s
(`[voices.sources.<voice>]`, picked by ear in `uv run audition`), then the rest
of the 10 s window, then the knobs.

- Race voices: `tools/voices/<race>-<gender>.wav` for the archetype most of a
  race is cast with, and one clip per other archetype, named for what Blizzard
  files it as: `dwarf-male-guard`, `human-male-official`, `gnome-female-happy`.
  The word comes from the CASC folder of that set's own recordings
  (`tools/soundpaths.py`, map committed at `tools/data/sound_sets.json`,
  refreshed with `fvo-soundpaths --refresh`); a set whose folder is shared with
  a more-used set, or belongs to another race, or is unnamed keeps
  `-s<NPCSounds row>` - 54 of the 182 castable archetypes get a word, 44 of the
  49 built. A voice is `<race>-<gender>`, so a third segment is what makes an
  archetype: `wowdata.base_voice` and `is_archetype` are the only things that
  know it, and nothing matches `-s\d+` any more. They were `-s<set>` throughout
  when first built on the owner's machine 2026-09-25 after #42: 49 archetype
  clips, 7 dropped for a thin head, every plain race clip rebuilt, species and
  named clips unchanged; the clips from before are kept in
  `tools/voices/before-42/` (gitignored) for A/B listening. That build restaged
  9,132 lines re-cast to an archetype (the voice-change check, plus dwarves
  under "text changed" because the archetype's fingerprint drops the
  `reference` knob) and nothing on a plain voice, since clip audio is not in
  the fingerprint (#51) - so the rename costs those lines a second restaging,
  and any already rendered under an `-s<set>` name are rendered again.
  The retail `SoundKitEntry` CSV is large and wago.tools timed out on it once;
  a `curl` into `tools/data/db2/<build>/` with a long timeout is the workaround. Sorting
  candidate clips longest-first used to hand the head to whichever set had the
  longest lines, which is one-off character sets: seven voices were cloned from
  a set used by a single creature display, tauren-female from an elder with one
  (Magatha Grimtotem). Four voices have no spoken emotes in this client - both
  blood elf and both goblin - because those races were playable only from
  Burning Crusade and Cataclysm. Retail has the recordings, and
  `speech_pool()` borrows them for exactly those voices (`RETAIL_BUILD` in
  `config.py`, fetched by FileDataID like everything else, no local CASC
  needed). It borrows for no one else: for every race this client covers,
  retail holds the same 1.x files and fewer of them (troll-male 10 here
  against 6 there), so there is nothing to gain and a re-record to risk.
  `vulpera-male.wav` is built too and nothing uses it: one vulpera display
  exists in the client (137545) and Blizzard casts it with set 49, a human
  male set used by 249 other displays, so no speaker ever resolves to it.
- **Three recipes, chosen per voice** (`recipe_for`): `speech-and-barks` is
  the default and what every race with its own emotes gets. A borrowed-speech
  race gets `pooled-barks` for its plain clip - every set's greetings, as the
  base pack was built before this branch - and its archetypes take
  `speech-and-barks` and `speech-only`, so the race keeps three personalities.
  Narrowing the plain voice to the dominant set is what left goblin-male a
  2.8 s reference that read as a gnome; rated by ear, goblin got worse the
  more borrowed speech went in (pooled best, 5.4 s head next, 11.3 s
  speech-only last), because the Classic bark actor and the Cataclysm emote
  actor are different people. A `pooled-barks` clip holds no speech slice, or
  it would keep the longest jokes for a clip that never reads them.
- **Clips chosen by ear beat every rule tried.** `[voices.sources.<voice>]` in
  `forever-vo.toml` lists FileDataIDs, head first, and `build_picked_reference`
  concatenates exactly those: no sorting by duration, no 0.8-8.0 s filter, no
  `TARGET_SECONDS`, no thin-head deletion, since each of those would undo the
  choice. Picks are honoured by `--named` too and join the stale sweep's keep
  set, or a rebuild would clobber or delete them. They join the fingerprint,
  keyed on the clip actually cloned from, so a re-pick restages that voice by
  itself - the clip's *bytes* still do not. Pick them in the audition page's
  Source clips panel, or with `fvo-refclips`.
- Named NPCs: `npc-<displayID>.wav` for greeting kits used by 3 or fewer
  models (64 of them: Varimathras, Thrall, Sylvanas, Cairne...). Thrall has
  just two greetings, so his clone is rougher.
- Species voices (PR #21, 2026-09-23): a speaker with no player race resolves
  through its model file (`tools/data/species_models.json`, keyed by
  `CreatureModelData.FileDataID`, which is also what `GetModelFileID()`
  returns) to `<species>-<gender>.wav` when the clip exists. The clips come
  from Warcraft III: `extract_wc3_units.py` reads the local Reforged install
  through CascLib (built from source, `tools/data/libcasc.so`, gitignored;
  build steps at the top of the script) into `tools/voices/raw-wc3/units/`,
  and `build_wc3_references.py` cuts the "what"/"yes" acknowledgements into
  dryad, keeper of the grove, ogre, satyr, banshee, dreadlord, flesh golem,
  dire troll and naga clips. The two child voices come from retail's
  `kul_tiran_kid` via `build_retail_references.py`. Built 2026-09-23; the
  voice-change check then regenerated ~514 lines.
- `[voices.fallbacks]` and `[voices.zone_hints]` in `forever-vo.toml` cover races
  without a clip and speakers without display data (Zephras Isle -> skyborne);
  `[voices.species_aliases]` sends a model folder with no clip of its own to a
  close one (orc children to the human child clips).
- **Chatterbox conditioning is `[tts]` in `forever-vo.toml`**, with per-voice
  overrides under `[tts.voices.<voice>]` that may also name a different clip to
  clone from (`reference`). The knobs are `exaggeration`, `cfg_weight` and
  `tempo`; the last is not a model parameter (Chatterbox's `generate()` has no
  pace control) but a pitch-preserving `atempo` stretch applied by
  `Synth.speak` at encode time, with the stretched length recorded as the
  duration. Only knobs that differ from the `[tts]` defaults join the
  fingerprint (`Tts.differences`), so adding a knob later never restages what
  was already stamped. `generate.VoiceCatalog` resolves a voice to the
  clip it actually uses (its own, else its fallback race's, else the narrator's,
  else human-male) and takes the tuning of *that* voice, so Dark Iron dwarves
  and tuskarr, who borrow the dwarf clip, get the dwarf settings (#18). An
  archetype (`<race>-<gender>-s<set>`) with no `[tts.voices]` row of its own
  keeps its wav and takes the plain race voice's exaggeration, cfg weight and
  tempo; the race's `reference` stays on the plain voice, and the knobs in the
  fingerprint restage the archetype lines. The
  settings join the text fingerprint only when they differ from the defaults,
  so tuning one voice restages exactly its files (and a file with no
  fingerprint in a tuned voice, which predates tuning); `--reindex` seeds the
  untuned key so it can never mark such a file current. Rebuilding a clip's
  audio is not in the fingerprint on purpose: `--force --voice <voice>` is the
  targeted way to redo one voice and the voices cloned from its clip. dwarf-male
  was tuned first (0.75 / 0.3 on npc-3597's four connected lines, set
  2026-09-24, restaging ~3,100 files). dwarf-female followed the same day at
  0.75 / 0.3 on her existing greeting montage: an A/B against a clip built from
  her /joke and /flirt lines (`EmotesTextSound`, emotes 328 FLIRT and 329 JOKE,
  the only long connected player-voice recordings the client has) lost to the
  montage at the same settings, so for that voice the settings alone did it.
  dwarf-male carried the same fix as `reference = "npc-3597"` at 0.75 / 0.3
  until 2026-09-25, when its clips were picked by ear instead and the entry
  went: a `reference` pointing elsewhere means the picks are never read. Both
  results are about the reference, which is the rule at the top of this
  section.
- `--assume-voice` on `generate.py` voices cache-only quests whose giver is
  unknown (used once for Zephras Isle with `skyborne-male`); the voice-change
  check fixes them once a capture names the giver.
- `narrator_alternates` under `[voices]` is the narrator menu: since 2026-09-25
  just orc-male beside the default (human-male's clip), over ~1,040 narrated
  quest and ~336 narrated gossip lines in the full Classic set (1,340 files per
  voice, ~4 h of GPU each). It was five alternates before: each cost about 6.5%
  of every pack, together a third, and the base split by level exists because
  of them. The four dropped voices' files (human-female, dwarf-male,
  nightelf-female, troll-female) are still under `Sounds/*/Narrator/` and in
  `sound_index.json`; `rebuild_tables` and the release only look at the
  configured voices, so they are dead weight until deleted. The player's pick
  lives in the `ForeverVO_narratorVoice` CVar as well as the settings, from
  when saved variables did not survive a session on this beta.
- The talking head defaults to the faction parchment; clearing `factionHead`
  gives Blizzard's dark panel (the "Normal" kit). Gold text vanished on the
  parchment until each kit got its own dark Name/Title/Text and no shadow:
  `FONT_COLORS` in `UI/TalkingHead.lua`. Check both kits after touching it.

## Things the owner wants next

- Per-line configurability: let end users nudge text, voice, exaggeration or
  pacing for a line and re-run Chatterbox for it themselves. `uv run audition`
  (2026-09-24) covers the owner's side of this: hear variants, keep a voice's
  settings or a respelling in the TOML, write one file into the pack. Not yet:
  end users without the repo, pacing, parts and narrator alternates, and there
  is deliberately no per-line table in the TOML.
- A Discord bot as an alternative inbox for `FVO1:` strings (same decoder).
- A complete base pack release once the Classic bulk run finishes (the first,
  partial one went up 2026-09-22 with ~12,900 of ~18,000 files; the alternate
  narrator voices were all still to come).
