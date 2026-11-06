"""Text normalisation and hashing, byte-for-byte identical to Util.Tokenize /
Util.NormalizeText / Util.HashText in ForeverVO/Core/Util.lua. The addon looks
gossip lines up by this key, so the two implementations must never drift.

The client resolves $n, $c and $r against whoever is reading before any addon
sees the text, so captured text says "Myrlin" and "rogue" where the database
says $n and $c. Both sides drop all three, so a line matches whoever reads it.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]")
_DOLLAR_CODE = re.compile(r"\$[a-z]")
_GENDER_CODE = re.compile(r"\$g[^;]*;")


def _literal(value: str, fold_case: bool) -> str:
    """Mirror of LiteralPattern in Util.lua: with fold_case, ASCII letters match
    in either case; everything else matches exactly."""
    return "".join(
        "[" + ch.lower() + ch.upper() + "]"
        if fold_case and ("a" <= ch <= "z" or "A" <= ch <= "Z")
        else re.escape(ch)
        for ch in value
    )


def tokenize(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
    short_race: bool = True,
) -> str | None:
    """Mirror of Util.Tokenize: puts $n/$c/$r back where the client expanded them.

    Capitalisation is kept, so a capitalised match becomes $N/$C/$R.
    textclean drops $n and $c instead of speaking them.

    The name is matched case-sensitively, class and race are not: the client
    always renders a character name capitalised, so a lowercase match is never
    the name (for a character called "It", it is the word "it"), whereas $c and
    $C render as "rogue" and "Rogue".

    A multi-word race renders $r as its last word alone: UnitRace says
    "Windshaper Skyborne" and Zamja's "$r" came out "skyborne", so the last word
    is matched too (addon 0.1.5). `short_race=False` is the old behaviour, which
    ingest keeps for captures that older addons tokenised, so a literal
    "skyborne" in Forever's own text is not turned into $r after the fact.
    """
    if not text:
        return text

    def put(subject: str, value: str | None, token: str, fold_case: bool) -> str:
        if not value:
            return subject
        # Explicit lookarounds rather than \b, because Python's \w includes "_"
        # and Lua's %w does not. ASCII only throughout -- re.IGNORECASE folds
        # Unicode and .isupper() is Unicode aware, but Lua patterns are bytes and
        # can do neither, so both sides are held to ASCII to stay identical.
        return re.sub(
            r"(?<![0-9A-Za-z])" + _literal(value, fold_case) + r"(?![0-9A-Za-z])",
            lambda m, t=token: t.upper() if "A" <= m.group(0)[:1] <= "Z" else t,
            subject,
        )

    text = put(text, player_name, "$n", False)
    first_name = player_name.split()[0] if player_name and player_name.split() else None
    if first_name and first_name != player_name:
        text = put(text, first_name, "$n", False)  # $n is the bare first name
    text = put(text, class_name, "$c", True)
    text = put(text, race_name, "$r", True)
    last_word = race_name.split()[-1] if race_name and race_name.split() else None
    if short_race and last_word and last_word != race_name:
        text = put(text, last_word, "$r", True)  # $r renders as the last word alone
    return text


def normalize(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
    short_race: bool = True,
) -> str:
    if not text:
        return ""
    text = (
        tokenize(text, player_name, class_name, race_name, short_race) or ""
    ).lower()
    text = _GENDER_CODE.sub("", text)  # $g male:female; branch
    text = _DOLLAR_CODE.sub("", text)  # $n, $c, $r, $b, ...
    # Lua strips per byte; encode to UTF-8 so multi-byte characters vanish the same way
    raw = text.encode("utf-8")
    return _NON_ALNUM.sub("", raw.decode("latin-1"))


def hash_text(normalized: str) -> str:
    """djb2 modulo 2^32 as 8 hex chars (the Lua side stays within double precision)."""
    h = 5381
    for byte in normalized.encode("latin-1"):
        h = (h * 33 + byte) % 4294967296
    return f"{h:08x}"


def text_key(
    text: str | None,
    player_name: str | None = None,
    class_name: str | None = None,
    race_name: str | None = None,
    short_race: bool = True,
) -> str:
    return hash_text(normalize(text, player_name, class_name, race_name, short_race))


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:] or ["Gryphons, eh? Never really cared for the beasts."]:
        print(text_key(arg), arg)
