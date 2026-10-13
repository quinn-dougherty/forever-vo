"""Generate a local character voice pack, without changing the shared pack.

Use tools/personal_setup.py to select an isolated CUDA/ROCm environment first.
Only quest and gossip lines containing a real $n/$N placeholder are selected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tools.config import ROOT, load_config
from tools.filelock import exclusive_lock
from tools.generate import (
    Item,
    Synth,
    Target,
    VoiceCatalog,
    load_items,
    load_sources,
    part_name,
    probe_duration,
    rebuild_tables,
    require_filters,
    speaker_int,
    tuning_filters,
)
from tools.luatable import lua_string
from tools.release_pack import transcode
from tools.textclean import is_speakable

SCHEMA = 1
TABLES = ("Quests", "Gossip", "Books", "NPCs", "Narrator")


def character_text(value: str) -> str:
    """A display name/realm or spoken pronunciation, never markup or a path."""
    if (
        not value.strip()
        or len(value) > 100
        or any(ord(c) < 32 or c in "$<>|/\\" for c in value)
    ):
        raise argparse.ArgumentTypeError(
            "use 1–100 characters, without markup or slashes"
        )
    return value.strip()


def folder_name(character: str, realm: str) -> str:
    key = json.dumps([character, realm], ensure_ascii=False).encode()
    return "ForeverVO_Personal_" + hashlib.sha256(key).hexdigest()[:16]


def select_device(torch: Any, requested: str, allow_cpu: bool) -> str:
    """ROCm exposes the CUDA API too. Do not silently fall back from a GPU."""
    if requested not in ("auto", "cuda", "cpu"):
        raise ValueError(f"unsupported device: {requested}")
    if requested == "cpu":
        return "cpu"  # choosing it explicitly is consent to slow generation
    if torch.cuda.is_available():
        return "cuda"
    if requested == "auto" and allow_cpu:
        return "cpu"
    raise SystemExit(
        "No usable CUDA/ROCm device in this environment. Check the driver and "
        "run tools/personal_setup.py for the matching PyTorch build. "
        "CPU generation requires --cpu or --device cpu. Apple MPS is not yet validated."
    )


def targets_for(item: Item) -> list[Target]:
    """Complete audio for one line, including both reader genders and all voices.

    A personal entry shadows a shared entry, so its parts and alternatives must
    be complete before publishing it. Reuse the existing target/path conventions.
    """
    targets = []
    alternates = item.config.voices.narrator_alternates
    for variant in item.variants():
        base, text, parts = variant
        if text:
            if not is_speakable(text):
                return []
            targets.append(Target(item, base, text, item.voice))
            if item.sex_alternate:
                sex, voice = item.sex_alternate
                targets.append(Target(item, base, text, voice, sex=sex))
            targets.extend(
                Target(item, base, text, voice, speaker=speaker)
                for speaker, voice in item.speaker_alternates.items()
                if voice
            )
            if item.is_narrator:
                targets.extend(Target(item, base, text, v, True) for v in alternates)
        for i, (role, words) in enumerate(parts, 1):
            if not is_speakable(words):
                return []
            voice = item.voice if role == "npc" else item.config.voices.narrator
            targets.append(Target(item, part_name(base, i), words, voice))
            if role == "narrator":
                targets.extend(
                    Target(item, part_name(base, i), words, v, True) for v in alternates
                )
    return targets


def personal_items(source: dict, catalog: VoiceCatalog, spoken_name: str) -> list[Item]:
    selected = []
    for item in load_items(source, include_progress=True, catalog=catalog):
        if item.kind == "books" or not re.search(r"\$[nN]", item.raw_text):
            continue
        if item.kind == "gossip" and speaker_int(item.speaker_key) is None:
            continue
        if (
            item.kind == "quests"
            and item.speaker_key is None
            and not item.entry.get("isObject")
        ):
            continue
        item.player_name = spoken_name
        selected.append(item)
    return selected


def signature(target: Target, backend: str, clips: dict[Path, str]) -> str:
    """Private cache: exact spoken text, reference bytes, recipe and backend.

    Shared seed pins describe the generic text and are deliberately not applied.
    """
    clip = target.item.catalog.resolve(target.voice).clip
    if clip and clip not in clips:
        clips[clip] = hashlib.sha256(clip.read_bytes()).hexdigest()
    values = [
        SCHEMA,
        target.text,
        target.voice,
        target.item.catalog.recipe(target.voice),
        clips.get(clip) if clip else None,
        backend,
    ]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def publish(
    pack: Path,
    character: str,
    realm: str,
    items: list[Item],
    index: dict,
    catalog: VoiceCatalog,
) -> None:
    """One atomic Lua file: a restart can never see a mix of table generations."""
    with tempfile.TemporaryDirectory(dir=pack) as tmp:
        data = Path(tmp)
        rebuild_tables(
            items,
            index,
            catalog.config,
            data_dir=data,
            pack_global="select(2, ...)",
            sounds_dir=pack / "Sounds",
            write_index=False,
        )
        fields = {
            "name": pack.name,
            "folder": pack.name,
            "version": "personal-1",
            "character": character,
            "realm": realm,
        }
        body = "local _, pack = ...\n" + "".join(
            f"pack.{key} = {lua_string(value)}\n" for key, value in fields.items()
        )
        body += "pack.priority = 1000\n"
        body += "\n".join(
            (data / f"{name}.lua").read_text(encoding="utf-8") for name in TABLES
        )
        body += "\nif ForeverVO and ForeverVO.RegisterPersonalPack then\n    ForeverVO.RegisterPersonalPack(pack)\nend\n"
        atomic_text(pack / "Personal.lua", body)
    atomic_text(
        pack / f"{pack.name}.toc",
        "## Interface: 16001\n## Title: Forever Voiceover: Personal\n"
        "## Notes: Local character voice pack. Enable character names in ForeverVO's Audio options.\n"
        "## Version: personal-1\n## Dependencies: ForeverVO\n"
        "## X-ForeverVO-Pack: 1\n\nPersonal.lua\n",
    )


def build(args: argparse.Namespace) -> int:
    config = load_config()
    catalog = VoiceCatalog(config)
    items = personal_items(load_sources(), catalog, args.pronunciation or args.name)
    # Keep all selected lines in the publication plan on a limited rerun.
    plans = [(item, targets_for(item)) for item in items]
    plans = [(item, targets) for item, targets in plans if targets]
    targets = [
        t
        for item, group in plans
        for t in group
        if not args.quest
        or (item.kind == "quests" and int(item.entry["questID"]) in args.quest)
    ]
    print(
        f"{len(targets)} audio files selected; {len(plans)} named quest/gossip lines in the corpus"
    )
    pack = args.output / folder_name(args.name, args.realm)
    print(f"Personal pack: {pack}")
    if args.dry_run:
        for target in targets[:30]:
            print(f"  {target.label} [{target.voice}] {target.text[:100]}")
        print(
            "Dry run: no model or audio output. Missing DB2 metadata may be cached; reference clips are checked during generation."
        )
        return 0
    if not targets:
        raise SystemExit(
            "No named lines selected. Ingest captures or import Classic text first."
        )
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit(
            "Install ffmpeg and ffprobe on PATH before generating a personal pack."
        )
    # Require a real reference: silently using Chatterbox's default voice would
    # not sound like the NPC. Existing intentional race fallbacks still apply.
    missing = sorted(
        {t.voice for t in targets if catalog.resolve(t.voice).clip is None}
    )
    if missing:
        raise SystemExit(
            f"Missing reference clips for {', '.join(missing)}. "
            "Build tools/voices/ with tools/build_voice_references.py first (see docs/personal-voices.md)."
        )
    # Do not restamp audio drawn from an out-of-date picked reference.
    from tools.build_voice_references import picked_reference_current

    stale = {
        r.clip.stem
        for t in targets
        if (r := catalog.resolve(t.voice)).clip
        and (pick := config.voices.sources.get(r.clip.stem))
        and pick.clips
        and not picked_reference_current(r.clip.stem, pick)
    }
    if stale:
        raise SystemExit(
            f"Rebuild these picked reference clips first: {', '.join(sorted(stale))}"
        )
    try:
        import torch
    except ImportError:
        raise SystemExit(
            "Install the TTS environment using tools/personal_setup.py first."
        ) from None
    device = select_device(torch, args.device, args.cpu)
    backend = f"{device}/{getattr(torch.version, 'hip', None) or 'cuda'}/{torch.__version__}/{platform.machine()}"
    print(f"Generation backend: {backend}")
    require_filters(tuning_filters(config) + ["loudnorm"])
    pack.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(pack / ".build.lock"):
        state_path = pack / ".personal.json"
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {}
        )
        index = state.get("index", {})
        clips: dict[Path, str] = {}

        def current(target: Target) -> bool:
            record = index.get(target.key, {})
            return (pack / "Sounds" / f"{target.label}").is_file() and record.get(
                "signature"
            ) == signature(target, backend, clips)

        todo = [t for t in targets if args.force or not current(t)]
        if args.limit:
            todo = todo[: args.limit]
        synth = (
            Synth(catalog, device=device, allow_cpu=args.cpu, allow_hip=True)
            if todo
            else None
        )
        try:
            for number, target in enumerate(todo, 1):
                assert synth is not None
                dest = pack / "Sounds" / target.label
                with tempfile.TemporaryDirectory(dir=pack) as tmp:
                    raw = Path(tmp) / "take.mp3"
                    synth.speak(target.text, target.voice, raw)
                    transcode(raw, dest, config.release.bitrate)
                index[target.key] = {
                    "d": probe_duration(dest),
                    "v": target.voice,
                    "signature": signature(target, backend, clips),
                }
                atomic_text(
                    state_path, json.dumps({"schema": SCHEMA, "index": index}, indent=2)
                )
                print(f"[{number}/{len(todo)}] {target.label}")
        finally:
            # A partially rendered gender/parts/alternate set never shadows the
            # complete shared line. Already finished lines remain usable.
            complete = [item for item, group in plans if all(current(t) for t in group)]
            publish(pack, args.name, args.realm, complete, index, catalog)
        print(
            f"Published {len(complete)} complete lines. Copy {pack.name} to Interface/AddOns, "
            "restart WoW, then enable 'Use my character name in speech'."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        required=True,
        type=character_text,
        help="exact full character name returned by UnitName('player')",
    )
    parser.add_argument(
        "--realm",
        required=True,
        type=character_text,
        help="exact realm name returned by GetRealmName()",
    )
    parser.add_argument(
        "--pronunciation",
        type=character_text,
        help="optional phonetic spelling used only for synthesis",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "addons",
        help="parent directory of the generated addon (default: addons/)",
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="allow slow CPU fallback when no GPU is usable",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--quest",
        type=int,
        action="append",
        help="only generate this quest; repeatable",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="maximum new files this run (0: unlimited); rerun to resume",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
