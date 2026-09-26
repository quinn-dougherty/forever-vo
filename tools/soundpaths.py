"""Blizzard's own name for each NPC voice set, from the CASC file paths.

`NPCSounds` rows carry four sound-kit columns and no name, so an archetype could only
be called `<race>-<gender>-s<row id>` - stable, and meaningless to read. The recordings
themselves are filed under names that say exactly what the set is:

    set 36  sound/creature/dwarfmalegrimnpc/dwarfmalegrimnpcgreeting01.ogg      -> grim
    set 37  sound/creature/dwarfmaleguardnpc/dwarfmaleguardnpcgreeting01.ogg    -> guard
    set 50  sound/creature/humanmalewarriornpc/humanmalewarriornpcgreeting01.ogg -> warrior
    set 172 sound/creature/magathagrimtotem/magathagrimtotemgreeting01.ogg      -> a character

Those paths are not in the client tables - CASC stores files by FileDataID - so they
come from the community listfile, fetched once and boiled down to the sets this pack
uses. The result is committed (`tools/data/sound_sets.json`), because a name decides a
voice's file name, and a file name decides which lines regenerate: a map that drifted
under us would restage a voice for nothing.

    ./tools/run.sh fvo-soundpaths            # show what each set is called
    ./tools/run.sh fvo-soundpaths --refresh  # re-derive from the listfile (~100 MB once)
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import requests

from tools.config import DATA_DIR, GENDER_DICT, RACE_DICT

SOUND_SETS = DATA_DIR / "sound_sets.json"
LISTFILE_URL = ("https://github.com/wowdev/wow-listfile/releases/latest/download/"
                "community-listfile.csv")
# The folder is named for the kit, the file for the line inside it; both carry the same
# stem, so the folder alone is enough and is stable across the numbered takes.
SOUND_PATH = re.compile(r"^sound/creature/([^/]+)/", re.IGNORECASE)


def kit_first_file() -> dict[int, int]:
    """NPCSounds row -> the FileDataID of its first greeting, which names the folder."""
    from tools.wowdata import load_db2  # only --refresh needs the client tables
    kits: dict[int, list[int]] = {}
    for row in load_db2("SoundKitEntry").values():
        kits.setdefault(int(row["SoundKitID"]), []).append(int(row["FileDataID"]))
    first: dict[int, int] = {}
    for sound_id, row in load_db2("NPCSounds").items():
        for col in ("SoundID_0", "SoundID_1", "SoundID_2", "SoundID_3"):
            files = kits.get(int(row.get(col) or 0))
            if files:
                first[int(sound_id)] = min(files)
                break
    return first


def refresh() -> dict[str, str]:
    """Downloads the listfile and keeps only the folder of each NPCSounds set."""
    # several NPCSounds rows can name the same sound kit - sets 36 and 156 are both
    # dwarfmalegrimnpc - so one FileDataID answers for a list of sets, not one
    wanted: dict[int, list[int]] = {}
    for sound_id, fdid in kit_first_file().items():
        wanted.setdefault(fdid, []).append(sound_id)
    found: dict[str, str] = {}
    print(f"looking up {len(wanted)} sound sets in the community listfile", file=sys.stderr)
    with requests.get(LISTFILE_URL, stream=True, timeout=600) as response:
        response.raise_for_status()
        for raw in response.iter_lines():
            if not raw:
                continue
            fdid, _, path = raw.decode("utf-8", "replace").partition(";")
            if not fdid.isdigit():
                continue
            sets = wanted.get(int(fdid))
            if not sets:
                continue
            match = SOUND_PATH.match(path)
            if match:
                for sound_id in sets:
                    found[str(sound_id)] = match.group(1).lower()
    SOUND_SETS.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(found)} of {len(wanted)} sets named, written to {SOUND_SETS}", file=sys.stderr)
    return found


def folders() -> dict[int, str]:
    if not SOUND_SETS.exists():
        return {}
    return {int(k): v for k, v in json.loads(SOUND_SETS.read_text(encoding="utf-8")).items()}


def descriptor(sound_id: int, voice: str) -> str | None:
    """`guard` for dwarf-male's set 37, or None when the folder says nothing new.

    The folder is the race, the gender and then what kind of NPC it is - so the useful
    part is what is left once the voice's own name is taken off the front and the "npc"
    marker off the back. A folder that is not built that way belongs to one character
    (magathagrimtotem), and a character is not an archetype of its race.
    """
    folder = folders().get(sound_id)
    if not folder:
        return None
    race, _, gender = voice.partition("-")
    for prefix in (f"{race}{gender}", f"{_client_race(race)}{gender}"):
        if prefix and folder.startswith(prefix):
            rest = folder[len(prefix):]
            rest = re.sub(r"npc$", "", rest).strip("_-")
            return rest or "standard"
    return None


def _client_race(race: str) -> str:
    """The pack's race name is not always Blizzard's: scourge is filed as undead."""
    return {"scourge": "undead", "nightelf": "nightelf"}.get(race, race)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-derive from the listfile")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.refresh:
        refresh()
    from tools.wowdata import load_db2
    known = folders()
    if not known:
        print("no map yet; run with --refresh")
        return 1
    extra = load_db2("CreatureDisplayInfoExtra")
    voices: dict[int, str] = {}
    for row in load_db2("CreatureDisplayInfo").values():
        sound_id = int(row.get("NPCSoundID") or 0)
        extra_id = int(row.get("ExtendedDisplayInfoID") or 0)
        if not sound_id or extra_id not in extra or sound_id in voices:
            continue
        race = RACE_DICT.get(int(extra[extra_id]["DisplayRaceID"]))
        gender = GENDER_DICT.get(int(extra[extra_id]["DisplaySexID"]))
        if race and gender:
            voices[sound_id] = f"{race}-{gender}"
    print(f"{'set':>6}  {'voice':<18} {'folder':<32} descriptor")
    for sound_id in sorted(known):
        voice = voices.get(sound_id, "")
        name = descriptor(sound_id, voice) if voice else None
        print(f"{sound_id:>6}  {voice:<18} {known[sound_id]:<32} {name or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
