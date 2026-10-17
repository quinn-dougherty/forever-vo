"""Paths, client constants and the typed configuration of the Forever Voiceover tools.

Two kinds of things live here. Constants nobody edits: the repository paths,
the two environment overrides (WOW_DIR, WOW_BETA_BUILD) and the client's own
race and sex IDs. And the pydantic models for everything the owner does edit,
which lives in configs/ at the repository root: voices and what they borrow
from, Chatterbox conditioning per voice, respellings, what is known about the
readers of old captures, and the release parameters. Each file there holds one
table and is named after it (configs/tts.toml is [tts], configs/voices.sources.toml
is [voices.sources]), written with its full headers. load_config() reads,
merges and validates the files once; a function that needs a section takes that
model by type hint (`voices: Voices`, `readers: Readers`, ...).
"""

from __future__ import annotations

import functools
import os
import re
import tomllib
from functools import cached_property
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
DATA_DIR = TOOLS_DIR / "data"
DB2_DIR = DATA_DIR / "db2"
CASC_DIR = DATA_DIR / "casc"  # files by build and FileDataID, see wowdata.fetch_file
VOICES_DIR = TOOLS_DIR / "voices"
CAPTURE_JSON = DATA_DIR / "capture.json"
SOUND_INDEX = DATA_DIR / "sound_index.json"
CONFIG_DIR = ROOT / "configs"

ADDON_NAME = "ForeverVO"
PACK_NAME = "ForeverVO_Data"
PACK_DIR = ROOT / PACK_NAME
PACK_DATA_DIR = PACK_DIR / "Data"
SOUNDS_DIR = PACK_DIR / "Sounds"

WOW_DIR = Path(
    os.environ.get(
        "WOW_DIR",
        Path.home() / "Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft",
    )
)
BETA_DIR = WOW_DIR / "_classic_beta_"
BETA_BUILD = os.environ.get("WOW_BETA_BUILD", "1.60.1.69913")
# Blood elves and goblins were not playable in Classic 1.x, so this client carries
# no /joke or /flirt for them and their references are barks only (#41). Retail has
# those recordings; it is the only source used for them, and only for a race with
# no spoken emotes here - for every Classic race retail holds the same 1.x files
# and fewer of them, so there is nothing to gain and a re-record to risk.
RETAIL_BUILD = os.environ.get("WOW_RETAIL_BUILD", "12.1.0.69933")
WAGO_BASE = "https://wago.tools"

# ChrRaces IDs -> voice family (forever-vo/tools/voices/<race>-<gender>.wav)
RACE_DICT = {
    -1: "narrator",
    1: "human",
    2: "orc",
    3: "dwarf",
    4: "nightelf",
    5: "scourge",
    6: "tauren",
    7: "gnome",
    8: "troll",
    9: "goblin",
    10: "bloodelf",
    11: "draenei",
    12: "felorc",
    13: "naga",
    14: "broken",
    15: "skeleton",
    16: "vrykul",
    17: "tuskarr",
    18: "foresttroll",
    19: "taunka",
    20: "northrendskeleton",
    21: "icetroll",
    22: "worgen",
    23: "human",
    24: "pandaren",
    25: "pandaren",
    26: "pandaren",
    27: "nightborne",
    28: "highmountaintauren",
    29: "voidelf",
    30: "lightforgeddraenei",
    31: "zandalari",
    32: "kultiran",
    33: "thinhuman",
    34: "darkirondwarf",
    35: "vulpera",
    36: "magharorc",
    37: "mechagnome",
    52: "dracthyr",
    70: "dracthyr",
    95: "skyborne",
    96: "skyborne",
}
GENDER_DICT = {0: "male", 1: "female"}


# ----------------------------------------------------------------------------
# configs/*.toml
# ----------------------------------------------------------------------------


class Strict(BaseModel):
    """A typo in the TOML is an error, not a silently ignored key."""

    model_config = ConfigDict(extra="forbid")


MAX_GAP_SECONDS = (
    3.0  # silence between two picked clips; longer is a pause, not a breath
)


class VoiceSources(Strict):
    """[voices.sources.<voice>]: the client clips that voice's reference wav is cut from.

    Composing a reference automatically fills the window without hearing what goes into
    it, and a greeting kit is not all conversation - a set's longest line is often a
    shout or a death cry, and that becomes the voice. Picking by ear beat every rule
    tried, so a voice may name its clips outright, head first, and the builder then
    sorts, filters and budgets nothing.

    Not to be confused with [tts.voices.<voice>].reference, which names an existing wav
    to clone from. This names what a wav is made of.

    FileDataIDs rather than positions in a listing, so a client update cannot silently
    repoint a pick at different audio.
    """

    clips: list[int] = []
    build: str | None = (
        None  # the wago build to fetch them from; the beta client by default
    )
    # Seconds of silence after each clip, aligned with `clips`; a missing entry is no
    # gap, and the last clip's is never used. Back-to-back clips inside the 6 s t3
    # window can run two deliveries together and throw the accent or the cadence, so a
    # pick may breathe between them. Empty, the reference is the plain concatenation.
    gaps: list[float] = []

    @model_validator(mode="after")
    def _gaps_fit(self) -> VoiceSources:
        if len(self.gaps) >= max(len(self.clips), 1):
            raise ValueError(
                f"[voices.sources] gaps has {len(self.gaps)} entries for "
                f"{len(self.clips)} clips; a gap follows every clip but the last"
            )
        for gap in self.gaps:
            if not 0 <= gap <= MAX_GAP_SECONDS:
                raise ValueError(
                    f"[voices.sources] gap {gap} is not between 0 and "
                    f"{MAX_GAP_SECONDS} seconds"
                )
        return self

    def gap_after(self, index: int) -> float:
        """The silence after clip `index`, 0 for the last clip or none set."""
        if index >= len(self.clips) - 1 or index >= len(self.gaps):
            return 0.0
        return self.gaps[index]


_SPEAKER_KEY = re.compile(r"-?[1-9][0-9]*")
# A line's whole-line file name: <questID>-<event> or <speaker>-<hash> (obj<id>
# for a game object, unknown for no speaker), as the pack and [lines] spell it
_LINE_NAME = re.compile(
    r"(?:\d+-(?:accept|progress|complete)|(?:\d+|obj\d+|unknown)-[0-9a-f]{8})"
)
_VOICE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_FOLDER_NAME = re.compile(r"[a-z0-9_-]+")


class Voices(Strict):
    """[voices]: which clip a speaker is cloned from when it has none of its own."""

    narrator: str = (
        "narrator"  # reads quests from objects and items; keeps the plain sound path
    )
    narrator_alternates: list[
        str
    ] = []  # every narrator line is also generated in each of these
    fallbacks: dict[str, str] = {}  # race without a clip -> race whose clip it borrows
    zone_hints: dict[
        str, str
    ] = {}  # zone name -> race, when the client tables give none
    species_aliases: dict[
        str, str
    ] = {}  # model folder -> voice name, where a close clip exists
    named_folders: dict[
        str, list[str]
    ] = {}  # a named set's sound folder -> more folders its character's lines are in
    clip_folders: list[
        str
    ] = []  # sound folders any voice may borrow clips from in the audition page
    sources: dict[
        str, VoiceSources
    ] = {}  # voice -> clips picked by ear, overriding the recipes
    speakers: dict[
        str, str
    ] = {}  # speaker key -> voice, for the few whose captured model is wrong
    lines: dict[
        str, str
    ] = {}  # line file name -> voice, for words an item or object carries that are someone's
    sound_sets: dict[
        str, str
    ] = {}  # NPCSounds set -> voice, for a display with no race and no species clip
    approved: dict[
        str, str
    ] = {}  # voice -> the recipe heard when it was approved by ear; nothing generates from it
    notes: dict[
        str, str
    ] = {}  # voice -> tasting notes by ear, free text for people; nothing reads them

    @field_validator("speakers")
    @classmethod
    def _speaker_voices(cls, value: dict[str, str]) -> dict[str, str]:
        """Keys as the pack writes them (a creature ID, negative for a game object),
        values shaped like a voice name. Whether the voice has a clip is the
        generator's to say: the clips are not in git, so CI could not check it."""
        for key, voice in value.items():
            if not _SPEAKER_KEY.fullmatch(key):
                raise ValueError(
                    f"[voices.speakers] key {key!r} is not a speaker key "
                    "(a creature ID, negative for a game object)"
                )
            if not _VOICE_NAME.fullmatch(voice):
                raise ValueError(
                    f"[voices.speakers] {key} = {voice!r} is not a voice name "
                    '(lowercase words joined by "-", like "goblin-male")'
                )
        return value

    @field_validator("lines")
    @classmethod
    def _line_voices(cls, value: dict[str, str]) -> dict[str, str]:
        """Keys as the pack names a line's file (832-accept, obj123-0123abcd),
        values shaped like a voice name, as for speakers."""
        for key, voice in value.items():
            if not _LINE_NAME.fullmatch(key):
                raise ValueError(
                    f"[voices.lines] key {key!r} is not a line's file name "
                    "(832-accept, 5688-19cbe7de; the whole line, no m-/f-)"
                )
            if not _VOICE_NAME.fullmatch(voice):
                raise ValueError(
                    f"[voices.lines] {key} = {voice!r} is not a voice name "
                    '(lowercase words joined by "-", like "orc-male-shady")'
                )
        return value

    @field_validator("sound_sets")
    @classmethod
    def _sound_set_voices(cls, value: dict[str, str]) -> dict[str, str]:
        for key, voice in value.items():
            if not key.isdigit() or key.startswith("0"):
                raise ValueError(
                    f"[voices.sound_sets] key {key!r} is not an NPCSounds set ID"
                )
            if not _VOICE_NAME.fullmatch(voice):
                raise ValueError(
                    f"[voices.sound_sets] {key} = {voice!r} is not a voice name "
                    '(lowercase words joined by "-", like "banshee-female")'
                )
        return value

    @field_validator("clip_folders")
    @classmethod
    def _folder_names(cls, value: list[str]) -> list[str]:
        """Folder names as the listfile spells them under sound/creature/ or
        sound/character/: lowercase, since named_folders.json is keyed that way and a
        capital would never match. Legion's player sets put a hyphen in theirs
        (`pc_-_nightborne_elf_male`)."""
        for folder in value:
            if not _FOLDER_NAME.fullmatch(folder):
                raise ValueError(
                    f"[voices] clip_folders {folder!r} is not a sound folder name "
                    '(lowercase letters, digits, "_" and "-", like "guldan")'
                )
        return value

    @field_validator("approved")
    @classmethod
    def _approved_voices(cls, value: dict[str, str]) -> dict[str, str]:
        for voice in value:
            if not _VOICE_NAME.fullmatch(voice):
                raise ValueError(
                    f"[voices.approved] {voice!r} is not a voice name "
                    '(lowercase words joined by "-", like "goblin-male")'
                )
        return value

    @field_validator("notes")
    @classmethod
    def _noted_voices(cls, value: dict[str, str]) -> dict[str, str]:
        for voice in value:
            if not _VOICE_NAME.fullmatch(voice):
                raise ValueError(
                    f"[voices.notes] {voice!r} is not a voice name "
                    '(lowercase words joined by "-", like "goblin-male")'
                )
        return value

    @property
    def narrator_voices(self) -> list[str]:
        return [self.narrator, *self.narrator_alternates]


class VoiceTuning(Strict):
    """[tts.voices.<voice>]: what one voice does differently from the [tts] defaults."""

    reference: str | None = (
        None  # clip stem under tools/voices/ to clone from instead of <voice>.wav
    )
    exaggeration: float | None = None
    cfg_weight: float | None = None
    tempo: float | None = Field(
        default=None, ge=0.5, le=2.0
    )  # time stretch at encode time, pitch kept; 1.0 is as generated
    pitch: float | None = Field(
        default=None, ge=-12.0, le=12.0
    )  # semitones at encode time, length kept; 0 is as generated, down is negative
    speed: float | None = Field(
        default=None, ge=0.5, le=2.0
    )  # varispeed at encode time: tempo and pitch move together, one resample; 1.0 as generated


class TtsSettings(Strict):
    """Resolved Chatterbox conditioning for one voice."""

    exaggeration: float
    cfg_weight: float
    tempo: float = 1.0
    pitch: float = 0.0
    speed: float = 1.0
    reference: str | None = None


class Tts(Strict):
    """[tts]: Chatterbox conditioning, with per-voice overrides."""

    exaggeration: float = 0.45
    cfg_weight: float = 0.5
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=0.0, ge=-12.0, le=12.0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    voices: dict[str, VoiceTuning] = {}

    @property
    def defaults(self) -> TtsSettings:
        return TtsSettings(
            exaggeration=self.exaggeration,
            cfg_weight=self.cfg_weight,
            tempo=self.tempo,
            pitch=self.pitch,
            speed=self.speed,
        )

    def settings_for(self, voice: str) -> TtsSettings:
        """The defaults with this voice's own overrides on top. Borrowed clips are
        not considered here: generate.VoiceCatalog resolves a voice to the voice
        whose clip it actually uses and asks for that one's settings."""
        tuning = self.voices.get(voice) or VoiceTuning()
        return TtsSettings(
            exaggeration=self.exaggeration
            if tuning.exaggeration is None
            else tuning.exaggeration,
            cfg_weight=self.cfg_weight
            if tuning.cfg_weight is None
            else tuning.cfg_weight,
            tempo=self.tempo if tuning.tempo is None else tuning.tempo,
            pitch=self.pitch if tuning.pitch is None else tuning.pitch,
            speed=self.speed if tuning.speed is None else tuning.speed,
            reference=tuning.reference,
        )

    def is_default(self, settings: TtsSettings) -> bool:
        return settings == self.defaults

    def differences(self, settings: TtsSettings) -> dict[str, object]:
        """The fields of `settings` that differ from the defaults, for the fingerprint:
        a knob left at its default (tempo 1.0 on every voice tuned before tempo
        existed) must not change a fingerprint that is already stamped."""
        defaults = self.defaults
        return {
            name: value
            for name, value in settings.model_dump().items()
            if value is not None and value != getattr(defaults, name)
        }


class Pronunciations(RootModel[dict[str, str]]):
    """[pronunciations]: written word -> how Chatterbox should be told to say it.

    Whole words, any case; a shouted all-caps word stays all caps. Only the
    spoken text changes: the pack tables and lookup keys keep the real spelling.
    Changing an entry changes the spoken-text fingerprint, so `generate.py
    --stale-only` regenerates exactly the affected files."""

    @cached_property
    def compiled(self) -> tuple[re.Pattern[str] | None, dict[str, str]]:
        if not self.root:
            return None, {}
        pattern = re.compile(
            r"\b(" + "|".join(map(re.escape, self.root)) + r")\b", re.IGNORECASE
        )
        return pattern, {word.lower(): spoken for word, spoken in self.root.items()}

    def respell(self, text: str) -> str:
        pattern, by_lower = self.compiled
        if pattern is None:
            return text

        def replace(match: re.Match[str]) -> str:
            spoken = by_lower[match.group(1).lower()]
            return spoken.upper() if match.group(1).isupper() else spoken

        return pattern.sub(replace, text)


class Reader(Strict):
    """What a contributor told us about the character that captured their lines."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    player: str | None = None
    class_: str | None = Field(default=None, alias="class")
    race: str | None = None
    restore_name: bool = False  # the name is an ordinary word ("It"), so every $n the old client wrote is that word


class Readers(Strict):
    """[readers]: how far captures are believed, and who read the ones that
    predate the addon recording it."""

    trusted_since: tuple[int, ...] = (
        0,
        1,
        4,
    )  # first addon release whose captures are taken at face value
    legacy: dict[
        str, Reader
    ] = {}  # by character name: owner captures before capture version 3
    community: dict[str, Reader] = {}  # by export origin (the issue comment id)

    @field_validator("trusted_since", mode="before")
    @classmethod
    def _parse_version(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(part) for part in value.split("."))
        return value

    @property
    def trusted_since_text(self) -> str:
        return ".".join(map(str, self.trusted_since))

    def known(self, player: str | None, origin: str | None) -> Reader | None:
        return self.legacy.get(player or "") or self.community.get(origin or "")


class Release(Strict):
    """[release]: what release_pack.py needs beyond the API key in .env."""

    curseforge_projects: dict[str, int] = {}
    base_split_level: int = (
        40  # Classic Quests is Classic's quests to this level; Classic Endgame the rest
    )
    bitrate: str = "32k"  # release mp3 bitrate (mono, 22.05 kHz); the working files keep full quality
    transcode_workers: int = (
        4  # ffmpeg is CPU work; leave cores for the GPU workers' own decoding
    )


class LinePin(Strict):
    """One line's pack file drawn from a seed heard in the audition page.

    `heard` is the line's fingerprint (VoiceCatalog.fingerprint) and `spoken` a hash
    of its exact text when the take was pinned: the pin holds only while both still
    match, since the same seed under another clip, tuning or text is another take.
    Every field is written, none defaulted, so a later change of the pack's chunk
    length cannot quietly change what a pinned seed draws."""

    seed: int = Field(ge=0, lt=2**31)
    chunk_chars: int = Field(ge=120, le=500)
    same_seed: bool
    heard: str
    spoken: str


# A pin names a line's own whole-line file: <questID>-<event> or <speaker>-<hash>,
# behind an m-/f- for a $g line. Parts, other-sex and alternate narrator files have
# keys of their own and cannot be pinned.
LINE_BASE = re.compile(
    r"^(?:[mf]-)?(?:\d+-(?:accept|progress|complete)|(?:\d+|obj\d+|unknown)-[0-9a-f]{8})$"
)


class Lines(RootModel[dict[str, LinePin]]):
    """[lines]: a pinned seed per line, written by the audition page's Pin this take."""

    @field_validator("root")
    @classmethod
    def _whole_line_bases(cls, value: dict[str, LinePin]) -> dict[str, LinePin]:
        bad = [key for key in value if not LINE_BASE.match(key)]
        if bad:
            raise ValueError(
                f"not a line's file name: {', '.join(bad)} (e.g. 415-accept, "
                "m-170-accept, 5688-19cbe7de; parts and alternate files cannot be pinned)"
            )
        return value


class Config(Strict):
    voices: Voices = Voices()
    tts: Tts = Tts()
    pronunciations: Pronunciations = Pronunciations({})
    readers: Readers = Readers()
    release: Release = Release()
    lines: Lines = Lines({})


class ConfigError(ValueError):
    pass


def config_file(config_dir: Path, *table: str) -> Path:
    """The file that holds `table` (a path of keys, ("voices", "speakers")): the
    most specific existing file named after a table that contains it, else the one
    named after its top-level table."""
    for n in range(len(table), 0, -1):
        path = config_dir / f"{'.'.join(table[:n])}.toml"
        if n == 1 or path.exists():
            return path
    raise ValueError("no table named")


def _merge(into: dict, data: dict, where: tuple[str, ...]) -> None:
    for key, value in data.items():
        if key not in into:
            into[key] = value
        elif isinstance(into[key], dict) and isinstance(value, dict):
            _merge(into[key], value, (*where, key))
        else:
            raise ValueError(f"{'.'.join((*where, key))} is set in two files")


def read_config(config_dir: Path, texts: dict[str, str] | None = None) -> Config:
    """configs/*.toml merged and validated, uncached; `texts` stands in for files
    by name (the audition page validates an edit before it writes it)."""
    texts = dict(texts or {})
    try:
        for path in sorted(config_dir.glob("*.toml")):
            texts.setdefault(path.name, path.read_text(encoding="utf-8"))
        merged: dict = {}
        for name, text in sorted(texts.items()):
            data = tomllib.loads(text)
            table = name.removesuffix(".toml").split(".")
            inner = data
            for depth, key in enumerate(table):
                if not isinstance(inner, dict) or set(inner) - {key}:
                    where = ".".join(table[:depth]) or "the top level"
                    raise ValueError(
                        f"{name} may hold only [{'.'.join(table)}], "
                        f"found {sorted(inner)} under {where}"
                    )
                inner = inner.get(key, {})
            _merge(merged, data, ())
        return Config.model_validate(merged)
    except (OSError, ValueError, tomllib.TOMLDecodeError, ValidationError) as e:
        raise ConfigError(f"{config_dir}: {e}") from e


@functools.cache
def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    """The validated configs/, read once per process."""
    return read_config(config_dir)
