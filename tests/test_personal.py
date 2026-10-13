"""Personal packs: name isolation, resumable builds and hardware selection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import generate, personal, personal_setup
from tools.config import load_config
from tools.generate import Item, VoiceCatalog
from tools.textclean import clean, segments


def torch_stub(available: bool, hip: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: available),
        version=SimpleNamespace(hip=hip),
        __version__="test",
    )


def test_device_requires_explicit_cpu_and_accepts_rocm() -> None:
    for hip in (None, "6.2.4"):
        assert personal.select_device(torch_stub(True, hip), "auto", False) == "cuda"
    with pytest.raises(SystemExit, match="No usable"):
        personal.select_device(torch_stub(False), "auto", False)
    with pytest.raises(SystemExit):
        personal.select_device(torch_stub(False), "cuda", True)
    assert personal.select_device(torch_stub(False), "auto", True) == "cpu"
    assert personal.select_device(torch_stub(True), "cpu", False) == "cpu"


@pytest.mark.parametrize(
    "backend,group", [("cuda", "tts"), ("rocm", "tts-rocm"), ("cpu", "tts")]
)
def test_setup_selects_only_one_dependency_group(backend: str, group: str) -> None:
    command = personal_setup.command("uv", backend, ["--name", "Arlen"])
    assert "--no-default-groups" in command
    assert command[command.index("--group") + 1] == group
    assert command[-1] == ("cpu" if backend == "cpu" else "auto")


def test_setup_dry_run_needs_no_hardware_or_tts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(personal_setup.shutil, "which", lambda _: "uv")
    monkeypatch.setattr(
        personal_setup, "detect_backend", lambda: pytest.fail("probed hardware")
    )
    monkeypatch.setattr(
        personal_setup.subprocess,
        "run",
        lambda cmd, **kw: calls.append((cmd, kw)) or SimpleNamespace(returncode=0),
    )
    assert (
        personal_setup.main(["--name", "Arlen", "--realm", "Realm", "--dry-run"]) == 0
    )
    command, kwargs = calls[0]
    assert "--group" not in command
    assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"].endswith(".venv-personal-plan")


def test_only_placeholders_change_and_parts_use_the_same_name() -> None:
    raw = "Welcome, $N. <The guard shows $n the way.> An adventurer and a $c!"
    assert (
        clean(raw, player_name="Ar-len")
        == "Welcome, Ar-len. An adventurer and a adventurer!"
    )
    assert segments(raw, player_name="Ar-len")[1] == (
        "narrator",
        "The guard shows Ar-len the way.",
    )
    assert "Welcome, Adventurer." in clean(raw)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = load_config().model_copy(deep=True)
    config.voices.sources = {}
    config.voices.speakers = {}
    config.voices.narrator_alternates = ["human-female"]
    voices = tmp_path / "voices"
    voices.mkdir()
    for name in ("human-male", "human-female", config.voices.narrator):
        (voices / f"{name}.wav").write_bytes(b"reference")
    catalog = VoiceCatalog(config, voices)
    monkeypatch.setattr(generate, "voice_for_npc", lambda npc, *a, **kw: "human-male")
    source = {
        "quests": {
            "1-accept": {
                "questID": 1,
                "event": "accept",
                "text": "Hello $N, $g sir:madam;!",
                "npc": "1",
            },
            "2-accept": {
                "questID": 2,
                "event": "accept",
                "text": "Hello adventurer!",
                "npc": "1",
            },
        },
        "gossip": {"1|hello": {"text": "Greetings, $n!", "npc": "1"}},
        "books": {"hello": {"text": "Dear $N", "title": "Letter"}},
        "npcs": {"1": {"name": "Guard", "sex": 2}},
    }
    return source, catalog


def test_names_never_change_source_text_or_lookup_keys(corpus) -> None:
    source, catalog = corpus
    before = json.dumps(source)
    named = personal.personal_items(source, catalog, "Arlen")
    assert len(named) == 2  # literal adventurer and books are not selected
    for item in named:
        generic = Item(item.kind, item.key, item.entry, item.npc, catalog)
        assert item.base_name == generic.base_name
        assert item.hash == generic.hash
        assert all("Arlen" in variant.text for variant in item.variants())
    assert json.dumps(source) == before
    assert personal.folder_name("Arlen", "One") != personal.folder_name("Arlen", "Two")


def test_complete_plan_includes_gender_parts_and_narrator(corpus) -> None:
    source, catalog = corpus
    source["quests"]["1-accept"]["text"] = (
        "Hello $N, $g sir:madam;. <The guard bows to $N.> Farewell."
    )
    item = personal.personal_items(source, catalog, "Arlen")[0]
    targets = personal.targets_for(item)
    assert {t.base[:2] for t in targets} == {"m-", "f-"}
    assert any(t.alternate and "Arlen" in t.text for t in targets)
    assert any(t.voice == catalog.config.voices.narrator for t in targets)
    assert len({t.key for t in targets}) == len(targets)


def test_cache_changes_with_name_reference_and_backend(corpus) -> None:
    source, catalog = corpus
    target = personal.targets_for(personal.personal_items(source, catalog, "Arlen")[0])[
        0
    ]
    first = personal.signature(target, "cuda", {})
    assert personal.signature(target, "rocm", {}) != first
    assert (
        personal.signature(
            target._replace(text=target.text.replace("Arlen", "Other")), "cuda", {}
        )
        != first
    )
    catalog.resolve(target.voice).clip.write_bytes(b"new reference")
    assert personal.signature(target, "cuda", {}) != first


def test_interrupted_build_publishes_only_complete_lines_and_resumes(
    corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, catalog = corpus
    monkeypatch.setattr(personal, "load_sources", lambda: source)
    monkeypatch.setattr(personal, "load_config", lambda: catalog.config)
    monkeypatch.setattr(personal, "VoiceCatalog", lambda _: catalog)
    monkeypatch.setattr(personal.shutil, "which", lambda name: name)
    monkeypatch.setattr(personal, "require_filters", lambda _: None)
    monkeypatch.setattr(
        generate, "save_sound_index", lambda *a, **kw: pytest.fail("wrote shared index")
    )
    monkeypatch.setitem(sys.modules, "torch", torch_stub(True))
    monkeypatch.setattr(personal, "probe_duration", lambda _: 2.5)
    monkeypatch.setattr(
        personal,
        "transcode",
        lambda src, dst, bitrate: (
            dst.parent.mkdir(parents=True, exist_ok=True),
            dst.write_bytes(src.read_bytes()),
        ),
    )
    spoken = []

    class Synth:
        def __init__(self, *a, **kw):
            pass

        def speak(self, text, voice, dest):
            spoken.append(text)
            dest.write_bytes(b"audio")

    monkeypatch.setattr(personal, "Synth", Synth)
    args = [
        "--name",
        "Arlen",
        "--realm",
        "Realm",
        "--output",
        str(tmp_path / "out"),
        "--quest",
        "1",
    ]
    assert personal.main(args + ["--limit", "1"]) == 0
    pack = tmp_path / "out" / personal.folder_name("Arlen", "Realm")
    assert "[1] =" not in (pack / "Personal.lua").read_text()
    assert personal.main(args) == 0
    lua = (pack / "Personal.lua").read_text()
    assert "[1] =" in lua and "RegisterPersonalPack" in lua
    assert 'pack.character = "Arlen"' in lua
    assert 'pack.realm = "Realm"' in lua
    assert len(spoken) == 2  # the first gender is reused
    assert personal.main(args) == 0
    assert len(spoken) == 2
    assert personal.main(args + ["--pronunciation", "Ar-len"]) == 0
    assert len(spoken) == 4
    assert all("Ar-len" in text for text in spoken[-2:])
    assert "$N" in source["quests"]["1-accept"]["text"]


def test_personal_manifest_is_inert_on_old_addons(corpus, tmp_path: Path) -> None:
    import shutil

    lua = next(
        (p for name in ("lua5.1", "lua", "luajit") if (p := shutil.which(name))), None
    )
    if lua is None:
        pytest.skip("Lua not installed")
    _, catalog = corpus
    pack = tmp_path / "ForeverVO_Personal_test"
    pack.mkdir()
    personal.publish(pack, 'Arlen "the Bold"', "Realm", [], {}, catalog)
    script = tmp_path / "check.lua"
    from tools.luatable import lua_string

    script.write_text(
        "ForeverVO = { RegisterPack = function() error('old addon registered personal audio') end }\n"
        f"assert(loadfile({lua_string(str(pack / 'Personal.lua'))}))('Personal', {{}})\n"
        "local received\nForeverVO.RegisterPersonalPack = function(pack) received = pack end\n"
        f"assert(loadfile({lua_string(str(pack / 'Personal.lua'))}))('Personal', {{}})\n"
        "assert(received.character == 'Arlen \"the Bold\"' and received.realm == 'Realm')\n"
    )
    subprocess.run([lua, str(script)], check=True)


def test_file_lock_releases_after_exception(tmp_path: Path) -> None:
    from tools.filelock import exclusive_lock

    path = tmp_path / "build.lock"
    with pytest.raises(RuntimeError), exclusive_lock(path):
        raise RuntimeError("interrupted build")
    # Reacquire from another process to cover real OS locking, not a mock.
    code = "from pathlib import Path; from tools.filelock import exclusive_lock; "
    code += f"lock = exclusive_lock(Path({str(path)!r})); lock.__enter__(); lock.__exit__(None, None, None)"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)


@pytest.mark.parametrize("value", ["", "\nName", "Name$N", "../Name", "<Name>"])
def test_invalid_names_are_rejected(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        personal.character_text(value)


def test_personal_lua_harness() -> None:
    import shutil

    lua = next(
        (p for name in ("lua5.1", "lua", "luajit") if (p := shutil.which(name))), None
    )
    if lua is None:
        pytest.skip("Lua 5.1 not installed; the addon CI job runs this harness")
    subprocess.run([lua, "tests/personal_pack.lua"], check=True)
