from pathlib import Path

from radio_llama.voice import CachedTextToSpeech, FakeTTS, Voice, cache_key

TEXT = "hello"
VOICE = Voice(voice_id="v1", model_id="m1", settings={"stability": 0.5})


def test_same_text_and_voice_is_synthesised_once(tmp_path: Path) -> None:
    fake = FakeTTS(tmp_path / "raw")
    tts = CachedTextToSpeech(fake, tmp_path)
    first = tts.synthesize(TEXT, VOICE)
    second = tts.synthesize(TEXT, VOICE)
    assert fake.calls == 1
    assert first == second
    assert first.exists()
    assert first.parent == tmp_path / "tts"
    assert first.name == cache_key(TEXT, VOICE) + ".mp3"


def test_different_settings_call_the_backend_again(tmp_path: Path) -> None:
    fake = FakeTTS(tmp_path / "raw")
    tts = CachedTextToSpeech(fake, tmp_path)
    tts.synthesize(TEXT, VOICE)
    other = Voice(voice_id="v1", model_id="m1", settings={"stability": 0.9})
    tts.synthesize(TEXT, other)
    assert fake.calls == 2


def test_cache_key_is_deterministic_and_ignores_dict_order() -> None:
    a = Voice(voice_id="v", model_id="m", settings={"stability": 0.5, "style": 0.7})
    b = Voice(voice_id="v", model_id="m", settings={"style": 0.7, "stability": 0.5})
    assert cache_key(TEXT, a) == cache_key(TEXT, a)
    assert cache_key(TEXT, a) == cache_key(TEXT, b)
    assert len(cache_key(TEXT, a)) == 64
    assert cache_key(TEXT, a) != cache_key("other", a)
    assert cache_key(TEXT, a) != cache_key(TEXT, Voice(voice_id="w", model_id="m"))


def test_raw_file_is_moved_into_the_cache(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    tts = CachedTextToSpeech(FakeTTS(raw_dir), tmp_path)
    tts.synthesize(TEXT, VOICE)
    assert list(raw_dir.iterdir()) == []
