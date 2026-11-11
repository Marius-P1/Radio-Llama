from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from radio_llama.script.clock import part_of_day, time_line

PARIS = ZoneInfo("Europe/Paris")


@pytest.mark.parametrize(
    ("hour", "part"),
    [
        (0, "night"),
        (4, "night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (23, "evening"),
    ],
)
def test_part_of_day_boundaries(hour: int, part: str) -> None:
    assert part_of_day(hour) == part


def test_time_line_in_english() -> None:
    now = datetime(2026, 11, 8, 16, 45, tzinfo=PARIS)  # a Sunday

    assert time_line(now, "en") == (
        "Local time: Sunday 8 November 2026, in the afternoon. "
        "Greet everyone for this time of day. Mention the day or the date once in a "
        "while, not in every break. Never say the exact time: the show is heard later "
        "than it is written."
    )
    assert "16:45" not in time_line(now, "en")


def test_time_line_in_french() -> None:
    now = datetime(2026, 11, 9, 7, 5, tzinfo=PARIS)

    assert time_line(now, "fr") == (
        "Heure locale : lundi 9 novembre 2026, le matin. "
        "Salue tout le monde selon ce moment de la journée. Glisse le jour ou la date "
        "de temps en temps, pas à chaque intervention. Ne donne jamais l'heure exacte : "
        "l'émission est écoutée plus tard qu'elle n'est écrite."
    )
    assert "7 h 05" not in time_line(now, "fr")


def test_time_line_follows_the_timezone_of_now() -> None:
    utc = datetime(2026, 11, 8, 19, 30, tzinfo=ZoneInfo("UTC"))  # 04:30 in Tokyo

    assert "at night" in time_line(utc.astimezone(ZoneInfo("Asia/Tokyo")), "en")
    assert "in the evening" in time_line(utc, "en")


def test_no_time_gives_no_line() -> None:
    assert time_line(None, "en") == ""
    assert time_line(None, "fr") == ""
