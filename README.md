# Forever Voiceover

Voiced quests and NPC dialogue for World of Warcraft: Forever, with audio you
generate yourself from a local text-to-speech model.

**The quest text for Forever additions is crowd sourced, please help out by downloading the addon and occasionally running `/fvo export`**. 

Four addons on CurseForge: the player, and three voice packs that stack.

- **[Forever Voiceover](https://www.curseforge.com/wow/addons/forever-voiceover)**
  (`ForeverVO`) — the player, and the only one you need to start. Reads quest
  offers, turn-ins, greetings and gossip from installed voice packs and plays
  them through a talking-head frame styled after the client's own, with a queue
  you can pause, skip and reorder, and a replay button on each quest you open in
  the quest log. It also records every line it sees so new audio can be
  generated for what is still missing.
Voice packs, each its own CurseForge project, no line in two of them:

- **[Forever Voiceover: Classic Quests](https://www.curseforge.com/wow/addons/forever-voiceover-classic-quests)**
  (`ForeverVO_Data_Classic_Quests`) — Classic's quests up to level 40.
- **[Forever Voiceover: Classic Endgame](https://www.curseforge.com/wow/addons/forever-voiceover-classic-endgame)**
  (`ForeverVO_Data_Classic_Endgame`) — Classic's quests from level 41 up.
- **[Forever Voiceover: Classic Gossip](https://www.curseforge.com/wow/addons/forever-voiceover-classic-gossip)**
  (`ForeverVO_Data_Classic_Gossip`) — what Classic's NPCs say when you talk to
  them.
- **[Forever Voiceover: Forever Quests](https://www.curseforge.com/wow/addons/forever-voiceover-forever-quests)**
  (`ForeverVO_Data_Forever_Quests`) — the quests Forever adds. These are the
  lines being crowd sourced, so it grows as players run `/fvo export`.
- **[Forever Voiceover: Forever Gossip](https://www.curseforge.com/wow/addons/forever-voiceover-forever-gossip)**
  (`ForeverVO_Data_Forever_Gossip`) — what Forever's NPCs say, crowd sourced
  the same way.
- **[Forever Voiceover: Books](https://www.curseforge.com/wow/addons/forever-voiceover-books)**
  (`ForeverVO_Data_Books`) — books, letters and plaques, Classic's and
  Forever's, read by the narrator when you press Play under the book.

The Classic packs change only when a voice is redone; a reworded Classic quest
stays in its Classic pack and is updated there. Every pack carries the
alternate narrator voices for its own lines. Until 2026-10-06 the packs were
Base, Base Endgame and Data: Forever (`ForeverVO_Data_Base`,
`ForeverVO_Data_Base_Endgame`, `ForeverVO_Data_Forever`); a copy of one still in
AddOns is only consulted for what the new packs lack, and can be deleted.

Install any of the packs, all of them, or none — the player works on its own, it just has
nothing to say until a pack is there.

Built for the Forever client only (Camelot, interface 16001). It uses the
Retail engine APIs: the Settings panel, the addon compartment, mixins, frame
pools. No embedded libraries.

## How it works

Quest and gossip text is not in the client files; the server sends it. So the
addon captures text as you play, and the tools turn the captured lines into
audio:

1. Play. Every quest you accept or hand in and every NPC you talk to is
   recorded in `ForeverVOCaptureDB`, with the speaker's creature ID and model.
2. Log out, then `./tools/run.sh tools/ingest.py` merges the saved
   variables into `tools/data/capture.json`.
3. `./tools/run.sh tools/generate.py` picks a voice per speaker (race and
   gender from the client's display tables), synthesises the missing lines with
   Chatterbox on your GPU, and rebuilds the pack tables.
4. Restart the client (new sound files are only seen at launch) and play on.

The capture persists between sessions (the beta reads saved variables back
since late September 2026; before that each login started empty), so `ingest.py`
can run whenever you like. It merges, so running it after every session costs
nothing.

## Voice pack format

A pack is an addon that depends on ForeverVO and calls
`ForeverVO.RegisterPack(pack)`; see `ForeverVO_Data/Data/Pack.lua` and the
comment at the top of `ForeverVO/Core/Packs.lua`. Quest audio is keyed by quest
ID and event; gossip by speaker ID plus a hash of the normalised text
(`tools/textkey.py` mirrors `Util.HashText`), with a fuzzy fallback. Packs have
priorities, so a pack of new or revised lines can sit on top of a base pack.

## Development

Experimental personal character voices: generate a local pack that speaks your
character name, then opt in under the addon's Audio settings. See
[Personal character voices](docs/personal-voices.md) for setup, hardware selection,
preview and installation. The shared packs remain the fallback.

`nix` is optional, `uv` is mandatory.

`uv run audition` opens a local page (port 8765) for hearing a line in a voice
under different reference clips and Chatterbox settings side by side, keeping
the winner in `configs/`, and writing a single regenerated file into the
pack.

It plays each line's current file from the repo's `ForeverVO_Data`, falling
back to the voice packs installed in your client. It finds them from `WOW_DIR`
(the folder holding `_classic_beta_`; the default is a Faugus install), so set
that if your game lives elsewhere, or pass `--addons <Interface/AddOns>`.
That default is unchanged. A checkout with no client can instead set
`AUDITION_ADDONS` to a directory of `ForeverVO_Data*` folders. `fvo-fetch-packs`
downloads the three CurseForge packs into `./addons` (gitignored) and does
nothing until you run it. The page does not read that folder unless the
variable or `--addons` points at it.

```sh
# unchanged: the client's packs
WOW_DIR="$HOME/Games/World of Warcraft" ./tools/run.sh audition

# no client, optional
./tools/run.sh fvo-fetch-packs
AUDITION_ADDONS="$PWD/addons" ./tools/run.sh audition
```

### Audition on an AMD GPU

The default `tts` group is the CUDA wheel. On AMD, audition uses the
`tts-rocm` group: the same Chatterbox release on PyTorch's ROCm 6.2.4 build
(`torch==2.6.0+rocm6.2.4`, the matching `torchaudio`, and
`pytorch-triton-rocm==3.2.0`). The two groups conflict, so `uv lock` keeps
both and a normal `./tools/run.sh` stays on CUDA.

The wheel carries its own ROCm libraries. What the machine must already have
is the `amdgpu` kernel driver, with `/dev/kfd` present (`ls -l /dev/kfd`) and
your user able to open it. `ffmpeg` has to be on `PATH`; the dev shell from
`flake.nix` provides it when you go through `./tools/run.sh`.

The unpacked torch is about 17 GB. Give `.venv-rocm` a real disk. A tmpfs,
including a worktree under `/tmp` on a machine that mounts `/tmp` that way,
will not hold it. uv hardlinks the wheel out of `~/.cache/uv` when the
environment is on the same filesystem as that cache, so the second copy costs
inodes rather than another 17 GB.

```bash
UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm audition
```

Open http://127.0.0.1:8765. The first take downloads the Chatterbox weights
(about 3 GB) into `~/.cache/huggingface` and then loads the model, which
takes around half a minute. Later takes reuse that process. The same
environment's smoke test prints the HIP version and writes `tools/smoke.wav`:

```bash
UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm tools/tts_smoke.py
```

This build reports the GPU through `torch.cuda.is_available()`, and the
device string stays `cuda`. That is the API Chatterbox already calls. Checked
on gfx1030 (Radeon RX 6800 / 6900 class): hipBLASLt logs that the
architecture is unsupported and uses hipblas instead, and the line still
completes. If `amdgpu.ids` is not installed, the device name prints as
"AMD Radeon Graphics"; that file is only the marketing name.

`fvo-generate` stays on the CUDA wheel. The sound index records the text and
the tuning, so a file made here would look current and the nightly run would
ship it. Keeping a voice's settings in
`configs/tts.toml` is the AMD path: the CUDA generator restages that voice
from those numbers. `.venv-rocm/` is gitignored.

The tools are a [uv](https://docs.astral.sh/uv/) project (`pyproject.toml`,
`uv.lock`, `.python-version`; uv fetches the interpreter itself) and
`flake.nix` provides the rest: uv, ffmpeg, lua 5.1 and the shared libraries the
CUDA wheels expect. `tools/run.sh` is `uv run` inside that shell, so every
script, console script (`fvo-ingest`, `fvo-generate`, ... see
`pyproject.toml`) and one-liner runs against the same pins. Without nix,
`uv run tools/<script>.py` works directly with `ffmpeg` on PATH. The GPU stack
is the `tts` dependency group, on by default; `uv run --no-group tts ...`
skips it for a checkout that only ingests or releases.

```bash
./tools/run.sh tools/tts_smoke.py            # CUDA check, writes tools/smoke.wav
./tools/run.sh tools/build_voice_references.py   # reference clips from the client's own voice lines

B="$HOME/Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft/_classic_beta_/Interface/AddOns"
ln -s "$PWD/ForeverVO" "$B/ForeverVO"
ln -s "$PWD/ForeverVO_Data" "$B/ForeverVO_Data"
```

Provide `tools/voices/narrator.wav` (10 to 20 s of clean speech) for quests and
gossip from items and objects. Those lines are generated again in each voice
listed as `narrator_alternates` in `configs/voices.toml`, under
`Sounds/<Quests|Gossip>/Narrator/<voice>/`, so
players can pick the narrator they prefer in the options. The extra passes sort after
every line that has no audio at all; `--narrator-voices none` leaves them out
of a run and `--narrator-only` makes a run of nothing else.

## In game

- `/fvo` opens the options. `/fvo pause|resume|skip|clear|replay|queue|head|reset|narrator|status|report|debug`.
- The narrator reads the lines with no speaker to voice them: quests and
  chatter from objects, items and signs. Options > Audio > Narrator voice picks
  which voice that is, from whatever the pack carries; `/fvo narrator` cycles.
- Right-click the talking head to skip; the X clears the queue; "Queue" shows
  what is waiting; drag to move (lockable in options).
- The bug icon on the talking head, or `/fvo report`, opens the text of a GitHub
  issue about the line that is playing. Edit it if you need to, then Copy
  Link and paste the link into a browser. The title and the description are
  already filled in. Your character name is removed.
- The addon compartment entry (next to the minimap) has the same controls.
- Opening a quest in the quest log puts a Play button beside Back, for quests
  that have audio.

## Credits

MIT licensed. Interface concept after
[VoiceOver](https://github.com/mrthinger/wow-voiceover) by mrthinger and
contributors; the button textures are theirs (public domain). Client data
comes from [wago.tools](https://wago.tools). Speech by
[Chatterbox](https://github.com/resemble-ai/chatterbox). Voice packs are fan
content: Blizzard's text, voices generated from the game's own recordings,
shared for non-commercial use.

## Contributing lines

Quest and gossip text only exists on the server, so the pack can only grow
from what players see. With the addon on, play (the new zones matter most),
then type `/fvo export`. The window shows the contribution. Copy Link, and
paste the link into a browser: it opens a "Contribute captured lines" issue
with the text filled in
(https://github.com/quinn-dougherty/forever-vo/issues/new?template=capture.yml).
A bot decodes it into `captures/`, reacts with 🚀, closes the issue, and the
next pack build voices it. Your character name is removed before export.
(Pasting it as a comment on the older inbox, issue #1, still works too.)
