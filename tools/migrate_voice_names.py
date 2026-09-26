"""Moves sound_index entries onto a renamed voice, without regenerating them.

A file is regenerated when the voice it resolves to changes name, which is what makes
a recast NPC pick up its new voice. Renaming a voice looks exactly the same to that
check - and when the rename is only a rename, that is 3,507 files re-rendered from a
reference clip that is byte-for-byte the one they were already made from.

`<race>-<gender>-s<NPCSounds row>` became `<race>-<gender>-<what Blizzard calls it>`:
human-male-s48 is human-male-official, the same set, the same clip, the same audio.
This rewrites the recorded voice of every file made under the old name so the next
run leaves them alone.

    ./tools/run.sh fvo-migrate-voice-names --dry-run
    ./tools/run.sh fvo-migrate-voice-names

It refuses to move an entry whose clip is not there to compare, and it never touches
the fingerprint: if the text or the tuning did change, that check still fires and the
file is regenerated on its own merits.

**Delete this file and its console script once it has run.** It exists for one rename,
`-s<NPCSounds row>` to the name Blizzard gives the set, and the condition it looks for
can only be true once: afterwards it finds nothing and prints so. Keeping it would leave
a migration in the tree that reads as though it might still be needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from tools.config import SOUND_INDEX, VOICES_DIR
from tools.generate import save_sound_index
from tools.wowdata import archetype_names, base_voice

OLD_STYLE = re.compile(r"^(?P<base>.+-(?:male|female))-s(?P<set>\d+)$")


def renames() -> dict[str, str]:
    """Old archetype name -> the name the builder gives that set now."""
    moves: dict[str, str] = {}
    index = json.loads(SOUND_INDEX.read_text(encoding="utf-8")) if SOUND_INDEX.exists() else {}
    seen = {entry["v"] for entry in index.values()
            if isinstance(entry, dict) and isinstance(entry.get("v"), str)}
    for voice in sorted(seen):
        match = OLD_STYLE.fullmatch(voice)
        if not match:
            continue
        current = archetype_names(base_voice(voice)).get(int(match.group("set")))
        if current and current != voice:
            moves[voice] = current
    return moves


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="say what would move and stop")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    moves = renames()
    if not moves:
        print("nothing to move: no entry names a voice that has been renamed")
        return 0

    index = json.loads(SOUND_INDEX.read_text(encoding="utf-8"))
    counted: dict[str, int] = {}
    skipped: dict[str, int] = {}
    dirty: set[str] = set()
    for key, entry in index.items():
        if not isinstance(entry, dict):
            continue
        was = entry.get("v")
        current = moves.get(was) if isinstance(was, str) else None
        if not current or not isinstance(was, str):
            continue
        if not (VOICES_DIR / f"{current}.wav").exists():
            # the renamed clip has to be here, or this is a guess about audio that
            # cannot be checked; leave it and let the voice-change check regenerate
            skipped[was] = skipped.get(was, 0) + 1
            continue
        counted[was] = counted.get(was, 0) + 1
        if not args.dry_run:
            entry["v"] = current
            dirty.add(key)

    width = max(len(name) for name in moves)
    for old in sorted(moves, key=lambda v: -counted.get(v, 0)):
        if counted.get(old) or skipped.get(old):
            note = f"  ({skipped[old]} left alone, no clip)" if skipped.get(old) else ""
            print(f"  {old:<{width}} -> {moves[old]:<{width}}  {counted.get(old, 0):>5} files{note}")
    total = sum(counted.values())
    if args.dry_run:
        print(f"\n{total} files would move to a renamed voice; nothing written")
        return 0
    save_sound_index(index, dirty)
    print(f"\n{total} files moved to a renamed voice; they will not be regenerated for the rename")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
