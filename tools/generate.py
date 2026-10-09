"""Generates missing voice lines with a local TTS model and rebuilds the
ForeverVO_Data voice pack tables.

    ./tools/run.sh tools/generate.py --dry-run          # what would be generated
    ./tools/run.sh tools/generate.py --limit 20         # generate a few
    ./tools/run.sh tools/generate.py                    # everything missing
    ./tools/run.sh tools/generate.py --tables-only      # just rebuild the pack tables

Input is tools/data/capture.json (see ingest.py). Audio goes to
ForeverVO_Data/Sounds/{Quests,Gossip}/ and the tables to ForeverVO_Data/Data/.
Tables are rebuilt from scratch every run from capture.json plus the files that
exist on disk, so the pack always matches what is actually present.

Voices: tools/voices/<race>-<gender>.wav are reference clips for cloning
(build_voice_references.py makes them from the client's own audio). Missing
voices fall back to narrator.wav, then to the model's built-in voice.

Quests read by the narrator (objects and items have no speaker to clone) are
also generated in the alternate voices listed under [voices] in configs/voices.toml,
into Sounds/Quests/Narrator/<voice>/, so players can pick the narrator they like
in the addon's options. Those files sort after everything else, so a time-boxed
run still spends its time on lines that have no audio at all:

    ./tools/run.sh tools/generate.py --narrator-voices none   # skip them
    ./tools/run.sh tools/generate.py --narrator-only          # a dedicated pass

A line that mixes the speaker and the narrator -- "Hmm... <Jorgen looks up at
you.> All right, I'll help ya." -- keeps its whole-line file with the stage
direction left out (older addons play that) and also gets one file per part in
reading order, 1241-p1-complete, 1241-p2-complete, ...: the speaker's words in
their voice and each stage direction in the narrator's, the latter again in
every alternate narrator voice. The addon plays the parts back to back and
swaps in the player's narrator. A line that is only a stage direction has
parts and no whole-line file.
"""

from __future__ import annotations

import argparse
import fcntl
import functools
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from tools.build_voice_references import build_pick, picked_reference_current
from tools.config import (
    CAPTURE_JSON,
    DATA_DIR,
    PACK_DATA_DIR,
    SOUND_INDEX,
    SOUNDS_DIR,
    VOICES_DIR,
    Config,
    LinePin,
    TtsSettings,
    load_config,
)
from tools.ingest import _aligned
from tools.luatable import lua_string
from tools.textclean import (
    CHUNK_CHARS,
    book_display,
    book_text,
    chunk,
    clean,
    halve,
    has_gender_branch,
    is_speakable,
    segments,
    split_gender,
)
from tools.textkey import text_key
from tools.wowdata import base_voice, display_model_file, is_archetype, voice_for_npc

QUEST_EVENTS = {"accept": "a", "progress": "p", "complete": "c"}
# A page of a book, letter or plaque (ItemTextFrame) is <hash>-page under Books/:
# keyed by its text alone, since the client names the book but gives no ID, and
# one page can be read from several items and objects.
BOOK_EVENT = "page"
SUBFOLDERS = {"quests": "Quests", "gossip": "Gossip", "books": "Books"}


# ----------------------------------------------------------------------------
# Voices: which clip a voice is cloned from, and with what conditioning
# ----------------------------------------------------------------------------


class ResolvedVoice(NamedTuple):
    clip: Path | None  # the reference audio, None when not even human-male.wav is there
    source: str  # the voice whose clip and tuning are used: the voice itself, or the one it borrows from
    settings: TtsSettings


def picks_digest(clips: list[int], build: str | None, gaps: list[float]) -> str:
    """The `clips=` a pick adds to every file's fingerprint in sound_index.json.

    A digest, not the list: sound_index carries this for every file, and a
    fifteen-clip pick would run longer than the text hash it qualifies. The audition
    page also reads it back, to tell which picks have audio in the pack.
    """
    joined = ",".join(str(c) for c in clips) + f"@{build or ''}"
    if any(gaps):
        # a pick with no gaps keeps the digest it had before gaps existed
        joined += "~" + ",".join(f"{g:g}" for g in gaps)
    return hashlib.blake2b(joined.encode(), digest_size=4).hexdigest()


class VoiceCatalog:
    """Resolves a voice name to its reference clip and Chatterbox settings from
    [voices] and [tts] in configs/.

    A voice with no clip of its own borrows a related race's ([voices.fallbacks]),
    then the narrator's, then human-male. The tuning goes with the clip: the
    accent drift that [tts.voices] corrects belongs to the clip, so a Dark Iron
    dwarf cloned from dwarf-male's clip needs dwarf-male's settings as much as a
    dwarf does (#18). A voice's own [tts.voices] entry may name a different clip
    to clone from (`reference`), which is how dwarf-male reads from npc-3597.
    An archetype with no row of its own is the exception: it keeps its wav and
    borrows the plain race voice's knobs. The race's `reference` is a different
    file and stays on the plain voice."""

    def __init__(self, config: Config, voices_dir: Path = VOICES_DIR):
        self.config = config
        self.voices_dir = voices_dir
        self._resolved: dict[str, ResolvedVoice] = {}

    def _clip_of(self, voice: str) -> Path | None:
        """This voice's own clip: the one its tuning names, else <voice>.wav."""
        tuning = self.config.tts.voices.get(voice)
        if tuning and tuning.reference:
            named = self.voices_dir / f"{tuning.reference}.wav"
            if named.exists():
                return named
        own = self.voices_dir / f"{voice}.wav"
        return own if own.exists() else None

    def resolve(self, voice: str) -> ResolvedVoice:
        if voice in self._resolved:
            return self._resolved[voice]
        race, _, gender = voice.partition("-")
        chain = [voice]
        # "<race>-<gender>-s<set>" is one archetype of a voice. If that clip is gone -
        # build_voice_references drops an archetype whose head is too thin to carry a
        # delivery - the voice's own clip is a better fallback than another race's.
        if is_archetype(voice):
            base = base_voice(voice)
            chain.append(base)
            race, _, gender = base.partition("-")
            own = self.voices_dir / f"{voice}.wav"
            # The archetype montage is a different recording from the plain voice.
            # With no [tts.voices] row it still needs the race's knobs (dwarf-male
            # is 0.75 / 0.3; the defaults send it back to an American accent), and
            # the race's `reference` must not replace this file.
            if voice not in self.config.tts.voices and own.exists():
                borrowed = self.config.tts.settings_for(base)
                settings = TtsSettings(
                    exaggeration=borrowed.exaggeration,
                    cfg_weight=borrowed.cfg_weight,
                    tempo=borrowed.tempo,
                    pitch=borrowed.pitch,
                    speed=borrowed.speed,
                )
                resolved = ResolvedVoice(own, voice, settings)
                self._resolved[voice] = resolved
                return resolved
        fallback = self.config.voices.fallbacks.get(race)
        if fallback:
            chain.append(f"{fallback}-{gender}" if gender else fallback)
            chain.append(f"{fallback}-male")
        chain += [self.config.voices.narrator, "human-male"]
        resolved = ResolvedVoice(None, voice, self.config.tts.settings_for(voice))
        for candidate in chain:
            clip = self._clip_of(candidate)
            if clip is not None:
                resolved = ResolvedVoice(
                    clip, candidate, self.config.tts.settings_for(candidate)
                )
                break
        self._resolved[voice] = resolved
        return resolved

    def fingerprint(self, voice: str, text: str) -> str:
        """Hash of the words actually spoken, plus the voice's conditioning when it
        is not the default. Recorded in sound_index.json so a corrected text or a
        retuned voice regenerates, the way a changed voice already does: a quest
        file keeps its <questID>-<event> name, so nothing else would notice. A
        voice on the default tuning hashes exactly as before, so tuning one voice
        does not restage every other file.

        Clips chosen by ear ([voices.sources]) join it too, keyed on the file actually
        cloned from rather than on the voice asking - a line reading from npc-3597.wav
        restages when that clip's picks change, whoever picked them. Rebuilding a clip
        is still invisible on its own: the bytes are not hashed, only the decision. A
        voice with no picks hashes exactly as before."""
        key = text_key(text)
        fields = self.conditioning(voice)
        if not fields:
            return key
        return (
            key
            + "+"
            + ",".join(f"{name}={value}" for name, value in sorted(fields.items()))
        )

    def conditioning(self, voice: str) -> dict[str, object]:
        """What `fingerprint` adds to the text hash: the settings that differ from the
        defaults, and a digest of the clips picked for the clip this voice reads from."""
        resolved = self.resolve(voice)
        fields = self.config.tts.differences(resolved.settings)
        picked = (
            self.config.voices.sources.get(resolved.clip.stem)
            if resolved.clip
            else None
        )
        if picked and picked.clips:
            fields = dict(fields)
            fields["clips"] = picks_digest(picked.clips, picked.build, picked.gaps)
        return fields

    def recipe(self, voice: str) -> str:
        """The clip this voice reads from and its `conditioning`, as one string.

        [voices.approved] stores it, so an approval holds only while the voice still
        sounds as it did when heard: a re-pick, a retuning, or a change to a voice it
        borrows from moves it, wherever that edit was made. The clip's name is in it
        and the text hash is not, unlike `fingerprint`, because two voices reading the
        same text alike is not the question here. Rebuilding a clip's audio without
        changing its picks does not move it, as it does not move `fingerprint` (#51)."""
        resolved = self.resolve(voice)
        fields: dict[str, object] = {
            "clip": resolved.clip.stem if resolved.clip else "none",
            **self.conditioning(voice),
        }
        return ",".join(f"{name}={value}" for name, value in sorted(fields.items()))

    def pin(self, key: str, voice: str, text: str) -> LinePin | None:
        """The [lines] pin for this line's file while it still holds: heard under
        today's fingerprint and spoken from today's exact text. A retune, a re-pick, a
        respelling or a corrected text makes the same seed another take, so the pin
        then stands aside and the line is drawn afresh."""
        pin = self.config.lines.root.get(key)
        if pin is None:
            return None
        if pin.heard != self.fingerprint(voice, text) or pin.spoken != spoken_hash(
            text
        ):
            return None
        return pin

    def tuned(self, voice: str) -> bool:
        return not self.config.tts.is_default(self.resolve(voice).settings)


# ----------------------------------------------------------------------------
# Work items
# ----------------------------------------------------------------------------


class Item:
    def __init__(
        self,
        kind: str,
        key: str,
        entry: dict,
        npc: dict | None,
        catalog: VoiceCatalog,
        others: dict[str, dict | None] | None = None,
    ):
        self.kind = kind  # "quests" | "gossip" | "books"
        self.key = key
        self.entry = entry
        self.npc = npc
        # The NPC records of the quest line's other speakers (#948), by key
        self.others = others or {}
        self.catalog = catalog
        self.config = catalog.config
        # A book has no speaker: the narrator reads every page
        self.voice = (
            self.config.voices.narrator
            if kind == "books"
            else voice_for_npc(
                npc,
                entry.get("zone"),
                voices=self.config.voices,
                speaker=entry.get("npc"),
            )
        )
        self.raw_text = entry.get("text") or ""
        self.event = entry.get("event") or (BOOK_EVENT if kind == "books" else "gossip")
        self.speaker_key = (
            None if kind == "books" else entry.get("npc")
        )  # "288" or "-123" (game object)

    @property
    def subfolder(self) -> str:
        return SUBFOLDERS[self.kind]

    @property
    def hash(self) -> str:
        return text_key(
            self.raw_text,
            self.entry.get("player"),
            self.entry.get("class"),
            self.entry.get("race"),
        )

    @property
    def base_name(self) -> str:
        if self.kind == "quests":
            return f"{int(self.entry['questID'])}-{self.event}"
        if self.kind == "books":
            return f"{self.hash}-{BOOK_EVENT}"
        speaker = self.speaker_key or "unknown"
        speaker = speaker.replace("-", "obj")
        return f"{speaker}-{self.hash}"

    @property
    def is_narrator(self) -> bool:
        """Lines read by the narrator (objects, items, speakers with no gender)
        get one file per alternate narrator voice."""
        return self.voice == self.config.voices.narrator

    @property
    def gendered(self) -> bool:
        return has_gender_branch(self.clean(self.raw_text))

    def clean(self, text: str) -> str:
        if self.kind == "books":
            text = book_text(text)
        return clean(
            text,
            keep_stage_directions=self.is_narrator,
            pronunciations=self.config.pronunciations,
        )

    @functools.cached_property
    def sex_alternate(self) -> tuple[str, str] | None:
        """(letter, voice) for the line in the speaker's other sex, or None.

        One creature ID can be either sex (Peacekeepers, city guards: #304), and
        the NPC record keeps every sex it is known as (known_sexes). The line's own
        file is in the voice its record resolves to; this is the other one, which
        the addon plays when the unit in front of the player is that sex. None
        for a speaker seen as one sex, a narrator, a pinned voice, a named clip,
        and a voice whose other sex has no clip of its own (a fallback to
        another sex, or the narrator, would be worse than the one it has)."""
        sexes = set(known_sexes(self.npc))
        if (
            not {"m", "f"} <= sexes
            or self.is_narrator
            or str(self.speaker_key) in self.config.voices.speakers
        ):
            return None
        race, _, gender = base_voice(self.voice).partition("-")
        if race == "npc" or gender not in ("male", "female"):
            return None
        other = "female" if gender == "male" else "male"
        voice = f"{race}-{other}"
        # Read by that sex of this race, or of the race it borrows from under
        # [voices.fallbacks]; not the narrator's or human-male's last resort
        source_race, _, source_gender = base_voice(
            self.catalog.resolve(voice).source
        ).partition("-")
        races = {race, self.config.voices.fallbacks.get(race)}
        if source_gender != other or source_race not in races:
            return None
        return other[0], voice

    @functools.cached_property
    def speaker_alternates(self) -> dict[str, str | None]:
        """The quest line's other speakers: speaker key -> the voice it is read
        in for them, or None when that is the line's own voice.

        One quest can be handed in to several NPCs, who say the same words: the
        crates of "A Sealed Crate" go to Dokimi, an orc, and to Marcy Baker, a
        human (#948). The line's own file is in its `npc`'s voice, and ingest
        keeps every speaker it was captured from (`speakers`); this is the rest,
        which the addon plays when one of them is in front of the player. None
        for a narrator's line: its file keeps the stage directions a speaker's
        leaves out."""
        if self.kind != "quests" or self.is_narrator:
            return {}
        out: dict[str, str | None] = {}
        for key in sorted(self.entry.get("speakers") or {}, key=int):
            if key == str(self.speaker_key):
                continue
            voice = voice_for_npc(
                self.others.get(key), None, voices=self.config.voices, speaker=key
            )
            out[key] = (
                None
                if voice == self.voice or voice == self.config.voices.narrator
                else voice
            )
        return out

    def variants(self) -> list[Variant]:
        """One Variant per file base name; two when the text branches on player gender."""
        raw = self.raw_text
        branches = [("", raw)]
        if self.gendered:
            male, female = split_gender(raw)
            branches = [("m-", male), ("f-", female)]
        out = []
        for prefix, text in branches:
            # The narrator reads a stage direction as prose. A speaker leaves it
            # out of the whole-line file and the line also gets parts, so the
            # narrator can say it between the speaker's words.
            parts = (
                []
                if self.is_narrator
                else segments(text, pronunciations=self.config.pronunciations)
            )
            if len(parts) == 1 and parts[0][0] == "npc":
                parts = []
            out.append(Variant(f"{prefix}{self.base_name}", self.clean(text), parts))
        return out


class Variant(NamedTuple):
    """One file base name of a line and what is spoken under it."""

    base: str
    text: str  # the whole line as its speaker reads it
    parts: list[tuple[str, str]]  # ("npc"|"narrator", words) in reading order when the
    # line mixes the speaker and the narrator, else empty


def part_name(base: str, index: int) -> str:
    """File base name of a line's part: 1241-p2-complete, 2492-p1-aa6f2374,
    m-170-p1-accept. The part number sits before the last segment so that the
    name still ends in the quest event or text hash: sound_folder() and every
    older tool that tells quests from gossip by the last segment keep working
    (an older generator still running probes any file it sees under Sounds/)."""
    head, _, last = base.rpartition("-")
    return f"{head}-p{index}-{last}"


# ----------------------------------------------------------------------------
# Narrator alternates
# ----------------------------------------------------------------------------
# A line's own file (whatever its voice, the default narrator included) keeps
# the plain path and the plain sound_index key, so nothing about the existing
# files changes; an alternate narrator recording is namespaced by its voice,
# which is the `location` below (None for the plain path).


def narrator_dir(sounds_dir: Path, voice: str, subfolder: str = "Quests") -> Path:
    return sounds_dir / subfolder / "Narrator" / voice


def sound_path(
    subfolder: str,
    base: str,
    location: str | None = None,
    sounds_dir: Path = SOUNDS_DIR,
) -> Path:
    if location is None:
        return sounds_dir / subfolder / f"{base}.mp3"
    return narrator_dir(sounds_dir, location, subfolder) / f"{base}.mp3"


def index_key(base: str, location: str | None = None) -> str:
    """Key under which a file's duration and voice are recorded in sound_index.json.
    The folder is left out: base names already say which one they belong to."""
    return base if location is None else f"Narrator/{location}/{base}"


# A speaker seen as both sexes (#304) has its line in the other sex's voice too,
# under Sounds/<Quests|Gossip>/Sex/<m|f>/<base>.mp3 and the index key
# Sex/<m|f>/<base>. The letter is the speaker's sex; the m-/f- prefix a $g line
# carries in its base name stays the player's.


def sex_path(
    subfolder: str, base: str, letter: str, sounds_dir: Path = SOUNDS_DIR
) -> Path:
    return sounds_dir / subfolder / "Sex" / letter / f"{base}.mp3"


def sex_key(base: str, letter: str) -> str:
    return f"Sex/{letter}/{base}"


# A quest line also spoken by another NPC in another voice (#948) is under
# Sounds/Quests/Speaker/<speaker key>/<base>.mp3, index key
# Speaker/<speaker key>/<base>.


def speaker_path(
    subfolder: str, base: str, speaker: str, sounds_dir: Path = SOUNDS_DIR
) -> Path:
    return sounds_dir / subfolder / "Speaker" / speaker / f"{base}.mp3"


def speaker_key_of(base: str, speaker: str) -> str:
    return f"Speaker/{speaker}/{base}"


class Target(NamedTuple):
    """One file to synthesise: a line's gender variant in one voice."""

    item: Item
    base: str
    text: str
    voice: str
    alternate: bool = False  # an alternate narrator voice rather than the line's own
    sex: str | None = None  # "m"/"f": the line in the speaker's other sex (#304)
    # the line whose [lines] pin this file follows, when it is not its own key: the
    # speaker's part of a line whose one speaker part is the whole-line text
    pin_key: str | None = None
    speaker: str | None = None  # another speaker of the quest line (#948)

    @property
    def location(self) -> str | None:
        return self.voice if self.alternate else None

    @property
    def key(self) -> str:
        if self.speaker:
            return speaker_key_of(self.base, self.speaker)
        if self.sex:
            return sex_key(self.base, self.sex)
        return index_key(self.base, self.location)

    @property
    def live_pin(self) -> LinePin | None:
        """The line's pinned seed, for its own whole-line file and, where the line has
        one speaker part saying the same words, that part (pin_key); the other sex's
        file, the alternate narrators and every other part are drawn as before."""
        if self.alternate or self.sex:
            return None
        return self.item.catalog.pin(self.pin_key or self.key, self.voice, self.text)

    @property
    def fingerprint(self) -> str:
        return with_pin(
            self.item.catalog.fingerprint(self.voice, self.text), self.live_pin
        )

    @property
    def path(self) -> Path:
        if self.speaker:
            return speaker_path(self.item.subfolder, self.base, self.speaker)
        if self.sex:
            return sex_path(self.item.subfolder, self.base, self.sex)
        return sound_path(self.item.subfolder, self.base, self.location)

    @property
    def label(self) -> str:
        return f"{self.item.subfolder}/{self.key}.mp3"


def parse_narrator_voices(spec: str | None, alternates: list[str]) -> list[str]:
    """--narrator-voices: 'all', 'none', or a comma separated list of the configured
    alternates. Returns alternates only."""
    if spec in (None, "all"):
        return alternates
    if spec == "none":
        return []
    chosen = [v.strip() for v in spec.split(",") if v.strip()]
    unknown = [v for v in chosen if v not in alternates]
    if unknown:
        raise SystemExit(
            f"unknown narrator voice(s) {unknown}; known: {', '.join(alternates)}"
        )
    return chosen


SOURCE_ORDER = ["classic", "capture"]  # later sources override earlier ones


def load_sources() -> dict:
    """Merges tools/data/bulk/*.json and capture.json field by field, capture winning."""
    merged = {"quests": {}, "gossip": {}, "books": {}, "npcs": {}}
    displays: dict[str, int] = {}
    files = {p.stem: p for p in (DATA_DIR / "bulk").glob("*.json")}
    if CAPTURE_JSON.exists():
        files["capture"] = CAPTURE_JSON
    for name in SOURCE_ORDER + sorted(set(files) - set(SOURCE_ORDER)):
        path = files.get(name)
        if not path:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for kind in ("quests", "gossip", "books", "npcs"):
            for key, entry in data.get(kind, {}).items():
                target = merged[kind].setdefault(str(key), {})
                for field, value in entry.items():
                    if (
                        value is None
                        or value == ""
                        or (field in ("displayID", "modelFileID") and not value)
                    ):
                        continue
                    if field == "sexes":
                        # Every source's sexes count: Classic's displays say a
                        # Ravenholdt Assassin is both, a capture that met only
                        # one must not take that back (#1127)
                        value = "".join(
                            letter
                            for letter in "mf"
                            if letter in value or letter in target.get("sexes", "")
                        )
                    target[field] = value
        displays.update(data.get("displays", {}))
        print(
            f"source {name}: {len(data.get('quests', {}))} quest, {len(data.get('gossip', {}))} gossip, "
            f"{len(data.get('books', {}))} book page, {len(data.get('npcs', {}))} npc entries"
        )
    filled = fill_displays(merged["npcs"], displays, display_model_file)
    if filled:
        print(f"display IDs from Classic, model checked: {len(filled)} captured npcs")
    return merged


SEX_LETTERS = {"sex": {2: "m", 3: "f"}, "sexID": {0: "m", 1: "f"}}  # UnitSex, display


def known_sexes(npc: dict | None) -> str:
    """Every sex the pipeline knows the speaker as, "m", "f", "mf" or "": its
    `sexes` (every one a player met, and Classic's displays), its captured `sex`
    and its display's `sexID`. Both, and the line gets the other sex's file too
    (Item.sex_alternate); the pack records the set (`pack.sexes`), and a player
    who meets a sex not in it exports the NPC record (Capture.lua), so the next
    nightly voices it."""
    npc = npc or {}
    seen = set(npc.get("sexes") or "")
    for field, letters in SEX_LETTERS.items():
        if npc.get(field) in letters:
            seen.add(letters[npc[field]])
    return "".join(letter for letter in "mf" if letter in seen)


def fill_displays(
    npcs: dict[str, dict],
    displays: dict[str, int],
    model_file: Callable[[int], int | None],
) -> list[str]:
    """Gives a captured NPC its Classic display when the models agree.

    A Forever capture records the model file an NPC draws and never its display ID,
    so a speaker Classic never lets talk has no display at all: Vol'jin's two lines
    were read as plain troll-male beside his own npc-10357 clip. classicdb's
    `displays` has every creature; one is taken only when its model file is the one
    the capture saw, since Forever remodels some (Quarrymaster Thesten, Morhan
    Coppertongue and Malorne Bladeleaf wear other models there and keep their voice).
    Marked `displayVia: "model"`, so model_cast still reports the model and the
    addon's recast check (#352) still covers the NPC.
    """
    filled = []
    for key, npc in npcs.items():
        model = npc.get("modelFileID")
        display = displays.get(key)
        if npc.get("displayID") or npc.get("isObject") or not model or not display:
            continue
        if model_file(int(display)) == int(model):
            npc["displayID"] = int(display)
            npc["displayVia"] = "model"
            filled.append(key)
    return filled


def load_items(
    capture: dict, include_progress: bool, catalog: VoiceCatalog
) -> list[Item]:
    items = []
    for kind in ("quests", "gossip", "books"):
        for key, entry in capture.get(kind, {}).items():
            if (
                kind == "quests"
                and entry.get("event") == "progress"
                and not include_progress
            ):
                continue
            if kind == "books":
                items.append(Item(kind, key, entry, None, catalog))
                continue
            npcs = capture.get("npcs", {})
            npc = npcs.get(str(entry.get("npc") or ""))
            if npc is None and entry.get("isObject"):
                npc = {"isObject": True, "name": entry.get("name")}
            others = {
                str(speaker): npcs.get(str(speaker))
                for speaker in entry.get("speakers") or {}
            }
            items.append(Item(kind, key, entry, npc, catalog, others))
    return items


# ----------------------------------------------------------------------------
# TTS
# ----------------------------------------------------------------------------


class TakeStopped(Exception):
    """Synth.render was asked to stop between two generate() calls."""


# Chatterbox ends a generate() call at 1000 speech tokens, 25 a second: 40.0 s. The
# English model has no alignment analyzer (chatterbox t3.py builds one for the
# multilingual model only), so nothing forces the end of speech, and a call that
# misses its stop token babbles on to the cap: 49 pack files sat at exactly 40.0 s,
# 200-character lines among them, kept because the longest try used to win.
TOKEN_CAP_SECONDS = 39.9
CHUNK_GAP_SECONDS = 0.35  # silence between two chunks of one line


# Bumped when what a seed draws changes (derive_seed, chunking, the retry rules), so
# pinned files are drawn again from their seeds; unpinned files never carry it.
PIN_VERSION = 1
PIN_TERMS = ("seed", "chunk", "pin", "same_seed")


def spoken_hash(text: str) -> str:
    """The exact text a pin was heard saying. text_key folds case and punctuation
    away, and both change what the model says and where a line is cut into chunks."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def with_pin(fingerprint: str, pin: LinePin | None) -> str:
    """A file's fingerprint with its pinned seed in it, so pinning or re-pinning a line
    restages exactly its file; the fingerprint itself when there is no live pin."""
    if pin is None:
        return fingerprint
    terms = f"seed={pin.seed},chunk={pin.chunk_chars},pin={PIN_VERSION}" + (
        ",same_seed=1" if pin.same_seed else ""
    )
    return fingerprint + ("," if "+" in fingerprint else "+") + terms


def strip_pin(fingerprint: str) -> str:
    """`fingerprint` without with_pin's terms: an unpinned line's file is current when
    this matches, so unpinning keeps the take the pin drew rather than rolling again."""
    key, plus, fields = fingerprint.partition("+")
    if not plus:
        return fingerprint
    kept = [f for f in fields.split(",") if f.partition("=")[0] not in PIN_TERMS]
    return key + "+" + ",".join(kept) if kept else key


def index_record(
    duration: float,
    target: Target,
    take: RenderedTake | None,
    max_chars: int,
    same_seed: bool = False,
) -> dict[str, Any]:
    """A generated file's sound_index.json entry: its length, voice and fingerprint,
    and the seed and chunk length it was drawn with (`s`, `c`, since 2026-10-07), so a
    take that turns out perfect can be pinned from the pack itself. The seed replays
    exactly only on the machine and libraries that drew it; files from before seeds
    have none. Nothing that reads the index needs `s` or `c`."""
    record: dict[str, Any] = {"d": duration, "v": target.voice, "t": target.fingerprint}
    if take is not None:
        record["s"] = take.seed
        record["c"] = max_chars
        if same_seed:
            record["same"] = 1  # every chunk drawn from the take's first seed
    return record


def pinnable_part(variant: Any) -> int | None:
    """The part number (from 1) a line's pin also decides: its one speaker part, when
    that part says exactly the whole-line text, as in "<Sob> Oh please, don't look at
    me!..." whose narrator part is the sob alone. The current addon plays the parts,
    so the pin reaches players through this file; None for a line with no parts, or
    with the speaker's words split between several parts."""
    speaker = [i for i, (role, _) in enumerate(variant.parts, 1) if role == "npc"]
    if len(speaker) != 1:
        return None
    index = speaker[0]
    return index if variant.parts[index - 1][1] == variant.text else None


def pin_report(
    items: list[Item], catalog: VoiceCatalog, include_progress: bool = True
) -> list[str]:
    """What the run says about [lines]: a pin whose line is gone, one that no longer
    holds (the text or the voice's settings changed since it was heard), and one on a
    line the addon plays as parts, where the whole-line file it pins is not heard. A
    pinned progress text in a run without --progress is not gone, only not in the
    run, and is said so: the nightly's bulk pass voices progress texts for captured
    lines only."""
    pins = catalog.config.lines.root
    if not pins:
        return []
    lines = {v.base: (item, v) for item in items for v in item.variants()}
    notes = []
    for key in sorted(pins):
        if key not in lines:
            if not include_progress and key.endswith("-progress"):
                notes.append(
                    f"pinned seed for {key}: a progress text, which a run voices only "
                    "with --progress"
                )
            else:
                notes.append(f"pinned seed for {key}: no such line now; unpin it")
            continue
        item, variant = lines[key]
        if catalog.pin(key, item.voice, variant.text) is None:
            notes.append(
                f"pinned seed for {key} no longer holds (its text or voice settings "
                "changed since it was heard); the line is drawn afresh"
            )
        elif variant.parts and pinnable_part(variant) is None:
            notes.append(
                f"pinned seed for {key}: narration splits the line's speech into "
                "several parts, so no file it pins is what players hear"
            )
    return notes


def text_current(recorded: str, fingerprint: str, pinned: bool) -> bool:
    """Whether a file's recorded fingerprint is today's. A pinned line is compared
    whole, so pinning or re-pinning restages its file; an unpinned one leaves the pin
    terms out, so unpinning keeps the take the pin drew instead of rolling again."""
    return (recorded if pinned else strip_pin(recorded)) == fingerprint


def ran_into_cap(recorded: Any, target: Target, catalog: VoiceCatalog) -> bool:
    """Whether a pack file is a take that ran into the token cap: a line short enough
    for one chunk whose length before tempo and speed is the cap's 40.0 s. Those were
    kept because the longest try used to win; the next run replaces them. A line of
    several chunks runs past 40 s honestly, so only single chunks are judged."""
    if not isinstance(recorded, dict) or not recorded.get("d"):
        return False
    pin = target.live_pin
    if pin is not None and recorded.get("t") == target.fingerprint:
        return False  # drawn from its pinned seed already: it would come out the same
    if len(chunk(target.text, pin.chunk_chars if pin else CHUNK_CHARS)) != 1:
        return False
    settings = catalog.resolve(target.voice).settings
    raw = recorded["d"] * (settings.tempo or 1.0) * (settings.speed or 1.0)
    return TOKEN_CAP_SECONDS <= raw <= 40.1


def derive_seed(take_seed: int, chunk: int, attempt: int) -> int:
    """The torch seed for one generate() call of a take: chunk by chunk and try by
    try, so a take replays from its seed alone and no two calls share one by accident."""
    digest = hashlib.blake2b(
        f"{take_seed}:{chunk}:{attempt}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


@dataclass
class ChunkMark:
    """One generate() call's place in a take, in seconds of the rendered audio."""

    start: float
    end: float
    text: str
    seed: int  # the seed of the try that was kept
    attempts: int
    capped: bool = False  # every try ran into the token cap; kept as the last resort
    split: bool = False  # half of a chunk that ran into the cap on every try
    source: int = 0  # which chunk of chunk() it is, the same for both halves of a split


@dataclass
class RenderedTake:
    audio: Any  # a torch tensor (1, samples); torch is imported lazily
    seconds: float
    seed: int
    chunks: list[ChunkMark]


class Synth:
    def __init__(
        self,
        catalog: VoiceCatalog,
        device: str = "cuda",
        allow_cpu: bool = False,
        allow_hip: bool = False,
    ):
        import perth
        import torch
        import torch.version

        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            perth.PerthImplicitWatermarker = perth.DummyWatermarker  # ty: ignore[invalid-assignment]
        from chatterbox.tts import ChatterboxTTS

        self.torch = torch
        # ROCm PyTorch implements the CUDA API, so the device string stays "cuda"
        # and torch.cuda.is_available() is true. Pack files are still made on the
        # CUDA wheel: the sound index records text and tuning, not which GPU
        # rendered the file, so a ROCm mp3 would look current and ship.
        self.hip = getattr(torch.version, "hip", None)
        if self.hip and not allow_hip:
            raise SystemExit(
                f"This torch is the ROCm build ({self.hip}). Pack generation stays on the "
                "CUDA wheel (the tts group). Audition listens on ROCm; it does not write pack files."
            )
        if device == "cuda" and not torch.cuda.is_available():
            # The CPU runs about 0.08x real time against the GPU's 1.5x, so a silent
            # fallback looks like a working run while making ~1 line a minute. A GPU
            # that vanished mid-job usually means a lost context (Xid after suspend:
            # `journalctl -k | grep Xid`), which can take a reboot to clear.
            if not allow_cpu:
                raise SystemExit(
                    "CUDA is not available, refusing to generate on the CPU (~18x slower "
                    "than real time). Check nvidia-smi and the kernel log, or pass --cpu. "
                    "An AMD GPU uses the tts-rocm group in its own environment: "
                    "UV_PROJECT_ENVIRONMENT=.venv-rocm ./tools/run.sh --no-group tts "
                    "--group tts-rocm audition"
                )
            device = "cpu"
            print(
                "CUDA is not available; generating on the CPU because --cpu was given"
            )
        elif self.hip:
            print(
                f"ROCm {self.hip} ({torch.cuda.get_device_name(0)}); the device string stays cuda"
            )
        self.model = ChatterboxTTS.from_pretrained(device=device)
        self.sr = self.model.sr
        self.catalog = catalog
        self.last_take: RenderedTake | None = None  # speak()'s, for the journal line

    def speak(
        self,
        text: str,
        voice: str,
        out_mp3: Path,
        seed: int | None = None,
        same_seed: bool = False,
        max_chars: int = CHUNK_CHARS,
    ) -> float:
        settings = self.catalog.resolve(voice).settings
        take = self.render_take(
            text, voice, seed=seed, same_seed=same_seed, max_chars=max_chars
        )
        self.last_take = take
        return self.encode(
            take.audio,
            out_mp3,
            settings.tempo,
            settings.pitch,
            settings.speed,
        )

    def render(self, text: str, voice: str, stopped=None):
        """The model's audio for `text` in `voice`, before tempo and pitch."""
        return self.render_take(text, voice, stopped).audio

    def render_take(
        self,
        text: str,
        voice: str,
        stopped=None,
        seed: int | None = None,
        same_seed: bool = False,
        max_chars: int = CHUNK_CHARS,
    ) -> RenderedTake:
        """The model's audio for `text` in `voice`, before tempo and pitch, with where
        each chunk sits in it and the seed it was drawn from.

        Apart from encode so the audio can be heard several ways: tempo and pitch are
        applied afterwards, so the audition page's sweep over them needs no second take.
        `stopped`, a callable, is asked before each generate() call; when it answers
        true the take is abandoned with TakeStopped. A long line is several chunks and
        a short one up to three tries, so checking only between takes left the
        audition page's Stop waiting minutes on the ROCm card.

        Every generate() call is seeded from the take's `seed` (derive_seed), drawn at
        random when none is given, so every take is a new one. `same_seed` gives every
        chunk the seed of the first, an experiment in whether a shared draw holds a
        voice across chunks; torch's seed is process-wide, which is safe only because
        one take renders at a time (the audition model lock, one generator process).
        `max_chars` is the chunk length (textclean.chunk).
        """
        resolved = self.catalog.resolve(voice)
        settings = resolved.settings
        kwargs = {"audio_prompt_path": str(resolved.clip)} if resolved.clip else {}
        take_seed = random.randrange(2**31) if seed is None else seed
        gap = int(self.sr * CHUNK_GAP_SECONDS)
        pieces: list[Any] = []
        marks: list[ChunkMark] = []
        position = 0  # samples
        calls = 0  # chunks drawn so far, halves of a split one included

        def speak_part(part: str, depth: int, split: bool, source: int) -> None:
            nonlocal position, calls
            index = 0 if same_seed else calls
            calls += 1
            # Chatterbox sometimes answers a short standalone sentence with a
            # blip: "Galgar wipes his brow." came back as 0.36 s where the other
            # narrator voices took 2 s, and 17 stage-direction parts of two to
            # four words were like it (2026-09-23). The output is sampled, so a
            # second try usually speaks; keep the longest of a few. A try that ran
            # into the token cap is a failure, never kept over one that did not.
            floor = max(0.5, 0.15 * len(part.split()))
            best, best_capped, best_seed, attempts = None, True, 0, 0
            for attempt in range(3):
                if stopped is not None and stopped():
                    raise TakeStopped
                call_seed = derive_seed(take_seed, index, attempt)
                self.torch.manual_seed(call_seed)
                wav = self.model.generate(
                    part,
                    exaggeration=settings.exaggeration,
                    cfg_weight=settings.cfg_weight,
                    **kwargs,
                ).cpu()
                attempts += 1
                capped = wav.shape[-1] / self.sr >= TOKEN_CAP_SECONDS
                if (
                    best is None
                    or (best_capped and not capped)
                    or (capped == best_capped and wav.shape[-1] > best.shape[-1])
                ):
                    best, best_capped, best_seed = wav, capped, call_seed
                if capped:
                    print(
                        f"    ran into the token cap ({len(part)} characters), retrying"
                    )
                    continue
                if best.shape[-1] / self.sr >= floor:
                    break
                print(
                    f"    short output ({wav.shape[-1] / self.sr:.2f}s for {len(part.split())} words), retrying"
                )
            if best_capped and depth < 2:
                halves = halve(part)
                if len(halves) == 2:
                    print(f"    capped on every try, splitting: {part[:50]}…")
                    for half in halves:
                        speak_part(half, depth + 1, True, source)
                    return
            assert best is not None
            if pieces:
                pieces.append(self.torch.zeros(1, gap))
                position += gap
            start = position
            pieces.append(best)
            position += best.shape[-1]
            marks.append(
                ChunkMark(
                    round(start / self.sr, 3),
                    round(position / self.sr, 3),
                    part,
                    best_seed,
                    attempts,
                    best_capped,
                    split,
                    source,
                )
            )

        for source, part in enumerate(chunk(text, max_chars)):
            speak_part(part, 0, False, source)
        return RenderedTake(
            self.torch.cat(pieces, dim=-1), position / self.sr, take_seed, marks
        )

    def encode(
        self,
        audio,
        out_mp3: Path,
        tempo: float = 1.0,
        pitch: float = 0.0,
        speed: float = 1.0,
    ) -> float:
        """Writes `audio` from render() as the pack's mp3; returns its length."""
        duration = audio.shape[-1] / self.sr

        import torchaudio

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        # Encoded beside the target and renamed into place: a worker killed mid-encode
        # (unit restart, lost GPU) would otherwise leave a truncated mp3 under the
        # final name, which every later run skips as done. The partial file has no
        # .mp3 suffix so the table rebuild's glob cannot pick it up either.
        out_part = out_mp3.with_suffix(f".{os.getpid()}.part")
        filters = encode_filters(tempo, pitch, speed, self.sr)
        require_filters(filters)
        filter_args = ["-af", ",".join(filters)] if filters else []
        try:
            torchaudio.save(str(tmp_path), audio, self.sr)
            out_mp3.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(tmp_path),
                    "-ac",
                    "1",
                    "-ar",
                    "44100",
                    *filter_args,
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    "-f",
                    "mp3",
                    str(out_part),
                ],
                check=True,
            )
            if filter_args:
                duration = probe_duration(
                    out_part
                )  # the stretched length is what the addon pages text against
            os.replace(out_part, out_mp3)
        finally:
            tmp_path.unlink(missing_ok=True)
            out_part.unlink(missing_ok=True)
        return duration


# ----------------------------------------------------------------------------
# Pack tables
# ----------------------------------------------------------------------------


def lua_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, dict):  # keyed sub-table, e.g. a duration per narrator voice
        return (
            "{ "
            + ", ".join(
                f"[{lua_value(k)}]={lua_value(v)}" for k, v in sorted(value.items())
            )
            + " }"
        )
    if isinstance(
        value, list
    ):  # array, e.g. the parts of a line; dict elements are records
        return (
            "{ "
            + ", ".join(
                lua_record(v) if isinstance(v, dict) else lua_value(v) for v in value
            )
            + " }"
        )
    raise TypeError(type(value))


def lua_record(fields: dict) -> str:
    parts = [
        f"{k}={lua_value(v)}"
        for k, v in fields.items()
        if v is not None and v is not False
    ]
    return "{ " + ", ".join(parts) + " }"


def write_table(
    filename: str,
    field: str,
    lines: list[str],
    data_dir: Path = PACK_DATA_DIR,
    pack_global: str = "ForeverVO_DataPack",
    prelude: str = "",
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    # Written through a temporary file: parallel shards (--shard) both rebuild the
    # tables every 25 files, and the client loads these, so a half-written
    # Quests.lua would be a syntax error in someone's game. Either shard's
    # snapshot is valid on its own, so last writer wins is fine; a torn file is not.
    # The temporary name carries the pid: with one shared name, two coinciding
    # writes would rename the other's half-written file into place.
    target = data_dir / filename
    tmp = target.with_suffix(f"{target.suffix}.{os.getpid()}.tmp")
    tmp.write_text(
        f"-- Generated by tools/generate.py; do not edit.\nlocal pack = {pack_global}\n{prelude}"
        f"pack.{field} = {{\n{body}\n}}\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)


def sound_folder(base: str) -> str:
    """Quests are <questID>-<event>, gossip is <speaker>-<hash>, a book's page
    <hash>-page."""
    last = base.rsplit("-", 1)[-1]
    if last in QUEST_EVENTS:
        return "Quests"
    return "Books" if last == BOOK_EVENT else "Gossip"


def encode_filters(
    tempo: float = 1.0, pitch: float = 0.0, speed: float = 1.0, sample_rate: int = 24000
) -> list[str]:
    """The ffmpeg filters for a voice's speed, tempo and pitch, none when all are neutral.

    Speed is varispeed, a tape played faster or slower: the samples are relabelled at
    another rate and resampled back, so pace and pitch move together in one pass
    (0.9 is 10% slower and about 1.8 semitones lower). Lowering tempo and pitch to the
    same end runs a time stretch and then a pitch shift, each compensating for what
    the other did, and both leave artefacts; speed does it with neither. It runs
    first, on the model's own rate (`sample_rate`), before any tempo or pitch.

    Neither is a model knob. Pace: Chatterbox's generate() has none, so tempo is a
    pitch-preserving time stretch. Pitch: Chatterbox pulls every clone toward its own
    mid-range voice, and Varimathras came out at 153 Hz against his recordings' 86 to
    89 Hz (#341), so pitch is a shift in semitones that keeps the length, down being
    negative. atempo stays for tempo alone, so a voice tuned before pitch existed
    encodes exactly as it did.
    """
    filters = []
    if speed != 1.0:
        filters.append(f"asetrate={round(sample_rate * speed)},aresample={sample_rate}")
    if tempo != 1.0:
        filters.append(f"atempo={tempo}")
    if pitch != 0.0:
        filters.append(f"rubberband=pitch={2 ** (pitch / 12):.6f}")
    return filters


@functools.cache
def ffmpeg_filters() -> frozenset[str]:
    """The audio and video filters this ffmpeg was built with."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(
        parts[1]
        for parts in (line.split() for line in out.splitlines())
        if len(parts) >= 3 and "->" in parts[2]
    )


def require_filters(filters: list[str]) -> None:
    """Stops before a take is wasted when ffmpeg lacks a filter a voice's tuning needs.

    rubberband (pitch) is only in nixpkgs' ffmpeg-full: the "small" ffmpeg the flake
    shipped until 2026-09-29 has none, so Varimathras's first line would have failed
    mid-run, and daily.sh's `|| true` would have hidden the stopped night.
    """
    # an entry may chain several (speed is asetrate,aresample)
    names = {part.split("=", 1)[0] for f in filters for part in f.split(",")}
    missing = sorted(names - ffmpeg_filters())
    if missing:
        raise SystemExit(
            f"this ffmpeg has no {', '.join(missing)} filter, which a voice's tuning needs "
            "(pitch is rubberband). flake.nix installs ffmpeg-full for it; off nix, "
            "install an ffmpeg built with librubberband."
        )


def ensure_picked_references(config: Config, dry_run: bool = False) -> set[str]:
    """Rebuilds every picked reference whose wav was not built from its saved pick,
    before anything is generated; returns the voices that could not be rebuilt.

    Picks reach this machine through git, the wavs do not, and a file's fingerprint
    names the picks: a merged re-pick restaged its voice from the old wav and stamped
    the files current, so nothing ever redid them (2,042 files, 2026-10-03). Under a
    lock, so the shards of one bulk run build each voice once. A voice that cannot be
    rebuilt (a clip gone from wago) has its lines left out of this run, not voiced
    from the wrong clip.
    """
    picked = {
        voice: pick for voice, pick in config.voices.sources.items() if pick.clips
    }
    stale = sorted(
        v for v, pick in picked.items() if not picked_reference_current(v, pick)
    )
    if not stale:
        return set()
    if dry_run:
        print(f"would rebuild {len(stale)} picked reference(s) first: {stale}")
        return set()
    failed: set[str] = set()
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    with open(VOICES_DIR / ".build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for voice in stale:
            if picked_reference_current(voice, picked[voice]):
                continue  # another shard built it while this one waited
            print(f"{voice}: reference not built from its saved pick, rebuilding")
            try:
                built = build_pick(voice, picked[voice])
            except (
                RuntimeError,
                ValueError,
                OSError,
                subprocess.CalledProcessError,
            ) as e:
                print(f"{voice}: {e}")
                built = None
            if built is None:
                failed.add(voice)
    if failed:
        print(
            f"warning: could not rebuild {sorted(failed)}; their lines are left out of this run"
        )
    return failed


def tuning_filters(config: Config) -> list[str]:
    """Every encode filter some voice's saved tuning asks for."""
    settings = [config.tts.settings_for(v) for v in config.tts.voices]
    return sorted(
        {
            f
            for s in [config.tts.defaults, *settings]
            for f in encode_filters(s.tempo, s.pitch, s.speed)
        }
    )


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out)


def index_rank(value) -> int:
    """How much an index entry knows: 3 for a generator's record (voice and text
    fingerprint), 1 for a pre-fingerprint record, 0 for a duration a table
    rebuild probed from the file, or a legacy bare float. An entry may only
    replace one of equal or lower rank, so a probe never erases a record."""
    if not isinstance(value, dict):
        return 0
    return (1 if value.get("v") is not None else 0) + (2 if "t" in value else 0)


def save_sound_index(sound_index: dict, keys: set[str] | None = None) -> None:
    """Merges this process's entries into the index on disk and replaces the file
    atomically, then brings the in-memory copy up to date with the disk.

    Two workers (--shard) each hold a copy in memory and write every 25 files.
    Only `keys` -- what this process wrote since its last save; everything when
    None -- are merged in, so a stale copy of an entry the other worker has since
    rewritten does not overwrite it, and an entry never replaces one that knows
    more (index_rank), so a duration a table rebuild probed from the other
    worker's fresh file cannot erase that worker's record of its voice and text
    (which is what left 2,790 files with no voice on 2026-09-22). The
    read-merge-write runs under a lock and the temporary name carries the pid,
    so two saves cannot interleave or rename each other's half-written file."""
    SOUND_INDEX.parent.mkdir(parents=True, exist_ok=True)
    lock_path = SOUND_INDEX.with_suffix(".lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged: dict = {}
        if SOUND_INDEX.exists():
            try:
                merged = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                merged = {}
        for key in list(keys if keys is not None else sound_index):
            value = sound_index.get(key)
            if value is None:
                # A key this process dropped: the file behind it was removed (an
                # alternate narrator recording of a line the narrator no longer
                # reads), so the entry goes with it.
                merged.pop(key, None)
            elif index_rank(value) >= index_rank(merged.get(key)):
                merged[key] = value
        tmp = SOUND_INDEX.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, SOUND_INDEX)
    for key, value in merged.items():
        if index_rank(value) >= index_rank(sound_index.get(key)):
            sound_index[key] = value
    if keys is not None:
        keys.clear()


def speaker_int(key: str | None) -> int | None:
    try:
        return int(key) if key is not None else None
    except ValueError:
        return None


def unclipped_speakers(catalog: VoiceCatalog) -> dict[str, str]:
    """The [voices.speakers] and [voices.sound_sets] entries whose voice would
    borrow another's clip: a typo, or a voice not built yet. An archetype falling
    back to its own race is fine, since that is how every other speaker of it is
    read. Keyed as the TOML spells them, `[voices.speakers] 248200` and so on."""
    voices = catalog.config.voices
    pinned = {
        **{f"[voices.speakers] {k}": v for k, v in voices.speakers.items()},
        **{f"[voices.sound_sets] {k}": v for k, v in voices.sound_sets.items()},
    }
    return {
        where: voice
        for where, voice in pinned.items()
        if catalog.resolve(voice).source not in (voice, base_voice(voice))
    }


def model_cast(item: Item) -> int | None:
    """The model file the item's voice was chosen from, when that is how it was
    chosen: a speaker with no display ID of its own (Forever's own NPCs; one
    fill_displays matched to its model still counts) and no voice pinned in
    [voices.speakers]. The addon compares it with the model a player sees and
    exports the NPC record when they differ (Packs:SpeakerModel, #352)."""
    npc = item.npc or {}
    model = npc.get("modelFileID")
    if (
        not model
        or (npc.get("displayID") and npc.get("displayVia") != "model")
        or npc.get("isObject")
        or str(item.speaker_key) in item.config.voices.speakers
    ):
        return None
    return int(model)


def _book_page_text(key: str, books: dict[str, dict], sources: dict[str, dict]) -> str:
    """The page's raw text, from its source entry or the table's `t`."""
    source = sources.get(key) or {}
    return source.get("text") or (books.get(key) or {}).get("t") or ""


def link_pages(books: dict[str, dict], sources: dict[str, dict]) -> None:
    """Sets `x` on each voiced page to the key of the voiced page after it.

    The page after has the same title and the next page number. A captured page
    replaces Classic's own `next` only when it is that page reworded (the same
    0.9 alignment ingest uses), or when it is the only page in that place.
    Several books share titles (Crystallized Note, Decoded Twilight Text); one
    captured page of another book must not take this book's link."""
    places: dict[tuple[str, int], list[str]] = {}
    for key, record in books.items():
        if record.get("b") and record.get("p"):
            places.setdefault((record["b"], int(record["p"])), []).append(key)
    for key, record in books.items():
        if not record.get("b") or not record.get("p"):
            continue
        candidates = places.get((record["b"], int(record["p"]) + 1), [])
        captured = [
            c
            for c in candidates
            if sources.get(c, {}).get("player") or sources.get(c, {}).get("origin")
        ]
        stated = sources.get(key, {}).get("next")
        # A captured page may take this slot when it is the only one there, or
        # when its text is this page's own next, reworded. Any other single
        # capture is a different book of the same name.
        reword = (
            len(captured) == 1
            and stated
            and _aligned(
                _book_page_text(captured[0], books, sources),
                _book_page_text(stated, books, sources),
            )
        )
        if len(captured) == 1 and (len(candidates) == 1 or reword):
            record["x"] = captured[0]
        elif stated in books:
            record["x"] = stated
        elif len(candidates) == 1:
            record["x"] = candidates[0]


def rebuild_tables(
    items: list[Item],
    sound_index: dict[str, Any],
    config: Config,
    data_dir: Path = PACK_DATA_DIR,
    pack_global: str = "ForeverVO_DataPack",
    sounds_dir: Path = SOUNDS_DIR,
    write_index: bool = True,
    dirty: set[str] | None = None,
) -> dict:
    """Writes the pack tables for `items` whose audio exists under sounds_dir.
    Returns {"quests": n, "gossip": n, "npcs": n, "files": set(base names),
    "narratorFiles": set(paths relative to Sounds/), "sexFiles": the same for the
    lines in a speaker's other sex, "speakerFiles": the same for a quest line's
    other speakers}.

    `config.voices` says which alternate narrator voices to look for. `dirty` is
    the set of index keys this process has written since its last save; the
    durations probed here join it, and only those keys are merged into the index
    on disk (see save_sound_index)."""
    if dirty is None:
        dirty = set()
    alternate_voices = config.voices.narrator_alternates
    present = {p.stem for p in sounds_dir.glob("*/*.mp3")}
    for name in present:
        if name not in sound_index:
            # A file we have no record of, usually the other worker's, made since
            # we last merged the disk. Its duration is enough for the tables; the
            # rank rule in save_sound_index keeps this from replacing that
            # worker's own record once it saves.
            sound_index[name] = {
                "d": probe_duration(sounds_dir / sound_folder(name) / f"{name}.mp3"),
                "v": None,
            }
            dirty.add(name)

    # Alternate narrator voices, one folder each under Quests and Gossip. A line
    # counts as available in a voice when its file is there, whoever generated it,
    # so a half-finished pass still yields a usable (if smaller) menu.
    narrator_present: dict[str, set[str]] = {}
    for voice in alternate_voices:
        names = {
            p.stem
            for subfolder in SUBFOLDERS.values()
            for p in narrator_dir(sounds_dir, voice, subfolder).glob("*.mp3")
        }
        if not names:
            continue
        narrator_present[voice] = names
        for name in names:
            key = index_key(name, voice)
            if key not in sound_index:
                sound_index[key] = {
                    "d": probe_duration(
                        sound_path(sound_folder(name), name, voice, sounds_dir)
                    ),
                    "v": voice,
                }
                dirty.add(key)

    # A speaker's line in their other sex (#304), one folder per letter
    sex_present: dict[str, set[str]] = {}
    for letter in ("m", "f"):
        names = {
            p.stem
            for subfolder in SUBFOLDERS.values()
            for p in (sounds_dir / subfolder / "Sex" / letter).glob("*.mp3")
        }
        sex_present[letter] = names
        for name in names:
            key = sex_key(name, letter)
            if key not in sound_index:
                sound_index[key] = {
                    "d": probe_duration(
                        sex_path(sound_folder(name), name, letter, sounds_dir)
                    ),
                    "v": None,
                }
                dirty.add(key)

    # A quest line in another speaker's voice (#948), one folder per speaker
    speaker_present: dict[str, set[str]] = {}
    for folder in (sounds_dir / "Quests" / "Speaker").glob("*"):
        names = {p.stem for p in folder.glob("*.mp3")}
        speaker_present[folder.name] = names
        for name in names:
            key = speaker_key_of(name, folder.name)
            if key not in sound_index:
                sound_index[key] = {
                    "d": probe_duration(
                        speaker_path("Quests", name, folder.name, sounds_dir)
                    ),
                    "v": None,
                }
                dirty.add(key)

    def duration_of(name: str) -> float:
        recorded = sound_index.get(name, 0.0)
        return recorded["d"] if isinstance(recorded, dict) else float(recorded)

    def recorded_voice(*names: str) -> str | None:
        """The voice sound_index recorded for these files, when they agree.
        That is the archetype (scourge-male-dark), which is also the clip's
        file name. A probed placeholder has no voice and is skipped."""
        found = set()
        for name in names:
            recorded = sound_index.get(name)
            voice = recorded.get("v") if isinstance(recorded, dict) else None
            if isinstance(voice, str) and voice:
                found.add(voice)
        if len(found) == 1:
            return found.pop()
        return None

    quests: dict[int, dict] = {}
    gossip: dict[int, list[dict]] = {}
    npcs: dict[int, str] = {}
    models: dict[int, int] = {}  # speaker -> the model file its voice was cast from
    sexes: dict[int, str] = {}  # speaker -> every sex the pipeline knows it as
    maps: dict[int, int] = {}  # speaker of a quest line with several -> its map
    narrator: dict[int, dict[str, dict]] = {}
    books: dict[str, dict] = {}
    book_sources: dict[str, dict] = {}  # page key -> its merged source entry
    used: set[str] = set()
    narrator_used: set[str] = set()
    sex_used: set[str] = set()
    speaker_used: set[str] = set()

    for item in items:
        variants = item.variants()
        available = [v.base for v in variants if v.base in present]
        # A line that mixes the speaker and the narrator also has parts, recorded
        # only when every variant has every part; a line that is only a stage
        # direction has parts and no whole-line file.
        part_count = len(variants[0].parts)
        parts_complete = part_count > 0 and all(
            len(v.parts) == part_count
            and all(part_name(v.base, i) in present for i in range(1, part_count + 1))
            for v in variants
        )
        if not available and not parts_complete:
            continue
        used.update(available)
        gendered = len(variants) == 2
        duration = (
            round(max(duration_of(base) for base in available), 3)
            if available
            else None
        )
        speaker = speaker_int(item.speaker_key)
        name = item.entry.get("name") or (item.npc or {}).get("name")
        if speaker is not None and name:
            npcs[speaker] = name
        cast_model = model_cast(item)
        if speaker is not None and cast_model:
            models[speaker] = cast_model
        if speaker is not None and not (item.npc or {}).get("isObject"):
            sexes[speaker] = known_sexes(item.npc)

        # The same line in the alternate narrator voices, each with its own
        # duration: voices differ in pace, and the text is paged against it.
        # Only a line the narrator still reads: a speaker that gains a voice of
        # its own (a dryad once a dryad clip exists) leaves its old alternates
        # on disk until the generator removes them, and the addon would play
        # one of those over the new voice for anyone with an alternate picked.
        alternates: dict[str, float] = {}
        for voice, names in narrator_present.items() if item.is_narrator else ():
            spoken = [v.base for v in variants if v.base in names]
            if not spoken:
                continue
            narrator_used.update(
                f"{item.subfolder}/Narrator/{voice}/{base}" for base in spoken
            )
            alternates[voice] = round(
                max(duration_of(index_key(base, voice)) for base in spoken), 3
            )

        # The whole line in the speaker's other sex, when every variant the line's
        # own voice has is there too: {letter: duration}. Parts are not doubled;
        # a line with parts plays them in the recorded sex.
        other_sex: dict[str, float] | None = None
        if item.sex_alternate and available:
            letter = item.sex_alternate[0]
            if all(base in sex_present[letter] for base in available):
                sex_used.update(
                    f"{item.subfolder}/Sex/{letter}/{base}" for base in available
                )
                other_sex = {
                    letter: round(
                        max(duration_of(sex_key(base, letter)) for base in available),
                        3,
                    )
                }

        # The quest line's other speakers: {d, v} for one with a file of its
        # own, true for one whose voice is the line's own, false for one whose
        # file is not made yet (the line's own file plays). Whole lines only,
        # like the other sex.
        other_speakers: dict[int, dict | bool] = {}
        for key, voice in item.speaker_alternates.items():
            spoken = available and all(
                base in speaker_present.get(key, ()) for base in available
            )
            if voice and spoken:
                speaker_used.update(
                    f"{item.subfolder}/Speaker/{key}/{base}" for base in available
                )
                names = [speaker_key_of(base, key) for base in available]
                other_speakers[int(key)] = {
                    "d": round(max(duration_of(name) for name in names), 3),
                    "v": recorded_voice(*names) or voice,
                }
            else:
                other_speakers[int(key)] = voice is None
            other = item.others.get(key) or {}
            if other.get("name"):
                npcs[int(key)] = other["name"]
        if other_speakers:
            for key, map_id in (item.entry.get("speakers") or {}).items():
                if map_id:
                    maps[int(key)] = int(map_id)

        # Parts: {d, n} per part in reading order (n marks the narrator's), and
        # for the narrator's parts the alternate voices' own durations by index.
        parts_record: list[dict] | None = None
        part_alternates: dict[str, dict[int, float]] = {}
        if parts_complete:
            parts_record = []
            for index in range(1, part_count + 1):
                names = [part_name(v.base, index) for v in variants]
                used.update(names)
                role = variants[0].parts[index - 1][0]
                parts_record.append(
                    {
                        "d": round(max(duration_of(n) for n in names), 3),
                        "n": True if role == "narrator" else None,
                    }
                )
                if role != "narrator":
                    continue
                for voice, voice_names in narrator_present.items():
                    spoken = [n for n in names if n in voice_names]
                    if not spoken:
                        continue
                    narrator_used.update(
                        f"{item.subfolder}/Narrator/{voice}/{n}" for n in spoken
                    )
                    part_alternates.setdefault(voice, {})[index] = round(
                        max(duration_of(index_key(n, voice)) for n in spoken), 3
                    )

        if item.kind == "quests":
            quest_id = int(item.entry["questID"])
            letter = QUEST_EVENTS[item.event]
            record = quests.setdefault(quest_id, {})
            if duration is not None:
                record[letter] = duration
            # va/vp/vc: the voice that rendered this event's file. The addon
            # names it on a report. Absent on a file probed before it was
            # generated, and when the m- and f- files disagree.
            line_voice = recorded_voice(*available) if available else None
            if line_voice:
                record["v" + letter] = line_voice
            # sa/sp/sc: {m|f = duration} for the line in the speaker's other sex,
            # under Sounds/Quests/Sex/<m|f>/, played when the unit is that sex
            if other_sex:
                record["s" + letter] = other_sex
            # xa/xp/xc: the event's other speakers (#948), {speaker = {d, v} for
            # its file under Sounds/Quests/Speaker/<speaker>/, or true/false:
            # the line's own file}. Every one, so the addon can tell a new one.
            if other_speakers:
                record["x" + letter] = other_speakers
            if parts_record:
                record[letter + "P"] = parts_record
            for voice, durations in part_alternates.items():
                narrator.setdefault(quest_id, {}).setdefault(voice, {})[
                    letter + "P"
                ] = durations
            if gendered:
                # $G branches per line, not per quest: quest 170's accept text
                # branches and its complete text does not. One flag for the quest
                # made FindQuest prefix m-/f- onto every event and look up a file
                # that was never written, so the turn-in played nothing. Record the
                # events that actually branched; Packs.lua still accepts `true`.
                letters = set(record.get("g") or "") | {QUEST_EVENTS[item.event]}
                record["g"] = "".join(sorted(letters))
            # wa/wp/wc: the readers ingest.py still wants this event captured by
            # (needs_of: "f" after a male reading of a line the client resolved
            # a $G branch out of, "mf" when the reader is unknown). The addon
            # exports the line again for such a reader although it is voiced.
            if item.entry.get("needs"):
                record["w" + letter] = item.entry["needs"]
            # ha/hp/hc: the text key (Util.TextKey) of the text this event was
            # voiced from, "<male>,<female>" when it branches, since the live
            # text holds the resolved word. The addon exports a voiced line whose
            # live text keys differently, so wrong source text (Classic's
            # truncated 752-complete, #318) is captured again and replaced.
            male, female = (text_key(t) for t in split_gender(item.raw_text))
            record["h" + letter] = male if male == female else f"{male},{female}"
            # npc is the giver, which the addon shows for an accept text the
            # client leaves unattributed (an item-started or shared quest);
            # ender the turn-in speaker, for a progress or complete text at a
            # game object. One field for both meant 172 item-started quests
            # wore their turn-in NPC's face while the narrator read.
            if speaker is not None:
                field = "npc" if item.event == "accept" else "ender"
                if record.get(field) is None:
                    record[field] = speaker
            for voice, seconds in alternates.items():
                narrator.setdefault(quest_id, {}).setdefault(voice, {})[
                    QUEST_EVENTS[item.event]
                ] = seconds
        elif item.kind == "books":
            if duration is None:
                continue
            # A page is a whole line in the narrator's voice: no parts, no other
            # sex. Keyed by the text key the addon computes from the live page.
            books[item.hash] = {
                "d": duration,
                # `t` is the raw page FindBook matches. `s` is the prose the
                # talking head shows, so a later page is not $B or <HTML>.
                "t": item.raw_text.replace("\r", " ").replace("\n", " "),
                "s": book_display(item.raw_text) or None,
                "b": item.entry.get("title") or None,
                "p": item.entry.get("page") or None,
                "g": gendered or None,
                "n": alternates or None,  # voice -> duration, indices below
            }
            book_sources[item.hash] = item.entry
        else:
            if speaker is None:
                continue
            gossip.setdefault(speaker, []).append(
                {
                    "f": item.base_name,
                    "h": item.hash,
                    "t": item.raw_text.replace("\r", " ").replace("\n", " "),
                    "d": duration,
                    "v": recorded_voice(*available) if available else None,
                    "g": gendered or None,
                    "s": other_sex,  # {m|f = duration}, as sa/sp/sc on a quest
                    "n": alternates
                    or None,  # voice -> duration, turned into indices below
                    "P": parts_record,
                    "nP": part_alternates
                    or None,  # voice -> {part index -> duration}, likewise
                }
            )

    # Only the voices this pack actually carries go in the menu, and the records
    # index into that list, so a voice added to the TOML later cannot shift them.
    spoken_voices = {voice for alternates in narrator.values() for voice in alternates}
    spoken_voices.update(
        voice
        for entries in gossip.values()
        for entry in entries
        for voice in list(entry["n"] or {}) + list(entry["nP"] or {})
    )
    spoken_voices.update(
        voice for entry in books.values() for voice in entry["n"] or {}
    )
    voices = [voice for voice in alternate_voices if voice in spoken_voices]

    for rec in quests.values():
        if rec.get("ender") == rec.get("npc"):
            rec.pop("ender", None)
    quest_lines = [
        f"\t[{qid}] = {lua_record(rec)}," for qid, rec in sorted(quests.items())
    ]
    gossip_lines = []
    for speaker, entries in sorted(gossip.items()):
        gossip_lines.append(f"\t[{speaker}] = {{")
        for entry in sorted(entries, key=lambda e: e["f"]):
            if entry["n"]:
                entry["n"] = {
                    voices.index(voice) + 1: seconds
                    for voice, seconds in entry["n"].items()
                }
            if entry["nP"]:
                entry["nP"] = {
                    voices.index(voice) + 1: durations
                    for voice, durations in entry["nP"].items()
                }
            gossip_lines.append(f"\t\t{lua_record(entry)},")
        gossip_lines.append("\t},")
    link_pages(books, book_sources)
    book_lines = []
    for key, entry in sorted(books.items()):
        if entry["n"]:
            entry["n"] = {
                voices.index(voice) + 1: seconds
                for voice, seconds in entry["n"].items()
            }
        book_lines.append(f"\t[{lua_string(key)}] = {lua_record(entry)},")
    npc_lines = [
        f"\t[{key}] = {lua_string(name)}," for key, name in sorted(npcs.items())
    ]

    narrator_lines = []
    for quest_id, by_voice in sorted(narrator.items()):
        parts = [
            f"[{voices.index(voice) + 1}]={lua_record(record)}"
            for voice, record in sorted(
                by_voice.items(), key=lambda pair: voices.index(pair[0])
            )
        ]
        narrator_lines.append(f"\t[{quest_id}] = {{ " + ", ".join(parts) + " },")
    voice_list = ", ".join(lua_string(voice) for voice in voices)

    write_table("Quests.lua", "quests", quest_lines, data_dir, pack_global)
    write_table("Gossip.lua", "gossip", gossip_lines, data_dir, pack_global)
    write_table("Books.lua", "books", book_lines, data_dir, pack_global)
    model_list = "".join(
        f"\t[{key}] = {model},\n" for key, model in sorted(models.items())
    )
    sex_list = "".join(
        f"\t[{key}] = {lua_string(letters)},\n"
        for key, letters in sorted(sexes.items())
    )
    map_list = "".join(
        f"\t[{key}] = {map_id},\n" for key, map_id in sorted(maps.items())
    )
    write_table(
        "NPCs.lua",
        "npcs",
        npc_lines,
        data_dir,
        pack_global,
        prelude=(
            f"pack.models = {{\n{model_list}}}\npack.sexes = {{\n{sex_list}}}\n"
            f"pack.maps = {{\n{map_list}}}\n"
        ),
    )
    write_table(
        "Narrator.lua",
        "narrator",
        narrator_lines,
        data_dir,
        pack_global,
        prelude=f"pack.narratorVoices = {{ {voice_list} }}\n",
    )
    if write_index:
        save_sound_index(sound_index, dirty)
    gossip_count = sum(len(v) for v in gossip.values())
    narrated_gossip = sum(
        1 for entries in gossip.values() for entry in entries if entry["n"]
    )
    narrated_books = sum(1 for entry in books.values() if entry["n"])
    narrator_note = (
        (
            f", {len(narrator)} quests, {narrated_gossip} gossip lines and {narrated_books} book "
            f"pages in {len(voices)} alternate "
            f"narrator {'voice' if len(voices) == 1 else 'voices'} ({len(narrator_used)} files)"
        )
        if voices
        else ""
    )
    print(
        f"pack tables ({data_dir.parent.name}): {len(quests)} quests, {gossip_count} gossip lines, "
        f"{len(books)} book pages, "
        f"{len(npcs)} speakers, {len(used)} sound files{narrator_note}"
    )
    return {
        "quests": len(quests),
        "gossip": gossip_count,
        "books": len(books),
        "npcs": len(npcs),
        "files": used,
        "narratorFiles": narrator_used,
        "sexFiles": sex_used,
        "speakerFiles": speaker_used,
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be generated"
    )
    parser.add_argument("--limit", type=int, default=0, help="generate at most N files")
    parser.add_argument(
        "--force", action="store_true", help="regenerate even if a file exists"
    )
    parser.add_argument(
        "--all", action="store_true", help="include texts another pack already covers"
    )
    parser.add_argument(
        "--progress", action="store_true", help="include quest progress texts"
    )
    parser.add_argument("--only", choices=list(SUBFOLDERS), help="restrict to one kind")
    parser.add_argument(
        "--quest", type=int, action="append", help="restrict to quest ID(s)"
    )
    parser.add_argument(
        "--tables-only", action="store_true", help="skip synthesis, rebuild tables"
    )
    parser.add_argument(
        "--shard",
        metavar="I/N",
        help="take every Nth file of the todo list (0/2 and 1/2 in two processes). "
        "The GPU is only ~60%% busy on one autoregressive stream; two together "
        "measured 2.28x realtime against 1.68x alone, three no better than two",
    )
    parser.add_argument(
        "--stale-only",
        action="store_true",
        help="only regenerate files whose recorded text fingerprint no longer matches, "
        "skipping lines that have no audio yet",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="record the current text's fingerprint for files that already exist and have "
        "none, without generating anything, so a later text change is detected. Existing "
        "fingerprints are kept, so a pending text change or retune is not marked done",
    )
    parser.add_argument(
        "--voice",
        action="append",
        metavar="VOICE",
        help="restrict to lines in this voice (or cloned from its clip), e.g. dwarf-male. Pairs "
        "with --force when a reference clip is rebuilt: the clip's audio is not part of the "
        "fingerprint, so nothing is restaged on its own the way a retuned voice is",
    )
    parser.add_argument(
        "--captured",
        action="store_true",
        help="only lines captured in game (not bulk sources)",
    )
    parser.add_argument(
        "--narrator-voices",
        default="all",
        metavar="SPEC",
        help="alternate narrator voices to generate: all (default), none, or a comma separated list",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="generate on the CPU when there is no GPU (very slow; off by default so a lost GPU fails loudly)",
    )
    parser.add_argument(
        "--narrator-only",
        action="store_true",
        help="generate only the alternate narrator voices, nothing else",
    )
    args = parser.parse_args(argv)
    config = load_config()
    catalog = VoiceCatalog(config)
    for where, voice in unclipped_speakers(catalog).items():
        print(
            f"warning: {where} = {voice!r} has no clip of its "
            f"own; it is read in {catalog.resolve(voice).source}"
        )
    narrator = config.voices.narrator
    alternate_voices = parse_narrator_voices(
        args.narrator_voices, config.voices.narrator_alternates
    )
    if args.narrator_only and not alternate_voices:
        raise SystemExit(
            "--narrator-only with --narrator-voices none has nothing to do"
        )

    unbuilt = (
        set()
        if args.tables_only or args.reindex
        else ensure_picked_references(config, dry_run=args.dry_run)
    )

    capture = load_sources()
    if not capture["quests"] and not capture["gossip"]:
        print("nothing to voice: run ingest.py or classicdb.py first")
        return 1
    sound_index = (
        json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
        if SOUND_INDEX.exists()
        else {}
    )
    items = load_items(capture, include_progress=args.progress, catalog=catalog)
    for note in pin_report(items, catalog, include_progress=args.progress):
        print(note)

    todo: list[Target] = []
    skipped: dict[str, int] = {}
    dirty: set[str] = set()  # index keys this process wrote since its last save

    stale: set[str] = set()

    def wanted(target: Target) -> bool:
        """False when the file is already there in the voice it should be in."""
        if not target.path.exists() or args.force:
            return True
        recorded = sound_index.get(target.key)
        previous_text = recorded.get("t") if isinstance(recorded, dict) else None
        if ran_into_cap(recorded, target, catalog):
            skipped["ran into the token cap, regenerating"] = (
                skipped.get("ran into the token cap, regenerating", 0) + 1
            )
            stale.add(target.key)
            return True
        if previous_text is not None and not text_current(
            previous_text, target.fingerprint, target.live_pin is not None
        ):
            skipped["text changed, regenerating"] = (
                skipped.get("text changed, regenerating", 0) + 1
            )
            stale.add(target.key)
            return True
        if previous_text is None and catalog.tuned(target.voice):
            # Made before fingerprints existed, so before any tuning: it has the
            # default conditioning, which this voice no longer uses
            skipped["retuned, regenerating"] = (
                skipped.get("retuned, regenerating", 0) + 1
            )
            stale.add(target.key)
            return True
        previous_voice = recorded.get("v") if isinstance(recorded, dict) else None
        if previous_voice == target.voice:
            return False
        if previous_voice is None:
            # A placeholder probed by a table rebuild, or a record the merge
            # stripped before 2026-09-22: nothing says which voice the file was
            # made in, so it may well be a fallback from before the speaker was
            # known (Tabitha Heartweaver's turn-in stayed human-female for days
            # while her offer was remade as scourge-female). Remade once, it
            # carries its voice and settles.
            skipped["voice unknown, regenerating"] = (
                skipped.get("voice unknown, regenerating", 0) + 1
            )
            stale.add(target.key)
            return True
        skipped["voice changed, regenerating"] = (
            skipped.get("voice changed, regenerating", 0) + 1
        )
        stale.add(target.key)
        return True

    def in_voice(target: Target) -> bool:
        """--voice names either the line's voice or the clip it is cloned from, so
        restaging dwarf-male after its clip changes also takes the Dark Iron dwarves."""
        return (
            not args.voice
            or target.voice in args.voice
            or catalog.resolve(target.voice).source in args.voice
        )

    for item in items:
        if args.only and item.kind != args.only:
            continue
        if args.captured and not item.entry.get("player"):
            continue
        if args.quest and (
            item.kind != "quests" or int(item.entry["questID"]) not in args.quest
        ):
            continue
        if (
            item.entry.get("found")
            and not item.entry.get("differs")  # the pack voiced other text (#318)
            and not args.all
            and item.entry.get("pack") != "Forever"
        ):
            skipped["covered by another pack"] = (
                skipped.get("covered by another pack", 0) + 1
            )
            continue
        if item.kind == "gossip" and speaker_int(item.speaker_key) is None:
            skipped["no speaker id"] = skipped.get("no speaker id", 0) + 1
            continue
        if (
            item.kind == "quests"
            and item.speaker_key is None
            and not item.entry.get("isObject")
        ):
            # A quest whose giver we have not met: wait for a capture that names
            # them, so the line gets the right voice
            skipped["speaker unknown (play it to capture)"] = (
                skipped.get("speaker unknown (play it to capture)", 0) + 1
            )
            continue
        for variant in item.variants():
            base, text = variant.base, variant.text
            candidates: list[Target] = []
            if not is_speakable(text):
                skipped["unresolved markup"] = skipped.get("unresolved markup", 0) + 1
            else:
                if not args.narrator_only:
                    candidates.append(Target(item, base, text, item.voice))
                    if item.sex_alternate:
                        letter, voice = item.sex_alternate
                        candidates.append(Target(item, base, text, voice, sex=letter))
                    candidates += [
                        Target(item, base, text, voice, speaker=speaker)
                        for speaker, voice in item.speaker_alternates.items()
                        if voice
                    ]
                if item.is_narrator:
                    candidates += [
                        Target(item, base, text, voice, True)
                        for voice in alternate_voices
                    ]
            if not item.is_narrator and not args.dry_run and not args.reindex:
                # A speaker that has gained a voice of its own (a species clip
                # built, a capture naming the giver) leaves whole-line alternate
                # narrator recordings behind. rebuild_tables no longer lists them,
                # but they would still be probed into the index and shipped, and
                # the line's own file is about to be regenerated anyway.
                for voice in config.voices.narrator_alternates:
                    leftover = sound_path(item.subfolder, base, voice)
                    if leftover.exists():
                        # Every shard walks every item here, so another may
                        # have removed it since
                        try:
                            leftover.unlink()
                        except FileNotFoundError:
                            continue
                        sound_index.pop(index_key(base, voice), None)
                        dirty.add(index_key(base, voice))
                        skipped["stale narrator alternate removed"] = (
                            skipped.get("stale narrator alternate removed", 0) + 1
                        )
            # A line that mixes the speaker and the narrator: the speaker's parts
            # in their voice, each <stage direction> in the narrator's, and the
            # narrator's parts again in every alternate narrator voice.
            for index, (role, words) in enumerate(variant.parts, 1):
                if not is_speakable(words):
                    skipped["unresolved markup"] = (
                        skipped.get("unresolved markup", 0) + 1
                    )
                    continue
                part = part_name(base, index)
                if role == "npc":
                    if not args.narrator_only:
                        candidates.append(
                            Target(
                                item,
                                part,
                                words,
                                item.voice,
                                pin_key=base
                                if pinnable_part(variant) == index
                                else None,
                            )
                        )
                    continue
                if not args.narrator_only:
                    candidates.append(Target(item, part, words, narrator))
                candidates += [
                    Target(item, part, words, voice, True) for voice in alternate_voices
                ]
            candidates = [target for target in candidates if in_voice(target)]
            if unbuilt:
                kept = [
                    target
                    for target in candidates
                    if catalog.resolve(target.voice).source not in unbuilt
                ]
                if len(kept) != len(candidates):
                    skipped["reference could not be rebuilt"] = (
                        skipped.get("reference could not be rebuilt", 0)
                        + len(candidates)
                        - len(kept)
                    )
                candidates = kept
            if args.reindex:
                for target in candidates:
                    recorded = sound_index.get(target.key)
                    if isinstance(recorded, dict) and target.path.exists():
                        # Seed only: an existing fingerprint may be stale on purpose,
                        # and overwriting it would mark the file current. The seed is
                        # the untuned text key, because that is how a file with none
                        # was made, so a tuned voice's old files still come out stale.
                        recorded.setdefault("t", text_key(target.text))
                        dirty.add(target.key)
                continue
            todo.extend(target for target in candidates if wanted(target))
    if args.reindex:
        save_sound_index(sound_index, dirty)
        stamped = sum(
            1
            for value in sound_index.values()
            if isinstance(value, dict) and value.get("t")
        )
        print(
            f"reindexed: {stamped} of {len(sound_index)} entries now carry a text fingerprint"
        )
        return 0

    if args.stale_only:
        todo = [target for target in todo if target.key in stale]

    if args.shard:
        index, _, count = args.shard.partition("/")
        index, count = int(index), int(count)
        if not 0 <= index < count:
            raise SystemExit(f"--shard {args.shard}: index must be 0..{count - 1}")
        # Interleave rather than split in half: both workers then follow the same
        # priority order, so stopping early still leaves the low-level zones done.
        todo = todo[index::count]

    # Low-level content first so early zones are playable soonest, and every line
    # in its own voice before any alternate, so a time-boxed run still adds breadth;
    # a speaker's other sex before the narrator alternates, since it is a speaker
    todo.sort(
        key=lambda t: (
            t.alternate,
            t.sex is not None,
            list(SUBFOLDERS).index(t.item.kind),
            t.item.entry.get("level") or 0,
            t.base,
            t.voice,
        )
    )
    if args.limit:
        todo = todo[: args.limit]

    narrator_count = sum(1 for target in todo if target.alternate)
    sex_count = sum(1 for target in todo if target.sex)
    voices_needed = sorted({target.voice for target in todo})
    missing_voices = [
        v for v in voices_needed if not (VOICES_DIR / f"{v}.wav").exists()
    ]
    print(
        f"{len(todo)} files to generate ({narrator_count} alternate narrator, {sex_count} in a "
        f"speaker's other sex); skipped: {skipped or 'none'}"
    )
    print(f"voices needed: {voices_needed}")
    if missing_voices:
        print(
            f"no reference clip for: {missing_voices} (falling back to narrator.wav / built-in voice)"
        )

    if args.dry_run:
        for target in todo[:50]:
            pin = target.live_pin
            print(
                f"  {target.label}  [{target.voice}]"
                + (f"  (pinned seed {pin.seed})" if pin else "")
                + f"  {target.text[:90]}…"
            )
        if len(todo) > 50:
            print(f"  … and {len(todo) - 50} more")
        return 0

    if todo and not args.tables_only:
        # before the model loads: a missing filter would otherwise stop the run at
        # the first line of the voice that needs it, hours into a night
        require_filters(tuning_filters(config))
        synth = Synth(catalog, allow_cpu=args.cpu)
        started = time.time()
        for n, target in enumerate(todo, 1):
            item = target.item
            t0 = time.time()
            pin = target.live_pin
            duration = synth.speak(
                target.text,
                target.voice,
                target.path,
                seed=pin.seed if pin else None,
                same_seed=pin.same_seed if pin else False,
                max_chars=pin.chunk_chars if pin else CHUNK_CHARS,
            )
            take = synth.last_take
            sound_index[target.key] = index_record(
                duration,
                target,
                take,
                pin.chunk_chars if pin else CHUNK_CHARS,
                pin.same_seed if pin else False,
            )
            dirty.add(target.key)
            print(
                f"[{n}/{len(todo)}] {target.label} {duration:5.1f}s audio in {time.time() - t0:4.1f}s  [{target.voice}] {item.entry.get('title') or item.entry.get('name')}"
                + (f"  seed {take.seed}" if take else "")
                + ("  (pinned)" if pin else "")
                + (
                    "  (ran into the token cap)"
                    if take and any(mark.capped for mark in take.chunks)
                    else ""
                )
            )
            if n % 25 == 0:
                # Keep the pack tables current so a client restart picks up what exists so far.
                # Sources are reloaded so files made by another run (e.g. a --captured pass) are included.
                rebuild_tables(
                    load_items(load_sources(), include_progress=True, catalog=catalog),
                    sound_index,
                    config,
                    dirty=dirty,
                )
        print(f"generated {len(todo)} files in {(time.time() - started) / 60:.1f} min")

    rebuild_tables(
        load_items(load_sources(), include_progress=True, catalog=catalog),
        sound_index,
        config,
        dirty=dirty,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
