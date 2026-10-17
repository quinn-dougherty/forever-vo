"""Synth.render_take's tries, seeds and chunk marks, with a stand-in model: no GPU."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.config import CONFIG_DIR
from tools.generate import (
    TOKEN_CAP_SECONDS,
    ChunkMark,
    RenderedTake,
    Synth,
    Target,
    VoiceCatalog,
    derive_seed,
    ran_into_cap,
)
from tools.textclean import chunk, halve

SR = 1000  # samples per second, so lengths read as milliseconds


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run's end calls notify-send, which on a desktop pops up for real."""
    from tools import audition

    monkeypatch.setattr(audition, "run_finished", lambda voice, outcome: None)


class Wav:
    """Just enough of a tensor for render_take: a shape, cpu(), cat and zeros."""

    def __init__(self, samples: int):
        self.shape = (1, samples)

    def cpu(self) -> Wav:
        return self


def fake_torch(seeds: list[int]) -> SimpleNamespace:
    return SimpleNamespace(
        zeros=lambda rows, samples: Wav(samples),
        cat=lambda parts, dim: Wav(sum(p.shape[-1] for p in parts)),
        manual_seed=seeds.append,
    )


def synth_with(lengths: list[float], seeds: list[int] | None = None) -> Any:
    """A Synth whose model answers each generate() with the next length, in seconds."""
    answers = iter(lengths)
    texts: list[str] = []

    class Model:
        def generate(self, text: str, **kwargs: object) -> Wav:
            texts.append(text)
            return Wav(int(next(answers) * SR))

    synth: Any = object.__new__(Synth)
    synth.sr = SR
    synth.torch = fake_torch(seeds if seeds is not None else [])
    synth.model = Model()
    settings = SimpleNamespace(
        exaggeration=0.5, cfg_weight=0.5, tempo=1.0, pitch=0.0, speed=1.0
    )
    synth.catalog = SimpleNamespace(
        resolve=lambda voice: SimpleNamespace(clip=None, settings=settings)
    )
    synth.texts = texts
    return synth


SENTENCE = "The first sentence is long enough to stand on its own. "


def test_a_runaway_never_beats_a_clean_try() -> None:
    # the longest try used to win, so a 40 s babble beat the line spoken in 4 s
    synth = synth_with([40.0, 4.0])
    take = synth.render_take("Bring me the head of the beast, and quickly.", "x")
    (mark,) = take.chunks
    assert take.seconds == pytest.approx(4.0)
    assert mark.attempts == 2 and not mark.capped


def test_a_chunk_that_caps_every_try_is_split_in_half() -> None:
    text = "Bring me the head of the beast. Then go and tell the king."
    synth = synth_with([40.0, 40.0, 40.0, 2.0, 3.0])
    take = synth.render_take(text, "x")
    assert [m.text for m in take.chunks] == [
        "Bring me the head of the beast.",
        "Then go and tell the king.",
    ]
    assert all(m.split and not m.capped for m in take.chunks)
    # the halves with the usual gap between them
    assert take.chunks[1].start == pytest.approx(2.35)
    assert take.seconds == pytest.approx(5.35)


def test_marks_follow_the_chunks_with_the_gap_between() -> None:
    synth = synth_with([12.0, 9.0])
    take = synth.render_take(SENTENCE * 6, "x")
    assert len(chunk(SENTENCE * 6)) == 2
    first, second = take.chunks
    assert (first.start, first.end) == (0.0, 12.0)
    assert (second.start, second.end) == (12.35, 21.35)
    assert take.seconds == pytest.approx(21.35)


def test_every_call_is_seeded_from_the_take() -> None:
    seeds: list[int] = []
    synth = synth_with([12.0, 9.0], seeds)
    take = synth.render_take(SENTENCE * 6, "x", seed=42)
    assert take.seed == 42
    assert seeds == [derive_seed(42, 0, 0), derive_seed(42, 1, 0)]
    assert [m.seed for m in take.chunks] == seeds
    # the same seed for every chunk when asked
    seeds.clear()
    synth = synth_with([12.0, 9.0], seeds)
    synth.render_take(SENTENCE * 6, "x", seed=42, same_seed=True)
    assert seeds == [derive_seed(42, 0, 0)] * 2


def test_a_retry_draws_a_new_seed() -> None:
    seeds: list[int] = []
    synth = synth_with([0.1, 4.0], seeds)  # a blip, then speech
    synth.render_take("Bring me the head of the beast, and quickly.", "x", seed=5)
    assert seeds == [derive_seed(5, 0, 0), derive_seed(5, 0, 1)]
    assert len(set(seeds)) == 2


def test_a_take_without_a_seed_draws_one() -> None:
    takes = [synth_with([4.0]).render_take("Bring me the head.", "x") for _ in range(4)]
    assert len({t.seed for t in takes}) > 1


def test_the_chunk_length_is_the_callers() -> None:
    synth = synth_with([5.0] * 6)
    take = synth.render_take(SENTENCE * 6, "x", max_chars=120)
    assert len(take.chunks) == len(chunk(SENTENCE * 6, 120)) > 2


def test_halve_cuts_near_the_middle() -> None:
    assert halve("One two three. Four five six.") == [
        "One two three.",
        "Four five six.",
    ]
    assert halve("one, two, three, four") == ["one, two,", "three, four"]
    assert halve("onetwothree") == ["onetwothree"]
    # a sentence end at the edge is no fair half; a comma near the middle is
    assert halve("Hi. One two three four five six, seven eight nine ten eleven") == [
        "Hi. One two three four five six,",
        "seven eight nine ten eleven",
    ]


def target(
    text: str, catalog: Any = None, voice: str = "human-male", **kwargs: Any
) -> Any:
    item: Any = SimpleNamespace(catalog=catalog, subfolder="Quests")
    return Target(item, kwargs.pop("base", "1-accept"), text, voice, **kwargs)


def test_a_single_chunk_at_the_cap_is_regenerated() -> None:
    from tools.config import load_config

    catalog = VoiceCatalog(load_config())
    short = target("Bring me the head of the beast.", catalog)
    tempo = catalog.resolve("human-male").settings.tempo or 1.0
    speed = catalog.resolve("human-male").settings.speed or 1.0
    at_cap = {"d": 40.0 / (tempo * speed), "v": "human-male"}
    assert ran_into_cap(at_cap, short, catalog)
    assert not ran_into_cap({"d": 39.0 / (tempo * speed)}, short, catalog)
    assert not ran_into_cap(None, short, catalog)
    # a line of several chunks runs past 40 s honestly
    assert not ran_into_cap(at_cap, target(SENTENCE * 6, catalog), catalog)
    assert TOKEN_CAP_SECONDS < 40.0


def test_marks_move_into_the_encoded_files_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A take stretched by tempo or speed keeps its marks on the gaps, and a past run
    reads its seed and marks back; a broken sidecar costs the marks, not the list."""
    from fastapi.testclient import TestClient

    from tools import audition

    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path)
    studio = object.__new__(audition.Studio)
    studio.config_path = CONFIG_DIR
    studio.model_lock = threading.Lock()
    studio.stops = {}
    asked: list[dict] = []

    class FakeSynth:
        catalog = None

        def render_take(self, text: str, voice: str, **kwargs: Any) -> RenderedTake:
            asked.append(kwargs)
            return RenderedTake(
                "audio",
                20.0,
                99,
                [
                    ChunkMark(0.0, 10.0, "One.", 1, 1),
                    ChunkMark(10.35, 20.0, "Two.", 2, 3, capped=True),
                ],
            )

        def encode(
            self, audio: str, out: Path, tempo: float, pitch: float, speed: float = 1.0
        ) -> float:
            out.write_bytes(b"")
            return 20.0 / (tempo * speed)

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    client = TestClient(audition.create_app(studio, addons=None))
    response = client.post(
        "/api/generate",
        json={
            "text": "Hello there.",
            "voice": "human-male",
            "exaggeration": [0.45],
            "cfg_weight": [0.5],
            "tempo": [1.0, 2.0],
            "chunk_chars": 400,
            "same_seed": True,
        },
    )
    takes = [
        json.loads(line)
        for line in response.text.splitlines()
        if json.loads(line)["event"] == "take"
    ]
    assert asked == [
        {
            "stopped": asked[0]["stopped"],
            "seed": None,
            "same_seed": True,
            "max_chars": 400,
        }
    ]
    plain, doubled = takes
    assert [c["start"] for c in plain["chunks"]] == [0.0, 10.35]
    # twice as fast
    assert [c["start"] for c in doubled["chunks"]] == pytest.approx(
        [0.0, 5.175], abs=0.01
    )
    assert doubled["chunks"][1]["capped"] and doubled["seed"] == 99
    assert doubled["same_seed"] and doubled["chunk_chars"] == 400

    (run,) = client.get("/api/sessions").json()
    restored = {t["name"]: t for t in run["takes"]}
    assert restored[plain["name"]]["chunks"] == plain["chunks"]
    assert restored[plain["name"]]["seed"] == 99
    # a sidecar cut short loses its marks; the run is still listed
    (Path(tmp_path) / run["session"] / plain["name"]).with_suffix(".json").write_text(
        "{", encoding="utf-8"
    )
    (run,) = client.get("/api/sessions").json()
    assert "chunks" not in {t["name"]: t for t in run["takes"]}[plain["name"]]
    assert len(run["takes"]) == 2


# --- [lines]: a seed pinned per line -------------------------------------------

LINE = "Bring me the head of the beast, and quickly."


def pinned_catalog(**pin: Any) -> tuple[Any, Any]:
    """The repository's config with one pin on 1-accept, heard as it reads today,
    and the plain catalog beside it."""
    from tools.config import Lines, load_config
    from tools.generate import spoken_hash

    config = load_config()
    plain = VoiceCatalog(config)
    fields = {
        "seed": 42,
        "chunk_chars": 300,
        "same_seed": False,
        "heard": plain.fingerprint("human-male", LINE),
        "spoken": spoken_hash(LINE),
        **pin,
    }
    lines = Lines.model_validate({"1-accept": fields})
    return VoiceCatalog(config.model_copy(update={"lines": lines})), plain


def test_a_live_pin_joins_only_its_own_files_fingerprint() -> None:
    from tools.generate import PIN_VERSION, strip_pin

    catalog, plain = pinned_catalog()
    fp = plain.fingerprint("human-male", LINE)
    own = target(LINE, catalog)
    assert own.fingerprint == fp + ("," if "+" in fp else "+") + (
        f"seed=42,chunk=300,pin={PIN_VERSION}"
    )
    assert strip_pin(own.fingerprint) == fp
    # the other sex's file and the alternate narrators stay unpinned
    assert target(LINE, catalog, sex="f").fingerprint == fp
    assert target(LINE, catalog, alternate=True).fingerprint == fp
    # another line, and the approvals' recipe, are untouched by any pin
    assert target(LINE, catalog, base="2-accept").fingerprint == fp
    assert catalog.recipe("human-male") == plain.recipe("human-male")


def test_with_pin_reads_back_in_either_fingerprint_shape() -> None:
    from tools.config import LinePin
    from tools.generate import strip_pin, with_pin

    pin = LinePin(seed=7, chunk_chars=420, same_seed=True, heard="x", spoken="y")
    assert with_pin("abcd1234", pin) == "abcd1234+seed=7,chunk=420,pin=1,same_seed=1"
    assert with_pin("abcd1234+clips=1a2b3c4d", pin) == (
        "abcd1234+clips=1a2b3c4d,seed=7,chunk=420,pin=1,same_seed=1"
    )
    assert strip_pin(with_pin("abcd1234", pin)) == "abcd1234"
    assert strip_pin("abcd1234+clips=1a2b3c4d") == "abcd1234+clips=1a2b3c4d"
    assert with_pin("abcd1234", None) == "abcd1234"


def test_a_pin_stands_aside_when_the_line_or_its_voice_changed() -> None:
    _, plain = pinned_catalog()
    fp = plain.fingerprint("human-male", LINE)
    for changed in ({"heard": "retuned+cfg_weight=0.9"}, {"spoken": "0" * 16}):
        catalog, _ = pinned_catalog(**changed)
        assert target(LINE, catalog).live_pin is None
        assert target(LINE, catalog).fingerprint == fp
    # punctuation alone is another take: the text, not its key, is what was heard
    catalog, _ = pinned_catalog()
    assert target(LINE.replace(",", ""), catalog).live_pin is None


def test_pinning_restages_and_unpinning_keeps_the_take() -> None:
    from tools.generate import text_current

    catalog, plain = pinned_catalog()
    pinned_fp = target(LINE, catalog).fingerprint
    plain_fp = plain.fingerprint("human-male", LINE)
    # pinned: a file drawn before the pin is stale, one drawn from it is current
    assert not text_current(plain_fp, pinned_fp, pinned=True)
    assert text_current(pinned_fp, pinned_fp, pinned=True)
    # unpinned: the file the pin drew stays
    assert text_current(pinned_fp, plain_fp, pinned=False)
    # a re-pin to another seed restages
    other, _ = pinned_catalog(seed=43)
    assert not text_current(pinned_fp, target(LINE, other).fingerprint, pinned=True)


def test_a_pinned_take_at_the_cap_is_not_drawn_again() -> None:
    catalog, _ = pinned_catalog()
    own = target(LINE, catalog)
    settings = catalog.resolve("human-male").settings
    d = 40.0 / ((settings.tempo or 1.0) * (settings.speed or 1.0))
    assert not ran_into_cap({"d": d, "t": own.fingerprint}, own, catalog)
    # drawn before the pin, it is: the seed may well do better
    assert ran_into_cap({"d": d, "t": "older"}, own, catalog)


def test_speak_draws_from_the_seed_it_is_given(tmp_path: Path) -> None:
    seeds: list[int] = []
    synth = synth_with([4.0], seeds)
    synth.encode = lambda audio, out, *rest: 4.0
    synth.speak(LINE, "x", tmp_path / "a.mp3", seed=42, max_chars=300)
    assert seeds == [derive_seed(42, 0, 0)]
    assert synth.last_take.seed == 42


def test_the_run_reports_pins_that_no_longer_apply() -> None:
    from tools.generate import pin_report

    catalog, _ = pinned_catalog()
    line = SimpleNamespace(base="1-accept", text=LINE, parts=[])
    item: Any = SimpleNamespace(voice="human-male", variants=lambda: [line])
    assert pin_report([item], catalog) == []  # live, and nothing to say
    changed = SimpleNamespace(base="1-accept", text=LINE + " Now.", parts=[])
    gone: Any = SimpleNamespace(voice="human-male", variants=lambda: [changed])
    assert "no longer holds" in pin_report([gone], catalog)[0]
    assert "no such line" in pin_report([], catalog)[0]
    split = [("npc", "Bring me"), ("narrator", "Sigh"), ("npc", "the head.")]
    parted = SimpleNamespace(base="1-accept", text=LINE, parts=split)
    parts: Any = SimpleNamespace(voice="human-male", variants=lambda: [parted])
    assert "narration splits" in pin_report([parts], catalog)[0]


# --- the audition side of pins --------------------------------------------------


@pytest.fixture
def pin_studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An audition server on a copy of the real TOML, with one line in its corpus and
    a stand-in model that answers every take in 2 s."""

    from fastapi.testclient import TestClient

    from tools import audition
    from tools.config import load_config

    toml = tmp_path / "configs"
    shutil.copytree(CONFIG_DIR, toml)
    monkeypatch.setattr(audition, "AUDITION_DIR", tmp_path / "takes")
    studio = object.__new__(audition.Studio)
    studio.config_path = toml
    studio.model_lock = threading.Lock()
    studio.stops = {}
    variant: Any = SimpleNamespace(base="1-accept", text=LINE, parts=[])
    item: Any = SimpleNamespace(voice="human-male", subfolder="Quests")
    studio._rows = []
    studio._by_base = {"1-accept": (item, variant)}

    class FakeSynth:
        catalog = None

        def render_take(self, text: str, voice: str, **kwargs: Any) -> RenderedTake:
            seed = kwargs.get("seed") or 1234
            return RenderedTake(
                "audio", 2.0, seed, [ChunkMark(0.0, 2.0, text, seed, 1)]
            )

        def encode(self, audio: Any, out: Path, *rest: Any) -> float:
            out.write_bytes(b"")
            return 2.0

    monkeypatch.setattr(studio, "synth", lambda: FakeSynth(), raising=False)
    monkeypatch.setattr(studio, "state", lambda: {"ok": True}, raising=False)
    # the tests' own index, not the repository's, which the nightly changes; and no
    # real git: the pin's uncommitted-configs check and the index's date read it
    index = tmp_path / "sound_index.json"
    index.write_text("{}", encoding="utf-8")
    git_answers: dict[str, str] = {"status": "", "log": "2026-10-12\n"}
    monkeypatch.setattr(
        audition, "_git", lambda *args, **kw: git_answers.get(args[0], "")
    )
    audition._index_date.cache_clear()
    client = TestClient(audition.create_app(studio, addons=None, sound_index=index))
    yield SimpleNamespace(
        client=client,
        studio=studio,
        toml=toml,
        variant=variant,
        index=index,
        git=git_answers,
    )
    load_config.cache_clear()
    audition._index_date.cache_clear()


def take_of(client: Any, **request: Any) -> dict:
    """One take of the line in human-male under its saved settings, unless told."""
    from tools.config import load_config

    saved = VoiceCatalog(load_config()).resolve("human-male").settings
    body = {
        "text": LINE,
        "voice": "human-male",
        "exaggeration": [saved.exaggeration],
        "cfg_weight": [saved.cfg_weight],
        "tempo": [saved.tempo or 1.0],
        "pitch": [saved.pitch or 0.0],
        "speed": [saved.speed or 1.0],
        "base": "1-accept",
        **request,
    }
    lines = client.post("/api/generate", json=body).text.splitlines()
    events = [json.loads(line) for line in lines]
    start = next(e for e in events if e["event"] == "start")
    take = next(e for e in events if e["event"] == "take")
    return {"session": start["session"], **take}


def test_a_take_of_the_line_pins_and_unpins(pin_studio: Any) -> None:
    from tools.config import load_config

    client = pin_studio.client
    take = take_of(client, seed=777)
    assert take["base"] == "1-accept" and take["line_voice"] == "human-male"
    assert take["spoken"] == LINE  # the line's own text, not cleaned a second time
    text = (pin_studio.toml / "lines.toml").read_text(encoding="utf-8")
    response = client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    assert response.status_code == 200, response.text
    load_config.cache_clear()
    pin = load_config(pin_studio.toml).lines.root["1-accept"]
    assert (pin.seed, pin.chunk_chars, pin.same_seed) == (777, 300, False)
    assert pin.voice == "human-male"  # the voice the take was read in
    written = (pin_studio.toml / "lines.toml").read_text(encoding="utf-8")
    # one inline table under [lines], and every comment of the file kept
    assert '"1-accept" = {seed = 777' in written or "1-accept = {seed = 777" in written
    assert [c for c in text.splitlines() if c.lstrip().startswith("#")] == [
        c for c in written.splitlines() if c.lstrip().startswith("#")
    ]
    # the preview reports the pinned chunk length for the line
    preview = client.post(
        "/api/chunks", json={"text": LINE, "chars": 200, "base": "1-accept"}
    ).json()
    assert (preview["line_chars"], preview["from"]) == (300, "pin")
    assert preview["chunks"] == [LINE]
    assert client.delete("/api/pin-seed/1-accept").status_code == 200
    load_config.cache_clear()
    assert "1-accept" not in load_config(pin_studio.toml).lines.root


def test_a_pin_is_refused_for_a_take_the_pack_would_not_draw(pin_studio: Any) -> None:
    client = pin_studio.client

    def pin(take: dict) -> Any:
        return client.post(
            "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
        )

    # other settings than the saved ones
    response = pin(take_of(client, exaggeration=[1.7]))
    assert response.status_code == 400 and "settings" in response.json()["detail"]
    # other words than the line's own
    response = pin(take_of(client, text=LINE + " Now!"))
    assert response.status_code == 400 and "words" in response.json()["detail"]
    # a take of free text names no line
    response = pin(take_of(client, base=None))
    assert response.status_code == 400 and "names no line" in response.json()["detail"]
    # a take of the line read in another voice than the line's
    response = pin(take_of(client, voice="human-female"))
    assert response.status_code == 400 and "read in" in response.json()["detail"]
    # a line that plays as parts
    pin_studio.variant.parts = [
        ("npc", "Bring me"),
        ("narrator", "Sigh"),
        ("npc", "it."),
    ]
    response = pin(take_of(client))
    assert response.status_code == 400 and "parts" in response.json()["detail"]


def test_a_seed_asks_for_one_take(pin_studio: Any) -> None:
    response = pin_studio.client.post(
        "/api/generate",
        json={
            "text": LINE,
            "voice": "human-male",
            "exaggeration": [0.5],
            "cfg_weight": [0.5],
            "takes": 2,
            "seed": 5,
        },
    )
    assert response.status_code == 400 and "one take" in response.json()["detail"]


def test_the_chunk_preview_cuts_as_the_generator_does(pin_studio: Any) -> None:
    text = SENTENCE * 6
    preview = pin_studio.client.post(
        "/api/chunks", json={"text": text, "chars": 300}
    ).json()
    assert preview["chunks"] == chunk(preview["spoken"], 300)
    assert len(preview["chunks"]) == 2 and preview["line_chars"] is None


def test_a_generated_files_entry_keeps_its_seed() -> None:
    from tools.generate import index_record

    catalog, plain = pinned_catalog()
    own = target(LINE, catalog, base="2-accept")  # no pin on this one
    take = RenderedTake("audio", 4.0, 123, [ChunkMark(0.0, 4.0, LINE, 9, 1)])
    record = index_record(4.0, own, take, 300)
    assert record == {
        "d": 4.0,
        "v": "human-male",
        "t": plain.fingerprint("human-male", LINE),
        "s": 123,
        "c": 300,
    }
    assert index_record(4.0, own, take, 420, same_seed=True)["same"] == 1
    # a probe or a run without a take records what it always did
    assert set(index_record(4.0, own, None, 300)) == {"d", "v", "t"}


def test_a_pinned_progress_text_outside_a_progress_run_is_not_called_gone() -> None:
    from tools.config import Lines, load_config
    from tools.generate import pin_report

    config = load_config()
    pin = {
        "seed": 1,
        "chunk_chars": 300,
        "same_seed": False,
        "heard": "x",
        "spoken": "y",
    }
    lines = Lines.model_validate({"8190-progress": pin})
    catalog = VoiceCatalog(config.model_copy(update={"lines": lines}))
    (note,) = pin_report([], catalog, include_progress=False)
    assert "--progress" in note and "no such line" not in note
    (note,) = pin_report([], catalog, include_progress=True)
    assert "no such line" in note


def test_the_cached_config_follows_an_edit_made_outside_the_page(
    pin_studio: Any,
) -> None:
    import os

    studio = pin_studio.studio
    assert "1-accept" not in studio.config_cached().lines.root
    lines = pin_studio.toml / "lines.toml"
    pin = '"1-accept" = {seed = 5, chunk_chars = 300, same_seed = false, heard = "x", spoken = "y"}\n'
    text = lines.read_text(encoding="utf-8").replace("[lines]\n", "[lines]\n" + pin, 1)
    lines.write_text(text, encoding="utf-8")
    stamp = lines.stat().st_mtime_ns + 1_000_000_000
    os.utime(lines, ns=(stamp, stamp))
    assert studio.config_cached().lines.root["1-accept"].seed == 5


def test_a_pin_decides_the_one_speaker_part_that_says_the_whole_line() -> None:
    from tools.generate import pinnable_part

    catalog, plain = pinned_catalog()
    sob = SimpleNamespace(text=LINE, parts=[("narrator", "Sob"), ("npc", LINE)])
    assert pinnable_part(sob) == 2
    # the speech split by narration: no one take is what players hear
    split = SimpleNamespace(
        text=LINE, parts=[("npc", "Bring me"), ("narrator", "Sigh"), ("npc", "it.")]
    )
    assert pinnable_part(split) is None
    assert pinnable_part(SimpleNamespace(text=LINE, parts=[])) is None
    # the part follows the line's pin; the narrator's part does not
    part = target(LINE, catalog, base="1-p2-accept", pin_key="1-accept")
    assert part.live_pin is not None and "seed=42" in part.fingerprint
    assert target("Sob", catalog, base="1-p1-accept").live_pin is None
    assert target(LINE, catalog, base="1-p2-accept").fingerprint == plain.fingerprint(
        "human-male", LINE
    )


def test_a_take_of_a_line_with_a_narrated_sob_pins(pin_studio: Any) -> None:
    from tools.config import load_config

    pin_studio.variant.parts = [("narrator", "Sob"), ("npc", LINE)]
    take = take_of(pin_studio.client)
    response = pin_studio.client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    assert response.status_code == 200, response.text
    load_config.cache_clear()
    assert "1-accept" in load_config(pin_studio.toml).lines.root


def test_a_bare_sound_is_left_unsaid_and_the_speech_joins_up() -> None:
    from tools.config import Pronunciations
    from tools.textclean import clean, is_bare_sound, segments

    assert (
        is_bare_sound("snort") and is_bare_sound("Cough cough") and is_bare_sound("hic")
    )
    assert is_bare_sound("snort snort") and is_bare_sound("cough, cough, cough...")
    assert is_bare_sound("belches") and is_bare_sound("cries") and is_bare_sound("sobs")
    assert not is_bare_sound("Galgar wipes his brow.") and not is_bare_sound("...")
    # Mangletooth's turn-in for Tribes at War, quest 878 (#1301): one piece of speech
    none = Pronunciations({})
    text = "Yes, yes... filled with joy because finally <snort> the Bristleback know."
    assert segments(text, pronunciations=none) == [
        ("npc", "Yes, yes... filled with joy because finally the Bristleback know.")
    ]
    # and so in the narrator's own reading of the whole line, which keeps the rest
    assert clean(text, keep_stage_directions=True, pronunciations=none) == (
        "Yes, yes... filled with joy because finally the Bristleback know."
    )
    assert clean(
        "Thank you. <Galgar wipes his brow.> Now go.",
        keep_stage_directions=True,
        pronunciations=none,
    ) == ("Thank you. Galgar wipes his brow. Now go.")
    # a direction written as prose is still the narrator's
    assert segments(
        "Thank you. <Galgar wipes his brow.> Now go.", pronunciations=none
    ) == [
        ("npc", "Thank you."),
        ("narrator", "Galgar wipes his brow."),
        ("npc", "Now go."),
    ]


# --- every file keeps the seed that drew it -------------------------------------


def test_kept_seed_reads_what_drew_the_file() -> None:
    from tools.generate import KeptSeed, kept_seed

    assert kept_seed({"d": 4.0, "v": "x", "t": "k", "s": 123, "c": 300}) == KeptSeed(
        123, 300, False, False
    )
    assert kept_seed({"d": 4.0, "s": 5, "c": 420, "same": 1, "cap": 1}) == KeptSeed(
        5, 420, True, True
    )
    early = kept_seed({"d": 4.0, "s": 5})  # before `c`: the default length
    assert early is not None and early.chunk_chars == 300
    # a file from before seeds, a probe, a legacy bare duration, a seed torch rejects
    assert kept_seed({"d": 4.0, "v": "x", "t": "k"}) is None
    assert kept_seed({"d": 4.0, "v": None}) is None
    assert kept_seed(4.0) is None
    assert kept_seed({"d": 4.0, "s": -1}) is None
    assert kept_seed({"d": 4.0, "s": 2**31}) is None
    assert kept_seed({"d": 4.0, "s": "123"}) is None


def test_a_file_is_drawn_from_its_pin_then_its_kept_seed_then_anew() -> None:
    from tools.generate import SeedChoice, seed_for

    catalog, _ = pinned_catalog(chunk_chars=420, same_seed=True)
    pinned = target(LINE, catalog)
    own = target(LINE, catalog, base="2-accept")
    kept = {"d": 4.0, "v": "human-male", "t": "k", "s": 77, "c": 300}
    # the pin wins, whatever the index says
    assert seed_for(pinned, kept, catalog) == SeedChoice(42, 420, True, "pinned")
    # the seed that drew the file replays, with its chunk length and same-seed flag
    assert seed_for(own, kept, catalog) == SeedChoice(77, 300, False, "kept")
    assert seed_for(own, {**kept, "c": 200, "same": 1}, catalog) == SeedChoice(
        77, 200, True, "kept"
    )
    new = SeedChoice(None, 300, False, "new")
    # a reroll, a take that ran into the cap, a file from before seeds, no file
    assert seed_for(own, kept, catalog, reroll=True) == new
    assert seed_for(own, {**kept, "cap": 1}, catalog) == new
    assert seed_for(own, {"d": 4.0, "v": "human-male", "t": "k"}, catalog) == new
    assert seed_for(own, None, catalog) == new
    # a pinned take is never redone for the cap
    assert seed_for(pinned, {**kept, "cap": 1}, catalog).origin == "pinned"


def test_a_take_that_stayed_capped_is_flagged_and_drawn_anew() -> None:
    from tools.generate import index_record

    catalog, _ = pinned_catalog()
    own = target(LINE * 8, catalog, base="2-accept")  # several chunks
    assert len(chunk(own.text)) > 1
    capped = RenderedTake(
        "audio",
        50.0,
        9,
        [
            ChunkMark(0.0, 40.0, LINE, 9, 3, capped=True),
            ChunkMark(40.35, 50.0, LINE, 9, 1),
        ],
    )
    record = index_record(50.0, own, capped, 300)
    assert record["cap"] == 1 and record["s"] == 9
    # over 40 s and in several chunks, the duration test alone would never see it
    assert ran_into_cap(record, own, catalog)
    assert not ran_into_cap({**record, "cap": 0}, own, catalog)
    clean = RenderedTake("audio", 50.0, 9, [ChunkMark(0.0, 50.0, LINE, 9, 1)])
    assert "cap" not in index_record(50.0, own, clean, 300)
    # capped takes in a row are counted, and after CAP_RETRIES the file is left as
    # it is and listed, rather than drawn every night for a text that never ends
    from tools.generate import CAP_RETRIES, given_up, seed_for

    again = index_record(50.0, own, capped, 300, previous=record)
    assert again["cap"] == 2 and not given_up(again)
    clean_record = index_record(50.0, own, clean, 300)
    assert index_record(50.0, own, capped, 300, previous=clean_record)["cap"] == 1
    last = {**record, "cap": CAP_RETRIES}
    assert not ran_into_cap(last, own, catalog) and given_up(last)
    assert seed_for(own, last, catalog).origin == "kept"  # a rebuild replays it
    assert seed_for(own, last, catalog, reroll=True).origin == "new"


def test_an_older_entry_is_judged_at_the_chunk_length_that_drew_it() -> None:
    from tools.config import load_config

    catalog = VoiceCatalog(load_config())
    text = LINE * 7  # one chunk at 500, several at 300
    assert len(chunk(text, 500)) == 1 and len(chunk(text, 300)) > 1
    own = target(text, catalog, base="2-accept")
    settings = catalog.resolve("human-male").settings
    d = 40.0 / ((settings.tempo or 1.0) * (settings.speed or 1.0))
    assert ran_into_cap({"d": d, "s": 1, "c": 500}, own, catalog)
    assert not ran_into_cap({"d": d, "s": 1, "c": 300}, own, catalog)


def test_a_seeded_record_outranks_a_seedless_one() -> None:
    from tools.generate import index_rank

    seeded = {"d": 4.0, "v": "x", "t": "k", "s": 1, "c": 300}
    assert index_rank(seeded) > index_rank({"d": 4.0, "v": "x", "t": "k"})
    assert index_rank({"d": 4.0, "v": "x", "t": "k"}) > index_rank(
        {"d": 4.0, "v": None}
    )
    assert index_rank(seeded) > index_rank(4.0)


def test_each_file_is_saved_as_it_is_drawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import generate
    from tools.generate import _exit_on_signal, draw_target

    monkeypatch.setattr(generate, "SOUND_INDEX", tmp_path / "sound_index.json")
    catalog, plain = pinned_catalog()
    own = target(LINE, catalog, base="2-accept")

    class FakeSynth:
        last_take: RenderedTake | None = None

        def speak(self, text: str, voice: str, out: Path, **kwargs: Any) -> float:
            seed = kwargs["seed"] if kwargs["seed"] is not None else 555
            self.last_take = RenderedTake(
                "audio", 2.0, seed, [ChunkMark(0.0, 2.0, text, seed, 1)]
            )
            return 2.0

    synth: Any = FakeSynth()
    sound_index: dict = {"2-accept": {"d": 4.0, "v": "human-male", "t": "k", "s": 77}}
    dirty: set[str] = set()
    duration, take, choice, missing = draw_target(
        synth, own, sound_index, catalog, dirty
    )
    assert take is not None
    assert (duration, take.seed, choice.origin, missing) == (2.0, 77, "kept", True)
    on_disk = json.loads((tmp_path / "sound_index.json").read_text())
    assert on_disk["2-accept"] == {
        "d": 2.0,
        "v": "human-male",
        "t": plain.fingerprint("human-male", LINE),
        "s": 77,
        "c": 300,
    }
    assert not dirty  # nothing left for a later save to lose
    # a reroll draws anew and the new seed is what the index then keeps
    _, take, choice, _ = draw_target(
        synth, own, sound_index, catalog, dirty, reroll=True
    )
    assert take is not None and (take.seed, choice.origin) == (555, "new")
    assert (
        json.loads((tmp_path / "sound_index.json").read_text())["2-accept"]["s"] == 555
    )
    # the nightly's SIGTERM unwinds to the loop's final save instead of killing
    with pytest.raises(SystemExit) as stop:
        _exit_on_signal(15, None)
    assert stop.value.code == 143


def test_a_pin_holds_only_in_the_voice_it_was_pinned_in() -> None:
    catalog, _ = pinned_catalog(voice="human-male")
    assert target(LINE, catalog).live_pin is not None
    catalog, _ = pinned_catalog(voice="human-male-guard")
    assert target(LINE, catalog).live_pin is None
    catalog, _ = pinned_catalog()  # a pin from before the field holds as before
    assert target(LINE, catalog).live_pin is not None


def test_the_run_says_which_term_moved_from_under_a_pin() -> None:
    from tools.generate import pin_report

    _, plain = pinned_catalog()
    fp = plain.fingerprint("human-male", LINE)
    line = SimpleNamespace(base="1-accept", text=LINE, parts=[])
    item: Any = SimpleNamespace(voice="human-male", variants=lambda: [line])
    heard = fp + ("," if "+" in fp else "+") + "reference=spirithealer-female"
    catalog, _ = pinned_catalog(heard=heard)
    (note,) = pin_report([item], catalog)
    assert "heard with reference=spirithealer-female, now none; text unchanged" in note
    assert "keeps the seed that drew its file" in note
    catalog, _ = pinned_catalog(voice="human-male-guard")
    (note,) = pin_report([item], catalog)
    assert "pinned in human-male-guard, the line is read in human-male" in note
    changed = SimpleNamespace(base="1-accept", text=LINE + " Now.", parts=[])
    moved: Any = SimpleNamespace(voice="human-male", variants=lambda: [changed])
    catalog, _ = pinned_catalog()
    assert "(text changed)" in pin_report([moved], catalog)[0]
    # given the index, a live pin the pack's file is not yet drawn from is said so
    assert (
        pin_report([item], catalog, sound_index={"1-accept": {"s": 42, "c": 300}}) == []
    )
    assert "not yet drawn" in pin_report([item], catalog, sound_index={})[0]
    assert (
        "not yet drawn"
        in pin_report([item], catalog, sound_index={"1-accept": {"s": 7}})[0]
    )


def test_reroll_names_what_it_rerolls(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import generate
    from tools.generate import line_of, main

    with pytest.raises(SystemExit) as refused:
        main(["--reroll", "--dry-run"])
    assert refused.value.code == 2  # argparse's usage error
    # a --line nothing in the run matches is said, not silently nothing to do
    monkeypatch.setattr(generate, "ensure_picked_references", lambda *a, **k: set())
    monkeypatch.setattr(
        generate,
        "load_sources",
        lambda: {"quests": {"1": {}}, "gossip": {}, "books": {}, "npcs": {}},
    )
    monkeypatch.setattr(generate, "load_items", lambda *a, **k: [])
    with pytest.raises(SystemExit) as missing:
        main(["--reroll", "--line", "999999-accept", "--dry-run"])
    assert "no such line" in str(missing.value)
    assert line_of("1241-p2-complete") == "1241-complete"
    assert line_of("m-170-p1-accept") == "m-170-accept"
    assert line_of("2492-p1-aa6f2374") == "2492-aa6f2374"
    assert line_of("016d57ec-p3-page") == "016d57ec-page"
    assert line_of("1241-complete") == "1241-complete"


def test_a_book_page_can_be_pinned() -> None:
    from tools.config import Lines

    fields = {
        "seed": 1,
        "chunk_chars": 300,
        "same_seed": False,
        "heard": "x",
        "spoken": "y",
    }
    assert "016d57ec-page" in Lines.model_validate({"016d57ec-page": fields}).root
    for key in ("1241-p2-complete", "Sex/m/1-accept", "Narrator/x/1-accept"):
        with pytest.raises(ValueError):
            Lines.model_validate({key: fields})


def test_the_page_shows_the_index_seed_beside_what_is_playing(pin_studio: Any) -> None:
    from tools import audition
    from tools.config import load_config

    client, index = pin_studio.client, pin_studio.index
    rows = [
        audition.LineRow(
            "1-accept", "Quests", "t", "s", "human-male", LINE, LINE, 1, "x", "1"
        )
    ]
    pin_studio.studio.rows = lambda: rows
    index.write_text(
        json.dumps(
            {"1-accept": {"d": 2.0, "v": "human-male", "t": "k", "s": 77, "c": 300}}
        )
    )
    (row,) = client.get("/api/lines", params={"q": "t"}).json()["rows"]
    assert row["kept"] == {
        "seed": 77,
        "chunk_chars": 300,
        "same_seed": False,
        "capped": False,
    }
    assert row["index_date"] == "2026-10-12" and row["pin"] is None
    assert not row["drawn"]  # no file here at all: the seed is the builder's
    # the preview names the index seed's chunk length while there is no pin
    preview = client.post(
        "/api/chunks", json={"text": LINE, "chars": 200, "base": "1-accept"}
    ).json()
    assert (preview["line_chars"], preview["from"]) == (300, "kept")
    # pinned lines are listed by voice, lapsed ones among them, and no others
    assert client.get("/api/lines", params={"pinned": 1}).json()["rows"] == []
    take = take_of(client, seed=77)
    client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    (listed,) = client.get("/api/lines", params={"pinned": 1}).json()["rows"]
    assert listed["base"] == "1-accept" and listed["pin"]["live"]
    # the knobs and clone-from the pin was heard under, defaults filled in, so the
    # page can mark a run option that holds the pin's value
    saved = VoiceCatalog(load_config(pin_studio.toml)).resolve("human-male").settings
    assert listed["pin"]["heard"] == {
        "exaggeration": saved.exaggeration,
        "cfg_weight": saved.cfg_weight,
        "tempo": saved.tempo,
        "pitch": saved.pitch,
        "speed": saved.speed,
        "reference": saved.reference,
    }
    assert (
        client.get("/api/lines", params={"pinned": 1, "voice": "orc-male"}).json()[
            "rows"
        ]
        == []
    )
    pin_studio.variant.text = (
        LINE + " Now."
    )  # the text moved: the pin lapsed, still listed
    (listed,) = client.get("/api/lines", params={"pinned": 1}).json()["rows"]
    assert not listed["pin"]["live"]
    pin_studio.variant.text = LINE
    client.delete("/api/pin-seed/1-accept")
    # a file from before seeds, and a capped take, are said so
    index.write_text(json.dumps({"1-accept": {"d": 2.0, "v": "human-male", "t": "k"}}))
    (row,) = client.get("/api/lines", params={"q": "t"}).json()["rows"]
    assert row["kept"] is None
    index.write_text(json.dumps({"1-accept": {"d": 2.0, "s": 77, "c": 300, "cap": 1}}))
    (row,) = client.get("/api/lines", params={"q": "t"}).json()["rows"]
    assert row["kept"]["capped"]
    # and the index seed cannot be pinned back while it is being rerolled
    take = take_of(client, seed=77)
    response = client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    assert response.status_code == 400 and "token cap" in response.json()["detail"]
    # nor a take whose own chunk stayed capped: a pinned take is never drawn again
    take = take_of(client, seed=79)
    sidecar = (audition.AUDITION_DIR / take["session"] / take["name"]).with_suffix(
        ".json"
    )
    about = json.loads(sidecar.read_text(encoding="utf-8"))
    about["chunks"][0]["capped"] = True
    sidecar.write_text(json.dumps(about), encoding="utf-8")
    response = client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    assert response.status_code == 400 and "babble" in response.json()["detail"]
    assert (
        client.post(
            "/api/pin-seed",
            json={
                "session": take_of(client, seed=78)["session"],
                "name": take_of(client, seed=78)["name"],
            },
        ).status_code
        == 200
    )


def test_a_pin_heard_under_uncommitted_settings_is_said_so(
    pin_studio: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import audition

    client = pin_studio.client
    # the check compares the repository's configs/, which the fixture's copy stands for
    monkeypatch.setattr(audition, "CONFIG_DIR", pin_studio.toml)
    take = take_of(client, seed=5)
    body = {"session": take["session"], "name": take["name"]}
    assert "warning" not in client.post("/api/pin-seed", json=body).json()
    # the pin's own file is always edited; anything else is a setting the builder lacks
    pin_studio.git["status"] = " M configs/lines.toml\n"
    assert "warning" not in client.post("/api/pin-seed", json=body).json()
    pin_studio.git["status"] = " M configs/lines.toml\n M configs/tts.toml\n"
    warning = client.post("/api/pin-seed", json=body).json()["warning"]
    assert "configs/tts.toml" in warning and "commit them" in warning
    # git failing is said, not passed off as clean
    pin_studio.git["status"] = None
    assert "git failed" in client.post("/api/pin-seed", json=body).json()["warning"]
    # another configs/ than the repository's has nothing to compare against
    monkeypatch.setattr(audition, "CONFIG_DIR", pin_studio.toml / "elsewhere")
    assert "warning" not in client.post("/api/pin-seed", json=body).json()


def test_a_retune_says_which_pins_it_lapses(
    pin_studio: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.config import load_config

    client, studio = pin_studio.client, pin_studio.studio
    monkeypatch.setattr(studio, "forget_corpus", lambda: None, raising=False)
    take = take_of(client, seed=5)
    client.post(
        "/api/pin-seed", json={"session": take["session"], "name": take["name"]}
    )
    load_config.cache_clear()
    # the page learns which lines hold under the voice's settings, and its clip's
    assert studio.live_pins()["human-male"] == ["1-accept"]
    saved = VoiceCatalog(load_config(pin_studio.toml)).resolve("human-male").settings
    body = {
        "voice": "human-male",
        "exaggeration": saved.exaggeration,
        "cfg_weight": saved.cfg_weight,
        "tempo": saved.tempo or 1.0,
        "pitch": saved.pitch or 0.0,
        "speed": saved.speed or 1.0,
        "reference": saved.reference,
    }
    # keeping the same settings lapses nothing
    assert "warning" not in client.post("/api/keep-tuning", json=body).json()
    # a retune does, and says so by name
    response = client.post(
        "/api/keep-tuning", json={**body, "exaggeration": saved.exaggeration + 0.2}
    ).json()
    assert "1-accept" in response["warning"] and "index seed" in response["warning"]
    load_config.cache_clear()
    assert studio.live_pins() == {}
