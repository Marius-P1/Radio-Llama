"""The sentence that tells the host what time it is (D031).

The model has no clock. Without this line every show was an evening show, because the
old prompts said "tonight". The pipeline computes `now` once per show in the configured
timezone; the names are written out here per language instead of relying on the
machine's locale.

The line gives the date and the part of the day, not the hour. A show is written once
and heard later, after the listener has pressed play and the songs before the break
have run, so any exact time the host says would be wrong by the time it is heard.
"""

from datetime import datetime

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}
MONTHS = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
}
PARTS_OF_DAY = {
    "en": {
        "night": "at night",
        "morning": "in the morning",
        "afternoon": "in the afternoon",
        "evening": "in the evening",
    },
    "fr": {
        "night": "la nuit",
        "morning": "le matin",
        "afternoon": "l'après-midi",
        "evening": "le soir",
    },
}
TEMPLATES = {
    "en": "Local time: {weekday} {day} {month} {year}, {part}. "
    "Greet everyone for this time of day, but never say the exact time: "
    "the show is heard later than it is written.",
    "fr": "Heure locale : {weekday} {day} {month} {year}, {part}. "
    "Salue tout le monde selon ce moment de la journée, mais ne donne jamais "
    "l'heure exacte : l'émission est écoutée plus tard qu'elle n'est écrite.",
}


def part_of_day(hour: int) -> str:
    """night before 5, morning before 12, afternoon before 18, evening after."""
    if hour < 5:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def time_line(now: datetime | None, language: str) -> str:
    """The sentence for the system prompt, or an empty string when no time is given."""
    if now is None:
        return ""
    return TEMPLATES[language].format(
        weekday=WEEKDAYS[language][now.weekday()],
        day=now.day,
        month=MONTHS[language][now.month - 1],
        year=now.year,
        part=PARTS_OF_DAY[language][part_of_day(now.hour)],
    )
