"""The voice lists behind the picker (D040): ElevenLabs on a stub client, and the fake."""

from typing import Any

import pytest
from elevenlabs import GetVoicesV2Response, VerifiedVoiceLanguageResponseModel
from elevenlabs import Voice as AccountVoice
from elevenlabs.core.api_error import ApiError

from radio_llama.voice import (
    ElevenLabsVoices,
    FakeVoices,
    TTSError,
    UnknownVoiceError,
    VoiceOption,
    find_voice,
)

MODEL = "eleven_multilingual_v2"
STORAGE = "https://storage.googleapis.com/eleven-public-prod"


def entry(language: str, model_id: str, preview_url: str | None) -> Any:
    return VerifiedVoiceLanguageResponseModel(
        language=language, model_id=model_id, preview_url=preview_url
    )


def account_voice(voice_id: str, **fields: Any) -> AccountVoice:
    return AccountVoice(voice_id=voice_id, **{"name": f"Voice {voice_id}", **fields})


def page(voices: list[AccountVoice], next_page_token: str | None = None) -> GetVoicesV2Response:
    return GetVoicesV2Response(
        voices=voices,
        has_more=next_page_token is not None,
        total_count=len(voices),
        next_page_token=next_page_token,
    )


class StubVoicesApi:
    """`pages` maps the page token of a request (None for the first) to its reply."""

    def __init__(self, pages: dict[str | None, GetVoicesV2Response]) -> None:
        self.pages = pages
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> GetVoicesV2Response:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.pages[kwargs["next_page_token"]]


class StubClient:
    def __init__(self, pages: dict[str | None, GetVoicesV2Response]) -> None:
        self.voices = StubVoicesApi(pages)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_voices(
    *voices: AccountVoice, clock: Clock | None = None
) -> tuple[ElevenLabsVoices, StubClient]:
    client = StubClient({None: page(list(voices))})
    listing = ElevenLabsVoices("key", MODEL, client=client, clock=clock or Clock())  # type: ignore[arg-type]
    return listing, client


def only(voice: AccountVoice) -> VoiceOption:
    listing, _ = make_voices(voice)
    (option,) = listing.list_voices()
    return option


def test_maps_the_labels_and_the_default_sample() -> None:
    option = only(
        account_voice(
            "abc",
            name="Gabriel",
            labels={
                "gender": "male",
                "accent": "parisian",
                "age": "middle_aged",
                "descriptive": "warm",
                "use_case": "narration",
                "language": "fr",
            },
            preview_url=f"{STORAGE}/abc/default.mp3",
        )
    )

    assert option == VoiceOption(
        id="abc",
        name="Gabriel",
        gender="male",
        accent="parisian",
        age="middle_aged",
        descriptive="warm",
        language="fr",
        languages=[],
        preview_url=f"{STORAGE}/abc/default.mp3",
        previews={},
    )


def test_a_voice_with_nothing_but_an_id_still_maps() -> None:
    option = only(AccountVoice(voice_id="bare", labels={"gender": ""}))

    assert option == VoiceOption(id="bare", name="bare")


def test_verified_languages_are_listed_once_in_order() -> None:
    option = only(
        account_voice(
            "abc",
            verified_languages=[
                entry("en", "eleven_turbo_v2_5", None),
                entry("fr", "eleven_turbo_v2_5", None),
                entry("en", MODEL, None),
                entry("fr", MODEL, None),
            ],
        )
    )

    assert option.languages == ["en", "fr"]
    assert option.previews == {}


def test_the_sample_of_the_configured_model_wins_else_the_first() -> None:
    option = only(
        account_voice(
            "abc",
            verified_languages=[
                entry("fr", "eleven_turbo_v2_5", f"{STORAGE}/fr-turbo.mp3"),
                entry("fr", MODEL, f"{STORAGE}/fr-multilingual.mp3"),
                entry("en", "eleven_turbo_v2_5", f"{STORAGE}/en-turbo.mp3"),
                entry("en", "eleven_flash_v2_5", f"{STORAGE}/en-flash.mp3"),
                entry("de", MODEL, None),
            ],
        )
    )

    assert option.languages == ["fr", "en", "de"]
    assert option.previews == {
        "fr": f"{STORAGE}/fr-multilingual.mp3",  # made with the configured model
        "en": f"{STORAGE}/en-turbo.mp3",  # no sample of that model: the first one
    }


def test_a_signed_sample_link_is_passed_through_unchanged() -> None:
    """Some samples are signed links on an ElevenLabs API host. They play without the API
    key, so they go to the browser as they are, query string included."""
    signed = "https://api.us.elevenlabs.io/v1/voices/abc/previews/audio?payload=eyJhIjoxfQ.sig%3D"
    signed_fr = "https://api.us.elevenlabs.io/v1/voices/abc/previews/audio?payload=ZnI.sig&x=1"
    option = only(
        account_voice(
            "abc",
            preview_url=signed,
            verified_languages=[
                entry("fr", "eleven_turbo_v2_5", f"{STORAGE}/fr-turbo.mp3"),
                entry("fr", MODEL, signed_fr),
                entry("en", MODEL, ""),
            ],
        )
    )

    assert option.preview_url == signed
    # The configured model's sample wins, signed or not. An empty URL is no sample.
    assert option.previews == {"fr": signed_fr}
    assert option.languages == ["fr", "en"]


def test_the_api_key_is_never_part_of_the_voice_list() -> None:
    client = StubClient({None: page([account_voice("a", preview_url=f"{STORAGE}/a.mp3")])})
    listing = ElevenLabsVoices("secret-api-key", MODEL, client=client)  # type: ignore[arg-type]

    dumped = [voice.model_dump_json() for voice in listing.list_voices()]

    assert dumped
    assert all("secret-api-key" not in text for text in dumped)


def test_every_page_is_read() -> None:
    client = StubClient(
        {
            None: page([account_voice("a"), account_voice("b")], next_page_token="page-2"),
            "page-2": page([account_voice("c")]),
        }
    )
    listing = ElevenLabsVoices("key", MODEL, client=client)  # type: ignore[arg-type]

    assert [voice.id for voice in listing.list_voices()] == ["a", "b", "c"]
    assert client.voices.calls == [
        {"page_size": 100, "next_page_token": None},
        {"page_size": 100, "next_page_token": "page-2"},
    ]


def test_the_list_is_kept_for_ten_minutes() -> None:
    clock = Clock()
    listing, client = make_voices(account_voice("a"), clock=clock)

    first = listing.list_voices()
    clock.now += 599
    assert listing.list_voices() == first
    assert len(client.voices.calls) == 1

    clock.now += 2  # past ten minutes: ElevenLabs is asked again
    client.voices.pages[None] = page([account_voice("a"), account_voice("new")])
    assert [voice.id for voice in listing.list_voices()] == ["a", "new"]
    assert len(client.voices.calls) == 2


def test_a_failure_is_readable_and_not_kept() -> None:
    listing, client = make_voices(account_voice("a"))
    client.voices.error = RuntimeError("connection reset")

    with pytest.raises(TTSError, match="Could not list the ElevenLabs voices") as info:
        listing.list_voices()
    assert "connection reset" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)

    # The next call asks again and gets the list.
    client.voices.error = None
    assert [voice.id for voice in listing.list_voices()] == ["a"]
    assert len(client.voices.calls) == 2


def test_an_api_error_names_the_status_without_the_headers() -> None:
    listing, client = make_voices(account_voice("a"))
    client.voices.error = ApiError(
        headers={"x-request-id": "secret-looking-header"},
        status_code=401,
        body={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}},
    )

    with pytest.raises(TTSError) as info:
        listing.list_voices()

    assert "HTTP 401" in str(info.value)
    assert "Invalid API key" in str(info.value)
    assert "x-request-id" not in str(info.value)


class CountingVoices:
    """The fake list, counting how often it is read."""

    def __init__(self) -> None:
        self.reads = 0

    def list_voices(self) -> list[VoiceOption]:
        self.reads += 1
        return FakeVoices().list_voices()


def test_find_voice_returns_the_listed_voice() -> None:
    voice = find_voice(FakeVoices(), "fake-voice-2")

    assert voice is not None
    assert (voice.id, voice.name) == ("fake-voice-2", "Basile")
    # Spaces around the id do not count.
    assert find_voice(FakeVoices(), "  fake-voice-2 ") == voice


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_find_voice_takes_a_blank_id_as_no_voice_without_reading_the_list(
    blank: str | None,
) -> None:
    voices = CountingVoices()

    assert find_voice(voices, blank) is None
    assert voices.reads == 0


def test_find_voice_refuses_an_unknown_id_in_the_pages_words() -> None:
    with pytest.raises(UnknownVoiceError) as info:
        find_voice(FakeVoices(), "nobody")

    assert str(info.value) == (
        "Unknown voice 'nobody'. Pick one of the listed voices, or leave the voice out "
        "to use the style's own voice."
    )


def test_find_voice_lets_a_list_failure_through() -> None:
    listing, client = make_voices(account_voice("a"))
    client.voices.error = RuntimeError("connection reset")

    with pytest.raises(TTSError, match="Could not list the ElevenLabs voices"):
        find_voice(listing, "a")


def test_fake_voices_are_a_fixed_list_without_samples() -> None:
    voices = FakeVoices().list_voices()

    assert [voice.id for voice in voices] == [f"fake-voice-{n}" for n in range(1, 6)]
    assert len({voice.name for voice in voices}) == 5
    assert {voice.gender for voice in voices} == {"female", "male", "neutral"}
    # Some speak English only, some French only, some both.
    assert {tuple(voice.languages) for voice in voices} == {("en",), ("fr",), ("en", "fr")}
    assert all(voice.preview_url is None and voice.previews == {} for voice in voices)


def test_the_fake_list_cannot_be_changed_by_a_caller() -> None:
    FakeVoices().list_voices().clear()

    assert len(FakeVoices().list_voices()) == 5
