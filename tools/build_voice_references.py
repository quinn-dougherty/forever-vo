"""Builds reference clips for voice cloning straight from the client's own audio.

chatterbox reads only the first 6 s (t3) and 10 s (s3gen) of a reference and then
continues it, so the head of a clip is the voice and everything past 10 s only nudges
an averaged embedding. Each clip is therefore composed rather than pooled:

  head  a few seconds of the race's spoken emote lines (EmotesTextSound: JOKE, FLIRT),
        a different slice per voice so archetypes of one race do not collapse together
  tail  that speaker's own NPC greetings, starting inside the 10 s window so the
        archetype colours the voice

Blizzard casts every creature display with an NPCSounds set - a young one, a warrior,
an elder - and wowdata.voice_for_npc resolves through it, so the outputs are:

    tools/voices/<race>-<gender>.wav          the race's dominant archetype
    tools/voices/<race>-<gender>-s<set>.wav   the other archetypes NPCs are cast with
    tools/voices/npc-<displayID>.wav          sets used by <= 3 displays: one character

    ./tools/run.sh tools/build_voice_references.py            # all voices
    ./tools/run.sh tools/build_voice_references.py tauren     # one race
    ./tools/run.sh tools/build_voice_references.py --named    # the named-NPC clips

Everything is fetched through wago.tools by FileDataID, so no local CASC extraction is
needed. Raw clips are kept under tools/voices/raw/.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from tools.config import (
    BETA_BUILD,
    GENDER_DICT,
    RACE_DICT,
    RETAIL_BUILD,
    VOICES_DIR,
    load_config,
)
from tools.wowdata import (
    archetype_names,
    archetype_of,
    base_voice,
    dominant_sound_set,
    fetch_file,
    is_archetype,
    load_db2,
    sound_set_displays,
)

RAW_DIR = VOICES_DIR / "raw"
TARGET_SECONDS = 20.0
MAX_CLIP_SECONDS = 15.0   # long enough for a spoken emote line; a bark is 1-3 s
MAX_FILES_PER_VOICE = 40   # download cap per voice; greeting kits repeat a lot
T3_SECONDS = 6.0           # chatterbox's t3 encoder reads this much of a reference
S3GEN_SECONDS = 10.0       # s3gen this much; past it a clip only nudges an averaged embedding
# ffmpeg concat stops consuming inputs at the first file it cannot open, writes
# everything before it, and exits 0 - so a truncated clip in the middle of a list
# yields a short reference that sounds fine and reports success. Compare the output
# against the inputs to catch it; loudnorm and the container change move the total a
# little, so allow a small slack rather than demanding an exact match.
CONCAT_SLACK_SECONDS = 0.3


def files_by_kit(build: str = BETA_BUILD) -> dict[int, list[int]]:
    result: dict[int, list[int]] = defaultdict(list)
    for entry in load_db2("SoundKitEntry", build).values():
        result[int(entry["SoundKitID"])].append(int(entry["FileDataID"]))
    return result


# A race has only so many spoken emote lines to hand out - scourge-male has 11 -
# and every archetype minted takes a slice of them, so a long tail of archetypes
# leaves the last ones with a 3 s head spliced from short clips. Better a few good
# personalities than many thin ones: keep the archetypes the most NPCs are cast
# with, and let everyone else fall back to the race voice. By rank rather than an
# absolute count, because races differ by an order of magnitude - human-male's third
# set has 245 displays where tauren-female's has 26.
MAX_ARCHETYPES_PER_VOICE = 3
MIN_ARCHETYPE_DISPLAYS = 5   # below this it is one character, and npc-<displayID> covers it

# Emotes whose recordings are spoken sentences. The rest of a race's emote set is
# wordless - crying, whistling, a chicken impression - and sorting by length puts
# exactly those at the head of a reference, where they become the voice.
SPEECH_EMOTES = {"JOKE", "FLIRT"}



def emote_speech_fdids(build: str = BETA_BUILD) -> dict[str, list[int]]:
    """voice name -> FileDataIDs of that race's spoken emote lines.

    NPC greeting kits are barks: "Hello there!" in one to three seconds, disjoint,
    with nothing about how the speaker forms a sentence. EmotesTextSound has the
    same actors telling jokes and flirting - four to thirteen seconds of connected
    speech - which is what chatterbox needs to clone prosody rather than timbre
    alone. VocalUISounds adds the spoken UI errors ("I can't carry any more").
    """
    kits = files_by_kit(build)
    names = {int(key): row["Name"] for key, row in load_db2("EmotesText", build).items()}
    voices: dict[str, list[int]] = defaultdict(list)
    for row in load_db2("EmotesTextSound", build).values():
        race = RACE_DICT.get(int(row.get("RaceID") or 0))
        gender = GENDER_DICT.get(int(row.get("SexID") or 0))
        if not race or not gender:
            continue
        if names.get(int(row.get("EmotesTextID") or 0)) not in SPEECH_EMOTES:
            continue
        for fdid in kits.get(int(row.get("SoundID") or 0), []):
            if fdid not in voices[f"{race}-{gender}"]:
                voices[f"{race}-{gender}"].append(fdid)
    return dict(voices)


@functools.cache
def borrowed_speech_voices() -> frozenset[str]:
    """Voices with no spoken emotes in this client that retail can supply (#41)."""
    own = set(emote_speech_fdids())
    return frozenset(v for v in emote_speech_fdids(RETAIL_BUILD)
                     if v not in own and v in sound_set_displays())


def speech_pool() -> dict[str, list[tuple[int, str]]]:
    """voice name -> [(FileDataID, build)] of connected speech to head its clips with.

    This client's own recordings wherever it has any. Blood elves and goblins became
    playable in Burning Crusade and Cataclysm, so Classic 1.x never recorded a /joke or
    /flirt for them and their references were barks alone - 2.8 s for goblin-male, and
    it read as a gnome (#41). Retail has those takes, and the diff is clean: for every
    race this client already covers, retail holds the same 1.x files and fewer of them
    after consolidation (troll-male 10 here against 6 there, dwarf-male 13 against 10),
    so borrowing is worth nothing there and risks a re-record. Only a race with nothing
    of its own reaches for it.
    """
    pool: dict[str, list[tuple[int, str]]] = {
        voice: [(fdid, BETA_BUILD) for fdid in fdids]
        for voice, fdids in emote_speech_fdids().items()
    }
    retail = emote_speech_fdids(RETAIL_BUILD)
    for voice in sorted(borrowed_speech_voices()):
        pool[voice] = [(fdid, RETAIL_BUILD) for fdid in retail[voice]]
    if borrowed_speech_voices():
        print(f"no spoken emotes in this client for {', '.join(sorted(borrowed_speech_voices()))}; "
              f"heading those with retail {RETAIL_BUILD}")
    return pool


def set_fdids(sound_id: int) -> list[int]:
    """The hello and goodbye files of one NPCSounds set (SoundID_2 is "pissed", _3 ack)."""
    kits = files_by_kit()
    npc_sounds = load_db2("NPCSounds")
    fdids: list[int] = []
    for col in ("SoundID_0", "SoundID_1"):
        for fdid in kits.get(int(npc_sounds[sound_id].get(col) or 0), []):
            if fdid not in fdids:
                fdids.append(fdid)
    return fdids


# How a clip is put together. A race with spoken emotes of its own always gets
# SPEECH_AND_BARKS, which won twelve-race listening. A race that has to borrow retail
# speech (#41) is different, and goblin-male settled it by ear: the plain voice is
# POOLED - every set's greetings, as the base pack was built before this branch - and
# the archetypes take the other two, so the race keeps three personalities instead of
# one. Narrowing the plain voice to the dominant set is what left goblin-male a 2.8 s
# reference that read as a gnome; with nothing to head it, a race needs every bark it
# has.
POOLED = "pooled-barks"
SPEECH_AND_BARKS = "speech-and-barks"
SPEECH_ONLY = "speech-only"
BORROWED_ARCHETYPE_RECIPES = (SPEECH_AND_BARKS, SPEECH_ONLY)


def pooled_set_fdids(voice: str) -> list[int]:
    """Every greeting file of every set this race and gender is cast with."""
    fdids: list[int] = []
    for sound_id, _ in sound_set_displays().get(voice, Counter()).most_common():
        for fdid in set_fdids(sound_id):
            if fdid not in fdids:
                fdids.append(fdid)
    return fdids


def npc_greeting_fdids() -> dict[str, list[int]]:
    """voice name -> FileDataIDs, from the archetype most of that race is cast with.

    Blizzard gives each race and gender several NPCSounds sets - a young one, a
    warrior, an elder - and each creature display names the one it was cast with.
    Pooling them all and taking the longest clips put a *rare* archetype at the head
    of nearly every reference: chatterbox reads only the first 6 s (t3) and 10 s
    (s3gen) of a clip, and one-off character sets have the longest lines, so seven of
    23 voices were cloned from a set used by a single creature display (eleven if you
    count heads whose sound kit is shared with a rare set). tauren-female came from
    set 172, an elder with one display in the game, whose clips are two of the first
    9.9 s and three of the six; set 70, cast for 65 displays, never reached the part
    of the clip that matters.

    So the plain <race>-<gender> clip now comes from the archetype the most NPCs
    actually use, and archetype_fdids() builds the others alongside it.
    """
    voices: dict[str, list[int]] = {}
    for voice in sound_set_displays():
        if voice in borrowed_speech_voices():
            fdids = pooled_set_fdids(voice)   # nothing to head it with; it needs them all
        else:
            sound_id = dominant_sound_set(voice)
            fdids = set_fdids(sound_id) if sound_id else []
        if fdids:
            voices[voice] = fdids
    return voices


def archetype_fdids() -> dict[str, list[int]]:
    """`<race>-<gender>-s<set>` -> files, one entry per archetype a race really uses.

    wowdata.voice_for_npc prefers these when a speaker's display names the set, so
    an elder is read by the elder's voice instead of lending her delivery to every
    young NPC of her race.
    """
    voices: dict[str, list[int]] = {}
    for voice, counts in sound_set_displays().items():
        kept = [(sid, n) for sid, n in counts.most_common(MAX_ARCHETYPES_PER_VOICE)
                if n >= MIN_ARCHETYPE_DISPLAYS]
        if voice in borrowed_speech_voices():
            # its plain clip is pooled from every set, so minting the dominant one
            # again would be a second personality built from the same recipe
            kept = [(sid, n) for sid, n in kept if sid != dominant_sound_set(voice)]
        for sound_id, _ in kept:
            fdids = set_fdids(sound_id)
            if fdids:
                voices[archetype_names(voice)[sound_id]] = fdids
    return voices


NAMED_MAX_DISPLAYS = 3   # a greeting kit shared by this few models belongs to a named NPC


def named_npc_fdids() -> dict[str, list[int]]:
    """voice name npc-<displayID> -> greeting FileDataIDs for NPCs with their own recorded lines
    (Varimathras, Thrall, Sylvanas, ...). Race voices come from kits shared by many models.

    Hello and goodbye only. SoundID_2 is the "pissed" kit - what the NPC says when you
    click it repeatedly - and it used to come in beside them: 41 files across 11 sets,
    reaching 20 of the 91 named clips. An angry take is the wrong thing to clone a quest
    giver from wherever it lands, and candidates are sorted longest first, which ranks a
    3.5 s shout above most greetings (median 1.2 s).
    """
    kits = files_by_kit()
    npc_sounds = load_db2("NPCSounds")
    displays_by_sound: dict[int, list[int]] = defaultdict(list)
    for display_id, row in load_db2("CreatureDisplayInfo").items():
        sound_id = int(row.get("NPCSoundID") or 0)
        if sound_id and sound_id in npc_sounds:
            displays_by_sound[sound_id].append(display_id)
    voices: dict[str, list[int]] = {}
    for sound_id, displays in displays_by_sound.items():
        if len(displays) > NAMED_MAX_DISPLAYS:
            continue
        fdids: list[int] = []
        for col in ("SoundID_0", "SoundID_1"):  # hello, goodbye; _2 is "pissed", _3 ack
            for fdid in kits.get(int(npc_sounds[sound_id].get(col) or 0), []):
                if fdid not in fdids:
                    fdids.append(fdid)
        if fdids:
            for display_id in displays:
                voices[f"npc-{display_id}"] = fdids
    return voices


def skyborne_fdids() -> dict[str, list[int]]:
    """VocalUISounds.NormalSoundID_0 is the male kit, _1 the female kit."""
    kits = files_by_kit()
    voices: dict[str, list[int]] = defaultdict(list)
    for row in load_db2("VocalUISounds").values():
        if int(row["RaceID"]) not in (95, 96):
            continue
        for gender, col in (("male", "NormalSoundID_0"), ("female", "NormalSoundID_1")):
            for fdid in kits.get(int(row.get(col) or 0), []):
                if fdid not in voices[f"skyborne-{gender}"]:
                    voices[f"skyborne-{gender}"].append(fdid)
    return voices


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out or 0)


SPEECH_HEAD_CLIPS = 2        # splices inside the window cost delivery; keep the head to a few
# An archetype is only worth minting if it can get a real head. Below the bar the
# speaker falls back to the race voice, which takes the longest slice and so always
# clears it. The bar was 4.5 s, read off twelve-race listening where every voice at
# 4.8 s or more was preferred and every voice at 4.0 s or less was worse - but those
# figures came from the head measurement this file used to make, which counted the
# first bark behind a one-clip head as speech. Against the corrected number the bar
# dropped 17 of 44 archetypes, every dwarf and every scourge-male among them. So it is
# set where it has actually been tested: scourge-male-s82 at 3.5 s (Dark Cleric Beryl,
# Sarvis) was rated a usable personality beside the 5.5 s race voice, once better.
# Raise it again only against fresh listening, and on real head seconds.
ARCHETYPE_MIN_HEAD = 3.5
# s3gen reads 10 s. Spending it all on shared emote speech left every archetype
# sounding like the race's emote actor and nothing else; half of it leaves room for
# the speaker's own clips inside the window, which is what makes one archetype
# different from another. 5.5 s measured best in the sweep: a 0.071 gap between two
# scourge archetypes against 0.104 for barks alone, without the flat bark delivery.
SPEECH_HEAD_SECONDS = 5.5
# A SPEECH_ONLY clip has no barks to fall back on, so it fills the s3gen window
# with speech rather than stopping at the head budget.
SPEECH_ONLY_SECONDS = S3GEN_SECONDS


def speech_heads(sources: dict[str, list[int]]) -> dict[str, tuple[list[tuple[int, str]], int]]:
    """{voice: (that race's spoken emote files, which slice of them this voice leads with)}.

    Barks clone a voice but not a delivery, and a head of spoken emote lines fixes
    that - but handing every archetype of a race the same speech collapses them into
    one voice (all seven scourge-male clips came out byte-identical). So each
    archetype leads with different lines from the same actor: same race, same timbre,
    a different performance continued. Measured on scourge-male, three archetypes
    built this way sit 0.978 within a personality against 0.892 across them, where
    sharing the head left a gap of 0.014.

    The slices are handed out most-used archetype first, so the longest lines go to
    the voices the most creature displays use: a head of one long clip beats two, and
    two beat three - the voice rated "robotic" in testing was the one whose head was
    three short clips spliced together.
    """
    speech = speech_pool()
    ranks = archetype_ranks(sources)
    by_voice: dict[str, list[str]] = defaultdict(list)
    for name in sources:
        # A POOLED clip uses no speech, so it must not hold a slice: while the plain
        # goblin voice took slot 0 it kept the two longest jokes for a clip that never
        # reads them, and both goblin archetypes fell under ARCHETYPE_MIN_HEAD and were
        # deleted - the race lost two of the three personalities it was given.
        if recipe_for(name, ranks) != POOLED:
            by_voice[base_voice(name)].append(name)
    heads: dict[str, tuple[list[tuple[int, str]], int]] = {}
    for race_gender, names in by_voice.items():
        pool = speech.get(race_gender)
        if not pool:
            continue        # no spoken emotes for this race; it stays bark-led
        counts: Counter[int] = sound_set_displays().get(race_gender) or Counter()

        def displays(name: str, race_gender: str = race_gender, counts: Counter[int] = counts) -> int:
            if name == race_gender:
                # The plain voice picks first because it speaks for the most NPCs on
                # this client: GetDisplayInfo never returns anything on Forever
                # (issue #2), so speakers resolve through modelFileID, which names no
                # display and therefore no archetype. Ranking it by the displays of
                # its sub-threshold sets put it last of four and left tauren-female
                # heading with a 2.0 s clip.
                return 1 << 30
            found = archetype_of(name)
            return counts.get(found[1], 0) if found else 0

        if len(pool) < len(names) * SPEECH_HEAD_CLIPS:
            print(f"{race_gender}: {len(pool)} spoken lines for {len(names)} voices, "
                  f"so some share a head and will sound alike")
        for index, name in enumerate(sorted(names, key=displays, reverse=True)):
            heads[name] = (pool, index)
    return heads


def recipe_for(voice: str, archetype_rank: dict[str, int]) -> str:
    """Which composition this clip uses. See the recipe constants."""
    base = base_voice(voice)
    if base not in borrowed_speech_voices():
        return SPEECH_AND_BARKS
    if voice == base:
        return POOLED
    rank = archetype_rank.get(voice, 0)
    return BORROWED_ARCHETYPE_RECIPES[rank % len(BORROWED_ARCHETYPE_RECIPES)]


def archetype_ranks(sources: dict[str, list[int]]) -> dict[str, int]:
    """Archetype clips numbered 0, 1, ... within their race, most displays first."""
    by_race: dict[str, list[str]] = defaultdict(list)
    for name in sources:
        if is_archetype(name):
            by_race[base_voice(name)].append(name)
    ranks: dict[str, int] = {}
    for race, names in by_race.items():
        counts = sound_set_displays().get(race) or Counter()
        def displays_of(name: str, counts: Counter[int] = counts) -> int:
            found = archetype_of(name)
            return -counts.get(found[1], 0) if found else 0

        ordered = sorted(names, key=displays_of)
        ranks.update({name: i for i, name in enumerate(ordered)})
    return ranks


def concat_line(path: Path) -> str:
    """One line of an ffmpeg concat list. A quote in a path would end the argument."""
    return "file '{}'\n".format(str(path.resolve()).replace("'", r"'\''"))


def write_concat(voice: str, paths: list[Path], name: str = "concat.txt",
                 list_dir: Path | None = None) -> Path:
    """The ffmpeg concat list for one clip, kept beside that voice's raw audio.

    The picked and the automatic builders use different names: both wrote concat.txt and
    clobbered each other, which also cost the file its one other use - a record of what
    actually went into the wav sitting next to it.
    """
    list_file = (list_dir or RAW_DIR / voice) / name
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("".join(concat_line(p) for p in paths), encoding="utf-8")
    return list_file


def concat_to_wav(voice: str, paths: list[Path], list_name: str = "concat.txt", *,
                  list_dir: Path | None = None, out: Path | None = None) -> Path:
    """Concatenates `paths` in order into tools/voices/<voice>.wav, or raises.

    Every input is probed first and the result is measured against their total, because
    ffmpeg reports success for a concat it truncated (see CONCAT_SLACK_SECONDS). Built to
    a temporary file and renamed, so a failure leaves the previous reference in place
    rather than replacing it with a shorter one.
    """
    if not paths:
        raise ValueError(f"{voice}: nothing to concatenate")
    expected = 0.0
    for path in paths:
        try:
            seconds = duration(path)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"{voice}: {path} will not probe, refusing to build from it") from e
        if seconds <= 0:
            raise RuntimeError(f"{voice}: {path} is empty, refusing to build from it")
        expected += seconds
    list_file = write_concat(voice, paths, list_name, list_dir)
    out = out or VOICES_DIR / f"{voice}.wav"
    tmp = out.with_suffix(f".wav.{os.getpid()}.part")
    try:
        subprocess.run(
            # -f wav because the temporary name ends in .part, which ffmpeg cannot
            # infer a container from
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-ac", "1", "-ar", "24000", "-af", "loudnorm", "-f", "wav", str(tmp)],
            check=True,
        )
        got = duration(tmp)
        if abs(got - expected) > max(CONCAT_SLACK_SECONDS, expected * 0.02):
            raise RuntimeError(f"{voice}: concatenated {got:.1f}s of an expected {expected:.1f}s; "
                               f"one of {len(paths)} inputs did not make it in "
                               f"(see {list_file})")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def build_picked_reference(voice: str, paths: list[Path]) -> Path:
    """One reference from clips chosen by ear, in the order given.

    Nothing here sorts, filters or budgets: the order is the choice. build_reference is
    deliberately not reused - it sorts by duration, drops everything outside 0.8-8.0 s
    (which is every spoken emote line), stops at TARGET_SECONDS, and deletes a `-s<N>`
    clip whose measured head is under ARCHETYPE_MIN_HEAD, which a plain concat never has.
    """
    out = concat_to_wav(voice, paths, "picked.txt")
    offset = 0.0
    t3 = s3gen = 0
    for path in paths:
        if offset < T3_SECONDS:
            t3 += 1
        if offset < S3GEN_SECONDS:
            s3gen += 1
        offset += duration(path)
    print(f"{out.name}: {len(paths)} clips picked by ear, {offset:.1f}s "
          f"({t3} reaching the {T3_SECONDS:.0f}s t3 window, {s3gen} the {S3GEN_SECONDS:.0f}s s3gen one)")
    return out


def build_reference(voice: str, files: list[Path], head: list[Path] | None = None,
                    rotation: int = 0, recipe: str = SPEECH_AND_BARKS) -> Path | None:
    """Composes one reference clip. See the recipe constants for the three shapes.

    chatterbox reads the first 6 s (t3) and 10 s (s3gen) of a reference and continues
    it, so the head is the voice and everything past 10 s only nudges an averaged
    embedding. Under SPEECH_AND_BARKS the head is spoken emote lines - this voice's
    slice of them - and the tail is the speaker's own greetings, which start inside the
    10 s window so the archetype colours the voice rather than only the averaged
    embedding. POOLED has no speech to head with and fills the window with greetings
    from every set the race is cast with; SPEECH_ONLY is the other extreme and lets no
    barks into the window at all, which matters where the barks and the borrowed speech
    are different actors (goblins: Classic NPC recordings against the Cataclysm
    playable voice).
    """
    def usable(paths: list[Path]) -> list[tuple[float, Path]]:
        # The old 8 s ceiling was there to skip long barks, and it also threw away
        # every spoken emote line - scourge-male's 12.8 s joke, the longest Forsaken
        # speech in the client, was excluded from the reference it should have headed
        return sorted(((duration(p), p) for p in paths), reverse=True)

    chosen, total, head_seconds = [], 0.0, 0.0
    if head and recipe == SPEECH_ONLY:
        pool = [(d, p) for d, p in usable(head) if 0.8 <= d <= MAX_CLIP_SECONDS]
        offset = (rotation * SPEECH_HEAD_CLIPS) % len(pool) if pool else 0
        for d, p in pool[offset:] + pool[:offset]:
            if total >= SPEECH_ONLY_SECONDS:
                break
            chosen.append(p)
            total += d
            head_seconds += d
    elif head and recipe == SPEECH_AND_BARKS:
        # Only clips that can fit the head budget, and the rotation is applied to
        # those. Rotating the unfiltered pool looked right and was not: every voice
        # skipped the same too-long clips and landed on the same largest one that
        # fit, so 13 archetypes came out byte-identical to their plain voice.
        pool = [(d, p) for d, p in usable(head) if 0.8 <= d <= SPEECH_HEAD_SECONDS]
        offset = (rotation * SPEECH_HEAD_CLIPS) % len(pool) if pool else 0
        for d, p in pool[offset:] + pool[:offset]:
            # Fit under the cap rather than stopping once past it. Checking after the
            # append let two long clips make a 20 s "head", which pushed the voice's
            # own clips past TARGET_SECONDS and out of the window the model reads -
            # the archetype then contributed nothing to how it sounded.
            if len(chosen) >= SPEECH_HEAD_CLIPS or total + d > SPEECH_HEAD_SECONDS:
                continue
            chosen.append(p)
            total += d
            head_seconds += d
    tail = [] if recipe == SPEECH_ONLY else usable(files)
    for d, p in tail:
        if not (0.8 <= d <= 8.0):       # skip grunts and long barks
            continue
        chosen.append(p)
        total += d
        if total >= TARGET_SECONDS:
            break
    if not chosen or (total < 4.0 and voice.startswith("npc-")):
        print(f"{voice}: not enough usable audio ({total:.1f}s)")
        return None
    out = concat_to_wav(voice, chosen)
    # Measured while the head is assembled, not as chosen[:SPEECH_HEAD_CLIPS] afterwards:
    # a voice whose budget fits only one speech clip would otherwise count the first
    # bark behind it as speech - goblin-male reported a 6.8 s head that was 5.4 s of
    # joke and 1.4 s of "Hey there!" - and a thin archetype could clear the gate on it.
    if is_archetype(voice) and head_seconds < ARCHETYPE_MIN_HEAD:
        # Not enough speech to hold a delivery; wowdata.archetype_voice falls back to
        # the race voice when the clip is absent, and that one has the longest head
        out.unlink(missing_ok=True)
        print(f"{out.name}: head only {head_seconds:.1f}s, leaving these NPCs on the race voice")
        return None
    print(f"{out.name}: {len(chosen)} clips, {total:.1f}s"
          + (f", head {head_seconds:.1f}s" if head else " (no speech available)"))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    wanted = set(argv)
    named_only = "--named" in wanted
    wanted.discard("--named")
    sources = {} if named_only else npc_greeting_fdids()
    if not named_only:
        sources.update(archetype_fdids())
        heads = speech_heads(sources)
        for voice, fdids in skyborne_fdids().items():
            # The Skyborne have no spoken emotes, but VocalUISounds is the same thing
            # for them: recorded sentences rather than barks. It goes in as the head,
            # leaving the greeting kit (set 3776 has 78 displays) as the tail. It used
            # to be prepended to sources instead, where MAX_FILES_PER_VOICE truncation
            # dropped every greeting file and a third of the VocalUI ones.
            heads.setdefault(voice, ([(fdid, BETA_BUILD) for fdid in fdids], 0))
            sources.setdefault(voice, [])
    else:
        heads = {}
    if named_only or "npc" in wanted:
        sources.update(named_npc_fdids())
        wanted.discard("npc")

    # Clips chosen by ear win over every recipe, in both modes: --named rebuilds each
    # npc-<displayID> unconditionally, and one of those is a production reference
    # ([tts.voices.dwarf-male] clones from npc-3597), so a pick has to survive it.
    picked = {name: entry for name, entry in load_config().voices.sources.items() if entry.clips}
    if named_only:
        picked = {name: entry for name, entry in picked.items() if name.startswith("npc-")}

    if not wanted and not named_only:
        # A pick for a set no recipe mints is absent from `sources`, and
        # wowdata.archetype_voice casts on the file being there, so sweeping it away
        # would silently recast those NPCs onto the race voice.
        keep = set(sources) | set(picked) | {p.stem for p in VOICES_DIR.glob("npc-*.wav")}
        for stale in sorted(VOICES_DIR.glob("*.wav")):
            if is_archetype(stale.stem) and stale.stem not in keep:
                # wowdata.archetype_voice resolves on existence alone, so a clip left
                # behind by an earlier set of constants still casts NPCs
                print(f"removing stale {stale.name}")
                stale.unlink()
    ranks = archetype_ranks(sources)
    for voice in sorted(set(sources) | set(picked)):
        race = voice.split("-")[0]
        if wanted and race not in wanted and voice not in wanted:
            continue
        entry = picked.get(voice)
        if entry:
            chosen = []
            for fdid in entry.clips:
                try:
                    chosen.append(fetch_file(fdid, RAW_DIR / voice / f"{fdid}.ogg",
                                             build=entry.build or BETA_BUILD))
                except FileNotFoundError as e:
                    print("skip:", e)
            if len(chosen) != len(entry.clips):
                # Refuse rather than quietly build a shorter clip than was chosen
                print(f"{voice}: {len(entry.clips) - len(chosen)} picked clip(s) missing, not rebuilt")
                continue
            build_picked_reference(voice, chosen)
            continue
        folder = RAW_DIR / voice
        paths = []
        for fdid in sources[voice][:MAX_FILES_PER_VOICE]:
            try:
                paths.append(fetch_file(fdid, folder / f"{fdid}.ogg"))
            except FileNotFoundError as e:
                print("skip:", e)
        head_paths = []
        pool, rotation = heads.get(voice, ([], 0))
        for fdid, build in pool[:MAX_FILES_PER_VOICE]:
            try:
                head_paths.append(fetch_file(fdid, RAW_DIR / base_voice(voice) / f"{fdid}.ogg",
                                             build=build))
            except FileNotFoundError as e:
                print("skip:", e)
        recipe = recipe_for(voice, ranks)
        print(f"{voice}: {len(paths)} clips downloaded, {len(head_paths)} speech clips, {recipe}")
        if paths or head_paths:
            build_reference(voice, paths, head_paths, rotation, recipe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
