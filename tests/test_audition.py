"""The audition page's edits to forever-vo.toml keep the comments and validate."""
from __future__ import annotations

import random
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import tomlkit

from tools.audition import (
    LineRow,
    random_line,
    search,
    sources_warnings,
    write_pronunciation,
    write_tuning,
    write_voice_sources,
)
from tools.config import CONFIG_TOML, load_config


@pytest.fixture
def toml_copy(tmp_path: Path) -> Iterator[Path]:
    copy = tmp_path / "forever-vo.toml"
    shutil.copy(CONFIG_TOML, copy)
    yield copy
    load_config.cache_clear()


def test_write_tuning_adds_and_removes_a_voice_and_keeps_comments(toml_copy: Path) -> None:
    before = toml_copy.read_text(encoding="utf-8")
    config = write_tuning(toml_copy, "gnome-male", 0.6, 0.4, None)
    assert config.tts.voices["gnome-male"].exaggeration == 0.6
    after = toml_copy.read_text(encoding="utf-8")
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in after
    assert "[tts.voices.gnome-male]" in after
    assert config.tts.voices["dwarf-female"].exaggeration == 0.75    # untouched

    # back to the defaults with no clip: the entry goes and the document reads as before
    config = write_tuning(toml_copy, "gnome-male", config.tts.exaggeration, config.tts.cfg_weight, None)
    assert "gnome-male" not in config.tts.voices
    restored = toml_copy.read_text(encoding="utf-8")
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in restored


def test_write_tuning_with_a_reference_and_a_tempo(toml_copy: Path) -> None:
    config = write_tuning(toml_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.1)
    assert config.tts.voices["troll-female"].reference == "npc-1234"
    assert config.tts.voices["troll-female"].tempo == 1.1
    text = toml_copy.read_text(encoding="utf-8")
    assert "[tts.voices.troll-female]" in text and "tempo = 1.1" in text
    # a tempo at the default is left out of the entry
    config = write_tuning(toml_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.0)
    assert config.tts.voices["troll-female"].tempo is None


def test_write_pronunciation_round_trips_and_removes(toml_copy: Path) -> None:
    config = write_pronunciation(toml_copy, "Ironforge", "Iron Forge")
    assert config.pronunciations.respell("To Ironforge!") == "To Iron Forge!"
    assert config.pronunciations.root["Gnomeregan"] == "Nomer-gahn"
    config = write_pronunciation(toml_copy, "Ironforge", "")
    assert "Ironforge" not in config.pronunciations.root


def test_search_needs_every_word_and_ranks_exact_hits_first() -> None:
    rows = [
        LineRow("415-accept", "Quests", "Rejold's New Brew", "Marleth Barleybrew", "dwarf-male", "raw a", "spoken a", 4, "classic"),
        LineRow("415-complete", "Quests", "Rejold's New Brew", "Rejold Barleybrew", "dwarf-male", "raw b", "spoken b", 4, "classic"),
        LineRow("2-accept", "Quests", "Sharptalon's Claw", "Senani Thunderheart", "tauren-female", "brew of claws", "x", 8, "classic"),
    ]
    assert [r.base for r in search(rows, "brew")] == ["415-accept", "415-complete", "2-accept"]
    assert [r.base for r in search(rows, "brew rejold")] == ["415-accept", "415-complete"]
    assert [r.base for r in search(rows, "415-complete")] == ["415-complete"]
    assert search(rows, "   ") == []


def test_random_line_stays_in_the_voice_and_prefers_quests() -> None:
    rows = [
        LineRow("415-accept", "Quests", "Rejold's New Brew", "Marleth", "dwarf-male", "a", "a", 4, "classic"),
        LineRow("99-1234abcd", "Gossip", "Marleth", "Marleth", "dwarf-male", "b", "b", 0, "classic"),
        LineRow("77-aaaa0000", "Gossip", "Innkeeper", "Innkeeper", "gnome-female", "c", "c", 0, "classic"),
    ]
    rng = random.Random(1)

    def base(voice: str) -> str | None:
        row = random_line(rows, voice, rng)
        return row.base if row else None

    assert {base("dwarf-male") for _ in range(20)} == {"415-accept"}   # quests first, never the gossip line
    assert base("gnome-female") == "77-aaaa0000"                        # gossip when that is all the voice has
    assert base("orc-male") is None


def test_write_voice_sources_adds_and_removes_and_keeps_the_reference_field(toml_copy: Path) -> None:
    # a voice the real file has no picks for, so the round trip is about this write and
    # not about whatever has been chosen by ear since
    voice = next(v for v in ("tauren-male", "gnome-male", "orc-female")
                 if v not in load_config(toml_copy).voices.sources)
    load_config.cache_clear()
    before = toml_copy.read_text(encoding="utf-8")
    config = write_voice_sources(toml_copy, voice, [539282, 539211, 556543])
    assert config.voices.sources[voice].clips == [539282, 539211, 556543]
    text = toml_copy.read_text(encoding="utf-8")
    assert f"[voices.sources.{voice}]" in text
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in text
    # [voices.sources.<v>] is what a clip is made of; [tts.voices.<v>].reference is a
    # different clip to clone from, and writing one must not disturb the other
    assert config.tts.voices == load_config(CONFIG_TOML).tts.voices

    config = write_voice_sources(toml_copy, voice, [])
    assert voice not in config.voices.sources
    assert tomlkit.parse(toml_copy.read_text(encoding="utf-8")).unwrap() == tomlkit.parse(before).unwrap()


def test_write_voice_sources_keeps_the_build(toml_copy: Path) -> None:
    config = write_voice_sources(toml_copy, "tauren-male", [541910], build="12.1.0.69933")
    assert config.voices.sources["tauren-male"].build == "12.1.0.69933"
    config = write_voice_sources(toml_copy, "tauren-male", [541910])
    assert config.voices.sources["tauren-male"].build is None


def test_sources_warnings_names_a_clip_the_voice_does_not_actually_read(toml_copy: Path) -> None:
    config = write_tuning(toml_copy, "tauren-male", 0.45, 0.5, "npc-3597")
    # a voice whose tuning clones from elsewhere: picking clips for its own wav is moot
    assert any("npc-3597" in w for w in sources_warnings(config, "tauren-male"))
    # human-male is the narrator's clip as well as its own
    assert any("narrator" in w for w in sources_warnings(config, "human-male"))
