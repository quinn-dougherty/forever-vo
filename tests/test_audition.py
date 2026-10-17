"""The audition page's edits to configs/ keep the comments and validate."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import tomlkit
from fastapi import HTTPException

from tools.audition import (
    LineRow,
    random_line,
    run_finished,
    search,
    sound_packs,
    sources_warnings,
    write_approval,
    write_pronunciation,
    write_speaker_voice,
    write_tuning,
    write_voice_sources,
)
from tools.config import CONFIG_DIR, load_config


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run's end calls notify-send, which on a desktop pops up for real; a test that
    wants it puts the real function (imported above, so unpatched) back with a
    stand-in for the command."""
    from tools import audition

    monkeypatch.setattr(audition, "run_finished", lambda voice, outcome: None)


def config_text(config_dir: Path) -> str:
    """Every file of a configs/ directory, one after another."""
    return "".join(
        path.read_text(encoding="utf-8") for path in sorted(config_dir.glob("*.toml"))
    )


def test_write_tuning_adds_and_removes_a_voice_and_keeps_comments(
    config_copy: Path,
) -> None:
    before = config_text(config_copy)
    config = write_tuning(config_copy, "gnome-male", 0.6, 0.4, None)
    assert config.tts.voices["gnome-male"].exaggeration == 0.6
    after = config_text(config_copy)
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in after
    assert "[tts.voices.gnome-male]" in after
    assert config.tts.voices["dwarf-female"].exaggeration == 0.75  # untouched

    # back to the defaults with no clip: the entry goes and the document reads as before
    config = write_tuning(
        config_copy, "gnome-male", config.tts.exaggeration, config.tts.cfg_weight, None
    )
    assert "gnome-male" not in config.tts.voices
    restored = config_text(config_copy)
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in restored


def test_write_tuning_with_a_reference_and_a_tempo(config_copy: Path) -> None:
    config = write_tuning(config_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.1)
    assert config.tts.voices["troll-female"].reference == "npc-1234"
    assert config.tts.voices["troll-female"].tempo == 1.1
    text = config_text(config_copy)
    assert "[tts.voices.troll-female]" in text and "tempo = 1.1" in text
    # a tempo at the default is left out of the entry
    config = write_tuning(config_copy, "troll-female", 0.7, 0.35, "npc-1234", tempo=1.0)
    assert config.tts.voices["troll-female"].tempo is None


def test_write_pronunciation_round_trips_and_removes(config_copy: Path) -> None:
    config = write_pronunciation(config_copy, "Ironforge", "Iron Forge")
    assert config.pronunciations.respell("To Ironforge!") == "To Iron Forge!"
    assert config.pronunciations.root["Gnomeregan"] == "Nomer-gahn"
    config = write_pronunciation(config_copy, "Ironforge", "")
    assert "Ironforge" not in config.pronunciations.root


def test_write_speaker_voice_pins_and_unpins_and_keeps_comments(
    config_copy: Path,
) -> None:
    config = write_speaker_voice(config_copy, "2991", "tauren-female")
    assert config.voices.speakers["2991"] == "tauren-female"
    assert config.voices.speakers["248200"] == "goblin-male"  # untouched
    text = config_text(config_copy)
    assert "# One speaker always in one voice" in text
    config = write_speaker_voice(config_copy, "2991", "")
    assert "2991" not in config.voices.speakers
    # the last entry takes the table with it, whichever pins the real file holds
    for speaker in list(config.voices.speakers):
        config = write_speaker_voice(config_copy, speaker, "")
    assert config.voices.speakers == {}
    assert "[voices.speakers]" not in config_text(config_copy)


def test_write_approval_sets_and_removes_a_recipe_and_keeps_comments(
    config_copy: Path,
) -> None:
    before = config_text(config_copy)
    # names no real approval can have, since the repository's file holds real ones
    config = write_approval(config_copy, "test-male-dark", "clip=test-male-dark")
    config = write_approval(config_copy, "test-female", "clip=test-female")
    assert config.voices.approved["test-male-dark"] == "clip=test-male-dark"
    assert config.voices.speakers == load_config(CONFIG_DIR).voices.speakers
    # approving again records the recipe heard this time
    config = write_approval(config_copy, "test-female", "clip=test-female,tempo=1.1")
    assert config.voices.approved["test-female"] == "clip=test-female,tempo=1.1"
    write_approval(config_copy, "test-female", None)
    config = write_approval(config_copy, "test-male-dark", None)
    assert "test-female" not in config.voices.approved
    assert "test-male-dark" not in config.voices.approved
    restored = config_text(config_copy)
    assert tomlkit.parse(restored).unwrap() == tomlkit.parse(before).unwrap()
    # the comments ahead of [voices.approved] and of the table before it stay put
    assert "# Voices someone has auditioned by ear and approved" in restored
    assert restored.index("# CurseForge project IDs.") < restored.index(
        "[release.curseforge_projects]"
    )


def test_write_approval_refuses_what_is_not_a_voice_name(config_copy: Path) -> None:
    with pytest.raises(HTTPException):
        write_approval(config_copy, "Scourge Male", "clip=scourge-male")


def test_write_speaker_voice_refuses_what_is_not_a_voice_name(
    config_copy: Path,
) -> None:
    before = config_text(config_copy)
    with pytest.raises(HTTPException):
        write_speaker_voice(config_copy, "2991", "Tauren Female")
    assert config_text(config_copy) == before


def test_search_needs_every_word_and_ranks_exact_hits_first() -> None:
    rows = [
        LineRow(
            "415-accept",
            "Quests",
            "Rejold's New Brew",
            "Marleth Barleybrew",
            "dwarf-male",
            "raw a",
            "spoken a",
            4,
            "classic",
        ),
        LineRow(
            "415-complete",
            "Quests",
            "Rejold's New Brew",
            "Rejold Barleybrew",
            "dwarf-male",
            "raw b",
            "spoken b",
            4,
            "classic",
        ),
        LineRow(
            "2-accept",
            "Quests",
            "Sharptalon's Claw",
            "Senani Thunderheart",
            "tauren-female",
            "brew of claws",
            "x",
            8,
            "classic",
        ),
    ]
    assert [r.base for r in search(rows, "brew")] == [
        "415-accept",
        "415-complete",
        "2-accept",
    ]
    assert [r.base for r in search(rows, "brew rejold")] == [
        "415-accept",
        "415-complete",
    ]
    assert [r.base for r in search(rows, "415-complete")] == ["415-complete"]
    assert search(rows, "   ") == []


def test_random_line_stays_in_the_voice_and_prefers_quests() -> None:
    rows = [
        LineRow(
            "415-accept",
            "Quests",
            "Rejold's New Brew",
            "Marleth",
            "dwarf-male",
            "a",
            "a",
            4,
            "classic",
        ),
        LineRow(
            "99-1234abcd",
            "Gossip",
            "Marleth",
            "Marleth",
            "dwarf-male",
            "b",
            "b",
            0,
            "classic",
        ),
        LineRow(
            "77-aaaa0000",
            "Gossip",
            "Innkeeper",
            "Innkeeper",
            "gnome-female",
            "c",
            "c",
            0,
            "classic",
        ),
    ]
    rng = random.Random(1)

    def base(voice: str) -> str | None:
        row = random_line(rows, voice, rng)
        return row.base if row else None

    assert {base("dwarf-male") for _ in range(20)} == {
        "415-accept"
    }  # quests first, never the gossip line
    assert (
        base("gnome-female") == "77-aaaa0000"
    )  # gossip when that is all the voice has
    assert base("orc-male") is None


def test_write_voice_sources_adds_and_removes_and_keeps_the_reference_field(
    config_copy: Path,
) -> None:
    # a voice the real file has no picks for, so the round trip is about this write and
    # not about whatever has been chosen by ear since
    from tools.config import RACE_DICT

    picked = load_config(config_copy).voices.sources
    voice = next(
        v
        for v in (
            f"{race}-{sex}"
            for race in sorted(set(RACE_DICT.values()) - {"narrator"})
            for sex in ("male", "female")
        )
        if v not in picked
    )
    load_config.cache_clear()
    before = config_text(config_copy)
    config = write_voice_sources(config_copy, voice, [539282, 539211, 556543])
    assert config.voices.sources[voice].clips == [539282, 539211, 556543]
    text = config_text(config_copy)
    assert f"[voices.sources.{voice}]" in text
    assert "# Dwarves came out of Chatterbox sounding American (#18)." in text
    # [voices.sources.<v>] is what a clip is made of; [tts.voices.<v>].reference is a
    # different clip to clone from, and writing one must not disturb the other
    assert config.tts.voices == load_config(CONFIG_DIR).tts.voices

    config = write_voice_sources(config_copy, voice, [])
    assert voice not in config.voices.sources
    assert (
        tomlkit.parse(config_text(config_copy)).unwrap()
        == tomlkit.parse(before).unwrap()
    )


def test_write_voice_sources_keeps_the_build(config_copy: Path) -> None:
    config = write_voice_sources(
        config_copy, "tauren-male", [541910], build="12.1.0.69933"
    )
    assert config.voices.sources["tauren-male"].build == "12.1.0.69933"
    config = write_voice_sources(config_copy, "tauren-male", [541910])
    assert config.voices.sources["tauren-male"].build is None


def test_sources_warnings_names_a_clip_the_voice_does_not_actually_read(
    config_copy: Path, tmp_path: Path
) -> None:
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "human-male.wav").touch()
    config = write_tuning(config_copy, "tauren-male", 0.45, 0.5, "npc-3597")
    # a voice whose tuning clones from elsewhere: picking clips for its own wav is moot
    assert any("npc-3597" in w for w in sources_warnings(config, "tauren-male", voices))
    # human-male is the narrator's clip as well as its own
    assert any("narrator" in w for w in sources_warnings(config, "human-male", voices))


def _pack(addons: Path, folder: str, name: str, priority: int) -> Path:
    (addons / folder / "Data").mkdir(parents=True)
    (addons / folder / "Data" / "Pack.lua").write_text(
        f'{folder}Pack = {{\n    name = "{name}",\n    priority = {priority},\n}}\n',
        encoding="utf-8",
    )
    sounds = addons / folder / "Sounds"
    (sounds / "Quests").mkdir(parents=True)
    return sounds


def test_sound_packs_put_the_working_folder_first_then_follow_the_addon_order(
    tmp_path: Path,
) -> None:
    working = tmp_path / "repo" / "Sounds"
    (working / "Quests").mkdir(parents=True)
    addons = tmp_path / "AddOns"
    _pack(addons, "ForeverVO_Data_Base", "Classic", 100)
    _pack(addons, "ForeverVO_Data_Forever", "Forever", 200)
    (addons / "ForeverVO_Data_Local" / "Data").mkdir(parents=True)
    (addons / "ForeverVO_Data_Local" / "Data" / "Pack.lua").write_text(
        'ForeverVO_DataPack = {\n    name = "Local",\n    priority = 300,\n}\n',
        encoding="utf-8",
    )
    (addons / "ForeverVO_Data_Local" / "Sounds").symlink_to(working)
    (addons / "ForeverVO_Data_Old").mkdir()  # no Sounds: not a pack

    packs = sound_packs(addons, working)

    assert [(p.key, p.label, p.priority) for p in packs] == [
        ("ForeverVO_Data", "working folder", 0),
        ("ForeverVO_Data_Forever", "Forever", 200),
        ("ForeverVO_Data_Base", "Classic", 100),
    ]
    assert packs[0].sounds == working


def test_sound_packs_without_a_client_is_just_the_working_folder(
    tmp_path: Path,
) -> None:
    packs = sound_packs(tmp_path / "missing", tmp_path)
    assert [(p.key, p.label) for p in packs] == [("ForeverVO_Data", "working folder")]


def test_a_working_copy_of_another_length_than_the_index_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tools import generate
    from tools.audition import WorkingCopies

    sounds = tmp_path / "Sounds" / "Gossip"
    sounds.mkdir(parents=True)
    lengths = {"9564-8d6952f8": 19.95, "1-aaaaaaaa": 4.0, "2-bbbbbbbb": 3.0}
    for base in lengths:
        (sounds / f"{base}.mp3").write_bytes(b"mp3")
    probed: list[str] = []

    def probe(path: Path) -> float:
        probed.append(path.stem)
        return lengths[path.stem]

    monkeypatch.setattr(generate, "probe_duration", probe)
    index = tmp_path / "sound_index.json"
    index.write_text(
        json.dumps({"9564-8d6952f8": {"d": 17.02}, "1-aaaaaaaa": {"d": 4.01}}),
        encoding="utf-8",
    )
    copies = WorkingCopies(index)

    # Frezza's take from before goblin-male-zany was tuned
    assert copies.stale(sounds / "9564-8d6952f8.mp3", "9564-8d6952f8")
    # within the tolerance, and a line the index has no length for, are trusted
    assert not copies.stale(sounds / "1-aaaaaaaa.mp3", "1-aaaaaaaa")
    assert not copies.stale(sounds / "2-bbbbbbbb.mp3", "2-bbbbbbbb")
    # probed once per file version
    copies.stale(sounds / "9564-8d6952f8.mp3", "9564-8d6952f8")
    assert probed.count("9564-8d6952f8") == 1
    # an index left in conflict trusts every copy
    index.write_text("<<<<<<< Updated upstream\n", encoding="utf-8")
    assert not copies.stale(sounds / "9564-8d6952f8.mp3", "9564-8d6952f8")


def test_stop_ends_a_run_after_the_take_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import threading

    from fastapi.testclient import TestClient

    from tools import audition

    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path)
    # the run's end reaches the desktop through notify-send, here a stand-in
    monkeypatch.setattr(audition, "run_finished", run_finished)
    notified: list[tuple[str, ...]] = []
    monkeypatch.setattr(audition.shutil, "which", lambda name: "/bin/notify-send")
    monkeypatch.setattr(
        audition.subprocess,
        "Popen",
        lambda args, **kw: notified.append(("human-male", args[-2], args[-1])),
    )
    # a Studio without its corpus thread or a model: only what /api/generate touches
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio.model_lock = threading.Lock()
    studio.stops = {}
    client = TestClient(audition.create_app(studio, addons=None))

    class FakeSynth:
        catalog = None

        def render_take(self, text: str, voice: str, **kwargs: object) -> object:
            from tools.generate import RenderedTake

            # the page presses Stop while the first take is being made
            (session,) = studio.stops
            assert client.post(f"/api/generate/{session}/stop").json() == {
                "stopping": True
            }
            return RenderedTake("audio", 1.0, 7, [])

        def encode(
            self, audio: str, out: Path, tempo: float, pitch: float, speed: float = 1.0
        ) -> float:
            out.write_bytes(b"")
            return 1.0

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    response = client.post(
        "/api/generate",
        json={
            "text": "Hello there.",
            "voice": "human-male",
            "exaggeration": [0.45],
            "cfg_weight": [0.5],
            "takes": 3,
        },
    )
    events = [json.loads(line)["event"] for line in response.text.splitlines()]
    assert events == ["start", "take", "stopped"]
    assert notified == [("human-male", "Audition: human-male", "stopped · 1 take done")]
    assert studio.stops == {}  # a finished run leaves nothing to stop
    assert client.post("/api/generate/nope/stop").json() == {"stopping": False}


def test_one_take_is_encoded_at_every_tempo_and_pitch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model runs once per take; a pitch sweep shifts that same audio."""
    import json
    import threading

    from fastapi.testclient import TestClient

    from tools import audition

    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path)
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio.model_lock = threading.Lock()
    studio.stops = {}
    renders: list[str] = []
    encodes: list[tuple[str, float, float]] = []

    class FakeSynth:
        catalog = None

        def render_take(self, text: str, voice: str, **kwargs: object) -> object:
            from tools.generate import RenderedTake

            renders.append(text)
            return RenderedTake(f"audio{len(renders)}", 1.0, 7, [])

        def encode(
            self, audio: str, out: Path, tempo: float, pitch: float, speed: float = 1.0
        ) -> float:
            encodes.append((audio, tempo, pitch))
            out.write_bytes(b"")
            return 1.0

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    client = TestClient(audition.create_app(studio, addons=None))
    response = client.post(
        "/api/generate",
        json={
            "text": "Hello there.",
            "voice": "human-male",
            "exaggeration": [0.45],
            "cfg_weight": [0.5],
            "tempo": [1.0],
            "pitch": [0.0, -3.0, -5.0],
            "takes": 2,
        },
    )
    takes = [json.loads(line) for line in response.text.splitlines()]
    # each take names the voice it was made for: Keep these settings saves to it
    assert {t["voice"] for t in takes if t["event"] == "take"} == {"human-male"}
    assert len(renders) == 2
    assert encodes == [
        ("audio1", 1.0, 0.0),
        ("audio1", 1.0, -3.0),
        ("audio1", 1.0, -5.0),
        ("audio2", 1.0, 0.0),
        ("audio2", 1.0, -3.0),
        ("audio2", 1.0, -5.0),
    ]
    names = [t["name"] for t in takes if t["event"] == "take"]
    assert names[1] == "human-male-e0.45-c0.5-t1.0-p-3.0-take1.mp3"
    # a past run reads its pitch back from the file name
    (run,) = client.get("/api/sessions").json()
    assert sorted(t["pitch"] for t in run["takes"]) == [
        -5.0,
        -5.0,
        -3.0,
        -3.0,
        0.0,
        0.0,
    ]


def test_write_tuning_keeps_pitch_only_when_it_differs(config_copy: Path) -> None:
    config = write_tuning(config_copy, "gnome-male", 0.6, 0.4, None, 1.0, -3.0)
    assert config.tts.voices["gnome-male"].pitch == -3.0
    config = write_tuning(config_copy, "gnome-male", 0.6, 0.4, None, 1.0, 0.0)
    assert config.tts.voices["gnome-male"].pitch is None


def test_local_clip_borrows_from_the_cache_or_another_voice_and_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import audition, refclips

    raw, casc = tmp_path / "raw", tmp_path / "casc"
    monkeypatch.setattr(refclips, "RAW_DIR", raw)
    monkeypatch.setattr(audition, "CASC_DIR", casc)
    monkeypatch.setattr(
        audition,
        "fetch_file",
        lambda fdid, dest, build: _link(casc / build / f"{fdid}.ogg", dest),
    )
    (raw / "orc-male").mkdir(parents=True)
    (raw / "orc-male" / "1.ogg").write_bytes(b"own")
    (casc / "1.0").mkdir(parents=True)
    (casc / "1.0" / "2.ogg").write_bytes(b"cached")
    (raw / "npc-4527").mkdir()
    (raw / "npc-4527" / "3.ogg").write_bytes(b"thrall, fetched before the cache")

    assert audition.local_clip("orc-male", 1, "1.0") == raw / "orc-male" / "1.ogg"
    cached = audition.local_clip("orc-male", 2, "1.0")
    assert cached is not None and cached.read_bytes() == b"cached"
    borrowed = audition.local_clip("orc-male", 3, "1.0")
    assert borrowed == raw / "orc-male" / "3.ogg"
    assert borrowed.read_bytes() == b"thrall, fetched before the cache"
    assert audition.local_clip("orc-male", 4, "1.0") is None  # never downloaded


def _link(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
    return dest


def test_clips_put_the_voices_added_from_also_first_last_added_on_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition, refclips
    from tools.config import load_config

    def row(fdid: int, group: str) -> dict[str, object]:
        return {"n": fdid, "fdid": fdid, "kind": "line", "group": group,
                "seconds": 1.0, "url": f"/{fdid}.ogg"}  # fmt: skip

    loaded = {
        "nightelf-male": [row(1, "speech"), row(2, "set 59")],
        "folder-pcdhnightelfmale": [row(3, "pcdhnightelfmale")],
        "folder-night_elf_male_ghost": [row(4, "night_elf_male_ghost"), row(1, "x")],
    }
    # a Studio with candidates already loaded: only what /api/clips touches
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    monkeypatch.setattr(studio, "clips", lambda voice, refresh=False: loaded[voice])
    monkeypatch.setattr(studio, "config", load_config)
    studio.clips_status = {}
    studio._rows = None  # the corpus still loading: the headings go without line counts
    # a set's heading reads the client tables (gitignored); the voice it is asked about
    # is the point here, not the wording
    monkeypatch.setattr(
        audition,
        "group_about",
        lambda voice, group, spoken: (
            "joke" if group == "speech" else f"{voice} {group}"
        ),
    )
    monkeypatch.setattr(audition, "seed_recipe_history", lambda voice: None)
    monkeypatch.setattr(audition, "npc_labels", dict)
    monkeypatch.setattr(audition, "unlisted_clip_folders", lambda config: [])
    # the real TOML's saved picks for the voice, which are not on this machine's disk
    monkeypatch.setattr(audition, "local_clip", lambda voice, fdid, build: None)
    monkeypatch.setattr(audition, "pick_history", lambda voice=None: {})
    monkeypatch.setattr(audition, "sources_warnings", lambda config, voice: [])
    monkeypatch.setattr(refclips.CLIP_SECONDS, "save", lambda: None)
    client = TestClient(audition.create_app(studio, addons=None))

    also = "folder-pcdhnightelfmale,folder-night_elf_male_ghost"
    d = client.get(f"/api/clips/nightelf-male?also={also}").json()
    # the ghost was added last, so it is on top; fdid 1 is the voice's own already
    assert [c["fdid"] for c in d["clips"]] == [4, 3, 1, 2]
    assert [c["source"] for c in d["clips"]] == ["also", "also", "own", "own"]
    assert d["groups"] == [
        "folder-night_elf_male_ghost: night_elf_male_ghost",
        "folder-pcdhnightelfmale: pcdhnightelfmale",
        "speech",
        "set 59",
    ]
    assert d["about"] == {
        # a borrowed group is described as its own voice's group
        "folder-night_elf_male_ghost: night_elf_male_ghost": (
            "folder-night_elf_male_ghost night_elf_male_ghost"
        ),
        "folder-pcdhnightelfmale: pcdhnightelfmale": (
            "folder-pcdhnightelfmale pcdhnightelfmale"
        ),
        "speech": "joke",
        "set 59": "nightelf-male set 59",
    }


def test_species_wanted_offers_a_speaking_species_with_no_clip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import Any

    from tools import audition, wowdata
    from tools.config import Voices

    species = {126042: "spirithealer", 1: "ogre", 2: "fleshgolem"}
    monkeypatch.setattr(wowdata, "speaker_species", lambda d, m: species.get(m))
    monkeypatch.setattr(wowdata, "display_race_sex", lambda d: (None, None))
    config = SimpleNamespace(voices=Voices())

    def item(model: int, voice: str, sex: int = 3) -> Any:  # a stand-in Item
        npc = {"modelFileID": model, "sex": sex}
        return SimpleNamespace(npc=npc, voice=voice, config=config, speaker_key="1")

    items = [
        item(126042, "human-female"),  # the spirit healer, female by UnitSex
        item(1, "human-male", 2),  # an ogre whose clip is on another machine
        item(2, "npc-10699", 2),  # a flesh golem on its own named clip
    ]
    wanted = audition.species_wanted(items, voiced={"ogre-male"})
    assert wanted == {
        "spirithealer-female": audition.SpeciesWant(1, "human-female", frozenset({"1"}))
    }


def test_wowhead_links_creatures_and_game_objects() -> None:
    from tools.audition import wowhead_url

    assert wowhead_url("10583") == "https://www.wowhead.com/npc=10583"
    assert wowhead_url("-123") == "https://www.wowhead.com/object=123"
    assert wowhead_url("") is None


def test_npcs_lists_the_voices_speakers_most_lines_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition

    def row(key: str, name: str, voice: str) -> audition.LineRow:
        return audition.LineRow(
            base=f"{key}-x", subfolder="Gossip", title=name, speaker=name,
            voice=voice, raw="", spoken="", level=1, source="capture",
            speaker_key=key,
        )  # fmt: skip

    rows = [
        row("10583", "Gryfe", "goblin-male-zany"),
        row("16075", "Kwee Q. Peddlefeet", "goblin-male-zany"),
        row("16075", "Kwee Q. Peddlefeet", "goblin-male-zany"),
        row("-42", "A Wanted Poster", "goblin-male-zany"),
        row("", "An item", "goblin-male-zany"),  # no speaker to link
        row("6491", "Spirit Healer", "human-female"),
    ]
    studio = object.__new__(audition.Studio)
    want = audition.SpeciesWant(1, "human-female", frozenset({"6491"}))
    monkeypatch.setattr(studio, "rows_if_loaded", lambda: rows)
    monkeypatch.setattr(
        studio, "species_want", lambda v: want if v == "spirithealer-female" else None
    )
    client = TestClient(audition.create_app(studio, addons=None))

    d = client.get("/api/npcs/goblin-male-zany").json()
    assert [(n["name"], n["lines"], n["url"]) for n in d["npcs"]] == [
        ("Kwee Q. Peddlefeet", 2, "https://www.wowhead.com/npc=16075"),
        ("A Wanted Poster", 1, "https://www.wowhead.com/object=42"),
        ("Gryfe", 1, "https://www.wowhead.com/npc=10583"),
    ]
    # nobody is cast on a species voice before its clip exists: who would move to it
    d = client.get("/api/npcs/spirithealer-female").json()
    assert [(n["name"], n["now"]) for n in d["npcs"]] == [
        ("Spirit Healer", "human-female")
    ]


def test_a_species_voice_offers_the_lines_that_will_move_to_it() -> None:
    from tools import audition

    healer = audition.LineRow(
        base="6491-22357dc5", subfolder="Gossip", title="Spirit Healer",
        speaker="Spirit Healer", voice="human-female", raw="It is not yet your time.",
        spoken="It is not yet your time.", level=1, source="capture",
        speaker_key="6491",
    )  # fmt: skip
    rows = [healer]
    assert audition.lines_in_voice(rows, "spirithealer-female") == []
    moving = frozenset({"6491"})
    assert audition.lines_in_voice(rows, "spirithealer-female", moving=moving) == [
        healer
    ]
    assert audition.random_line(rows, "spirithealer-female", moving=moving) is healer


def test_clips_say_loading_until_the_rows_are_in_even_if_the_load_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition, refclips
    from tools.config import load_config

    # refresh=1 restarts the load, which for a voice with nothing to fetch finishes
    # on its thread before the answer is built: no rows yet, status already done
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio._rows = None
    studio.clips_status = {"spirithealer-female": "0 clips"}
    monkeypatch.setattr(studio, "clips", lambda voice, refresh=False: None)
    monkeypatch.setattr(studio, "config", load_config)
    monkeypatch.setattr(audition, "seed_recipe_history", lambda voice: None)
    monkeypatch.setattr(audition, "pick_history", lambda voice=None: {})
    monkeypatch.setattr(audition, "sources_warnings", lambda config, voice: [])
    monkeypatch.setattr(audition, "unlisted_clip_folders", lambda config: [])
    monkeypatch.setattr(refclips.CLIP_SECONDS, "save", lambda: None)
    client = TestClient(audition.create_app(studio, addons=None))

    d = client.get("/api/clips/spirithealer-female?refresh=1").json()
    assert d["status"] == "loading"  # the page keeps polling
    assert d["pending"] == ["spirithealer-female"]
    studio.clips_status["spirithealer-female"] = "failed: wago said no"
    d = client.get("/api/clips/spirithealer-female").json()
    assert d["status"].startswith("failed")  # and stops on a failure


def test_borrowed_clips_are_listed_while_the_voice_still_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition, refclips
    from tools.config import load_config

    banshee = {"n": 1, "fdid": 1243396, "kind": "vo_70_weeping_banshee_04",
               "group": "weeping_banshee", "seconds": 3.6, "url": "/x.ogg"}  # fmt: skip
    loaded = {"nightborne-female": None, "folder-weeping_banshee": [banshee]}
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio._rows = None
    studio.clips_status = {"nightborne-female": "loading"}
    studio.clips_progress = {
        "nightborne-female": {"done": 3, "total": 13, "failed": 1, "since": 0.0}
    }
    monkeypatch.setattr(studio, "clips", lambda voice, refresh=False: loaded[voice])
    monkeypatch.setattr(studio, "config", load_config)
    monkeypatch.setattr(audition, "seed_recipe_history", lambda voice: None)
    monkeypatch.setattr(audition, "pick_history", lambda voice=None: {})
    monkeypatch.setattr(audition, "sources_warnings", lambda config, voice: [])
    monkeypatch.setattr(audition, "unlisted_clip_folders", lambda config: [])
    monkeypatch.setattr(audition, "group_about", lambda voice, group, spoken: "")
    monkeypatch.setattr(refclips.CLIP_SECONDS, "save", lambda: None)
    client = TestClient(audition.create_app(studio, addons=None))

    d = client.get("/api/clips/nightborne-female?also=folder-weeping_banshee").json()
    assert d["status"] == "loading"
    assert d["pending"] == ["nightborne-female"]
    assert [c["fdid"] for c in d["clips"]] == [1243396]  # not an empty table
    progress = d["progress"]["nightborne-female"]
    assert (progress["done"], progress["total"], progress["failed"]) == (3, 13, 1)


def test_a_voice_is_filed_under_its_races_expansion() -> None:
    from tools.audition import EXPANSIONS, RACE_EXPANSION, voice_expansion
    from tools.config import RACE_DICT

    assert voice_expansion("skyborne-female") == "Forever"
    assert voice_expansion("goblin-male-zany") == "Classic"  # barks in Booty Bay in 1.x
    assert voice_expansion("nightborne-male") == "Legion"
    assert voice_expansion("npc-11657") is None  # named NPCs are listed apart
    assert voice_expansion("spirithealer-female") is None  # a species
    # every race the client names has one, and each is an expansion the page orders
    assert {r for r in RACE_DICT.values() if r != "narrator"} <= set(RACE_EXPANSION)
    assert set(RACE_EXPANSION.values()) <= set(EXPANSIONS)


def test_a_take_stops_between_the_sentences_of_a_long_line() -> None:
    from types import SimpleNamespace
    from typing import Any

    from tools.generate import Synth, TakeStopped

    pressed: list[bool] = []

    class Model:
        calls = 0

        def generate(self, text: str, **kwargs: object) -> object:
            Model.calls += 1
            pressed.append(True)  # Stop is pressed while the first sentence renders
            return SimpleNamespace(shape=(1, 240_000), cpu=lambda: self.wav)

        wav = SimpleNamespace(shape=(1, 240_000))  # 10 s: speech, not a blip

    synth: Any = object.__new__(Synth)  # a Synth with a stand-in model, no GPU
    synth.sr = 24_000
    synth.torch = SimpleNamespace(
        zeros=lambda *shape: None, manual_seed=lambda seed: None
    )
    synth.model = Model()
    settings = SimpleNamespace(exaggeration=0.5, cfg_weight=0.5)
    synth.catalog = SimpleNamespace(
        resolve=lambda voice: SimpleNamespace(clip=None, settings=settings)
    )
    # two chunks at the pack's 300 characters
    text = "The first sentence is long enough to stand on its own. " * 6
    with pytest.raises(TakeStopped):
        synth.render(text, "human-male", stopped=lambda: bool(pressed))
    assert Model.calls == 1  # the second sentence was never started


def test_a_voice_lists_every_line_when_asked_for_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition

    def row(i: int) -> audition.LineRow:
        return audition.LineRow(
            base=f"{i}-accept", subfolder="Quests", title=f"Quest {i}", speaker="Gryfe",
            voice="goblin-male-zany", raw="x" * i, spoken="x" * i, level=1,
            source="capture", speaker_key="10583",
        )  # fmt: skip

    rows = [row(i) for i in range(1, 76)]
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio._rows = None  # no corpus behind the rows: no pins to look up
    monkeypatch.setattr(studio, "rows", lambda: rows)
    monkeypatch.setattr(studio, "moving_to", lambda voice: frozenset())
    monkeypatch.setattr(audition, "_git", lambda *args, **kw: "")  # the index date
    audition._index_date.cache_clear()
    index = tmp_path / "sound_index.json"  # the tests' own, not the repository's
    index.write_text('{"75-accept": {"d": 1.0, "s": 9, "c": 300}}', encoding="utf-8")
    client = TestClient(audition.create_app(studio, addons=None, sound_index=index))

    d = client.get("/api/lines?voice=goblin-male-zany").json()
    assert (len(d["rows"]), d["total"]) == (60, 75)  # the button's longest sixty
    d = client.get("/api/lines?voice=goblin-male-zany&limit=0").json()
    assert (len(d["rows"]), d["total"]) == (75, 75)  # choosing a voice lists them all
    assert d["rows"][0]["base"] == "75-accept"  # longest first
    # the index seed rides along with every row, pins or no corpus
    assert d["rows"][0]["kept"]["seed"] == 9 and d["rows"][1]["kept"] is None
    audition._index_date.cache_clear()


def test_write_voice_sources_keeps_gaps_and_writes_none_for_no_gap(
    config_copy: Path,
) -> None:
    from tools.audition import tidy_gaps

    assert tidy_gaps([0.25, 0.0, 0.0], 4) == [0.25]  # trailing zeros dropped
    assert tidy_gaps([0.0, 0.0], 3) == []  # no gap is 0, and writes nothing
    assert tidy_gaps([0.3, 0.3, 0.3], 2) == [0.3]  # none after the last clip
    config = write_voice_sources(
        config_copy, "tauren-male", [1, 2, 3], gaps=[0.25, 0.0]
    )
    assert config.voices.sources["tauren-male"].gaps == [0.25]
    assert "gaps = [0.25]" in config_text(config_copy)
    config = write_voice_sources(config_copy, "tauren-male", [1, 2, 3], gaps=[0, 0])
    assert config.voices.sources["tauren-male"].gaps == []
    entry = config_text(config_copy).split("[voices.sources.tauren-male]")
    assert "gaps" not in entry[1].split("[", 1)[0]


def test_build_refuses_a_gap_before_touching_the_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition

    built: list[object] = []
    monkeypatch.setattr(audition, "build_picked_reference", lambda *a: built.append(a))
    studio = object.__new__(audition.Studio)
    client = TestClient(audition.create_app(studio, addons=None))
    r = client.post(
        "/api/clips/build",
        json={"voice": "tauren-male", "clips": [1, 2], "gaps": [5.0], "keep": False},
    )
    assert r.status_code == 400 and "between 0 and 3" in r.json()["detail"]
    assert built == []  # refused before anything was built


def test_a_pick_names_the_sources_its_clips_come_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import audition
    from tools.config import Config, Voices, VoiceSources

    offers = {
        1730262: ["folder-pc_-_nightborne_elf_male"],
        556587: ["nightelf-male", "bloodelf-male"],  # a race set two voices offer
        9: ["nightelf-male"],
    }
    monkeypatch.setattr(audition, "_clip_offers", lambda folders: offers)
    config = Config(voices=Voices())
    # the voice's own candidates need nothing; a clip only it offers needs nothing more
    assert audition.pick_sources(
        "nightelf-male", [1730262, 556587, 9], {556587}, config
    ) == ["folder-pc_-_nightborne_elf_male"]
    # a clip the voice does not list comes from the first other voice offering it
    assert audition.pick_sources("nightelf-male", [556587], set(), config) == [
        "bloodelf-male"
    ]

    # the saved pick is marked, and leads the history when no build matches it
    monkeypatch.setattr(
        audition,
        "pick_history",
        lambda voice=None: {
            "nightelf-male": [{"clips": [9], "at": "2026-10-03 08:00"}]
        },
    )
    saved = VoiceSources(clips=[1730262, 9], gaps=[0.1])
    rows = audition.pick_history_for("nightelf-male", saved, set(), config)
    assert rows[0]["saved"] and rows[0]["at"] == "saved" and rows[0]["gaps"] == [0.1]
    assert rows[0]["uses"] == ["folder-pc_-_nightborne_elf_male"]
    assert not rows[1].get("saved")
    same = VoiceSources(clips=[9])
    rows = audition.pick_history_for("nightelf-male", same, set(), config)
    assert len(rows) == 1 and rows[0]["saved"]  # a matching build is marked, not added


def test_released_picks_never_leave_the_history_and_experiments_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from tools import audition
    from tools.config import Config, Voices, VoiceSources

    monkeypatch.setattr(audition, "SOURCE_PICKS", tmp_path / "source_picks.json")
    monkeypatch.setattr(audition, "PICK_HISTORY", 2)
    # pick [1] has audio in the pack; the others are experiments
    monkeypatch.setattr(
        audition, "released_files", lambda row: 30 if row.get("clips") == [1] else 0
    )
    rows = [{"clips": [c], "at": str(c)} for c in (5, 4, 3, 2, 1)]
    kept = audition.trim_history(rows)
    assert [r["clips"] for r in kept] == [[5], [4], [1]]  # two newest, and the released

    (tmp_path / "source_picks.json").write_text(json.dumps({"x-male": kept}))
    saved = Config(voices=Voices(sources={"x-male": VoiceSources(clips=[5])}))
    monkeypatch.setattr(audition, "load_config", lambda: saved)
    assert audition.forget_pick("x-male", [4], [])  # an experiment goes
    assert not audition.forget_pick("x-male", [1], [])  # a released pick stays
    assert not audition.forget_pick("x-male", [5], [])  # and so does the saved one
    left = json.loads((tmp_path / "source_picks.json").read_text())["x-male"]
    assert [r["clips"] for r in left] == [[5], [1]]


def test_released_reads_the_main_repositorys_main_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from tools import audition

    remotes = (
        "origin\thttps://github.com/someone/forever-vo.git (fetch)\n"
        "upstream\tgit@github.com:quinn-dougherty/forever-vo.git (fetch)\n"
    )
    index = {"1-accept": {"t": "abc+clips=0a1b2c3d,exaggeration=0.6", "v": "orc-male"}}
    calls: list[tuple[str, ...]] = []

    def git(*args: str, timeout: float = 30) -> str | None:
        calls.append(args)
        if args == ("remote", "-v"):
            return remotes
        if args[0] == "rev-parse":
            return "f00d\n"
        if args[0] == "show":
            return json.dumps(index)
        return ""

    monkeypatch.setattr(audition, "_git", git)
    monkeypatch.setattr(audition, "_main_fetched", 1e18)  # no background fetch here
    monkeypatch.setattr(audition, "_main_commit", ("", 0.0))
    audition._main_remote.cache_clear()
    audition._released_at.cache_clear()
    assert audition._main_remote() == "upstream"  # the remote naming the main repo
    assert audition._released() == {"0a1b2c3d": 1}
    assert ("rev-parse", "upstream/main") in calls
    assert ("show", "f00d:tools/data/sound_index.json") in calls

    # outside a git checkout: the working copy
    monkeypatch.setattr(audition, "_git", lambda *a, **kw: None)
    monkeypatch.setattr(audition, "_main_commit", ("", 0.0))
    audition._main_remote.cache_clear()
    audition._released_at.cache_clear()
    assert audition._main_remote() is None
    assert audition._released() == audition._released_at("")
    audition._main_remote.cache_clear()
    audition._released_at.cache_clear()


def test_revert_puts_a_voice_back_to_the_pick_and_knobs_approved(
    config_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition, generate
    from tools.audition import write_approval, write_tuning

    approved_clips, approved_gaps = [11, 12, 13], [0.1]
    digest = generate.picks_digest(approved_clips, None, approved_gaps)
    heard = f"clip=x-male,clips={digest},exaggeration=0.7,pitch=-1.0"
    write_approval(config_copy, "x-male", heard)
    # since then: other picks and other knobs
    write_voice_sources(config_copy, "x-male", [21, 22])
    write_tuning(config_copy, "x-male", 1.2, 0.2, None, 1.3, 0.0)
    history = {"x-male": [{"clips": [21, 22], "at": "now"},
                          {"clips": approved_clips, "gaps": approved_gaps, "at": "then"}]}  # fmt: skip
    monkeypatch.setattr(audition, "pick_history", lambda voice=None: history)
    built: list[tuple] = []
    monkeypatch.setattr(
        audition, "build_picked_reference", lambda v, paths, gaps, **_: built.append((v, gaps)) or Path("x.wav")
    )  # fmt: skip
    monkeypatch.setattr(
        audition, "local_clip", lambda v, fdid, build: Path(f"{fdid}.ogg")
    )
    monkeypatch.setattr(audition, "record_pick", lambda *a, **kw: None)
    monkeypatch.setattr(audition, "reference_seconds", lambda path: 9.0)

    studio = object.__new__(audition.Studio)
    studio.config_path = config_copy
    monkeypatch.setattr(studio, "config", lambda: audition.load_config(config_copy))
    monkeypatch.setattr(studio, "forget_corpus", lambda: None)
    monkeypatch.setattr(studio, "state", dict)
    target = audition.approved_target(studio.config(), "x-male")
    assert target is not None and target[0]["clips"] == approved_clips
    client = TestClient(audition.create_app(studio, addons=None))

    r = client.post("/api/voice/revert", json={"voice": "x-male"})
    assert r.status_code == 200, r.text
    assert built == [("x-male", approved_gaps)]  # the reference rebuilt from the pick
    config = audition.load_config(config_copy)
    picked = config.voices.sources["x-male"]
    assert (picked.clips, picked.gaps) == (approved_clips, approved_gaps)
    tuning = config.tts.voices["x-male"]
    assert (tuning.exaggeration, tuning.pitch) == (0.7, -1.0)
    assert tuning.tempo in (None, 1.0) and tuning.cfg_weight in (None, 0.5)

    # nothing to go back to: refused, and nothing written
    write_approval(
        config_copy, "x-male", "clip=x-male,exaggeration=0.7"
    )  # no picks heard
    r = client.post("/api/voice/revert", json={"voice": "x-male"})
    assert r.status_code == 409


def test_keeping_settings_or_picks_keeps_comments_inside_the_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "configs"
    path.mkdir()
    (path / "tts.toml").write_text(
        "[tts.voices.x-male]\n"
        "exaggeration = 0.75\n"
        "cfg_weight = 0.3\n"
        "\n"
        "# Respellings: a paragraph that drifted in under this table\n",
        encoding="utf-8",
    )
    (path / "voices.sources.toml").write_text(
        "[voices.sources.x-male]\nclips = [1, 2]\n# why these clips\n",
        encoding="utf-8",
    )
    (path / "pronunciations.toml").write_text(
        '[pronunciations]\nGnomeregan = "Nomer-gahn"\n', encoding="utf-8"
    )
    write_tuning(path, "x-male", 0.75, 0.3, None, 1.0, 1.0)
    write_voice_sources(path, "x-male", [3, 4, 5], gaps=[0.1])
    text = config_text(path)
    assert "# Respellings: a paragraph that drifted in under this table" in text
    assert "# why these clips" in text
    config = load_config(path)
    assert config.tts.voices["x-male"].pitch == 1.0
    assert config.voices.sources["x-male"].clips == [3, 4, 5]
    assert config.voices.sources["x-male"].gaps == [0.1]
    # and taking a key back out leaves the comment too
    write_tuning(path, "x-male", 0.75, 0.3, None, 1.0, 0.0)
    assert "# Respellings" in config_text(path)
    assert load_config(path).tts.voices["x-male"].pitch is None


def test_tasting_notes_are_kept_per_voice_and_removed_when_emptied(
    config_copy: Path,
) -> None:
    from fastapi import HTTPException

    from tools.audition import NOTES_COMMENT, write_notes

    config = write_notes(
        config_copy,
        "dwarf-female",
        "  Too bright on long lines.\nTry set 33 at the head.  ",
    )
    assert (
        config.voices.notes["dwarf-female"]
        == "Too bright on long lines.\nTry set 33 at the head."
    )
    text = config_text(config_copy)
    assert NOTES_COMMENT[0] in text  # the table is made with what it is for
    config = write_notes(config_copy, "dwarf-female", "")
    assert "dwarf-female" not in config.voices.notes
    assert NOTES_COMMENT[0] in config_text(config_copy)  # the table stays
    with pytest.raises(HTTPException):  # refused: not a voice name
        write_notes(config_copy, "Not A Voice", "x")


def test_clip_sources_names_what_a_pasted_pick_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from tools import audition

    seen: list[tuple] = []

    def sources(
        voice: str, clips: list[int], own: set[int], config: object
    ) -> list[str]:
        seen.append((voice, clips, own))
        return ["folder-pc_-_nightborne_elf_male"]

    monkeypatch.setattr(audition, "pick_sources", sources)
    studio = object.__new__(audition.Studio)
    monkeypatch.setattr(
        studio, "clips", lambda voice, refresh=False: [{"fdid": 556587}]
    )
    monkeypatch.setattr(studio, "config", lambda: None)
    client = TestClient(audition.create_app(studio, addons=None))
    d = client.get(
        "/api/clips/sources?voice=nightelf-male-warrior&clips=1730262,556587,x"
    ).json()
    assert d == {"uses": ["folder-pc_-_nightborne_elf_male"]}
    # the voice's own candidates are passed as own; a stray value is ignored
    assert seen == [("nightelf-male-warrior", [1730262, 556587], {556587})]
