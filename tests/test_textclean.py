"""Spoken text drops the player address instead of saying Adventurer."""

from __future__ import annotations

import pytest

from tools.textclean import clean, segments, split_gender


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("Hello, $N.", "Hello."),
        ("Thank you, $N!", "Thank you!"),
        ("Have you found the statuette, $N?", "Have you found the statuette?"),
        ("Oh, $N, you have saved my daughter!", "Oh, you have saved my daughter!"),
        ("Please, $N, find the site.", "Please, find the site."),
        ("$N, do you have those shells yet?", "Do you have those shells yet?"),
        ("Adventurer! The wind bridge is unstable.", "The wind bridge is unstable."),
        ("Greetings, adventurer.", "Greetings."),
        (
            "Praise you, brave $C. The rocks shall be lit.",
            "Praise you. The rocks shall be lit.",
        ),
        (
            "I trust the Light is with you, $n. Is there something I can help you with?",
            "I trust the Light is with you. Is there something I can help you with?",
        ),
        (
            "Well done, $n. With valiant adventurers such as yourself fighting alongside us.",
            "Well done. With valiant adventurers such as yourself fighting alongside us.",
        ),
        ("Ye should look fer a $c trainer.", "Ye should look fer a trainer."),
        ("You look well traveled for a $c.", "You look well traveled."),
        (
            "I know who you are, $n. Believe me, $r, when I tell you this.",
            "I know who you are. Believe me, traveler, when I tell you this.",
        ),
        (
            "Here is the rod, $N. Reconstructing it was simple... it's finding the shrine.",
            "Here is the rod. Reconstructing it was simple... it's finding the shrine.",
        ),
        ("Ah, a $c. Very good.", "Ah. Very good."),
        ("$N... yeah, I've heard of you.", "Yeah, I've heard of you."),
        (
            "Greetings, $c - I'm a Commendation Officer.",
            "Greetings - I'm a Commendation Officer.",
        ),
        ("Take $n, then.", "Take, then."),
        ("$N is up to us to meet $n.", "Is up to us to meet."),
        (
            "The $c who flinches is the $c who dies.",
            "The who flinches is the who dies.",
        ),
    ],
)
def test_address_is_not_spoken(raw: str, spoken: str) -> None:
    assert clean(raw) == spoken


def test_ordinary_noun_stays() -> None:
    raw = "You're an adventurer, like my mom and dad were."
    assert clean(raw) == raw
    quoted = "You 'adventurer' types are often victims of circumstance."
    assert clean(quoted) == quoted


def test_parts_drop_the_address_too() -> None:
    assert segments("Hello, $n. <the guard spits> Welcome, $N.") == [
        ("npc", "Hello."),
        ("narrator", "the guard spits"),
        ("npc", "Welcome."),
    ]


def test_gender_branch_survives_the_address_strip() -> None:
    male, female = split_gender(clean("Hello, $N. $gHe:She; is ready."))
    assert male == "Hello. He is ready."
    assert female == "Hello. She is ready."
    # The semicolon closes the branch; the period closes the sentence.
    male, female = split_gender(clean("Thank you, $g lad:lass;. Take this, $c."))
    assert male == "Thank you, lad. Take this."
    assert female == "Thank you, lass. Take this."
