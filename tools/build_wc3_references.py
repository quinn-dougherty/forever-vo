"""Builds cloning references from Warcraft III unit audio.

Some speakers have no recorded voice anywhere in World of Warcraft. A dryad, a
keeper of the grove, an ogre or a human child has a model and a name but its
sound kit is grunts and roars, so it ends up borrowing a human's voice - or the
narrator's, which is worse. Warcraft III voiced those same units properly, and
a player who owns it has the files locally.

Extract the units folder from the CASC storage first: extract_wc3_units.py does
it from a local Reforged install through CascLib (Ladik's CascView works too),
and this script reads its output folder by default:

    ./tools/run.sh tools/extract_wc3_units.py
    ./tools/run.sh tools/build_wc3_references.py --dry-run
    ./tools/run.sh tools/build_wc3_references.py --only dryad --only ogre
    python tools/build_wc3_references.py --src "C:/Users/you/Downloads/wc3-units"

Writes tools/voices/<species>.wav, which voice_for_npc picks up by name the same
way as a race voice.

Two filters matter. "Pissed" lines are the joke responses to repeated clicking -
in character but played for laughs, and wrong in a quest. Combat calls (attack,
warcry, death) are shouted rather than spoken. What remains - the "what" and
"yes" acknowledgements - is the unit talking normally, which is what a quest
giver should sound like.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from tools.build_voice_references import concat_to_wav
from tools.config import VOICES_DIR

TARGET_SECONDS = 20.0
MIN_TOTAL = 2.5      # some units have only a handful of lines; report rather than refuse
MIN_CLIP, MAX_CLIP = 0.4, 12.0

# A WC3 unit set is what1-4, yes1-4, ready1, warcry1, pissed1-9. The "pissed"
# lines are the joke responses to repeated clicking - and, annoyingly, the longest
# ones, so a naive "take the longest clips" picks nothing but jokes. warcry and
# the death/pain calls are shouted. What is left - what, yes, ready, yesattack -
# is the unit speaking normally, which is what a quest giver needs.
EXCLUDE = re.compile(r"(pissed|warcry|death|die|pain|birth|burn)", re.IGNORECASE)

# species voice name -> WC3 unit folder names (matched as substrings)
SPECIES: dict[str, tuple[str, ...]] = {
    "dryad-female": ("dryad",),
    "keeperofthegrove-male": ("herokeeperofthegrove",),
    "ogre-male": ("ogre",),
    "satyr-male": ("satyr",),
    "banshee-female": ("banshee",),
    "dreadlord-male": ("herodreadlord", "tichondrius", "varimathras"),
    "fleshgolem-male": ("abomination",),
    # Children are NOT here: WC3's villagerkid is five clips totalling 4.1s, and
    # it is one voice for both sexes, so boys came out sounding like girls. Retail
    # has kul_tiran_kid with separate _m and _f line sets - see
    # build_retail_references.py.
    "trolldire-male": ("foresttroll", "icetroll"),
    "naga-female": ("nagasiren", "ladyvashj"),
    "naga-male": ("nagamyrmidon", "nagaroyalguard"),
}


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False).stdout.strip()   # an unreadable file reads as 0.0 below
    try:
        return float(out)
    except ValueError:
        return 0.0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=VOICES_DIR / "raw-wc3" / "units",
                    help="folder of extracted WC3 units audio (default: what extract_wc3_units.py writes)")
    ap.add_argument("--only", action="append", help="build just these species (prefix match)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.src.is_dir():
        print(f"no such folder: {args.src}", file=sys.stderr)
        return 1

    by_folder: dict[str, list[Path]] = {}
    for f in (p for p in args.src.rglob("*") if p.suffix.lower() in (".flac", ".ogg", ".wav", ".mp3")):
        by_folder.setdefault(f.parent.name.lower(), []).append(f)
    print(f"{len(by_folder)} unit folders under {args.src}\n")

    made = 0
    for species, frags in SPECIES.items():
        if args.only and not any(species.startswith(p) for p in args.only):
            continue
        pool: list[Path] = []
        for folder, files in by_folder.items():
            # Prefix, not substring: "satyr" must take satyr and satyrtrickster
            # but not femalesatyr, whose lines are a woman's.
            if any(folder.startswith(fr) for fr in frags):
                pool += files
        usable = []
        for p in pool:
            if EXCLUDE.search(p.name):
                continue
            d = duration(p)
            if MIN_CLIP <= d <= MAX_CLIP:
                usable.append((d, p))
        usable.sort(reverse=True)
        chosen, total = [], 0.0
        for d, p in usable:
            chosen.append(p)
            total += d
            if total >= TARGET_SECONDS:
                break
        if total < MIN_TOTAL:
            print(f"{species:24} only {total:.1f}s usable from {len(pool)} files - skipped")
            continue
        print(f"{species:24} {len(chosen):2} clips, {total:5.1f}s  from {', '.join(sorted({p.parent.name for p in chosen}))}")
        if args.dry_run:
            continue
        # Shared so this builder also refuses a concat ffmpeg truncated and called a success
        concat_to_wav(species, chosen, list_dir=VOICES_DIR / "raw-wc3" / species)
        made += 1

    print(f"\n{made} reference clip(s) written to {VOICES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
