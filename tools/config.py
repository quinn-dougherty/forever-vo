"""Paths, client constants and the typed configuration of the Forever Voiceover tools.

Two kinds of things live here. Constants nobody edits: the repository paths,
the two environment overrides (WOW_DIR, WOW_BETA_BUILD) and the client's own
race and sex IDs. And the pydantic models for everything the owner does edit,
which lives in forever-vo.toml at the repository root: voices and what they
borrow from, Chatterbox conditioning per voice, respellings, what is known
about the readers of old captures, and the release parameters. load_config()
reads and validates the file once; a function that needs a section takes that
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
)

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
DATA_DIR = TOOLS_DIR / "data"
DB2_DIR = DATA_DIR / "db2"
VOICES_DIR = TOOLS_DIR / "voices"
CAPTURE_JSON = DATA_DIR / "capture.json"
SOUND_INDEX = DATA_DIR / "sound_index.json"
CONFIG_TOML = ROOT / "forever-vo.toml"

ADDON_NAME = "ForeverVO"
PACK_NAME = "ForeverVO_Data"
PACK_DIR = ROOT / PACK_NAME
PACK_DATA_DIR = PACK_DIR / "Data"
SOUNDS_DIR = PACK_DIR / "Sounds"

WOW_DIR = Path(os.environ.get(
    "WOW_DIR",
    Path.home() / "Faugus/battlenet/drive_c/Program Files (x86)/World of Warcraft",
))
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
    1: "human", 2: "orc", 3: "dwarf", 4: "nightelf", 5: "scourge", 6: "tauren",
    7: "gnome", 8: "troll", 9: "goblin", 10: "bloodelf", 11: "draenei",
    12: "felorc", 13: "naga", 14: "broken", 15: "skeleton", 16: "vrykul",
    17: "tuskarr", 18: "foresttroll", 19: "taunka", 20: "northrendskeleton",
    21: "icetroll", 22: "worgen", 23: "human", 24: "pandaren", 25: "pandaren",
    26: "pandaren", 27: "nightborne", 28: "highmountaintauren", 29: "voidelf",
    30: "lightforgeddraenei", 31: "zandalari", 32: "kultiran", 33: "thinhuman",
    34: "darkirondwarf", 35: "vulpera", 36: "magharorc", 37: "mechagnome",
    52: "dracthyr", 70: "dracthyr",
    95: "skyborne", 96: "skyborne",
}
GENDER_DICT = {0: "male", 1: "female"}


# ----------------------------------------------------------------------------
# forever-vo.toml
# ----------------------------------------------------------------------------

class Strict(BaseModel):
    """A typo in the TOML is an error, not a silently ignored key."""
    model_config = ConfigDict(extra="forbid")


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
    build: str | None = None     # the wago build to fetch them from; the beta client by default


class Voices(Strict):
    """[voices]: which clip a speaker is cloned from when it has none of its own."""
    narrator: str = "narrator"                  # reads quests from objects and items; keeps the plain sound path
    narrator_alternates: list[str] = []         # every narrator line is also generated in each of these
    fallbacks: dict[str, str] = {}              # race without a clip -> race whose clip it borrows
    zone_hints: dict[str, str] = {}             # zone name -> race, when the client tables give none
    species_aliases: dict[str, str] = {}        # model folder -> voice name, where a close clip exists
    sources: dict[str, VoiceSources] = {}       # voice -> clips picked by ear, overriding the recipes

    @property
    def narrator_voices(self) -> list[str]:
        return [self.narrator, *self.narrator_alternates]


class VoiceTuning(Strict):
    """[tts.voices.<voice>]: what one voice does differently from the [tts] defaults."""
    reference: str | None = None    # clip stem under tools/voices/ to clone from instead of <voice>.wav
    exaggeration: float | None = None
    cfg_weight: float | None = None
    tempo: float | None = Field(default=None, ge=0.5, le=2.0)   # time stretch at encode time, pitch kept; 1.0 is as generated


class TtsSettings(Strict):
    """Resolved Chatterbox conditioning for one voice."""
    exaggeration: float
    cfg_weight: float
    tempo: float = 1.0
    reference: str | None = None


class Tts(Strict):
    """[tts]: Chatterbox conditioning, with per-voice overrides."""
    exaggeration: float = 0.45
    cfg_weight: float = 0.5
    tempo: float = Field(default=1.0, ge=0.5, le=2.0)
    voices: dict[str, VoiceTuning] = {}

    @property
    def defaults(self) -> TtsSettings:
        return TtsSettings(exaggeration=self.exaggeration, cfg_weight=self.cfg_weight, tempo=self.tempo)

    def settings_for(self, voice: str) -> TtsSettings:
        """The defaults with this voice's own overrides on top. Borrowed clips are
        not considered here: generate.VoiceCatalog resolves a voice to the voice
        whose clip it actually uses and asks for that one's settings."""
        tuning = self.voices.get(voice) or VoiceTuning()
        return TtsSettings(
            exaggeration=self.exaggeration if tuning.exaggeration is None else tuning.exaggeration,
            cfg_weight=self.cfg_weight if tuning.cfg_weight is None else tuning.cfg_weight,
            tempo=self.tempo if tuning.tempo is None else tuning.tempo,
            reference=tuning.reference,
        )

    def is_default(self, settings: TtsSettings) -> bool:
        return settings == self.defaults

    def differences(self, settings: TtsSettings) -> dict[str, object]:
        """The fields of `settings` that differ from the defaults, for the fingerprint:
        a knob left at its default (tempo 1.0 on every voice tuned before tempo
        existed) must not change a fingerprint that is already stamped."""
        defaults = self.defaults
        return {name: value for name, value in settings.model_dump().items()
                if value is not None and value != getattr(defaults, name)}


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
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, self.root)) + r")\b", re.IGNORECASE)
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
    restore_name: bool = False      # the name is an ordinary word ("It"), so every $n the old client wrote is that word


class Readers(Strict):
    """[readers]: how far captures are believed, and who read the ones that
    predate the addon recording it."""
    trusted_since: tuple[int, ...] = (0, 1, 4)   # first addon release whose captures are taken at face value
    legacy: dict[str, Reader] = {}               # by character name: owner captures before capture version 3
    community: dict[str, Reader] = {}            # by export origin (the issue comment id)

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
    base_split_level: int = 40          # Base is quests to this level with all gossip; Base Endgame the rest
    bitrate: str = "32k"                # release mp3 bitrate (mono, 22.05 kHz); the working files keep full quality
    transcode_workers: int = 4          # ffmpeg is CPU work; leave cores for the GPU workers' own decoding


class Config(Strict):
    voices: Voices = Voices()
    tts: Tts = Tts()
    pronunciations: Pronunciations = Pronunciations({})
    readers: Readers = Readers()
    release: Release = Release()


class ConfigError(ValueError):
    pass


@functools.cache
def load_config(path: Path = CONFIG_TOML) -> Config:
    """The validated forever-vo.toml, read once per process."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return Config.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as e:
        raise ConfigError(f"{path}: {e}") from e
