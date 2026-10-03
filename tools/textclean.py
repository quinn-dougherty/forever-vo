"""Turns raw WoW quest/gossip text into something a TTS model should read aloud.

Mirrors the rules of the upstream tts_cli (dollar-code substitution, stage
directions in angle brackets, $G gender branches) and adds sentence chunking for
models that prefer short inputs. Respellings for names the model gets wrong are
[pronunciations] in forever-vo.toml (config.Pronunciations).
"""

from __future__ import annotations

import re

from tools.config import Pronunciations, load_config

# $b/$B and $r/$R only. $n/$N (the player's name) and $c/$C (their class) are
# not spoken: strip_addresses deletes them. "Adventurer" on every greeting wore
# the line out, and another noun in its place would be the same word in every
# voice. $r stays "traveler"; that word was not the complaint.
REPLACE = {
    "$b": "\n",
    "$B": "\n",
    "$r": "traveler",
    "$R": "Traveler",
}

# A direct address: the placeholder, or the singular word "adventurer" (never
# "adventurers"), optionally "Master"/"brave"/... immediately in front, and a
# possessive "'s" stuck to the token. An ordinary noun ("an adventurer like
# you", "you 'adventurer' types") has no comma or sentence break setting it
# off, so the rules below leave it: cutting the word there leaves a hole.
_ADJECTIVE = (
    "noble|fair|brave|young|good|dear|little|mighty|hearty|valiant|fine|strong|master"
)
_PLACEHOLDER = r"\$[nNcC](?:'s)?"
_LITERAL = r"\badventurer\b(?:'s)?"
_TOKEN = rf"(?:{_PLACEHOLDER}|{_LITERAL})"
_PHRASE = rf"(?:\b(?:{_ADJECTIVE})\b\s+)?{_TOKEN}"
# Comma before the address. Keep a sentence end that belonged to it
# ("Thank you, $N!" -> "Thank you!"); a comma after stays put, so
# "Oh, $N, you" becomes "Oh, you" and "Please, $N, find" becomes "Please, find".
# Whitespace after the token is consumed only together with the sentence end.
# Otherwise ", $c - I'm" loses the space and comes out "Greetings- I'm".
_COMMA_BEFORE = re.compile(
    rf",\s*{_PHRASE}(?:\s*(\.\.\.|[.!?…]))?",
    re.IGNORECASE,
)
# Address at the start of a sentence: the whole text, after a newline ($B was
# already substituted), or after . ! ? or an ellipsis. The prefix is kept. A
# comma, bang, question, period, or ellipsis that only closed the address goes.
_LEAD = re.compile(
    rf"(^|\n+|\.\.\.\s*|(?<!\.)[.!?…]\s*){_PHRASE}\s*(?:\.\.\.|[,.!?…]+)?\s*",
    re.IGNORECASE,
)
_LEFTOVER = re.compile(_PLACEHOLDER, re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.!?;:…])")
# Comma only. A $g branch ends in a semicolon ("$g lad:lass;."), and collapsing
# that semicolon into the period makes the branch unreadable.
_COMMA_THEN_END = re.compile(r",[ \t]*(\.\.\.|[.!?…])")
_DANGLING_WORD = re.compile(
    r"\b(?:a|an|the|for|of)[ \t]*(\.\.\.|[,.!?…])",
    re.IGNORECASE,
)
# A period that is itself part of "..." is not a sentence end, so "simple... it's"
# stays lowercase.
_SENTENCE_START = re.compile(r"(^|\n+|(?<!\.)[.!?…][ \t]+)([a-z])")

_GENDER = re.compile(r"\$[Gg]\s*([^:;]+?)\s*:\s*([^:;]+?)\s*;")
_STAGE_DIRECTION = re.compile(r"<[^<>]*>\s?")
_STAGE_DIRECTION_TEXT = re.compile(r"<([^<>]*)>")
_STAGE_SPLIT = re.compile(r"(<[^<>]*>)")
_WHITESPACE = re.compile(r"\s+")


def has_gender_branch(text: str) -> bool:
    return bool(_GENDER.search(text))


def split_gender(text: str) -> tuple[str, str]:
    """Returns (male_text, female_text) for `$G he:she;` style branches."""
    return _GENDER.sub(r"\1", text), _GENDER.sub(r"\2", text)


def _tidy_address(text: str) -> str:
    """Spaces and punctuation left behind once an address word is gone.

    A dangling a/an/the/for/of before punctuation goes too, and repeats:
    "traveled for a $c." has already lost "$c", and "for a." is not worth saying.
    """
    for _ in range(6):
        updated = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        updated = _COMMA_THEN_END.sub(r"\1", updated)
        updated = _DANGLING_WORD.sub(r"\1", updated)
        updated = re.sub(r"[ \t]{2,}", " ", updated)
        updated = _SENTENCE_START.sub(
            lambda m: m.group(1) + m.group(2).upper(), updated
        )
        if updated == text:
            break
        text = updated
    return text.strip()


def strip_addresses(text: str) -> str:
    """Delete spoken "Adventurer" and the punctuation that only set it off.

    Placeholders always go, so "a $c trainer" becomes "a trainer" and a name
    used as a noun ("$N is up to us") is simply gone. The literal word goes
    only as a direct address ("Greetings, adventurer.", "Adventurer!"). A line
    with neither is returned untouched, so tidy rules cannot restage every
    other file.
    """
    if not re.search(r"\$[nNcC]|\badventurer\b", text, re.IGNORECASE):
        return text
    updated = _COMMA_BEFORE.sub(lambda m: m.group(1) or "", text)
    updated = _LEAD.sub(lambda m: m.group(1) or "", updated)
    updated = _LEFTOVER.sub("", updated)
    if updated == text:
        return text
    return _tidy_address(updated)


def _substitute(text: str) -> str:
    for key, value in REPLACE.items():
        text = text.replace(key, value)
    return strip_addresses(text)


def _finish(text: str, pronunciations: Pronunciations) -> str:
    text = pronunciations.respell(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def clean(
    text: str,
    keep_stage_directions: bool = False,
    pronunciations: Pronunciations | None = None,
) -> str:
    """The whole line as one reader says it. Stage directions (<the guard spits>)
    are the narrator's, not the speaker's, so they are dropped -- unless the
    narrator reads the whole line anyway, when their text is kept as prose.
    `pronunciations` defaults to the repository's forever-vo.toml."""
    if pronunciations is None:
        pronunciations = load_config().pronunciations
    text = _substitute(text)
    if keep_stage_directions:
        text = _STAGE_DIRECTION_TEXT.sub(r"\1", text)
    else:
        text = _STAGE_DIRECTION.sub("", text)
    return _finish(text, pronunciations)


def segments(
    text: str, pronunciations: Pronunciations | None = None
) -> list[tuple[str, str]]:
    """The line in reading order as ("npc", words) and ("narrator", words) pieces,
    each cleaned like clean(): the speaker's own words and, between them, every
    <stage direction> for the narrator. Adjacent pieces of one role are merged.
    A line with no stage direction is a single npc piece."""
    if pronunciations is None:
        pronunciations = load_config().pronunciations
    out: list[tuple[str, str]] = []
    for piece in _STAGE_SPLIT.split(_substitute(text)):
        if piece.startswith("<") and piece.endswith(">"):
            role, piece = "narrator", piece[1:-1]
        else:
            role = "npc"
        piece = _finish(piece, pronunciations)
        if not piece:
            continue
        if out and out[-1][0] == role:
            out[-1] = (role, f"{out[-1][1]} {piece}")
        else:
            out.append((role, piece))
    return out


def is_speakable(text: str) -> bool:
    """False when unresolved markup remains ($ codes, angle brackets) or nothing is left."""
    return bool(text) and "$" not in text and "<" not in text and ">" not in text


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'(A-Z0-9])")


def chunk(text: str, max_chars: int = 300) -> list[str]:
    """Splits text into sentence-aligned chunks no longer than max_chars where possible."""
    sentences = _SENTENCE_END.split(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            # Fall back to splitting on commas/semicolons for run-on sentences
            parts = re.split(r"(?<=[,;:])\s+", sentence)
            for part in parts:
                if current and len(current) + 1 + len(part) > max_chars:
                    chunks.append(current)
                    current = part
                else:
                    current = f"{current} {part}".strip()
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


# Key normalisation used by the addon's lookup tables (DataModules.lua replaces
# double quotes with single quotes before matching, generators strip newlines).
def lookup_key(text: str) -> str:
    return text.replace('"', "'").replace("\r", " ").replace("\n", " ")


def first_n_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"\S+", text)[:n])


def last_n_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"\S+", text)[-n:])


def quest_text_excerpt(text: str) -> str:
    """The addon fuzzy-matches on the first and last 15 words of the quest text."""
    excerpt = first_n_words(text, 15) + " " + last_n_words(text, 15)
    excerpt = re.sub(r"(\$[Bb])+", " ", lookup_key(excerpt))
    return excerpt
