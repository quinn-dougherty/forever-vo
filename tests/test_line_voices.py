"""[voices.lines] names one line's voice by file name, for words that reach the
player through an item or an object but are somebody's: the Eye of Burning Shadow
(832-accept) carries the Burning Blade's orders, read by a shady orc."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.config import Config, Voices, load_config
from tools.generate import Item, VoiceCatalog, unclipped_speakers

PENDANT = {
    "questID": 832,
    "event": "accept",
    "isObject": True,
    "npc": None,
    "text": "<Words emanate from the pendant and into your mind.>\n\nGazz'uz...report!",
}
NEERU = {"creatureType": "Humanoid", "modelFileID": 917116, "sex": 2}


def catalog(tmp_path: Path, **lines: str) -> VoiceCatalog:
    return VoiceCatalog(Config(voices=Voices(lines=lines)), voices_dir=tmp_path)


def test_a_pinned_line_takes_its_voice_though_it_has_no_speaker(tmp_path: Path) -> None:
    item = Item(
        "quests",
        "832-accept",
        PENDANT,
        None,
        catalog(tmp_path, **{"832-accept": "orc-male-shady"}),
    )
    assert item.voice == "orc-male-shady"
    assert item.pinned_voice == "orc-male-shady"
    assert not item.is_narrator
    # the narrator says the stage direction between the orc's words, as a part
    [variant] = item.variants()
    assert [kind for kind, _ in variant.parts] == ["narrator", "npc"]
    assert item.sex_alternate is None
    assert item.speaker_alternates == {}


def test_an_unpinned_line_resolves_as_before(tmp_path: Path) -> None:
    cat = catalog(tmp_path, **{"832-accept": "orc-male-shady"})
    narrated = Item(
        "quests",
        "832-accept",
        PENDANT,
        None,
        VoiceCatalog(Config(), voices_dir=tmp_path),
    )
    assert narrated.is_narrator
    assert narrated.pinned_voice is None
    complete = {**PENDANT, "event": "complete", "isObject": None, "npc": "3216"}
    item = Item("quests", "832-complete", complete, NEERU, cat)
    assert item.pinned_voice is None
    assert item.voice != "narrator"


def test_a_pinned_voice_with_no_clip_is_reported(tmp_path: Path) -> None:
    (tmp_path / "human-male.wav").write_bytes(
        b""
    )  # the last resort every voice falls to
    where = unclipped_speakers(catalog(tmp_path, **{"832-accept": "gazzuz-male"}))
    assert "[voices.lines] 832-accept" in where


def test_the_repository_file_pins_the_pendant() -> None:
    assert load_config().voices.lines["832-accept"] == "orc-male-shady"


@pytest.mark.parametrize(
    "lines",
    [
        {"m-832-accept": "orc-male-shady"},  # the whole line, not one sex's file
        {"832": "orc-male-shady"},
        {"832-greet": "orc-male-shady"},
        {"832-accept": "Orc Male"},
    ],
)
def test_a_key_or_voice_of_the_wrong_shape_is_refused(lines: dict) -> None:
    with pytest.raises(ValidationError):
        Voices(lines=lines)


def test_gossip_and_object_lines_can_be_pinned() -> None:
    voices = Voices(
        lines={"5688-19cbe7de": "troll-female-old", "obj123-0123abcd": "narrator"}
    )
    assert voices.lines["obj123-0123abcd"] == "narrator"
