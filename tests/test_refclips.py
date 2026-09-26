"""Clips chosen by ear reach the reference in the order they were chosen."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.build_voice_references import concat_to_wav, write_concat


def tone(path: Path, seconds: float, hz: int) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", str(seconds),
                    "-i", f"sine=frequency={hz}:sample_rate=24000", "-ac", "1", str(path)], check=True)
    return path


def test_write_concat_keeps_the_order_it_was_given(tmp_path: Path) -> None:
    # deliberately not longest-first: build_reference would have sorted these and
    # dropped the short one, which is the whole reason picks bypass it
    picks = [tone(tmp_path / "a.ogg", 0.4, 300), tone(tmp_path / "b.ogg", 3.0, 400),
             tone(tmp_path / "c.ogg", 1.0, 500)]
    listing = write_concat("x-male", picks, "picked.txt", tmp_path)
    assert [line.split("'")[1] for line in listing.read_text().splitlines()] == [str(p.resolve()) for p in picks]


def test_the_picked_list_does_not_clobber_the_automatic_one(tmp_path: Path) -> None:
    one = write_concat("x-male", [tone(tmp_path / "a.ogg", 0.4, 300)], "concat.txt", tmp_path)
    two = write_concat("x-male", [tone(tmp_path / "b.ogg", 0.5, 400)], "picked.txt", tmp_path)
    assert one != two
    assert one.read_text() != two.read_text()


def test_concat_refuses_an_input_it_cannot_probe(tmp_path: Path) -> None:
    good, bad = tone(tmp_path / "a.ogg", 2.0, 300), tmp_path / "bad.ogg"
    bad.write_text("not an ogg", encoding="utf-8")
    out = tmp_path / "x-male.wav"
    concat_to_wav("x-male", [good, good], list_dir=tmp_path, out=out)
    assert out.exists()
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        # ffmpeg would write a 2 s wav here and exit 0, leaving a truncated reference
        concat_to_wav("x-male", [good, bad, good], list_dir=tmp_path, out=out)
    assert abs(_seconds(out) - 4.0) < 0.3      # the good build survived


def _seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())
