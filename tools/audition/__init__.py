"""Audition: a local page for hearing one line in one voice under several
reference clips and Chatterbox settings side by side, and keeping what wins.

    uv run audition                    # serves http://127.0.0.1:8765
    uv run audition --port 9000 --open # and opens the browser

On an AMD GPU the same page uses the ROCm wheel, in its own environment so the
default CUDA one (what the nightly run generates with) stays put:

    UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts --group tts-rocm audition

One model instance, loaded on the first take and kept. Nothing here goes around
the pipeline's bookkeeping:

- "keep" writes [tts.voices.<voice>], a [pronunciations] entry or a
  [voices.speakers] entry (one NPC always in one voice) into
  the configs/ file that holds the table, through tomlkit, so the comments
  survive, and the result is validated by the same models before the old file
  is replaced;
- "pin this take" writes the take's seed to [lines], for one line's pack file,
  only for a take of that line's exact text under its saved settings, which the
  server reads from the take's own sidecar rather than from the page.

Takes land under tools/data/audition/<session>/ (gitignored) and are served
from there. The page is index.html beside this file: one HTML file, no build.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from tools import generate
from tools.build_voice_references import (
    S3GEN_SECONDS,
    T3_SECONDS,
    build_picked_reference,
)
from tools.config import (
    BETA_BUILD,
    BETA_DIR,
    CASC_DIR,
    CONFIG_DIR,
    DATA_DIR,
    GENDER_DICT,
    LINE_BASE,
    PACK_NAME,
    RETAIL_BUILD,
    ROOT,
    SOUND_INDEX,
    SOUNDS_DIR,
    VOICES_DIR,
    Config,
    ConfigError,
    LinePin,
    Tts,
    VoiceSources,
    VoiceTuning,
    config_file,
    load_config,
    read_config,
)
from tools.generate import (
    SUBFOLDERS,
    Item,
    Synth,
    TakeStopped,
    Variant,
    VoiceCatalog,
    load_items,
    load_sources,
    sound_path,
)
from tools.textclean import CHUNK_CHARS, chunk, clean
from tools.wowdata import (
    archetype_names,
    archetype_of,
    base_voice,
    dominant_sound_set,
    fetch_file,
    is_archetype,
    set_voice,
    sibling_sets,
    sound_set_displays,
)

AUDITION_DIR = DATA_DIR / "audition"
ADDONS_DIR = BETA_DIR / "Interface" / "AddOns"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def addons_from_env() -> Path:
    """Where installed ForeverVO_Data* packs are read from.

    Unset, this is the client's AddOns folder from WOW_DIR, as before.
    AUDITION_ADDONS names another directory of those folders, for a checkout
    with no client. fvo-fetch-packs can fill ./addons; nothing reads it until
    the variable or --addons points there."""
    override = os.environ.get("AUDITION_ADDONS")
    return Path(override) if override else ADDONS_DIR


# ----------------------------------------------------------------------------
# configs/ edits (comments survive: tomlkit round-trips the document)
# ----------------------------------------------------------------------------


@contextlib.contextmanager
def _config_lock(path: Path) -> Iterator[None]:
    """Serialises read-modify-write of the TOML across requests and processes.

    Endpoints here are sync `def`, so FastAPI runs them in a threadpool and two saves
    genuinely overlap: both parsed the same document, and the second to finish wrote a
    file missing the first one's change while reporting success. The lock file is
    separate from the TOML because the write is a rename, which would drop the lock
    with the inode it was taken on. configs/ has one lock for the whole directory,
    since an edit is validated against every file.
    """
    lock = path / ".lock" if path.is_dir() else path.with_suffix(".toml.lock")
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _parse(file: Path) -> tomlkit.TOMLDocument:
    """One file of configs/ as a tomlkit document, empty when it does not exist yet."""
    if not file.exists():
        return tomlkit.document()
    return tomlkit.parse(file.read_text(encoding="utf-8"))


def _validated_write(config_dir: Path, file: Path, doc: tomlkit.TOMLDocument) -> Config:
    """Writes the document to `file` only if the models accept configs/ with it in
    place; returns the fresh Config.

    The temporary file carries this process and thread, so two overlapping saves
    cannot promote each other's document.
    """
    text = tomlkit.dumps(doc)
    try:
        read_config(config_dir, {file.name: text})
    except ConfigError as e:
        raise HTTPException(400, f"refused, the result would not validate: {e}") from e
    trial = file.with_name(f".{file.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        trial.write_text(text, encoding="utf-8")
        trial.replace(file)
    finally:
        trial.unlink(missing_ok=True)
        load_config.cache_clear()
    return load_config(config_dir)


def write_tuning(
    config_dir: Path,
    voice: str,
    exaggeration: float,
    cfg_weight: float,
    reference: str | None,
    tempo: float = 1.0,
    pitch: float = 0.0,
    speed: float = 1.0,
) -> Config:
    """Sets [tts.voices.<voice>]; a tuning equal to the defaults with no reference
    removes the entry instead, so the file only lists what differs."""
    with _config_lock(config_dir):
        return _write_tuning(
            config_dir, voice, exaggeration, cfg_weight, reference, tempo, pitch, speed
        )


def _write_tuning(
    config_dir: Path,
    voice: str,
    exaggeration: float,
    cfg_weight: float,
    reference: str | None,
    tempo: float = 1.0,
    pitch: float = 0.0,
    speed: float = 1.0,
) -> Config:
    file = config_file(config_dir, "tts")
    doc = _parse(file)
    tts = doc.get("tts")
    if tts is None:
        tts = tomlkit.table()
        doc["tts"] = tts
    defaults = (
        float(tts.get("exaggeration", 0.45)),
        float(tts.get("cfg_weight", 0.5)),
        float(tts.get("tempo", 1.0)),
        float(tts.get("pitch", 0.0)),
        float(tts.get("speed", 1.0)),
    )
    voices = tts.get("voices")
    if voices is None:
        voices = tomlkit.table(is_super_table=True)
        tts["voices"] = voices
    if reference is None:
        # The page's "Clone from" is empty for "the voice's own (as configured)", which
        # is not the same as "this voice clones from nothing". Keeping a take at the
        # default settings used to delete the whole entry and take the reference with
        # it: dwarf-male lost `reference = "npc-3597"` and its 0.75/0.3 that way, which
        # would have restaged 3,114 files in a voice #18 had already corrected. An
        # existing reference is carried over; clearing one is a TOML edit.
        current = load_config(config_dir).tts.voices.get(voice)
        reference = current.reference if current else None
    if (exaggeration, cfg_weight, tempo, pitch, speed) == defaults and not reference:
        if voice in voices:
            del voices[voice]
    else:
        _set_keys(
            voices,
            voice,
            {
                "reference": reference or None,
                "exaggeration": exaggeration,
                "cfg_weight": cfg_weight,
                "tempo": tempo if tempo != defaults[2] else None,
                "pitch": pitch if pitch != defaults[3] else None,
                "speed": speed if speed != defaults[4] else None,
            },
        )
    return _validated_write(config_dir, file, doc)


def _set_keys(
    parent: Any, name: str, values: dict[str, Any], blank_after: bool = False
) -> None:
    """Sets `parent[name]`'s keys to `values`, None removing one, in the table that is
    already there rather than a new one: a comment that sits inside a table belongs to
    it in tomlkit, and replacing the table dropped it. That is how the paragraph on
    respellings, which had drifted under [tts.voices.dwarf-female], was lost when
    dwarf-female's pitch was kept (2026-10-03). A new table is made only when there
    is none, its keys in the order given."""
    entry = parent.get(name)
    if entry is None:
        entry = tomlkit.table()
        for key, value in values.items():
            if value is not None:
                entry[key] = value
        if blank_after:
            entry.add(tomlkit.nl())
        parent[name] = entry
        return
    for key, value in values.items():
        if value is None:
            if key in entry:
                del entry[key]
        else:
            entry[key] = value


def write_pronunciation(config_dir: Path, word: str, spoken: str) -> Config:
    """Adds or replaces one [pronunciations] entry; an empty spoken form removes it."""
    with _config_lock(config_dir):
        return _write_pronunciation(config_dir, word, spoken)


def _write_pronunciation(config_dir: Path, word: str, spoken: str) -> Config:
    file = config_file(config_dir, "pronunciations")
    doc = _parse(file)
    table = doc.get("pronunciations")
    if table is None:
        table = tomlkit.table()
        doc["pronunciations"] = table
    if spoken:
        table[word] = spoken
    elif word in table:
        del table[word]
    return _validated_write(config_dir, file, doc)


def write_speaker_voice(config_dir: Path, speaker: str, voice: str) -> Config:
    """Sets [voices.speakers].<speaker>; an empty voice removes the entry, and the
    table with its last one, so the speaker goes back to what the capture says."""
    with _config_lock(config_dir):
        file = config_file(config_dir, "voices", "speakers")
        doc = _parse(file)
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        speakers = voices.get("speakers")
        if speakers is None:
            speakers = tomlkit.table()
            voices["speakers"] = speakers
        if voice:
            speakers[speaker] = voice
        elif speaker in speakers:
            del speakers[speaker]
        if not speakers:
            del voices["speakers"]
        return _validated_write(config_dir, file, doc)


def write_line_pin(config_dir: Path, base: str, pin: dict[str, Any] | None) -> Config:
    """Sets [lines].<base> to one inline table, or removes it when `pin` is None. The
    table itself stays, with its comment, when the last pin goes."""
    with _config_lock(config_dir):
        file = config_file(config_dir, "lines")
        doc = _parse(file)
        lines = doc.get("lines")
        if lines is None:
            lines = tomlkit.table()
            doc["lines"] = lines
        if pin is None:
            if base in lines:
                del lines[base]
        else:
            entry = tomlkit.inline_table()
            entry.update(pin)
            lines[base] = entry
        return _validated_write(config_dir, file, doc)


def write_approval(config_dir: Path, voice: str, recipe: str | None) -> Config:
    """Sets [voices.approved].<voice> to the recipe heard; None takes the approval off.
    The table stays when it empties, so its comment keeps its place in the file."""
    with _config_lock(config_dir):
        file = config_file(config_dir, "voices", "approved")
        doc = _parse(file)
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        approved = voices.get("approved")
        if approved is None:
            approved = tomlkit.table()
            voices["approved"] = approved
        if recipe:
            approved[voice] = recipe
        elif voice in approved:
            del approved[voice]
        return _validated_write(config_dir, file, doc)


NOTES_COMMENT = [
    "Tasting notes, one per voice, written in the audition page's voice card: what",
    "a voice sounds like by ear, which lines break it, what was tried and what to",
    "try next. For people; nothing generates from them.",
]


def write_notes(config_dir: Path, voice: str, text: str) -> Config:
    """Sets [voices.notes].<voice>; empty text removes it. Several lines are kept as a
    multi-line string. The table, made with its comment the first time, stays when
    it empties, so the comment keeps its place."""
    text = text.strip()
    with _config_lock(config_dir):
        file = config_file(config_dir, "voices", "notes")
        doc = _parse(file)
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        notes = voices.get("notes")
        if notes is None:
            notes = tomlkit.table()
            for line in NOTES_COMMENT:
                notes.add(tomlkit.comment(line))
            voices["notes"] = notes
        if text:
            notes[voice] = tomlkit.string(text, multiline="\n" in text)
        elif voice in notes:
            del notes[voice]
        return _validated_write(config_dir, file, doc)


def tidy_gaps(gaps: list[float], clips: int) -> list[float]:
    """Gaps as the TOML keeps them: one per clip but the last, to the hundredth, with
    the trailing zeros dropped, so a pick with no gaps writes none."""
    out = [round(max(0.0, g), 2) for g in gaps[: max(clips - 1, 0)]]
    while out and not out[-1]:
        out.pop()
    return out


def write_voice_sources(
    config_dir: Path,
    voice: str,
    clips: list[int],
    build: str | None = None,
    gaps: list[float] | None = None,
) -> Config:
    """Sets [voices.sources.<voice>].clips, head first, and its gaps when it has any;
    an empty list removes the entry, so the file lists only the voices that were
    picked by ear."""
    with _config_lock(config_dir):
        file = config_file(config_dir, "voices", "sources")
        doc = _parse(file)
        voices = doc.get("voices")
        if voices is None:
            voices = tomlkit.table()
            doc["voices"] = voices
        sources = voices.get("sources")
        if sources is None:
            # a super table, or the children render inline instead of as their own
            # [voices.sources.<voice>] headers
            sources = tomlkit.table(is_super_table=True)
            voices["sources"] = sources
        if not clips:
            if voice in sources:
                del sources[voice]
            if not sources:
                # leaving an empty [voices.sources] behind would not round-trip
                del voices["sources"]
        else:
            array = tomlkit.array()
            array.extend(clips)
            spaced = tidy_gaps(gaps or [], len(clips))
            gap_array = tomlkit.array()
            gap_array.extend(spaced)
            values = {
                "clips": array.multiline(len(clips) > 6),
                "build": build or None,
                "gaps": gap_array if spaced else None,
            }
            # in place, so a comment inside the entry stays (_set_keys); a new one
            # gets a blank line before whatever follows
            _set_keys(sources, voice, values, blank_after=True)
        return _validated_write(config_dir, file, doc)


def sources_warnings(
    config: Config, voice: str, voices_dir: Path = VOICES_DIR
) -> list[str]:
    """What the owner has to know before picking for this voice, worst first.

    Each of these is a way a pick reaches nobody, or reaches far more lines than the
    name suggests. Warnings rather than refusals: the owner knows the pack better than
    this function does.
    """
    warnings: list[str] = []
    tuning = config.tts.voices.get(voice)
    if tuning and tuning.reference and tuning.reference != voice:
        warnings.append(
            f"[tts.voices.{voice}] clones from {tuning.reference}.wav, so {voice}.wav is not "
            f"what this voice reads - pick for {tuning.reference}, or drop that reference first"
        )
    catalog = VoiceCatalog(config, voices_dir)
    borrowers = []
    for other in _known_voices(config, voices_dir):
        if other == voice:
            continue
        resolved = catalog.resolve(other)
        if resolved.clip and resolved.clip.stem == voice:
            borrowers.append(other)
    if borrowers:
        shown = ", ".join(borrowers[:8]) + ("..." if len(borrowers) > 8 else "")
        warnings.append(
            f"{len(borrowers)} other voice(s) read from {voice}.wav: {shown}"
        )
    narrator = config.voices.narrator
    reads_for_narrator = voice == narrator or (
        not (voices_dir / f"{narrator}.wav").exists()
        and catalog.resolve(narrator).clip == voices_dir / f"{voice}.wav"
    )
    if reads_for_narrator:
        warnings.append(
            "this clip is what the narrator reads: every quest from an object "
            "or item, and every <stage direction>"
        )
    return warnings


def unlisted_folders(config: Config) -> list[str]:
    """[voices.named_folders] entries that named_folders.json does not have yet: a new
    folder is offered only once `fvo-soundpaths --folders` has probed it."""
    from tools.soundpaths import named_folder_files

    listed = named_folder_files()
    missing = sorted(
        {
            folder
            for extras in config.voices.named_folders.values()
            for folder in extras
            if folder.lower() not in listed
        }
    )
    if not missing:
        return []
    return [
        (
            f"[voices.named_folders] names {', '.join(missing)}, which "
            "tools/data/named_folders.json does not list yet: run "
            "./tools/run.sh fvo-soundpaths --folders"
        )
    ]


def unlisted_clip_folders(config: Config) -> list[str]:
    """[voices] clip_folders that named_folders.json does not have yet, which "Also offer
    clips from" would otherwise offer with nothing in them."""
    from tools.soundpaths import named_folder_files

    listed = named_folder_files()
    missing = [f for f in config.voices.clip_folders if f not in listed]
    return [
        f"[voices] clip_folders names {folder}, which tools/data/named_folders.json "
        f"does not list yet: run ./tools/run.sh fvo-soundpaths --folders --only {folder}"
        for folder in missing
    ]


@functools.cache
@functools.cache
def named_display_count(display_id: int) -> int:
    """How many creature displays share this named clip's kit - three at most, by the
    rule that mints them (NAMED_MAX_DISPLAYS)."""
    from tools.build_voice_references import NAMED_MAX_DISPLAYS
    from tools.wowdata import display_sound_set, load_db2

    sound_id = display_sound_set(display_id)
    if not sound_id:
        return 1
    shared = sum(
        1
        for row in load_db2("CreatureDisplayInfo").values()
        if int(row.get("NPCSoundID") or 0) == sound_id
    )
    return min(shared, NAMED_MAX_DISPLAYS) or 1


def npc_labels() -> dict[int, str]:
    """creature display -> something to call its clip, because npc-10357 is nobody.

    A named clip is keyed by display ID, which is all the client offers and all the
    pack needs. For reading, the corpus knows what 22 of them are called, and Blizzard
    files the rest under the creature or character the kit belongs to (voljin, peon,
    wolf rider). Cached for the process: state() is polled every 15 s and load_sources
    walks the whole corpus.
    """
    from tools.soundpaths import folders
    from tools.wowdata import display_sound_set

    with contextlib.redirect_stdout(io.StringIO()):
        sources = generate.load_sources()
    by_display: dict[int, str] = {}
    for npc in sources.get("npcs", {}).values():
        display, name = npc.get("displayID"), npc.get("name")
        if display and name:
            by_display.setdefault(int(display), str(name))
    known = folders()
    labels: dict[int, str] = {}
    for path in VOICES_DIR.glob("npc-*.wav"):
        try:
            display = int(path.stem.split("-", 1)[1])
        except ValueError:
            continue
        folder = known.get(display_sound_set(display) or 0)
        label = by_display.get(display) or folder
        if label:
            labels[display] = label
    return labels


SOURCE_PICKS = DATA_DIR / "source_picks.json"
PICK_HISTORY = 20  # per voice; picking is iterative and the last few are what matter


def pick_history(voice: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Every set of clips a voice has been built from, newest first.

    configs/ records one pick per voice, the one in force, so each build used to
    erase the one before it - and picking is experimental by nature: the way to find out
    whether a clip belongs at the head is to try it and try the other one. This is the
    undo, and the record of what has already been heard.
    """
    if not SOURCE_PICKS.exists():
        return {}
    data = json.loads(SOURCE_PICKS.read_text(encoding="utf-8"))
    return {voice: data.get(voice, [])} if voice else data


@functools.cache
def _clip_offers(clip_folders: tuple[str, ...]) -> dict[int, list[str]]:
    """FileDataID -> the voices and sound folders whose candidates include it, in the
    order the page would borrow from them: a [voices] clip_folders folder first (the
    reason it was added), then a race's speech and its sets, then a named NPC's kit,
    which shares files with race sets and is the narrower source. What lets a saved
    pick bring its sources back with it."""
    from tools.build_voice_references import (
        emote_speech_fdids,
        named_npc_fdids,
        set_fdids,
    )
    from tools.soundpaths import named_folder_files

    offers: dict[int, list[str]] = {}

    def add(fdid: int, voice: str) -> None:
        if voice not in offers.setdefault(fdid, []):
            offers[fdid].append(voice)

    files = named_folder_files()
    for folder in clip_folders:
        for fdid, _, _ in files.get(folder, []):
            add(fdid, f"folder-{folder}")
    for build in (BETA_BUILD, RETAIL_BUILD):
        with contextlib.suppress(Exception):  # a table that cannot be had offers none
            for race_gender, fdids in emote_speech_fdids(build).items():
                for fdid in fdids:
                    add(fdid, race_gender)
    for race_gender, sets in sound_set_displays().items():
        for sound_id in sets:
            for fdid in set_fdids(sound_id):
                add(fdid, race_gender)
    for voice, fdids in named_npc_fdids().items():
        for fdid in fdids:
            add(fdid, voice)
    return offers


def pick_sources(
    voice: str, clips: list[int], own: set[int], config: Config
) -> list[str]:
    """The other voices and folders a pick's clips come from, for the ones the voice's
    own candidates do not offer: what "Also offer clips from" needs for the whole pick
    to be listed. The first source that offers each clip, in first-needed order."""
    offers = _clip_offers(tuple(config.voices.clip_folders))
    needed: list[str] = []
    for fdid in clips:
        if fdid in own:
            continue
        source = next((v for v in offers.get(fdid, []) if v != voice), None)
        if source and source not in needed:
            needed.append(source)
    return needed


def pick_history_for(
    voice: str, picked: VoiceSources | None, own: set[int] | None, config: Config
) -> list[dict[str, Any]]:
    """The voice's earlier picks for the page, each with the sources it needs (`uses`),
    and the pick saved in configs/ marked `saved`. The saved pick leads the list
    when no build in the history matches it, as when it predates the history: it is the
    one to go back to, and it used to be missing (dwarf-female's approved pick)."""
    rows = [dict(row) for row in pick_history(voice).get(voice, [])]
    if picked and picked.clips:
        saved = (picked.clips, tidy_gaps(picked.gaps, len(picked.clips)))
        match = [r for r in rows if (r.get("clips"), r.get("gaps", [])) == saved]
        for row in match[:1]:
            row["saved"] = True
        if not match:
            rows.insert(
                0,
                {
                    "clips": picked.clips,
                    "gaps": saved[1],
                    "build": picked.build,
                    "at": "saved",
                    "saved": True,
                    "recipe": False,
                },
            )
    for row in rows:
        row["released"] = released_files(row)  # pack files made from it, 0 for none
    if own is not None:
        for row in rows:
            row["uses"] = pick_sources(voice, row.get("clips", []), own, config)
    return rows


def approved_target(
    config: Config, voice: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """What reverting a voice to its approval would write: the pick heard (from the
    history, by the digest the approval recorded) and its knobs. None when there is
    nothing to revert to here: no approval, an approval of a clip another voice owns,
    one heard before its picks were kept (no digest), or a pick no longer in the
    history (released picks never leave it)."""
    heard = config.voices.approved.get(voice)
    if not heard:
        return None
    fields = dict(part.split("=", 1) for part in heard.split(",") if "=" in part)
    digest = fields.get("clips")
    if fields.get("clip") != voice or not digest:
        return None
    saved = config.voices.sources.get(voice)
    rows = list(pick_history(voice).get(voice, []))
    if saved and saved.clips:
        rows.insert(0, {"clips": saved.clips, "build": saved.build, "gaps": saved.gaps})
    pick = next(
        (
            row
            for row in rows
            if generate.picks_digest(
                row.get("clips", []), row.get("build"), row.get("gaps", [])
            )
            == digest
        ),
        None,
    )
    if pick is None:
        return None
    defaults = config.tts.defaults
    knobs = {
        "exaggeration": float(fields.get("exaggeration", defaults.exaggeration)),
        "cfg_weight": float(fields.get("cfg_weight", defaults.cfg_weight)),
        "tempo": float(fields.get("tempo", defaults.tempo or 1.0)),
        "pitch": float(fields.get("pitch", defaults.pitch or 0.0)),
        "speed": float(fields.get("speed", defaults.speed or 1.0)),
        "reference": fields.get("reference"),
    }
    return pick, knobs


def seed_recipe_history(voice: str) -> None:
    """Record the automatic build as the oldest entry, so picking has an undo.

    Only picks were kept, which left no way back to what the recipes made - and that is
    exactly what someone wants when their own clips are not landing. The recipe writes
    its concat list beside the voice's raw audio and picks write a different file, so
    that list survives the first pick and says which clips it used, in order.
    """
    from tools.refclips import RAW_DIR as CLIP_RAW

    if any(row.get("recipe") for row in pick_history(voice).get(voice, [])):
        return  # already recorded; a voice picked before this existed still needs it
    listing = CLIP_RAW / voice / "concat.txt"
    if not listing.exists():
        return
    clips: list[int] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        stem = Path(line.split("'")[1]).stem if "'" in line else ""
        if stem.isdigit():
            clips.append(int(stem))
    if not clips:
        return
    with _config_lock(SOURCE_PICKS):
        history = pick_history()
        rows = [row for row in history.get(voice, []) if row.get("clips") != clips]
        # oldest, not newest: it is what the voice was before anyone picked for it
        rows.append(
            {
                "clips": clips,
                "build": None,
                "recipe": True,
                "at": "before picking",
                "seconds": round(_concat_seconds(clips, voice), 1),
            }
        )
        history[voice] = trim_history(rows)
        tmp = SOURCE_PICKS.with_suffix(f".json.{os.getpid()}.part")
        tmp.write_text(
            json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, SOURCE_PICKS)


def _concat_seconds(clips: list[int], voice: str) -> float:
    """How long those clips run, for the history line."""
    from tools.build_voice_references import duration
    from tools.refclips import RAW_DIR as CLIP_RAW

    total = 0.0
    for fdid in clips:
        path = CLIP_RAW / voice / f"{fdid}.ogg"
        if path.exists():
            try:
                total += duration(path)
            except (subprocess.CalledProcessError, OSError):
                pass
    return total


MAIN_REPO = "quinn-dougherty/forever-vo"  # whose main the nightly run commits to
MAIN_FETCH_SECONDS = 600  # how stale main may get before it is fetched again
_main_lock = threading.Lock()
_main_fetched = 0.0
_main_commit: tuple[str, float] = ("", 0.0)  # main's commit, and when it was asked


def _git(*args: str, timeout: float = 30) -> str | None:
    """Output of a git command in the repository, or None if it fails. Never prompts:
    a fetch over SSH that wants a passphrase fails instead of hanging the server."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
    }
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, timeout=timeout, env=env, check=True,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


@functools.cache
def _main_remote() -> str | None:
    """The remote whose URL is the main repository: upstream on a contributor's clone,
    origin on the owner's. origin when none names it, None outside a git checkout."""
    listing = _git("remote", "-v")
    if listing is None:
        return None
    remotes = [
        line.split()[:2] for line in listing.splitlines() if len(line.split()) >= 2
    ]
    named = [name for name, url in remotes if MAIN_REPO in url]
    if named:
        return named[0]
    return "origin" if any(name == "origin" for name, _ in remotes) else None


def _fetch_main() -> None:
    """Fetches main in the background, at most every MAIN_FETCH_SECONDS: the marks follow
    the nightly run's commits without any request waiting on the network."""
    global _main_fetched
    remote = _main_remote()
    with _main_lock:
        if remote is None or time.time() - _main_fetched < MAIN_FETCH_SECONDS:
            return
        _main_fetched = time.time()
    threading.Thread(
        target=lambda: _git("fetch", "--quiet", remote, "main", timeout=120),
        daemon=True,
    ).start()


@functools.cache
def _released_at(commit: str) -> dict[str, int]:
    """picks digest -> how many pack files main's sound_index.json stamps with it, at
    one commit of main (or "" for the working copy)."""
    if commit:
        text = _git("show", f"{commit}:tools/data/sound_index.json", timeout=60)
    else:
        try:
            text = SOUND_INDEX.read_text(encoding="utf-8")
        except OSError:
            text = None
    try:
        index = json.loads(text or "")
    except ValueError:
        return {}
    found: Counter[str] = Counter()
    for entry in index.values():
        match = re.search(r"(?:^|[+,])clips=([0-9a-f]{8})", entry.get("t") or "")
        if match:
            found[match.group(1)] += 1
    return dict(found)


def _released() -> dict[str, int]:
    """The digests main's sound index stamps, read from the latest main this checkout
    has fetched, so a branch cut weeks ago still sees what last night's run voiced;
    the working copy only when git cannot say."""
    global _main_commit
    _fetch_main()
    # which commit main is at, asked of git at most every 30 s: each history row asks
    if time.time() - _main_commit[1] > 30:
        remote = _main_remote()
        commit = (_git("rev-parse", f"{remote}/main") or "").strip() if remote else ""
        _main_commit = (commit, time.time())
    found = _released_at(_main_commit[0]) if _main_commit[0] else {}
    return found or _released_at("")


def released_files(row: dict[str, Any]) -> int:
    """How many pack files were generated from this pick, by its digest in main's
    sound_index.json: the index the nightly run commits as it voices and ships them.
    The release record itself (release_state.json) is gitignored
    and lives only on the machine that uploads, so this is what every checkout has."""
    digest = generate.picks_digest(
        row.get("clips", []), row.get("build"), row.get("gaps", [])
    )
    return _released().get(digest, 0)


def trim_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A voice's history kept to the last PICK_HISTORY picks, except that a pick with
    audio in the pack is kept for good, wherever it falls: it is what players heard."""
    kept, others = [], 0
    for row in rows:
        if released_files(row):
            kept.append(row)
        elif others < PICK_HISTORY:
            kept.append(row)
            others += 1
    return kept


def _write_history(history: dict[str, list[dict[str, Any]]]) -> None:
    tmp = SOURCE_PICKS.with_suffix(f".json.{os.getpid()}.part")
    tmp.write_text(
        json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, SOURCE_PICKS)


def forget_pick(voice: str, clips: list[int], gaps: list[float]) -> bool:
    """Drops one experiment from the voice's history. False, and nothing dropped, for a
    pick with audio in the pack or the one saved in configs/, which are kept."""
    spaced = tidy_gaps(gaps, len(clips))
    saved = load_config().voices.sources.get(voice)
    if saved and (saved.clips, tidy_gaps(saved.gaps, len(saved.clips))) == (
        clips,
        spaced,
    ):
        return False
    with _config_lock(SOURCE_PICKS):
        history = pick_history()
        rows = history.get(voice, [])
        keep = [
            r
            for r in rows
            if (r.get("clips"), r.get("gaps", [])) != (clips, spaced)
            or released_files(r)
        ]
        if len(keep) == len(rows):
            return False
        history[voice] = keep
        _write_history(history)
    return True


def record_pick(
    voice: str,
    clips: list[int],
    build: str | None,
    seconds: float,
    recipe: bool = False,
    gaps: list[float] | None = None,
) -> None:
    """Puts this pick at the head of the voice's history, if it is not already there.
    The same clips with other gaps are another pick, and a row without gaps has none."""
    spaced = tidy_gaps(gaps or [], len(clips))
    with _config_lock(SOURCE_PICKS):
        history = pick_history()
        rows = [
            row
            for row in history.get(voice, [])
            if (row.get("clips"), row.get("gaps", [])) != (clips, spaced)
        ]
        row: dict[str, Any] = {
            "clips": clips,
            "build": build,
            "seconds": round(seconds, 1),
            "at": time.strftime("%Y-%m-%d %H:%M"),
            "recipe": recipe,
        }
        if spaced:
            row["gaps"] = spaced
        rows.insert(0, row)
        history[voice] = trim_history(rows)
        tmp = SOURCE_PICKS.with_suffix(f".json.{os.getpid()}.part")
        tmp.write_text(
            json.dumps(history, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, SOURCE_PICKS)


def reference_seconds(path: Path) -> float:
    from tools.build_voice_references import duration

    try:
        return round(duration(path), 1)
    except (subprocess.CalledProcessError, OSError):
        return 0.0


def restage_note(voice: str, existed: bool, kept: bool) -> str:
    """What still has to happen for a re-cut clip to reach players.

    The two cases differ. A clip that did not exist changes which voice NPCs resolve to,
    and generate.wanted() already restages a changed voice name. A clip that did exist
    changes no name, and its bytes are not fingerprinted - only the recorded pick is, so
    the restage follows from writing [voices.sources], not from rebuilding the wav.
    """
    if not existed:
        return (
            f"{voice}.wav is new. NPCs cast with it resolve to that voice now, so the "
            f"next generate run restages their lines on its own."
        )
    if kept:
        return (
            f"{voice}.wav was re-cut and the picks are saved, so its lines are stale and "
            f"the nightly run will redo them. To do it now: "
            f"./tools/run.sh tools/generate.py --voice {voice}"
        )
    return (
        f"{voice}.wav was re-cut but the picks were not saved, and a clip's audio is in "
        f"no fingerprint - nothing restages. Save the picks, or run: "
        f"./tools/run.sh tools/generate.py --force --voice {voice}"
    )


def _known_voices(config: Config, voices_dir: Path = VOICES_DIR) -> list[str]:
    """Voice names worth resolving: the clips on disk plus anything the TOML names."""
    names = {p.stem for p in voices_dir.glob("*.wav")}
    names |= (
        set(config.tts.voices) | set(config.voices.fallbacks) | {config.voices.narrator}
    )
    names |= set(config.voices.narrator_alternates)
    return sorted(names)


# ----------------------------------------------------------------------------
# The corpus, one row per file base name
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LineRow:
    base: str  # file base name, e.g. 415-accept, m-170-accept, 5688-19cbe7de
    subfolder: str  # Quests | Gossip | Books
    title: str  # quest title or gossip speaker
    speaker: str
    voice: str
    raw: str  # the text as captured
    spoken: str  # what the model is asked to say (cleaned, respelled)
    level: int
    source: str
    speaker_key: str = ""  # "288", "-123" for a game object, "" for an item

    @property
    def haystack(self) -> str:
        return f"{self.base} {self.title} {self.speaker} {self.raw}".lower()


def line_rows(items: list[Item]) -> list[LineRow]:
    rows = []
    for item in items:
        speaker = item.entry.get("name") or (item.npc or {}).get("name") or ""
        title = item.entry.get("title") or speaker
        for variant in item.variants():
            rows.append(
                LineRow(
                    variant.base,
                    item.subfolder,
                    title,
                    speaker,
                    item.voice,
                    item.raw_text,
                    variant.text,
                    int(item.entry.get("level") or 0),
                    item.entry.get("source") or "capture",
                    item.speaker_key or "",
                )
            )
    return rows


def _in_voice(row: LineRow, voice: str, moving: frozenset[str]) -> bool:
    """Spoken in `voice`, or by a speaker who moves to it once its clip exists (a
    species voice no one is cast on yet: the spirit healer for spirithealer-female)."""
    return row.voice == voice or (bool(row.speaker_key) and row.speaker_key in moving)


def random_line(
    rows: list[LineRow],
    voice: str,
    rng: random.Random | None = None,
    moving: frozenset[str] = frozenset(),
) -> LineRow | None:
    """A random line spoken in `voice` (the resolved race-gender or npc voice):
    a quest line when the voice has any, else a gossip line, else None."""
    rng = rng or random.Random()
    mine = [row for row in rows if _in_voice(row, voice, moving)]
    quests = [row for row in mine if row.subfolder == "Quests"]
    pool = quests or mine
    return rng.choice(pool) if pool else None


def search(rows: list[LineRow], query: str, limit: int = 40) -> list[LineRow]:
    """Every word of the query must appear in the base name, title, speaker or text.
    Exact base name and title hits sort first, then by level."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = [row for row in rows if all(w in row.haystack for w in words)]
    q = query.lower().strip()
    hits.sort(
        key=lambda r: (
            r.base.lower() != q,
            r.title.lower() != q,
            q not in r.title.lower(),
            r.level,
            r.base,
        )
    )
    return hits[:limit]


def lines_in_voice(
    rows: list[LineRow],
    voice: str,
    limit: int = 60,
    moving: frozenset[str] = frozenset(),
) -> list[LineRow]:
    """That voice's own lines, the longest first.

    Auditioning a voice on the words it will actually say beats free text, and beats one
    random draw: a reference that holds up on a short greeting can still fall apart on a
    paragraph. Longest first because those are the ones that show a delivery - and
    because the pack's own long lines are what the owner notices in play.
    """
    hits = [row for row in rows if _in_voice(row, voice, moving)]
    hits.sort(key=lambda r: (-len(r.spoken), r.base))
    return hits[:limit]


# ----------------------------------------------------------------------------
# The studio: one model, one corpus, the config file
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SoundPack:
    """A folder of pack audio the page can play: the working folder or an installed pack."""

    key: str  # the folder name, which the /api/pack URLs carry
    label: str  # what the page shows: "working folder", or the manifest's name
    priority: int
    sounds: Path
    version: str = ""  # the release's date version, so the page can name a take's age


PACK_FIELD = re.compile(r'^\s*(name|priority|version)\s*=\s*"?([^",]+)"?', re.MULTILINE)


def sound_packs(
    addons: Path | None = ADDONS_DIR, working: Path = SOUNDS_DIR
) -> list[SoundPack]:
    """Where a line's "in the pack now" comes from: the working folder first, since
    it is what the generator writes and what the owner's machine holds complete,
    then each pack installed in the client, in the order the addon consults them
    (Packs.lua: higher priority first, then name). A contributor's working folder
    holds a few hundred files and the rest of their audio is in the CurseForge packs,
    so without those every line read "no file in the pack yet". The owner links the
    working folder into the client as ForeverVO_Data_Local; that link is skipped,
    since it is the working folder again."""
    installed: list[SoundPack] = []
    for folder in sorted(addons.glob("ForeverVO_Data*")) if addons else []:
        sounds = folder / "Sounds"
        if not sounds.is_dir() or sounds.resolve() == working.resolve():
            continue
        fields: dict[str, str] = {}
        with contextlib.suppress(OSError):
            manifest = (folder / "Data" / "Pack.lua").read_text(encoding="utf-8")
            for key, value in PACK_FIELD.findall(manifest):
                fields.setdefault(key, value.strip())
        priority = 0
        with contextlib.suppress(ValueError):
            priority = int(fields.get("priority", 0))
        installed.append(
            SoundPack(
                folder.name,
                fields.get("name", folder.name),
                priority,
                sounds,
                fields.get("version", ""),
            )
        )
    installed.sort(key=lambda pack: (-pack.priority, pack.label))
    return [SoundPack(PACK_NAME, "working folder", 0, working), *installed]


class WorkingCopies:
    """Whether the working folder's file of a line is the one sound_index.json records.

    A contributor's working folder keeps every line they once generated, and the
    index in git follows the owner's machine: when a voice is restaged there, the
    contributor's copy stays the old take while the installed packs carry the new one
    (Frezza's 9564-8d6952f8, a 19.95 s take from before goblin-male-zany was tuned,
    beside the 17.02 s the index and the game have). A copy whose length is not the
    index's is stale, and "in the pack now" plays the installed pack's file instead.
    Lengths are probed once per file version; an index that cannot be read (a merge
    left in conflict) or holds no length for the line trusts the copy, as before."""

    # seconds: ffprobe reads a generator mp3 at its exact length, which is what the
    # index records (some older entries rounded to 0.01), while Chatterbox takes
    # differ in 0.04 s steps, so even a near miss is another take
    TOLERANCE = 0.02

    def __init__(self, index_path: Path = SOUND_INDEX):
        self.index_path = index_path
        self.lock = threading.Lock()
        self._index: dict[str, Any] = {}
        self._index_stamp: tuple[int, int] | None = None
        self._seconds: dict[tuple[str, int, int], float | None] = {}

    def index(self) -> dict[str, Any]:
        try:
            stat = self.index_path.stat()
        except OSError:
            return {}
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self.lock:
            if stamp != self._index_stamp:
                try:
                    loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    loaded = {}
                self._index = loaded if isinstance(loaded, dict) else {}
                self._index_stamp = stamp
            return self._index

    def seconds(self, path: Path) -> float | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._seconds:
            try:
                self._seconds[key] = generate.probe_duration(path)
            except (OSError, ValueError, subprocess.CalledProcessError):
                self._seconds[key] = None
        return self._seconds[key]

    def prime(self, paths: list[Path]) -> None:
        """Probe many files at once, so a voice's few hundred lines list in seconds."""
        from concurrent.futures import ThreadPoolExecutor

        todo = [path for path in paths if path.exists()]
        if todo:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(self.seconds, todo))

    def stale(self, path: Path, base: str) -> bool:
        entry = self.index().get(generate.index_key(base))
        recorded = entry.get("d") if isinstance(entry, dict) else entry
        if not isinstance(recorded, int | float):
            return False
        seconds = self.seconds(path)
        return seconds is not None and abs(seconds - recorded) > self.TOLERANCE

    def stamp(self) -> tuple[int, int] | None:
        """The index file's version (mtime and size), after reading it."""
        self.index()
        return self._index_stamp

    def kept(self, base: str) -> dict[str, Any] | None:
        """The seed the index says drew the line's file (generate.kept_seed): the
        builder's file, whether or not this machine has it."""
        kept = generate.kept_seed(self.index().get(generate.index_key(base)))
        if kept is None:
            return None
        return {
            "seed": kept.seed,
            "chunk_chars": kept.chunk_chars,
            "same_seed": kept.same_seed,
            "capped": bool(kept.capped),
        }


def pinned_settings(pin: LinePin, tts: Tts) -> dict[str, Any]:
    """The knobs and clone-from a pin was heard under, read back from its `heard`:
    the fingerprint carries only what differs from the [tts] defaults, so the rest
    is the defaults. The page turns a run option blue while it holds this value."""
    terms = generate.fingerprint_terms(pin.heard)
    heard: dict[str, Any] = tts.defaults.model_dump()
    for name, default in list(heard.items()):
        if name not in terms:
            continue
        value = terms[name]
        heard[name] = float(value) if isinstance(default, int | float) else value
    return heard


def lapsed_pins(gone: list[str]) -> str | None:
    """A warning naming the pins a settings or clip write lapsed (Studio.lapsed_since);
    None when it lapsed none."""
    if not gone:
        return None
    shown = ", ".join(gone[:5]) + (
        f" and {len(gone) - 5} more" if len(gone) > 5 else ""
    )
    return (
        f"this lapses the pin on {shown}: the pinned take was heard under the old "
        "settings, so the line falls back to its index seed until a take under the "
        "new ones is pinned"
    )


@functools.lru_cache(maxsize=8)
def _index_date(stamp: tuple[int, int] | None) -> str | None:
    """When sound_index.json last changed in git (its commit date), so the page can
    say how old the index seed it shows is: a contributor's checkout may lag the
    nightly by days, and the seed it shows may since have been rerolled. Keyed on
    the file's version (WorkingCopies.stamp), so a pull while the server runs
    moves the date with the seeds."""
    out = _git("log", "-1", "--format=%cs", "--", "tools/data/sound_index.json")
    return out.strip() or None if out else None


class Studio:
    def __init__(self, config_path: Path = CONFIG_DIR, allow_cpu: bool = False):
        self.config_path = config_path
        self.allow_cpu = allow_cpu
        self.model_lock = threading.Lock()
        self.corpus_lock = threading.Lock()
        self._synth: Synth | None = None
        self._rows: list[LineRow] | None = None
        self._by_base: dict[str, tuple[Item, Variant]] = {}
        self.model_status = "not loaded"
        self.corpus_status = "not loaded"
        self._lines_per_voice: Counter[str] | None = None
        self._speakers_per_voice: Counter[str] | None = None
        self._species_wanted: dict[str, SpeciesWant] = {}
        self.clips_lock = threading.Lock()
        self._clips: dict[str, list[dict[str, Any]]] = {}
        self.clips_status: dict[str, str] = {}
        # voice -> {done, total, failed, since} while its candidates download
        self.clips_progress: dict[str, dict[str, float]] = {}
        # session -> set by the page's Stop button; a run checks it between takes
        self.stops: dict[str, threading.Event] = {}
        threading.Thread(target=self.rows, daemon=True).start()

    def config(self) -> Config:
        load_config.cache_clear()
        return load_config(self.config_path)

    def config_cached(self) -> Config:
        """The config as last read, read again only when the file has changed: for the
        chunk preview, which asks on every pause in typing, where config() would clear
        the process-wide cache each time."""
        stamp = sorted(
            (f.name, f.stat().st_mtime_ns) for f in self.config_path.glob("*.toml")
        )
        cached = getattr(self, "_config_cached", None)
        if cached is None or cached[0] != stamp:
            # load_config is cached by path, so it would hand back the old file
            load_config.cache_clear()
            cached = (stamp, load_config(self.config_path))
            self._config_cached = cached
        return cached[1]

    def catalog(self, config: Config | None = None) -> VoiceCatalog:
        return VoiceCatalog(config or self.config())

    def synth(self) -> Synth:
        with self.model_lock:
            if self._synth is None:
                self.model_status = "loading"
                try:
                    self._synth = Synth(
                        self.catalog(), allow_cpu=self.allow_cpu, allow_hip=True
                    )
                except BaseException as e:
                    self.model_status = f"failed: {e}"
                    raise
                self.model_status = "ready"
            return self._synth

    def rows(self) -> list[LineRow]:
        with self.corpus_lock:
            if self._rows is None:
                self.corpus_status = "loading"
                catalog = self.catalog()
                items = load_items(
                    load_sources(), include_progress=True, catalog=catalog
                )
                self._by_base = {
                    v.base: (item, v) for item in items for v in item.variants()
                }
                self._rows = line_rows(items)
                self._species_wanted = species_wanted(items, indexed_voices())
                self.corpus_status = f"{len(self._rows)} lines"
            return self._rows

    def rows_if_loaded(self) -> list[LineRow] | None:
        """The corpus rows, or None while they load; never waits on the corpus lock."""
        return self._rows

    def species_want(self, voice: str) -> SpeciesWant | None:
        return self._species_wanted.get(voice)

    def moving_to(self, voice: str) -> frozenset[str]:
        """The speakers who move to this species voice once its clip exists."""
        want = self.species_want(voice)
        return want.speakers if want else frozenset()

    def spoken_if_loaded(self) -> dict[str, int]:
        """lines_per_voice, or {} while the corpus is still loading, so a request that
        only wants the counts for a label never waits minutes on the corpus lock."""
        return self.lines_per_voice() if self._rows is not None else {}

    def lines_per_voice(self) -> dict[str, int]:
        """How many lines each voice actually speaks - the reason to bother with it.

        Cached beside the corpus it is counted from, because state() is polled every
        15 s and this walks every row.
        """
        rows = self.rows()  # takes corpus_lock itself; must not be held here
        with self.corpus_lock:
            if self._lines_per_voice is None:
                self._lines_per_voice = Counter(row.voice for row in rows)
                # and how many NPCs speak them, items and keyless lines aside
                self._speakers_per_voice = Counter(
                    voice
                    for voice, _ in {
                        (row.voice, row.speaker_key) for row in rows if row.speaker_key
                    }
                )
            return dict(self._lines_per_voice)

    def speakers_per_voice(self) -> dict[str, int]:
        """How many speakers each voice is cast on, counted with lines_per_voice."""
        self.lines_per_voice()
        return dict(self._speakers_per_voice or {})

    def clips(self, voice: str, refresh: bool = False) -> list[dict[str, Any]] | None:
        """This voice's candidate source clips, or None while a background load runs.

        refclips.candidates downloads every candidate it does not already have and runs
        ffprobe over each, so the first call for a voice takes minutes: it goes on a
        thread and the page polls, the way the corpus already does. Afterwards fetch_file
        short-circuits on the file being there and only ffprobe runs.
        """
        with self.clips_lock:
            if refresh:
                self._clips.pop(voice, None)
            if voice in self._clips:
                return self._clips[voice]
            if self.clips_status.get(voice) == "loading":
                return None
            # a failed attempt must not look like one still running, or Load candidates
            # can never try again and the voice reads "loading" until a restart
            self.clips_status[voice] = "loading"
        threading.Thread(target=self._load_clips, args=(voice,), daemon=True).start()
        return None

    def _load_clips(self, voice: str) -> None:
        from tools import refclips

        def progress(done: int, total: int, failed: int) -> None:
            # the page's progress bar while wago is slow: files settled of the
            # voice's candidates, and how many could not be had
            self.clips_progress[voice] = {
                "done": done,
                "total": total,
                "failed": failed,
                "since": started,
            }

        started = time.time()
        try:
            found = [
                {
                    "n": i,
                    "fdid": c.fdid,
                    "kind": c.kind,
                    "group": c.group,
                    "seconds": round(c.seconds, 2),
                    "url": f"/api/clips/audio/{voice}/{c.fdid}.ogg",
                }
                # the page's config, not the repository's: --config may name another
                for i, c in enumerate(
                    refclips.candidates(voice, self.config().voices, progress=progress),
                    1,
                )
            ]
        except Exception as e:  # noqa: BLE001 - the status line is the error report
            with self.clips_lock:
                self.clips_status[voice] = (
                    f"failed: {e} (press Load candidates to retry)"
                )
                self.clips_progress.pop(voice, None)
            return
        with self.clips_lock:
            self._clips[voice] = found
            self.clips_status[voice] = f"{len(found)} clips"
            self.clips_progress.pop(voice, None)

    def forget_corpus(self) -> None:
        """After a config change the spoken text or voices may differ; reload lazily."""
        with self.corpus_lock:
            self._rows = None
            self._lines_per_voice = None
            self._speakers_per_voice = None
            self._by_base = {}
        threading.Thread(target=self.rows, daemon=True).start()

    def line(self, base: str) -> tuple[Item, Variant]:
        self.rows()
        try:
            return self._by_base[base]
        except KeyError:
            raise HTTPException(404, f"no line with base name {base!r}") from None

    def line_if_loaded(self, base: str | None) -> tuple[Item, Variant] | None:
        """The line, without waiting on a corpus that is loading (a Keep reloads it):
        None while it loads or when there is no such line."""
        if not base or self._rows is None:
            return None
        return self._by_base.get(base)

    def voices(self) -> list[str]:
        return sorted(p.stem for p in VOICES_DIR.glob("*.wav"))

    def pickable(self) -> list[dict[str, Any]]:
        """Every voice clips can be chosen for, whether or not it has a clip yet.

        The voice list is the wavs on disk, which leaves out the archetypes no recipe
        mints - and those are the ones worth picking for: the game casts 182 archetypes
        and 49 have a file, so 133 sets fall back to the plain race voice. human-male-s50
        is 245 creature displays. wowdata.archetype_voice casts on the file being there,
        so building one is what puts those NPCs on it.
        """
        on_disk = set(self.voices())
        labels = npc_labels()
        counts = sound_set_displays()
        # How many creature displays each voice answers for. An archetype's is its own
        # NPCSounds set; a race voice's is every set of that race and gender, since it
        # is what a speaker falls back to; a named clip's is the handful sharing its kit.
        displays: dict[str, int] = {}
        for race_gender, sets in counts.items():
            displays[race_gender] = sum(sets.values())
            for sound_id, n in sets.items():
                displays[archetype_names(race_gender)[sound_id]] = n
        for display_id in labels:
            displays[f"npc-{display_id}"] = named_display_count(display_id)
        spoken = self.lines_per_voice()
        speakers = self.speakers_per_voice()

        def label_of(voice: str) -> str | None:
            if not voice.startswith("npc-"):
                return None
            try:
                return labels.get(int(voice.split("-", 1)[1]))
            except ValueError:
                return None

        rows: list[dict[str, Any]] = [
            {
                "voice": v,
                "clip": True,
                "displays": displays.get(v),
                "archetype": is_archetype(v),
                "label": label_of(v),
                "lines": spoken.get(v, 0),
                "speakers": speakers.get(v, 0),
            }
            for v in self.voices()
        ]
        for race_gender, sets_of in counts.items():
            names = archetype_names(race_gender)
            for sound_id, counted in sets_of.items():
                name = names[sound_id]
                if name not in on_disk:
                    rows.append(
                        {
                            "voice": name,
                            "clip": False,
                            "displays": counted,
                            "archetype": True,
                            "label": None,
                            "lines": spoken.get(name, 0),
                            "speakers": speakers.get(name, 0),
                        }
                    )
        # A species whose speakers have lines but no clip of their kind: the spirit
        # healer, read as human-female. Building <species>-<sex>.wav is what casts
        # them on it (wowdata.species_voice), so only the species that speak are
        # offered, not all 411 in species_models.json.
        listed = {r["voice"] for r in rows}
        for name, want in self._species_wanted.items():
            count, now = want.lines, want.now
            if name not in listed:
                rows.append(
                    {
                        "voice": name,
                        "clip": False,
                        "displays": None,
                        "archetype": False,
                        "label": None,
                        "lines": count,
                        "speakers": len(want.speakers),
                        "species": now,
                    }
                )
        # A race in [voices.fallbacks] reads another race's clip until it has its own:
        # nightborne from nightelf-male.wav. Offered so it can get one; building
        # <race>-<gender>.wav is what takes its speakers off the borrowed voice, since
        # VoiceCatalog prefers a voice's own clip to its fallback.
        listed = {r["voice"] for r in rows}
        speaking = speech_voices()
        for race, borrowed in self.config().voices.fallbacks.items():
            if race == "narrator":
                continue
            for gender in GENDER_DICT.values():
                name = f"{race}-{gender}"
                if name not in listed:
                    rows.append(
                        {
                            "voice": name,
                            "clip": False,
                            "displays": displays.get(name),
                            "archetype": False,
                            "label": None,
                            "lines": spoken.get(name, 0),
                            "speakers": speakers.get(name, 0),
                            "borrows": f"{borrowed}-{gender}",
                            # has clips to offer of its own: spoken emotes here or in
                            # retail (nightborne, an allied race), or sets it is cast with
                            "offers": name in speaking or name in counts,
                        }
                    )
        from tools.soundpaths import folders

        sets = folders()
        for row in rows:
            row["expansion"] = voice_expansion(row["voice"])
            # an archetype is one actor's NPC bark set: which, its folder, and the
            # sibling sets the same actor was cast under, for the voice card
            cast = archetype_of(row["voice"]) if row["archetype"] else None
            if cast:
                race_gender, sound_id = cast
                row["barks"] = {
                    "set": sound_id,
                    "folder": sets.get(sound_id),
                    "siblings": list(sibling_sets(race_gender, sound_id)),
                }
        return sorted(rows, key=lambda r: r["voice"])

    def held_lines(
        self, config: Config | None = None
    ) -> dict[str, tuple[Item, Variant]]:
        """The lines whose [lines] pin holds today, with their corpus entries. Taken
        before a settings or clip write, so what the write lapsed can be told after
        it without the corpus, which a write forgets. Empty until the corpus loads."""
        config = config or self.config()
        catalog = self.catalog(config)
        held = {}
        for base in sorted(config.lines.root):
            line = self.line_if_loaded(base)
            if line is not None and catalog.pin(base, line[0].voice, line[1].text):
                held[base] = line
        return held

    def live_pins(self, config: Config | None = None) -> dict[str, list[str]]:
        """held_lines by the voice each is read in and, when that voice borrows its
        clip, by the clip's voice too: a pin is heard under the voice's knobs and the
        clip's picks, so Keep these settings or a new pick on either lapses it."""
        config = config or self.config()
        catalog = self.catalog(config)
        by_voice: dict[str, list[str]] = {}
        for base, (item, _) in self.held_lines(config).items():
            by_voice.setdefault(item.voice, []).append(base)
            source = catalog.resolve(item.voice).source
            if source != item.voice:
                by_voice.setdefault(source, []).append(base)
        return by_voice

    def lapsed_since(self, held: dict[str, tuple[Item, Variant]]) -> list[str]:
        """Which of held_lines no longer hold under the configuration as it is now."""
        catalog = self.catalog(self.config())
        return sorted(
            base
            for base, (item, variant) in held.items()
            if catalog.pin(base, item.voice, variant.text) is None
        )

    def state(self) -> dict[str, Any]:
        config = self.config()
        catalog = self.catalog(config)
        resolved = {}
        for voice in self.voices():
            r = catalog.resolve(voice)
            resolved[voice] = {
                "clip": r.clip.name if r.clip else None,
                "source": r.source,
                "exaggeration": r.settings.exaggeration,
                "cfg_weight": r.settings.cfg_weight,
                "tempo": r.settings.tempo,
                "pitch": r.settings.pitch,
                "speed": r.settings.speed,
                "reference": r.settings.reference,
                "tuned": catalog.tuned(voice),
            }
        return {
            "config_path": str(self.config_path),
            "voices": self.voices(),
            "pickable": self.pickable(),
            "narrator": config.voices.narrator,
            "defaults": {
                "exaggeration": config.tts.exaggeration,
                "cfg_weight": config.tts.cfg_weight,
                "tempo": config.tts.tempo,
                "pitch": config.tts.pitch,
                "speed": config.tts.speed,
            },
            "overrides": {
                v: t.model_dump(exclude_none=True) for v, t in config.tts.voices.items()
            },
            "resolved": resolved,
            "pronunciations": config.pronunciations.root,
            "speakers": config.voices.speakers,
            # the lines whose pin holds, by the voice (and borrowed clip) it holds
            # under: what a retune or a re-pick would lapse
            "pinned": self.live_pins(config),
            # [lines], as written; whether each still holds is the line's to say
            "pins": {
                base: {
                    "seed": pin.seed,
                    "chunk_chars": pin.chunk_chars,
                    "same_seed": pin.same_seed,
                }
                for base, pin in config.lines.root.items()
            },
            # offered beside the voices in "Also offer clips from", as folder-<name>
            "clip_folders": config.voices.clip_folders,
            # the order the "Also offer clips from" list groups voices in
            "expansions": EXPANSIONS,
            # current: heard as it is configured now; stale: approved, then something
            # it reads from changed (VoiceCatalog.recipe)
            "approved": {
                voice: "current" if catalog.recipe(voice) == heard else "stale"
                for voice, heard in config.voices.approved.items()
            },
            # tasting notes by voice, for the voice card
            "notes": config.voices.notes,
            # stale approvals the page can offer to go back to (approved_target)
            "revertable": [
                voice
                for voice, heard in config.voices.approved.items()
                if catalog.recipe(voice) != heard and approved_target(config, voice)
            ],
            "sources": {
                v: e.model_dump(exclude_none=True)
                for v, e in config.voices.sources.items()
            },
            "windows": {"t3": T3_SECONDS, "s3gen": S3GEN_SECONDS},
            "model": self.model_status,
            "corpus": self.corpus_status,
            "bulk_service": bulk_service_state(),
        }


def bulk_service_state() -> str:
    try:
        return (
            subprocess.run(
                ["systemctl", "--user", "is-active", "forever-vo-bulk.service"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str
    reference: str | None = (
        None  # a clip stem to clone from instead of the voice's own resolution
    )
    exaggeration: list[float] = Field(min_length=1, max_length=6)
    cfg_weight: list[float] = Field(min_length=1, max_length=6)
    tempo: list[float] = Field(default=[1.0], min_length=1, max_length=4)
    pitch: list[float] = Field(default=[0.0], min_length=1, max_length=4)
    speed: list[float] = Field(default=[1.0], min_length=1, max_length=4)
    takes: int = Field(default=1, ge=1, le=5)
    # characters per generate() call (textclean.chunk); the pack uses CHUNK_CHARS
    chunk_chars: int = Field(default=CHUNK_CHARS, ge=120, le=500)
    # every chunk of a take drawn from the same seed (Synth.render_take)
    same_seed: bool = False
    # draw every take of the run from this seed (shown on each take's row): one take,
    # since a second would be the first again; a sweep of settings on one draw is fine
    seed: int | None = Field(default=None, ge=0, lt=2**31)
    # the picked line, stamped on its takes so one can be pinned to it
    base: str | None = None


class KeepTuning(BaseModel):
    voice: str
    exaggeration: float
    cfg_weight: float
    tempo: float = 1.0
    pitch: float = 0.0
    speed: float = 1.0
    reference: str | None = None


class KeepPronunciation(BaseModel):
    word: str = Field(min_length=1)
    spoken: str = ""


class KeepApproval(BaseModel):
    voice: str
    approved: bool


class KeepSpeakerVoice(BaseModel):
    speaker: str = Field(pattern=r"^-?[1-9][0-9]*$")
    voice: str = ""  # empty: back to the voice the capture resolves to


class PinTake(BaseModel):
    session: str
    name: str  # the take's mp3


class ChunkPreview(BaseModel):
    text: str
    chars: int = Field(default=CHUNK_CHARS, ge=120, le=500)
    base: str | None = None  # the picked line, whose own text is used as it stands


class RevertVoice(BaseModel):
    voice: str


class VoiceNotes(BaseModel):
    voice: str
    text: str = Field(default="", max_length=20000)


class ForgetPick(BaseModel):
    """One Earlier pick to drop, by its clips and gaps."""

    voice: str
    clips: list[int] = Field(min_length=1, max_length=40)
    gaps: list[float] = Field(default_factory=list, max_length=40)


class BuildSources(BaseModel):
    """Build a voice's reference from clips chosen by ear, head first."""

    voice: str
    clips: list[int] = Field(min_length=1, max_length=40)
    build: str | None = None
    keep: bool = (
        True  # also write [voices.sources.<voice>] into configs/voices.sources.toml
    )
    # seconds of silence after each clip, aligned with `clips` (VoiceSources.gaps)
    gaps: list[float] = Field(default_factory=list, max_length=40)


def run_finished(voice: str, outcome: dict[str, Any]) -> None:
    """A desktop notification that a generate run ended, from the server.

    The page tried the browser's Notification API first, and on the owner's KDE
    desktop it never showed one, while `notify-send` from this process did at once.
    So the server, which runs in the desktop session the page is open in, says it;
    where there is no notify-send (a server on another machine) the page alone does.
    """
    notify = shutil.which("notify-send")
    if not notify:
        return
    if outcome["event"] == "error":
        body = f"failed: {outcome.get('message', '')}"
    else:
        n = outcome.get("count", 0)
        takes = f"{n} take{'' if n == 1 else 's'}"
        body = (
            f"stopped · {takes} done"
            if outcome["event"] == "stopped"
            else f"{takes} done"
        )
    try:
        subprocess.Popen(
            [notify, "--app-name=Forever VO audition", f"Audition: {voice}", body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # a missed notification is not worth failing the run's last event over


def local_clip(voice: str, fdid: int, build: str | None) -> Path | None:
    """A clip under this voice's raw folder, linked from the download cache when it was
    fetched for another voice; None when it was never downloaded. No network.

    Clips fetched before the shared cache existed (2026-09-29) are only in the raw
    folder of the voice they were fetched for - Thrall's 523 among them - so the other
    voices' folders are the second place to look."""
    from tools.refclips import RAW_DIR as CLIP_RAW

    dest = CLIP_RAW / voice / f"{fdid}.ogg"
    if dest.exists():
        return dest
    if (CASC_DIR / (build or BETA_BUILD) / f"{fdid}.ogg").exists():
        return fetch_file(fdid, dest, build=build or BETA_BUILD)
    found = next(CLIP_RAW.glob(f"*/{fdid}.ogg"), None)
    if found is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(found, dest)
    except FileExistsError:
        pass  # another request linked it first
    except OSError:
        shutil.copyfile(found, dest)
    return dest


@functools.cache
def speech_voices() -> frozenset[str]:
    """Every race-gender with spoken emotes in this client or in retail, which is what
    refclips.speech_candidates offers a voice with no sets of its own."""
    from tools.build_voice_references import emote_speech_fdids
    from tools.config import RETAIL_BUILD

    found: set[str] = set()
    for build in (BETA_BUILD, RETAIL_BUILD):
        # a table that cannot be had offers nothing, rather than failing /api/state
        with contextlib.suppress(Exception):
            found.update(emote_speech_fdids(build))
    return frozenset(found)


def indexed_voices() -> set[str]:
    """Every voice sound_index.json records a file in."""
    try:
        index = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {entry.get("v") for entry in index.values() if entry.get("v")}


@dataclass(frozen=True)
class SpeciesWant:
    lines: int
    now: str  # the voice most of those lines are read in today
    speakers: frozenset[str]  # their speaker keys, for the NPC list


def species_wanted(
    items: list[Item], voiced: set[str] | None = None
) -> dict[str, SpeciesWant]:
    """Species voices that would take lines, with the voice those lines have now:
    {"spirithealer-female": SpeciesWant(1, "human-female", {"6491"})}. A speaker counts when its model
    names a species and it is not read in a voice of that species, which happens
    when the species has no clip; the name offered is the most specific
    wowdata.species_voice_names would try.

    `voiced` is every voice the sound index has files in. A species clip is not in
    git, so one that exists on the machine that generates the pack (ogre-male, from
    Warcraft III) can be missing here; offering it would invite a clip that shadows
    that one."""
    from tools.wowdata import (
        display_race_sex,
        speaker_species,
        species_voice_names,
    )

    found: dict[str, Counter[str]] = {}
    speakers: dict[str, set[str]] = {}
    for item in items:
        npc = item.npc or {}
        if npc.get("isObject") or npc.get("isObjectOrItem"):
            continue
        # a speaker's own clip or a pinned voice stays ahead of any species voice
        if (
            item.voice.startswith("npc-")
            or str(item.speaker_key) in item.config.voices.speakers
        ):
            continue
        species = speaker_species(npc.get("displayID"), npc.get("modelFileID"))
        if not species:
            continue
        sex = display_race_sex(npc.get("displayID"))[1]
        if sex is None:
            sex = {2: 0, 3: 1}.get(npc.get("sex"))
        names = species_voice_names(species, sex, item.config.voices)
        if not names or item.voice in names or set(names) & (voiced or set()):
            continue
        found.setdefault(names[0], Counter())[item.voice] += 1
        speakers.setdefault(names[0], set()).add(str(item.speaker_key or ""))
    return {
        name: SpeciesWant(
            sum(now.values()), now.most_common(1)[0][0], frozenset(speakers[name])
        )
        for name, now in sorted(found.items())
    }


def group_about(voice: str, group: str, spoken: dict[str, int]) -> str:
    """One line on what a clip group is, for its heading in the Source clips table.

    For a set: its folder, how many creature displays use it in this race, whether
    the plain voice is built from it, and whether it has an archetype clip of its own
    and how many lines that speaks (`spoken`, empty while the corpus loads)."""
    from tools.refclips import FOLDER_PREFIX
    from tools.soundpaths import folders, named_folder_files

    race_gender = base_voice(voice)
    if group.startswith("set ") and group[4:].isdigit():
        sound_id = int(group[4:])
        counts = sound_set_displays().get(race_gender) or Counter()
        folder = folders().get(sound_id)
        n = counts.get(sound_id, 0)
        about = [folder or "folder unknown", f"{n} display{'' if n == 1 else 's'}"]
        shared = [
            other
            for other in counts
            if other != sound_id and folder and folders().get(other) == folder
        ]
        if shared:
            about.append(
                "same folder as " + ", ".join(f"set {other}" for other in shared)
            )
        if sound_id == dominant_sound_set(race_gender):
            # what the automatic build makes the plain clip from; once the set has an
            # archetype of its own, its NPCs no longer speak in the plain voice
            about.append("the most-used set")
        cast = set_voice(race_gender, sound_id) or race_gender
        lines = spoken.get(cast)
        # the voice's lines in all, not this set's: the corpus counts by voice
        counted = f"; that voice has {lines} lines" if lines else ""
        if cast == voice:
            about.append(
                f"its NPCs speak in this voice{'; ' + str(lines) + ' lines in all' if lines else ''}"
            )
        elif archetype_names(race_gender).get(sound_id) == cast:
            about.append(f"its NPCs speak in {cast}, its own clip{counted}")
        elif cast != race_gender:
            about.append(f"its NPCs speak in {cast}, a sibling's clip{counted}")
        else:
            about.append(f"no clip of its own: its NPCs speak in {cast}{counted}")
        return " · ".join(about)
    if group == "speech":
        return f"spoken /joke and /flirt emotes, {race_gender}'s only connected speech"
    if group == "retail speech":
        return f"/joke and /flirt from retail: this client has none for {race_gender}"
    if group == "greetings":
        return "the greeting kit this NPC's sound set links"
    if group == "saved picks from other voices":
        return "named by a saved pick, offered by none of the groups here"
    files = named_folder_files().get(group.lower())
    if files is not None:
        where = (
            "sound folder from [voices] clip_folders"
            if voice.startswith(FOLDER_PREFIX)
            else "sound folder"
        )
        return f"{where} · {len(files)} real lines"
    return ""


# The expansion a race's voice comes from, for grouping the page's "Also offer clips
# from" list. A race is filed where its NPCs first spoke, not where it became playable:
# goblins bark in Booty Bay in 1.x. Skyborne are Forever's own.
EXPANSIONS = ["Forever", "Classic", "The Burning Crusade", "Wrath of the Lich King",
              "Cataclysm", "Mists of Pandaria", "Legion", "Battle for Azeroth",
              "Dragonflight"]  # fmt: skip
RACE_EXPANSION = {
    "skyborne": "Forever",
    **dict.fromkeys(
        [
            "human",
            "orc",
            "dwarf",
            "nightelf",
            "scourge",
            "tauren",
            "gnome",
            "troll",
            "goblin",
            "skeleton",
            "foresttroll",
            "naga",
        ],
        "Classic",
    ),
    **dict.fromkeys(["bloodelf", "draenei", "felorc", "broken"], "The Burning Crusade"),
    **dict.fromkeys(
        ["vrykul", "tuskarr", "taunka", "northrendskeleton", "icetroll"],
        "Wrath of the Lich King",
    ),
    "worgen": "Cataclysm",
    "pandaren": "Mists of Pandaria",
    **dict.fromkeys(
        ["nightborne", "highmountaintauren", "voidelf", "lightforgeddraenei"], "Legion"
    ),
    **dict.fromkeys(
        [
            "zandalari",
            "kultiran",
            "thinhuman",
            "darkirondwarf",
            "vulpera",
            "magharorc",
            "mechagnome",
        ],
        "Battle for Azeroth",
    ),
    "dracthyr": "Dragonflight",
}


def voice_expansion(voice: str) -> str | None:
    """The expansion of the race a voice speaks for, a race voice's or an archetype's.
    None for a named NPC, whose display's race is no guide (Forever recast Sylvanas's
    display as a skyborne) and which the page lists apart, and for a species."""
    if voice.startswith("npc-"):
        return None
    return RACE_EXPANSION.get(voice.split("-", 1)[0])


def wowhead_url(speaker_key: str) -> str | None:
    """wowhead's page for a speaker: npc=<id> for a creature, object=<id> for a game
    object, whose key is the negated ID."""
    if speaker_key.isdigit():
        return f"https://www.wowhead.com/npc={speaker_key}"
    if speaker_key.startswith("-") and speaker_key[1:].isdigit():
        return f"https://www.wowhead.com/object={speaker_key[1:]}"
    return None


def _voice_label(voice: str, labels: dict[int, str]) -> str:
    """npc-4527 (Thrall), or the name alone for a voice with no character label."""
    if voice.startswith("npc-"):
        try:
            label = labels.get(int(voice.removeprefix("npc-")))
        except ValueError:
            label = None
        if label:
            return f"{voice} ({label})"
    return voice


def _safe(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise HTTPException(400, f"bad name {name!r}")
    return name


def _under(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, relative)
    return path


def take_about(rendered: Any, seconds: float, request: GenerateRequest) -> dict:
    """A take's seed and chunk marks for the page, the marks moved into the encoded
    file's time: tempo and speed stretch the render evenly (pitch keeps its length),
    so one ratio maps every mark, whatever encode did."""
    ratio = seconds / rendered.seconds if rendered.seconds else 1.0
    return {
        "seed": rendered.seed,
        "same_seed": request.same_seed,
        "chunk_chars": request.chunk_chars,
        "chunks": [
            {
                "start": round(mark.start * ratio, 2),
                "end": round(mark.end * ratio, 2),
                "text": mark.text,
                "seed": mark.seed,
                "attempts": mark.attempts,
                "capped": mark.capped,
                "split": mark.split,
                "source": mark.source,
            }
            for mark in rendered.chunks
        ],
    }


SIDECAR_KEYS = (
    "seed",
    "same_seed",
    "chunk_chars",
    "chunks",
    # what Pin this take checks: the line, its voice, the exact text spoken and the
    # fingerprint the take was made under
    "base",
    "line_voice",  # the voice the take was read in, for a take of a line
    "spoken",
    "fingerprint",
    "clip",
    "source",
    "reference",
)


def write_sidecar(mp3: Path, about: dict) -> None:
    """`<take>.json` beside the take, so a past run keeps its seed and chunk marks;
    written whole and renamed, since /api/sessions may read it while a run goes on."""
    tmp = mp3.with_name(f"{mp3.stem}.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(about, indent=1), encoding="utf-8")
    os.replace(tmp, mp3.with_suffix(".json"))


def read_sidecar(mp3: Path) -> dict:
    """What write_sidecar kept for a take, for the page; nothing for a take from
    before sidecars or one whose file cannot be read."""
    try:
        about = json.loads(mp3.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(about, dict):
        return {}
    return {key: about[key] for key in SIDECAR_KEYS if key in about}


def create_app(
    studio: Studio,
    dev: bool = False,
    addons: Path | None = ADDONS_DIR,
    sound_index: Path = SOUND_INDEX,
) -> FastAPI:
    """`dev` re-reads index.html on every request, so page edits show on a browser
    refresh; Python edits still need a restart (main's --reload does that).
    `sound_index` is what a working folder copy is checked against (WorkingCopies)."""
    app = FastAPI(title="Forever Voiceover audition")
    page_file = resources.files(__package__) / "index.html"
    page = page_file.read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page_file.read_text(encoding="utf-8") if dev else page

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return studio.state()

    packs = sound_packs(addons)
    packs_by_key = {pack.key: pack for pack in packs}

    working = WorkingCopies(sound_index)

    def line_pin(row: LineRow, catalog: VoiceCatalog) -> dict[str, Any]:
        """The line's [lines] pin and whether it still holds, and whether the line
        plays as parts, where a pin would change nothing heard."""
        line = studio.line_if_loaded(row.base)
        if line is None:
            return {"pin": None, "parts": False}
        item, variant = line
        pin = catalog.config.lines.root.get(row.base)
        return {
            "pin": {
                "seed": pin.seed,
                "chunk_chars": pin.chunk_chars,
                "same_seed": pin.same_seed,
                "live": catalog.pin(row.base, item.voice, variant.text) is not None,
                # the knobs and clone-from it was heard under, for the page to mark
                "heard": pinned_settings(pin, catalog.config.tts),
            }
            if pin
            else None,
            # plays as parts that a pin cannot decide: the speaker's words split
            "parts": bool(variant.parts) and generate.pinnable_part(variant) is None,
        }

    def payload(row: LineRow, catalog: VoiceCatalog | None = None) -> dict[str, Any]:
        """The first pack that has the line's file, skipping a working folder copy
        that is an older take than the index records; that copy still plays when no
        installed pack has the line, marked `stale`."""
        catalog = catalog or VoiceCatalog(studio.config_cached())
        found, stale = None, False
        for pack in packs:
            path = sound_path(row.subfolder, row.base, sounds_dir=pack.sounds)
            if not path.exists():
                continue
            if pack.key == PACK_NAME and working.stale(path, row.base):
                stale = True
                continue
            found = pack
            break
        if found is None and stale:
            found = packs[0]
        return {
            **row.__dict__,
            "exists": found is not None,
            "pack": found.label if found else None,
            "pack_version": found.version if found else None,
            "pack_url": f"/api/pack/{found.key}/{row.subfolder}/{row.base}.mp3"
            if found
            else None,
            # the working folder's copy is an older take (WorkingCopies)
            "stale": stale,
            # the seed the index says drew the builder's file; the file playing
            # here was drawn from it only when it is the working folder's own
            # current copy: an installed pack is a release, maybe an earlier take
            "kept": working.kept(row.base),
            "drawn": found is not None and found.key == PACK_NAME and not stale,
            "index_date": _index_date(working.stamp()),
            **line_pin(row, catalog),
        }

    def payloads(rows: list[LineRow]) -> list[dict[str, Any]]:
        working.prime(
            [
                sound_path(row.subfolder, row.base, sounds_dir=packs[0].sounds)
                for row in rows
            ]
        )
        catalog = VoiceCatalog(studio.config_cached())
        return [payload(row, catalog) for row in rows]

    @app.get("/api/lines")
    def lines(
        q: str = "", voice: str = "", limit: int = 60, pinned: bool = False
    ) -> dict[str, Any]:
        """Search by words, or list one voice's own lines when `voice` is given, the
        `limit` longest of them (0 for all: the page lists a voice's every line when
        the voice is chosen), or, with `pinned`, every line with a [lines] pin, live
        or lapsed (the payload's `pin.live` tells), in one voice when `voice` is
        given too, by voice then file name."""
        rows = studio.rows()
        if pinned:
            pins = studio.config_cached().lines.root
            found = sorted(
                (
                    row
                    for row in rows
                    if row.base in pins and (not voice or row.voice == voice)
                ),
                key=lambda row: (row.voice, row.base),
            )
            return {
                "rows": payloads(found),
                "total": len(found),
                "voice": voice or None,
            }
        if voice:
            _safe(voice)
            moving = studio.moving_to(voice)
            found = lines_in_voice(rows, voice, limit=limit or len(rows), moving=moving)
            total = sum(1 for row in rows if _in_voice(row, voice, moving))
            return {
                "rows": payloads(found),
                "total": total,
                "voice": voice,
            }
        if not q:
            raise HTTPException(
                400, "give q= words to search for, or voice= to list a voice's lines"
            )
        return {
            "rows": payloads(search(rows, q)),
            "total": None,
            "voice": None,
        }

    @app.get("/api/random")
    def random_in_voice(voice: str = Query(min_length=1)) -> dict[str, Any]:
        row = random_line(studio.rows(), _safe(voice), moving=studio.moving_to(voice))
        if row is None:
            raise HTTPException(404, f"no lines are spoken in {voice}")
        return payload(row)

    @app.get("/api/pack/{pack}/{subfolder}/{name}")
    def pack_audio(pack: str, subfolder: str, name: str) -> FileResponse:
        if pack not in packs_by_key:
            raise HTTPException(404, pack)
        if subfolder not in SUBFOLDERS.values():
            raise HTTPException(404, subfolder)
        return FileResponse(
            _under(packs_by_key[pack].sounds, f"{subfolder}/{_safe(name)}"),
            media_type="audio/mpeg",
        )

    @app.get("/api/audio/{session}/{name}")
    def take_audio(session: str, name: str) -> FileResponse:
        return FileResponse(
            _under(AUDITION_DIR, f"{_safe(session)}/{_safe(name)}"),
            media_type="audio/mpeg",
        )

    @app.get("/api/sessions")
    def sessions(limit: int = Query(default=12, ge=1, le=100)) -> list[dict[str, Any]]:
        """Past generate runs, newest first, so a page reload or a server restart
        does not lose the takes: they are still on disk under tools/data/audition/."""
        out = []
        if not AUDITION_DIR.exists():
            return out
        for folder in sorted(
            (p for p in AUDITION_DIR.iterdir() if p.is_dir()), reverse=True
        )[:limit]:
            takes = []
            for path in sorted(folder.glob("*.mp3")):
                recipe = re.match(
                    r"^(?P<voice>.+)-e(?P<e>[0-9.]+)-c(?P<c>[0-9.]+)(?:-t(?P<t>[0-9.]+))?"
                    r"(?:-p(?P<p>-?[0-9.]+))?(?:-s(?P<s>[0-9.]+))?-take(?P<take>\d+)\.mp3$",
                    path.name,
                )
                takes.append(
                    {
                        "name": path.name,
                        "url": f"/api/audio/{folder.name}/{path.name}",
                        "voice": recipe["voice"] if recipe else None,
                        "exaggeration": float(recipe["e"]) if recipe else None,
                        "cfg_weight": float(recipe["c"]) if recipe else None,
                        "tempo": float(recipe["t"]) if recipe and recipe["t"] else 1.0,
                        "pitch": float(recipe["p"]) if recipe and recipe["p"] else 0.0,
                        "speed": float(recipe["s"]) if recipe and recipe["s"] else 1.0,
                        "take": int(recipe["take"]) if recipe else None,
                        # seed and chunk marks, from takes made since they were kept
                        **read_sidecar(path),
                    }
                )
            if takes:
                out.append({"session": folder.name, "takes": takes})
        return out

    @app.post("/api/generate")
    def generate_takes(request: GenerateRequest) -> StreamingResponse:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        config = studio.config()
        if request.seed is not None and request.takes > 1:
            raise HTTPException(
                400,
                "a seed makes every take the same; ask for one take (a sweep of "
                "settings on that one draw is fine)",
            )
        line = studio.line_if_loaded(request.base)
        if line is not None and request.text == line[1].text:
            # the picked line's own text is spoken as the generator speaks it: it was
            # cleaned and respelled already, and a second pass could respell twice or
            # read a narrator's stage directions differently
            spoken = line[1].text
        else:
            spoken = clean(request.text, pronunciations=config.pronunciations)
        if not spoken:
            raise HTTPException(400, "nothing to say once the text is cleaned")
        # what Pin this take checks, from the take's sidecar: the line it was made
        # for, the voice it was read in (the page's, which need not be the line's), the
        # exact text and the fingerprint it was made under. The fingerprint holds no
        # clip name, so two untuned voices without picks share one, and only this
        # tells a take in another voice from one in the line's.
        line_about = (
            {"base": request.base, "line_voice": request.voice}
            if line is not None
            else {"base": None, "line_voice": None}
        )
        # Build one tuning before the stream opens. The settings come from three free
        # text boxes and the models have bounds - tempo is 0.5 to 2.0 - so a typo used
        # to raise inside the generator, after 200 had been sent, and the page could
        # only report that the connection broke. A tempo of 10 looked like a network
        # error.
        for exaggeration, cfg_weight, tempo, pitch, speed in itertools.product(
            request.exaggeration,
            request.cfg_weight,
            request.tempo,
            request.pitch,
            request.speed,
        ):
            try:
                VoiceTuning(
                    reference=request.reference,
                    exaggeration=exaggeration,
                    cfg_weight=cfg_weight,
                    tempo=tempo,
                    pitch=pitch,
                    speed=speed,
                )
            except ValidationError as e:
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                    for err in e.errors()
                )
                raise HTTPException(
                    400,
                    f"{detail} (you gave exaggeration {exaggeration}, "
                    f"cfg_weight {cfg_weight}, tempo {tempo}, pitch {pitch}, "
                    f"speed {speed})",
                ) from e
        session = time.strftime("%Y%m%d-%H%M%S")
        out_dir = AUDITION_DIR / session
        out_dir.mkdir(parents=True, exist_ok=True)

        def variant_config(
            exaggeration: float,
            cfg_weight: float,
            tempo: float = 1.0,
            pitch: float = 0.0,
            speed: float = 1.0,
        ) -> Config:
            tuning = VoiceTuning(
                reference=request.reference,
                exaggeration=exaggeration,
                cfg_weight=cfg_weight,
                tempo=tempo,
                pitch=pitch,
                speed=speed,
            )
            tts = config.tts.model_copy(
                update={"voices": {**config.tts.voices, request.voice: tuning}}
            )
            return config.model_copy(update={"tts": tts})

        stop = studio.stops.setdefault(session, threading.Event())

        def stream() -> Iterator[str]:
            outcome = None
            try:
                for line in takes():
                    event = json.loads(line)
                    if event["event"] in ("done", "stopped", "error"):
                        outcome = event
                    yield line
            finally:  # also when the page goes away mid-run
                studio.stops.pop(session, None)
                if outcome:
                    run_finished(request.voice, outcome)

        def takes() -> Iterator[str]:
            yield (
                json.dumps({"event": "start", "session": session, "spoken": spoken})
                + "\n"
            )
            try:
                synth = studio.synth()
            except (
                SystemExit,
                RuntimeError,
                OSError,
                ImportError,
            ) as e:  # no GPU (SystemExit), CUDA or model load trouble
                yield json.dumps({"event": "error", "message": str(e)}) + "\n"
                return
            n = 0
            # The model runs once per take of each exaggeration and cfg_weight; every
            # tempo and pitch is then encoded from that same audio, so a sweep over
            # them compares one delivery, not several different takes.
            for exaggeration, cfg_weight in itertools.product(
                request.exaggeration, request.cfg_weight
            ):
                catalog = VoiceCatalog(variant_config(exaggeration, cfg_weight))
                for take in range(1, request.takes + 1):
                    # between takes, and inside one between generate() calls (a long
                    # line is several): Chatterbox's generate() itself cannot be
                    # interrupted, so the call in progress always finishes
                    if stop.is_set():
                        yield json.dumps({"event": "stopped", "count": n}) + "\n"
                        return
                    t0 = time.time()
                    try:
                        with studio.model_lock:
                            synth.catalog = catalog
                            rendered = synth.render_take(
                                spoken,
                                request.voice,
                                stopped=stop.is_set,
                                seed=request.seed,
                                same_seed=request.same_seed,
                                max_chars=request.chunk_chars,
                            )
                    except TakeStopped:
                        yield json.dumps({"event": "stopped", "count": n}) + "\n"
                        return
                    for tempo, pitch, speed in itertools.product(
                        request.tempo, request.pitch, request.speed
                    ):
                        variant_catalog = VoiceCatalog(
                            variant_config(
                                exaggeration, cfg_weight, tempo, pitch, speed
                            )
                        )
                        resolved = variant_catalog.resolve(request.voice)
                        settings = resolved.settings
                        n += 1
                        # speed joins the name only when it is not 1, so the takes of
                        # older runs still read back the way they were written
                        name = (
                            f"{request.voice}-e{exaggeration}-c{cfg_weight}"
                            f"-t{tempo}-p{pitch}"
                            + (f"-s{speed}" if speed != 1.0 else "")
                            + f"-take{take}.mp3"
                        )
                        seconds = synth.encode(
                            rendered.audio,
                            out_dir / name,
                            settings.tempo,
                            settings.pitch,
                            settings.speed,
                        )
                        about = {
                            **take_about(rendered, seconds, request),
                            **line_about,
                            "spoken": spoken,
                            "fingerprint": variant_catalog.fingerprint(
                                request.voice, spoken
                            ),
                        }
                        write_sidecar(
                            out_dir / name,
                            {
                                **about,
                                "seconds": round(seconds, 2),
                                "clip": resolved.clip.name if resolved.clip else None,
                                "source": resolved.source,
                                "reference": request.reference,
                            },
                        )
                        yield (
                            json.dumps(
                                {
                                    **about,
                                    "event": "take",
                                    # the voice it was made for, which Keep these
                                    # settings saves to, whatever is selected later
                                    "voice": request.voice,
                                    "n": n,
                                    "take": take,
                                    "name": name,
                                    "url": f"/api/audio/{session}/{name}",
                                    "seconds": round(seconds, 1),
                                    "elapsed": round(time.time() - t0, 1),
                                    "clip": resolved.clip.name
                                    if resolved.clip
                                    else None,
                                    "source": resolved.source,
                                    "exaggeration": settings.exaggeration,
                                    "cfg_weight": settings.cfg_weight,
                                    "tempo": settings.tempo,
                                    "pitch": settings.pitch,
                                    "speed": settings.speed,
                                    "reference": request.reference,
                                }
                            )
                            + "\n"
                        )
                        t0 = (
                            time.time()
                        )  # later shifts of the take cost the encode only
            yield json.dumps({"event": "done", "count": n}) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/generate/{session}/stop")
    def stop_takes(session: str) -> dict[str, bool]:
        """Ends a run after the take in progress. False when it has already finished."""
        stop = studio.stops.get(session)
        if stop:
            stop.set()
        return {"stopping": stop is not None}

    @app.post("/api/keep-tuning")
    def keep_tuning(request: KeepTuning) -> dict[str, Any]:
        _safe(request.voice)
        if request.reference:
            _safe(request.reference)
        held = studio.held_lines()
        write_tuning(
            studio.config_path,
            request.voice,
            request.exaggeration,
            request.cfg_weight,
            request.reference,
            request.tempo,
            request.pitch,
            request.speed,
        )
        studio.forget_corpus()
        state = studio.state()
        warning = lapsed_pins(studio.lapsed_since(held))
        if warning:
            state["warning"] = warning
        return state

    @app.post("/api/keep-pronunciation")
    def keep_pronunciation(request: KeepPronunciation) -> dict[str, Any]:
        write_pronunciation(
            studio.config_path, request.word.strip(), request.spoken.strip()
        )
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/keep-approval")
    def keep_approval(request: KeepApproval) -> dict[str, Any]:
        """Marks a voice approved by ear as it is configured now, or takes the mark
        off. Only a note in the TOML: nothing is generated or restaged by it."""
        _safe(request.voice)
        recipe = studio.catalog().recipe(request.voice) if request.approved else None
        write_approval(studio.config_path, request.voice, recipe)
        return studio.state()

    @app.post("/api/keep-speaker-voice")
    def keep_speaker_voice(request: KeepSpeakerVoice) -> dict[str, Any]:
        """Pins one speaker to a voice; the voice-change check in generate.py then
        restages exactly that speaker's files."""
        if request.voice:
            _safe(request.voice)
            if request.voice not in studio.voices():
                raise HTTPException(400, f"no voice named {request.voice}")
        write_speaker_voice(studio.config_path, request.speaker, request.voice)
        studio.forget_corpus()
        return studio.state()

    @app.post("/api/chunks")
    def chunk_preview(request: ChunkPreview) -> dict[str, Any]:
        """How a text is cut into model calls at a chunk length, as the model hears
        it (respelled), with the chunk length the picked line's seed goes with: its
        live pin's, else the length that drew the pack's file (`from`: "pin" or
        "kept"). Reads the config only when the file has changed: the page asks as
        you type."""
        config = studio.config_cached()
        line = studio.line_if_loaded(request.base)
        if line is not None and request.text == line[1].text:
            spoken = line[1].text
        else:
            spoken = clean(request.text, pronunciations=config.pronunciations)
        pin = (
            VoiceCatalog(config).pin(request.base or "", line[0].voice, line[1].text)
            if line is not None
            else None
        )
        kept = working.kept(request.base) if request.base and line else None
        line_chars, origin = (
            (pin.chunk_chars, "pin")
            if pin
            else (kept["chunk_chars"], "kept")
            if kept
            else (None, None)
        )
        return {
            "spoken": spoken,
            "chars": request.chars,
            "chunks": chunk(spoken, request.chars) if spoken else [],
            "line_chars": line_chars,
            "from": origin,
        }

    @app.post("/api/pin-seed")
    def pin_seed(request: PinTake) -> dict[str, Any]:
        """Pins a take's seed to its line. Everything comes from the take's own
        sidecar, written when it was made, and is checked against the line as it reads
        today: its text exactly, its voice, and the fingerprint the take was made under,
        which holds the clip, its picks and the tuning, so only a take of what the pack
        would generate can be pinned."""
        try:
            mp3 = _under(
                AUDITION_DIR, f"{_safe(request.session)}/{_safe(request.name)}"
            )
        except HTTPException:
            raise HTTPException(
                404, "that take is no longer on disk (tools/data/audition)"
            ) from None
        about = read_sidecar(mp3)
        base = about.get("base")
        if not base or "seed" not in about:
            raise HTTPException(
                400,
                "this take names no line: pick a line from the corpus, then generate",
            )
        if studio.rows_if_loaded() is None:
            raise HTTPException(409, "the lines are reloading; try again in a moment")
        line = studio.line_if_loaded(base)
        if line is None:
            raise HTTPException(404, f"no line {base} now")
        item, variant = line
        if variant.parts and generate.pinnable_part(variant) is None:
            raise HTTPException(
                400,
                "this line plays as parts with the speaker's words split between "
                "several of them (narration in between), so no one take is what "
                "players hear",
            )
        if about.get("line_voice") != item.voice:
            raise HTTPException(
                400,
                f"the take was read in {about.get('line_voice')}, the line is read in {item.voice}",
            )
        if about.get("spoken") != variant.text:
            raise HTTPException(
                400,
                "the take said other words than the line's own text (edited in the box?)",
            )
        heard = studio.catalog().fingerprint(item.voice, variant.text)
        if about.get("fingerprint") != heard:
            raise HTTPException(
                400,
                "the take was made under other settings than the voice's saved ones: "
                "keep its settings first, or the pack would not draw this take",
            )
        if any(mark.get("capped") for mark in about.get("chunks") or []):
            # A pinned take is never redone for the cap (generate.ran_into_cap)
            raise HTTPException(
                400,
                "a chunk of this take ran into the token cap on every try, so it "
                "holds babble, and a pinned take is never drawn again: pin another",
            )
        kept = working.kept(base)
        if kept and kept["capped"] and kept["seed"] == about["seed"]:
            # A checkout days behind the nightly still shows the seed the builder
            # has since rolled away from; pinning it would bring the babble back
            raise HTTPException(
                400,
                "that seed is the index seed, and its take ran into the token cap on "
                "the machine that builds the pack: it is being drawn anew; pin another",
            )
        write_line_pin(
            studio.config_path,
            base,
            {
                "seed": about["seed"],
                "chunk_chars": about.get("chunk_chars", CHUNK_CHARS),
                "same_seed": bool(about.get("same_seed")),
                "heard": heard,
                "spoken": generate.spoken_hash(variant.text),
                "voice": item.voice,
            },
        )
        # `heard` was checked against this checkout's configs/. Four pins went dead
        # on 2026-10-13 because the reference they were heard under was never
        # committed: the builder's checkout never had it, so the pin never held.
        state = studio.state()
        if studio.config_path.resolve() != CONFIG_DIR.resolve():
            return state  # another configs/ than the repository's: nothing to compare
        edited = _git("status", "--porcelain", "--", "configs")
        changed = sorted(
            line[3:].strip() for line in (edited or "").splitlines() if line.strip()
        )
        if edited is None:
            state["warning"] = (
                "could not check configs/ for uncommitted edits (git failed): the "
                "pin holds on the machine that builds the pack only under committed "
                "settings"
            )
        elif changed and changed != ["configs/lines.toml"]:
            state["warning"] = (
                "this pin was heard under uncommitted edits in "
                f"{', '.join(changed)}: commit them with the pin, or the machine "
                "that builds the pack will not hold it"
            )
        return state

    @app.delete("/api/pin-seed/{base}")
    def unpin_seed(base: str) -> dict[str, Any]:
        """Removes a line's pin. The file the pin drew stays (generate.text_current);
        the line is drawn afresh only when something else changes it."""
        if not LINE_BASE.match(base):
            raise HTTPException(400, f"not a line's file name: {base}")
        write_line_pin(studio.config_path, base, None)
        return studio.state()

    @app.post("/api/clips/history/forget")
    def forget_history(request: ForgetPick) -> dict[str, Any]:
        """Drops an experiment from a voice's Earlier picks. A pick with audio in the
        pack and the one saved in configs/ are never dropped."""
        voice = _safe(request.voice)
        if not forget_pick(voice, request.clips, request.gaps):
            raise HTTPException(
                409, "that pick is released or saved, or already gone; it stays"
            )
        config = studio.config()
        return {
            "history": pick_history_for(
                voice, config.voices.sources.get(voice), None, config
            )
        }

    @app.get("/api/clips/sources")
    def clip_sources(voice: str, clips: str) -> dict[str, Any]:
        """The other voices and folders that offer these clips, for a pick pasted from
        another voice: what "Also offer clips from" needs so the whole pick is listed."""
        _safe(voice)
        fdids = [int(c) for c in clips.split(",") if c.strip().isdigit()]
        own = {c["fdid"] for c in studio.clips(voice) or []}
        return {"uses": pick_sources(voice, fdids, own, studio.config())}

    @app.post("/api/voice/notes")
    def voice_notes(request: VoiceNotes) -> dict[str, Any]:
        """Saves a voice's tasting notes; empty text removes them."""
        voice = _safe(request.voice)
        config = write_notes(studio.config_path, voice, request.text)
        return {"notes": config.voices.notes}

    @app.post("/api/voice/revert")
    def revert_voice(request: RevertVoice) -> dict[str, Any]:
        """Puts a voice back to what was approved: the picks heard (clips, build and
        gaps), the knobs, and its reference wav rebuilt from those clips. Every clip is
        checked first, so a revert that cannot finish touches nothing."""
        voice = _safe(request.voice)
        config = studio.config()
        target = approved_target(config, voice)
        if target is None:
            raise HTTPException(409, f"{voice} has no approved pick to go back to")
        pick, knobs = target
        clips, build = pick["clips"], pick.get("build")
        gaps = tidy_gaps(pick.get("gaps", []), len(clips))
        paths = []
        for fdid in clips:
            path = local_clip(voice, fdid, build)
            if path is None:
                raise HTTPException(
                    404, f"clip {fdid} is not downloaded; load the candidates first"
                )
            paths.append(path)
        try:
            out = build_picked_reference(voice, paths, gaps, clips=clips, build=build)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as e:
            raise HTTPException(400, str(e)) from e
        write_voice_sources(studio.config_path, voice, clips, build, gaps)
        write_tuning(
            studio.config_path,
            voice,
            knobs["exaggeration"],
            knobs["cfg_weight"],
            knobs["reference"],
            knobs["tempo"],
            knobs["pitch"],
            knobs["speed"],
        )
        studio.forget_corpus()
        record_pick(voice, clips, build, reference_seconds(out), gaps=gaps)
        after = studio.config()
        matches = VoiceCatalog(after).recipe(voice) == after.voices.approved.get(voice)
        return {
            **studio.state(),
            "reverted": matches,
            "message": f"{voice} is back to what was approved"
            if matches
            else f"{voice} was reverted, but its recipe still differs from the approval",
        }

    @app.get("/api/npcs/{voice}")
    def npcs(voice: str) -> dict[str, Any]:
        """The speakers read in this voice, most lines first, for the page's NPC list.
        A species voice nobody is cast on yet lists the speakers who would move to it
        once its clip exists (`moving`). Creature keys link to wowhead's npc pages,
        game objects (negative keys) to its object pages; items have no key."""
        _safe(voice)
        rows = studio.rows_if_loaded()
        if rows is None:
            return {"voice": voice, "status": "loading", "npcs": []}
        moving = studio.moving_to(voice)
        found: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row.speaker_key
            if not key or not (row.voice == voice or key in moving):
                continue
            entry = found.setdefault(
                key,
                {
                    "key": key,
                    "name": row.speaker or key,
                    "lines": 0,
                    "url": wowhead_url(key),
                    "now": row.voice if row.voice != voice else None,
                },
            )
            entry["lines"] += 1
        listed = sorted(found.values(), key=lambda n: (-n["lines"], n["name"]))
        return {"voice": voice, "status": "ready", "npcs": listed}

    @app.get("/api/clips/audio/{voice}/{name}")
    def clip_audio(voice: str, name: str) -> FileResponse:
        from tools.refclips import RAW_DIR as CLIP_RAW

        return FileResponse(
            _under(CLIP_RAW, f"{_safe(voice)}/{_safe(name)}"), media_type="audio/ogg"
        )

    @app.get("/api/clips/reference/{name}")
    def clip_reference(name: str) -> FileResponse:
        """The built reference itself, so the result can be heard without a generate."""
        return FileResponse(_under(VOICES_DIR, _safe(name)), media_type="audio/wav")

    @app.get("/api/clips/{voice}")
    def clips(voice: str, refresh: bool = False, also: str = "") -> dict[str, Any]:
        """Candidate clips for one voice. Returns status "loading" while the first
        fetch runs; the page polls.

        `also` names other voices whose candidates join the table, each group labelled
        with the voice it came from: Thrall's lines for an orc archetype. They come
        first, the last one added at the top, since one is added to be listened to and
        would otherwise sit below a few hundred of the voice's own rows. A saved pick
        found among none of them is listed anyway, from the download cache, so a
        rebuild cannot drop a clip borrowed in an earlier session. `pending` names the
        borrowed voices still loading. Each row's `source` is "own", "also" or "saved",
        for the group headings the page draws."""
        from tools import refclips

        _safe(voice)
        others = [_safe(v) for v in also.split(",") if v and v != voice]
        found = studio.clips(voice, refresh=refresh)
        seed_recipe_history(voice)
        config = studio.config()
        picked = config.voices.sources.get(voice)
        rows = [{**c, "source": "own"} for c in found or []]
        pending = []
        spoken = studio.spoken_if_loaded()
        about = {c["group"]: group_about(voice, c["group"], spoken) for c in rows}
        # borrowed sources are listed while this voice's own load still runs, which
        # with wago slow can be minutes: before, the table stayed empty and the page
        # blanked it on every poll until the voice's own list was in
        if found is None:
            pending.append(voice)
        labels = npc_labels()
        seen = {c["fdid"] for c in rows}
        borrowed: list[dict[str, Any]] = []
        for other in reversed(others):
            theirs = studio.clips(other)
            if theirs is None:
                pending.append(other)
                continue
            name = _voice_label(other, labels)
            for c in theirs:
                if c["fdid"] not in seen:
                    seen.add(c["fdid"])
                    group = f"{name}: {c['group']}"
                    if group not in about:
                        about[group] = group_about(other, c["group"], spoken)
                    borrowed.append({**c, "group": group, "source": "also"})
        rows = borrowed + rows
        # a saved pick not among the candidates, listed from the download cache; only
        # once the voice's own rows are in, or one of them would be filed here
        if found is not None:
            saved_build = picked.build if picked else None
            for fdid in picked.clips if picked else []:
                if fdid in seen:
                    continue
                path = local_clip(voice, fdid, saved_build)
                if path:
                    seen.add(fdid)
                    rows.append(
                        {
                            "n": len(rows) + 1,
                            "fdid": fdid,
                            "kind": "saved pick",
                            "group": "saved picks from other voices",
                            "source": "saved",
                            "seconds": round(
                                refclips.CLIP_SECONDS.get(
                                    fdid, saved_build or BETA_BUILD, path
                                ),
                                2,
                            ),
                            "url": f"/api/clips/audio/{voice}/{fdid}.ogg",
                        }
                    )
            refclips.CLIP_SECONDS.save()
        # "loading" whenever this answer was built without the voice's rows: a load
        # with nothing to fetch (spirithealer-female, no candidates of its own)
        # finishes on its thread before this line, and reporting its "0 clips" with
        # no rows told the page to stop polling for good. A failure still says so.
        status = studio.clips_status.get(voice, "not loaded")
        if found is None and not status.startswith("failed"):
            status = "loading"
        return {
            "voice": voice,
            "status": status,
            "clips": rows,
            "pending": pending,
            # for each voice still loading, how far its downloads have got
            "progress": {
                v: {**p, "elapsed": round(time.time() - p["since"])}
                for v in pending
                if (p := getattr(studio, "clips_progress", {}).get(v))
            },
            # in first-seen order, for the page's folder select
            "groups": list(dict.fromkeys(c["group"] for c in rows)),
            # one line on each, for the table's group headings
            "about": {
                group: about.get(group) or group_about(voice, group, spoken)
                for group in dict.fromkeys(c["group"] for c in rows)
            },
            "picked": picked.clips if picked else [],
            "gaps": picked.gaps if picked else [],
            "build": picked.build if picked else None,
            "windows": {"t3": T3_SECONDS, "s3gen": S3GEN_SECONDS},
            "warnings": sources_warnings(config, voice)
            + (unlisted_folders(config) if voice.startswith("npc-") else [])
            + unlisted_clip_folders(config),
            "history": pick_history_for(
                voice,
                picked,
                {c["fdid"] for c in found} if found is not None else None,
                config,
            ),
            # the sources the saved pick's clips come from, which the page adds to
            # "Also offer clips from" so the whole pick is listed, not as strays
            "uses": pick_sources(
                voice, picked.clips, {c["fdid"] for c in found}, config
            )
            if picked and found is not None
            else [],
            "reference_url": (
                f"/api/clips/reference/{voice}.wav"
                if (VOICES_DIR / f"{voice}.wav").exists()
                else None
            ),
        }

    @app.post("/api/clips/build")
    def build_sources(request: BuildSources) -> dict[str, Any]:
        from tools.refclips import RAW_DIR as CLIP_RAW

        voice = _safe(request.voice)
        # checked before anything is built: a gap the TOML would refuse must not
        # replace the reference first and fail on the write after. No gap is 0.
        gaps = tidy_gaps(request.gaps, len(request.clips))
        try:
            VoiceSources(clips=request.clips, gaps=gaps)
        except ValidationError as e:
            raise HTTPException(400, e.errors()[0]["msg"]) from e
        # The picks can only have come from /api/clips, so they are already downloaded:
        # under this voice's folder, or, borrowed from another voice, in the download
        # cache, linked here locally. Either way this endpoint stays off the network.
        paths = []
        for fdid in request.clips:
            path = local_clip(voice, fdid, request.build)
            if path is None:
                raise HTTPException(404, f"clip {fdid} is not downloaded")
            paths.append(_under(CLIP_RAW, f"{voice}/{path.name}"))
        existed = (VOICES_DIR / f"{voice}.wav").exists()
        held = studio.held_lines()
        try:
            out = build_picked_reference(
                voice, paths, gaps, clips=request.clips, build=request.build
            )
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as e:
            raise HTTPException(400, str(e)) from e
        if request.keep:
            write_voice_sources(
                studio.config_path, voice, request.clips, request.build, gaps
            )
        # A new clip changes what wowdata.archetype_voice casts, which the corpus caches
        studio.forget_corpus()
        seconds = reference_seconds(out)
        record_pick(voice, request.clips, request.build, seconds, gaps=gaps)
        return {
            **studio.state(),
            "built": out.name,
            "seconds": seconds,
            "existed": existed,
            "history": pick_history_for(
                voice, studio.config().voices.sources.get(voice), None, studio.config()
            ),
            "reference_url": f"/api/clips/reference/{voice}.wav?t={int(time.time())}",
            "restage": restage_note(voice, existed, request.keep),
            "warning": lapsed_pins(studio.lapsed_since(held)) if request.keep else None,
        }

    @app.post("/api/rebuild-tables")
    def rebuild_tables() -> dict[str, Any]:
        code = generate.main(["--tables-only"])
        return {"ok": code == 0}

    return app


def app_from_env() -> FastAPI:
    """uvicorn's --reload re-imports the module in a fresh process, so main() hands
    its options over through the environment and this factory builds the app."""
    import os

    studio = Studio(
        config_path=Path(os.environ.get("AUDITION_CONFIG", str(CONFIG_DIR))),
        allow_cpu=os.environ.get("AUDITION_CPU") == "1",
    )
    addons = os.environ.get("AUDITION_ADDONS")
    return create_app(
        studio,
        dev=os.environ.get("AUDITION_DEV") == "1",
        addons=Path(addons) if addons else ADDONS_DIR,
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_DIR,
        help="the configs/ directory to read and write (default: the repo's)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="allow generating on the CPU when there is no GPU (very slow)",
    )
    parser.add_argument(
        "--addons",
        type=Path,
        default=addons_from_env(),
        help="directory of ForeverVO_Data* packs played beside the working folder "
        "(default: AUDITION_ADDONS, else the client's AddOns from WOW_DIR)",
    )
    parser.add_argument(
        "--open", action="store_true", help="open the page in the browser"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="for working on the page: index.html is re-read on every request, and a change to a "
        ".py file under tools/ restarts the server (which reloads the model, ~30 s, and "
        "drops a generate in flight)",
    )
    args = parser.parse_args(argv)

    import os

    import uvicorn

    os.environ["AUDITION_CONFIG"] = str(args.config)
    os.environ["AUDITION_CPU"] = "1" if args.cpu else "0"
    os.environ["AUDITION_DEV"] = "1" if args.reload else "0"
    os.environ["AUDITION_ADDONS"] = str(args.addons)
    if not args.addons.is_dir():
        print(f"audition: no {args.addons}, so only the working folder's audio plays")
    url = f"http://{args.host}:{args.port}"
    print(
        f"audition: {url}  (config {args.config}{', reloading on edits' if args.reload else ''})"
    )
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(
        "tools.audition:app_from_env",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",
        reload=args.reload,
        reload_dirs=[str(Path(__file__).resolve().parent.parent)]
        if args.reload
        else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
