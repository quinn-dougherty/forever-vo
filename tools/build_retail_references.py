"""Builds cloning references from retail World of Warcraft creature voice-over.

Some speakers have no recorded voice in the Forever client and none in Warcraft
III either (see build_wc3_references.py). Retail has them: kul_tiran_kid is a
child's voice set with separate boy and girl lines, which is what the Human and
Orcish Orphans need. Output lands in tools/voices/<species>-<gender>.wav, and
wowdata.species_voice picks it up by name when a speaker's model names that
species.

    python tools/build_retail_references.py --dry-run
    python tools/build_retail_references.py --only humanmalekid

Files are fetched by FileDataID through wago.tools, so no CASC extraction is
needed. Every entry in SOURCES must be a name generate.Synth.reference_for or
species_voice will look for: the pack has no per-race voice variants, so a
"skyborne-female-civilian.wav" would be written and never read.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import requests

from tools.build_voice_references import concat_to_wav
from tools.config import DATA_DIR, VOICES_DIR, WAGO_BASE

RETAIL_BUILD = "12.1.0.69875"
LISTFILE = DATA_DIR / "verified-listfile.csv"
LISTFILE_URL = (
    "https://github.com/wowdev/wow-listfile/releases/download/"
    "202609211740/verified-listfile.csv"
)
RAW_DIR = VOICES_DIR / "raw-retail"
CANDIDATES = 14        # files downloaded per voice before picking
TARGET_SECONDS = 20.0
MIN_CLIP, MAX_CLIP = 2.0, 12.0   # a spoken line, not a grunt or a whole cinematic

# A creature's vo_ files are mostly combat: swings, crits, shouts and death cries.
# Those pass a duration filter happily and then get cloned, which is how a quest
# giver ends up sounding like she is screaming down a pipe. Several generic
# npc_-_* voice sets are 100% combat and contain no speech at all, so a source
# that yields nothing after this filter must be replaced, not re-filtered.
COMBAT = re.compile(
    r"_(attack|attackcrit|battleshout|death|aggro|wound|pissed|flee|taunt|jump|"
    r"fall|gasp|grunt|pain|spell|cast)\w*_?\d*\.ogg$", re.IGNORECASE)

# voice name -> creature directory under sound/creature/, or (directory, pattern)
# when one folder holds more than one voice.
#
# Choosing a source: the generic npc_-_* sets look ideal by name and are often
# a trap, every line a swing, a shout or a death cry with no speech at all; the
# light/heavy/caster suffix is body and armour type, and heavy sets come out
# raspy on a village herbalist; a named character's processed voice (Cho'gall)
# does not generalise. A source that yields no speech after the combat filter
# must be replaced, not re-filtered. Named sets that were tried and talk, for
# whoever adds a per-race variant mechanism one day: nightborne_*_citizen and
# _light, vereesa_windrunner, alleria_windrunner, first_arcanist_thalyssra,
# lady_liadrin, lorthemar, magister_umbric, halduron_brightwing, rokhan,
# voljin, bwonsamdi, princess_talanji, genericdhbloodelf{fe,}male, taelia,
# danath_trollbane. Until then only names the pipeline looks up belong here.
SOURCES: dict[str, str | tuple[str, str]] = {
    # Children. One folder holds both sexes, marked by an _m / _f suffix on the
    # file, so each voice takes only its own - a shared reference makes every boy
    # sound like a girl. species_voice reaches these from the humanmalekid and
    # humanfemalekid models, and the orc children through [voices.species_aliases].
    "humanmalekid-male": ("kul_tiran_kid", r"_m\.ogg$"),
    "humanfemalekid-female": ("kul_tiran_kid", r"_f\.ogg$"),
}


def ensure_listfile() -> Path:
    if LISTFILE.exists():
        return LISTFILE
    LISTFILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading listfile -> {LISTFILE}")
    with requests.get(LISTFILE_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with LISTFILE.open("wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
    return LISTFILE


def fdids_for(listfile: Path, source: str | tuple[str, str]) -> tuple[list[int], int]:
    """(speech FileDataIDs, how many combat lines were rejected).

    `source` is a creature folder, or (folder, pattern) when one folder holds more
    than one voice - kul_tiran_kid carries both sexes, marked _m and _f.
    """
    creature_dir, extra = (source, None) if isinstance(source, str) else source
    pattern = re.compile(
        rf"^(\d+);sound/creature/{re.escape(creature_dir)}/(vo_[^/]*\.ogg)$", re.IGNORECASE
    )
    within = re.compile(extra, re.IGNORECASE) if extra else None
    out, rejected = [], 0
    with listfile.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pattern.match(line.strip())
            if not m:
                continue
            if within and not within.search(m.group(2)):
                continue
            if COMBAT.search(m.group(2)):
                rejected += 1
                continue
            out.append(int(m.group(1)))
    return out, rejected


def fetch(fdid: int, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(f"{WAGO_BASE}/api/casc/{fdid}", params={"version": RETAIL_BUILD}, timeout=180)
    except requests.RequestException as e:
        print(f"    fdid {fdid}: {e}")
        return None
    if r.status_code != 200 or r.headers.get("content-type", "").startswith("application/json"):
        return None
    dest.write_bytes(r.content)
    return dest


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,   # an unreadable file reads as 0.0 below
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def build(label: str, clips: list[Path]) -> Path:
    # Shared so this builder also refuses a concat ffmpeg truncated and called a success
    return concat_to_wav(label, clips, list_dir=RAW_DIR / label)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="resolve FileDataIDs, download nothing")
    ap.add_argument("--only", action="append", help="build just these labels (prefix match)")
    ap.add_argument("--candidates", type=int, default=CANDIDATES,
                    help="files to sample per voice; raise it when a source is mostly short barks")
    ap.add_argument("--force", action="store_true", help="rebuild even if the wav exists")
    args = ap.parse_args(argv)

    listfile = ensure_listfile()
    made = 0
    for label, creature in SOURCES.items():
        if args.only and not any(label.startswith(p) for p in args.only):
            continue
        fdids, rejected = fdids_for(listfile, creature)
        if not fdids:
            print(f"{label:28} NO SPEECH for sound/creature/{creature}/ "
                  f"({rejected} combat lines rejected) - pick another source")
            continue
        print(f"{label:28} {len(fdids):5} speech lines ({rejected} combat rejected) ({creature})")
        if args.dry_run:
            continue
        if (VOICES_DIR / f"{label}.wav").exists() and not args.force:
            print("    already built")
            continue
        clips: list[tuple[float, Path]] = []
        for fdid in fdids[:args.candidates]:
            p = fetch(fdid, RAW_DIR / label / f"{fdid}.ogg")
            if p:
                d = duration(p)
                if MIN_CLIP <= d <= MAX_CLIP:
                    clips.append((d, p))
        clips.sort(reverse=True)
        chosen, total = [], 0.0
        for d, p in clips:
            chosen.append(p)
            total += d
            if total >= TARGET_SECONDS:
                break
        if total < 8.0:
            print(f"    only {total:.1f}s usable, skipping")
            continue
        build(label, chosen)
        print(f"    -> {label}.wav  {len(chosen)} clips, {total:.1f}s")
        made += 1
    print(f"\n{made} reference clips written to {VOICES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
