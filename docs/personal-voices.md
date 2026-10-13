# Personal character voices (experimental)

Generate quest and gossip recordings that say your character's name instead of
"adventurer". Chatterbox runs locally, before playing; the addon plays ordinary
MP3 files. No name is uploaded by this command. This is separate from the proposal
to include names in community exports (#402).

The addon option is **Audio > Use my character name in speech**, off by default.
A personal pack is used only for its exact character name and realm. Everyone
else, and lines not yet generated, use the installed shared packs. Toggling clears
the playback queue so already resolved recordings cannot keep the old selection.
An older ForeverVO version ignores the personal pack.

## Prepare the generator

Use a checkout of this branch, uv, and ffmpeg/ffprobe on PATH (including the
rubberband filter if your selected voices use pitch tuning). Python 3.12 is managed
by uv. Model weights and PyTorch require several GB; the ROCm environment can
require about 17 GB for PyTorch alone. The script does not install GPU drivers.

The text corpus and reference voices are the same inputs as the regular generator:

- Committed `tools/data/capture.json` supplies captured lines. For the Classic
  corpus, use the existing `tools/classicdb.py` workflow; see the main README.
- Reference WAVs are **not in Git** or in the released packs. Build them with
  `uv run --no-group tts tools/build_voice_references.py` (downloads game audio
  used as reference clips). The existing voice-building tools and configs also
  provide named, retail and species voices. The generator refuses a voice with
  no usable clip, and asks you to rebuild any outdated picked reference.
- Install the modified `ForeverVO` addon along with your usual shared voice packs.

First inspect the work, without loading a model or installing TTS dependencies:

Missing game metadata may be downloaded into the existing DB2 cache during planning.

```sh
uv run --no-project --python 3.12 tools/personal_setup.py --name "Arlen" --realm "Example" --dry-run
```

Use the complete name and realm exactly as the client reports them. In-game:
`/dump UnitName("player")` and `/dump GetRealmName()`.

Then generate a small first batch:

```sh
uv run --no-project --python 3.12 tools/personal_setup.py --name "Arlen" --realm "Example" --quest 783
```

Omit `--quest` to process all named quest/gossip lines. Add `--pronunciation
"Ar-len"` if needed; this affects speech only, not character matching. Listen to
a small batch before generating the whole corpus. `--limit 20` limits new files,
and rerunning resumes. One line can require several files (gender branches,
stage directions, narrator voices, and alternate speakers); it is published only
when the whole set is complete. Books are not personalized in this first version.

## Hardware selection

The setup script detects NVIDIA through `nvidia-smi` or Linux ROCm through
`/dev/kfd`, and selects the existing `tts` or `tts-rocm` dependency group in its
own `.venv-personal-*` directory. The generator then checks PyTorch's actual
device availability. Detection is a starting point, not a guarantee that your
GPU/driver is supported by the pinned PyTorch version.

- NVIDIA: CUDA environment; drivers must already work.
- AMD: pinned ROCm environment, Linux x86_64 only. Native Windows ROCm is not
  supported by these wheels.
- CPU: opt in with `--backend cpu`; expect very slow generation.
- Apple MPS: not enabled until Chatterbox generation is validated on it.

Override discovery with `--backend cuda`, `--backend rocm` or `--backend cpu`.
An unavailable GPU fails instead of silently switching to CPU. Within an already
prepared environment, `uv run --no-sync fvo-personal ... --device auto|cuda|cpu`
provides the same generator. Do not run a full GPU batch while playing if the two
compete for VRAM. Native Windows and real GPU output still need tester validation.

## Install and update

The output is `addons/ForeverVO_Personal_<id>/`, where the ID derives from the
character and realm. Copy the whole directory into the game's `Interface/AddOns/`,
restart the client (new files are not discovered by `/reload`), and enable the
Audio option. The command does not modify the game installation automatically.

The shared source text, lookup hashes, captures, `ForeverVO_Data`, and shared
sound index are unchanged. The personal directory has its own resumable index;
changed spoken text, pronunciation, reference bytes, tuning, or backend causes
regeneration. Shared seed pins are not reused for different words. Use `--force`
to draw a fresh take. Output uses the release encoder, including loudness
normalization and no Xing/Info header.

Keep the personal directory local: its Lua manifest contains the character name
and realm, and its audio speaks the name. The default output and environments are
gitignored. Do not upload it as a shared CurseForge pack. You can delete the
personal addon to return to the shared recordings.

This adds a generation tool, not real-time speech or instant generation from the
addon menu. Full regeneration cost depends on the selected corpus, voices and GPU.
