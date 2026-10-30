"""Turn a target speech length into a word budget (D017)."""

# Typical radio speech rate. French runs a little slower in words.
WORDS_PER_SECOND = {"en": 2.5, "fr": 2.3}

# Spoken segment lengths in seconds (D017).
SPEECH_SECONDS = {"intro": 15.0, "transition": 10.0, "outro": 10.0}


def word_budget(seconds: float, language: str) -> int:
    """How many words fit in `seconds` of speech in `language`."""
    return round(seconds * WORDS_PER_SECOND[language])
