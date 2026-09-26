"""Measures voices instead of guessing at them.

Two questions keep coming up in the voice issues, and both were being answered by
ear from single samples (#18, #26). One generation is not evidence: the mel decoder
draws fresh noise per call (chatterbox s3gen/flow_matching.py sets rand_noise = None),
so two takes of one line in one voice can differ more than two settings do.

    ./tools/run.sh fvo-voicecheck rate                    # how fast is each voice?
    ./tools/run.sh fvo-voicecheck rate --voice orc-female
    ./tools/run.sh fvo-voicecheck takes --voice orc-female --takes 5

"rate" needs no GPU and no generation: it reads the durations already in
sound_index.json and the texts behind them, so it reports on every line that has
ever been voiced. That answers "too slow" (#26) over thousands of files rather than
one.

"takes" generates the same lines repeatedly under one or more settings and reports
speaker similarity against the reference clip, with the spread across takes, so a
claim like "0.75/0.3 is more Scottish" comes with error bars.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

from tools.config import DATA_DIR, SOUND_INDEX, VOICES_DIR, load_config
from tools.textclean import clean, has_gender_branch, split_gender
from tools.wowdata import base_voice

CAPTURE_JSON = DATA_DIR / "capture.json"


def load_texts(alternates: list[str]) -> dict[str, str]:
    """Sound index key -> the words actually spoken in it.

    Three shapes have to match `generate.index_key` and `Item.variants`, or whole
    classes of line fall out of the sample: gendered variants carry an m-/f- prefix
    (gossip branches too, not just quests), and an alternate narrator recording is
    keyed `Narrator/<voice>/<base>`. Missing the last one dropped every alternate -
    6,655 of 23,067 entries - and with them most of the text for the five voices in
    the narrator alternates, which is exactly where "too slow" (#26) was measured.

    Narrator lines keep their stage directions because the narrator reads them aloud
    (`generate.Item.variants` passes keep_stage_directions), so counting them without
    is counting fewer words than the audio contains.
    """
    sources: dict[str, str] = {}

    def add(base: str, text: str) -> None:
        sources[base] = text
        for voice in alternates:
            sources[f"Narrator/{voice}/{base}"] = text

    files = [DATA_DIR / "bulk" / "classic.json", DATA_DIR / "bulk" / "questcache.json", CAPTURE_JSON]
    for path in files:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("quests", {}).values():
            text = entry.get("text")
            if not text:
                continue
            base = f"{entry.get('questID')}-{entry.get('event')}"
            cleaned = clean(text, keep_stage_directions=True)
            if has_gender_branch(cleaned):
                male, female = split_gender(cleaned)
                add(f"m-{base}", male)
                add(f"f-{base}", female)
            else:
                add(base, cleaned)
        for key, entry in data.get("gossip", {}).items():
            text = entry.get("text")
            if not text:
                continue
            # capture keys are "<speaker>|<hash>"; files are "<speaker>-<hash>"
            base = str(key).replace("|", "-")
            cleaned = clean(text, keep_stage_directions=True)
            if has_gender_branch(cleaned):
                male, female = split_gender(cleaned)
                add(f"m-{base}", male)
                add(f"f-{base}", female)
            else:
                add(base, cleaned)
    return sources


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", text))


def cmd_rate(args) -> int:
    index = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
    texts = load_texts(load_config().voices.narrator_alternates)
    by_voice: dict[str, list[float]] = {}
    missing = 0
    for name, entry in index.items():
        if not isinstance(entry, dict):
            continue
        voice, duration = entry.get("v"), entry.get("d")
        if not voice or not duration:
            continue
        text = texts.get(name)
        if text is None:
            missing += 1
            continue
        count = words(text)
        if count < 8 or duration < 1.0:      # too short to time reliably
            continue
        by_voice.setdefault(voice, []).append(count / duration * 60.0)
    # "% vs all voices" and the line count have to be taken before filtering, or
    # --voice compares a voice against itself and reports its own sample as the total
    every = [r for rates in by_voice.values() for r in rates]
    overall = statistics.median(every) if every else 0
    if args.voice:
        # a voice's archetypes are the same voice for this purpose
        by_voice = {v: r for v, r in by_voice.items()
                    if v in args.voice or base_voice(v) in args.voice}
    rows = sorted(by_voice.items(), key=lambda kv: statistics.median(kv[1]))
    print(f"{'voice':<18} {'lines':>6} {'words/min':>10} {'spread (p10-p90)':>20}")
    for voice, rates in rows:
        rates.sort()
        p10 = rates[int(len(rates) * 0.10)]
        p90 = rates[int(len(rates) * 0.90)]
        flag = ""
        if overall:
            delta = (statistics.median(rates) - overall) / overall * 100
            if abs(delta) >= 5:
                flag = f"  {delta:+.0f}% vs all voices"
        print(f"{voice:<18} {len(rates):>6} {statistics.median(rates):>10.1f} {f'{p10:.0f} - {p90:.0f}':>20}{flag}")
    if overall:
        print(f"\nmedian across every voiced line: {overall:.1f} words/min "
              f"({len(every)} lines measured of {len(index)} index entries; {missing} had no text, "
              f"the rest were too short to time)")
    return 0


def speaker_similarity(model, paths: list[Path], reference: Path) -> list[float]:
    """Cosine similarity of each clip's speaker embedding to the reference's.

    Uses the CAMPPlus encoder chatterbox already loads for voice cloning, so this
    measures the same thing the model conditions on.
    """
    import librosa
    import torch
    import torch.nn.functional as F

    encoder = model.s3gen.speaker_encoder
    sr = 16000

    def embed(path: Path):
        wav, _ = librosa.load(str(path), sr=sr)
        tensor = torch.from_numpy(wav).float().unsqueeze(0).to(model.device)
        with torch.no_grad():
            return encoder.inference(tensor).squeeze(0)

    ref = embed(reference)
    return [float(F.cosine_similarity(embed(p), ref, dim=-1)) for p in paths]


def cmd_takes(args) -> int:
    import time

    import perth
    import torchaudio
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker  # ty: ignore[invalid-assignment]
    from chatterbox.tts import ChatterboxTTS

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = LINES[: args.lines]
    settings = [tuple(float(x) for x in s.split("/")) for s in args.settings.split(",")]

    model = ChatterboxTTS.from_pretrained(device=args.device)
    for voice in args.voice:
        reference = VOICES_DIR / f"{voice}.wav"
        if not reference.exists():
            print(f"{voice}: no reference clip at {reference}")
            continue
        print(f"\n{voice}  (reference {reference.name})")
        for exaggeration, cfg_weight in settings:
            made, rates = [], []
            for li, text in enumerate(lines):
                for take in range(args.takes):
                    path = out_dir / f"{voice}-e{exaggeration}-c{cfg_weight}-l{li}-t{take}.mp3"
                    if not path.exists():
                        started = time.time()
                        wav = model.generate(text, exaggeration=exaggeration,
                                             cfg_weight=cfg_weight, audio_prompt_path=str(reference)).cpu()
                        torchaudio.save(str(path), wav, model.sr, format="mp3")
                        seconds = wav.shape[-1] / model.sr
                        print(f"   line {li} take {take}: {seconds:4.1f}s in {time.time()-started:3.0f}s", flush=True)
                    else:
                        import subprocess
                        seconds = float(subprocess.run(
                            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, check=False).stdout.strip() or 0)
                    made.append(path)
                    rates.append(words(text) / seconds * 60.0)
            sims = speaker_similarity(model, made, reference)
            print(f"  exaggeration {exaggeration}, cfg_weight {cfg_weight}: "
                  f"similarity {statistics.mean(sims):.3f} +/- {statistics.pstdev(sims):.3f} "
                  f"(worst {min(sims):.3f}), {statistics.median(rates):.0f} words/min over {len(made)} takes")
    return 0


# Neutral quest-giver prose, long enough to time and to let the voice settle.
LINES = [
    ("The road south is not safe for travellers, and the guards will not go with you. "
     "Take what supplies you can carry and keep to the high ground until morning."),
    ("I have seen what they do to those they capture. Do not let them take you alive, "
     "and do not come back here without the proof I asked for."),
    ("My family has worked this land for three generations. I will not abandon it now, "
     "whatever the elders decide at the next council."),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    rate = sub.add_parser("rate", help="words per minute per voice, from files already voiced (no GPU)")
    rate.add_argument("--voice", action="append", help="restrict to these voices")
    rate.set_defaults(func=cmd_rate)

    takes = sub.add_parser("takes", help="generate repeated takes and measure speaker similarity")
    takes.add_argument("--voice", action="append", required=True)
    takes.add_argument("--takes", type=int, default=5, help="takes per line per setting (default 5)")
    takes.add_argument("--lines", type=int, default=2, help="how many sample lines (default 2, max 3)")
    takes.add_argument("--settings", default="0.45/0.5",
                       help="comma separated exaggeration/cfg_weight pairs, e.g. 0.45/0.5,0.75/0.3")
    takes.add_argument("--device", default="cuda")
    takes.add_argument("--out", default="tools/data/voicecheck", help="where to write the takes")
    takes.set_defaults(func=cmd_takes)

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
