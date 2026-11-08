"""Break shapes (D033): the files per language and the seeded pick."""

import random
from pathlib import Path

import pytest

from radio_llama.script import Shape, load_shapes, plan_shapes

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SHAPES = load_shapes(PROMPTS_DIR, "en")


def test_both_languages_declare_the_same_shapes() -> None:
    en = load_shapes(PROMPTS_DIR, "en")
    fr = load_shapes(PROMPTS_DIR, "fr")

    assert [(s.id, s.seconds, s.needs_facts) for s in en] == [
        (s.id, s.seconds, s.needs_facts) for s in fr
    ]
    assert len({s.id for s in en}) == len(en)
    assert all(s.instruction.strip() for s in en + fr)


def test_shapes_cover_a_range_of_lengths() -> None:
    seconds = sorted(s.seconds for s in SHAPES)

    assert seconds[0] <= 5
    assert seconds[-1] >= 20
    assert seconds[-1] <= 40  # D029: breaks between 5 and 40 s
    assert any(s.needs_facts for s in SHAPES)
    assert any(not s.needs_facts for s in SHAPES)


def test_plan_never_repeats_a_shape_twice_in_a_row() -> None:
    for seed in range(50):
        plan = plan_shapes(SHAPES, [True] * 8, random.Random(seed))
        assert len(plan) == 8
        assert all(a.id != b.id for a, b in zip(plan, plan[1:], strict=False))


def test_plan_keeps_fact_shapes_for_tracks_with_facts() -> None:
    for seed in range(50):
        plan = plan_shapes(SHAPES, [False, False, False], random.Random(seed))
        assert all(not shape.needs_facts for shape in plan)


def test_plan_is_reproducible_with_a_seed() -> None:
    first = plan_shapes(SHAPES, [True, False, True], random.Random(7))
    second = plan_shapes(SHAPES, [True, False, True], random.Random(7))

    assert [s.id for s in first] == [s.id for s in second]


def test_plan_uses_every_shape_over_many_seeds() -> None:
    seen = {
        shape.id
        for seed in range(100)
        for shape in plan_shapes(SHAPES, [True, True], random.Random(seed))
    }

    assert seen == {shape.id for shape in SHAPES}


def test_plan_with_one_option_left_still_works() -> None:
    only = [Shape(id="a", seconds=5, instruction="x"), Shape(id="b", seconds=5, instruction="y")]

    plan = plan_shapes(only, [False] * 5, random.Random(1))

    assert [s.id for s in plan] in (["a", "b", "a", "b", "a"], ["b", "a", "b", "a", "b"])


def test_plan_with_no_breaks() -> None:
    assert plan_shapes(SHAPES, [], random.Random(1)) == []


@pytest.mark.parametrize("language", ["en", "fr"])
def test_instructions_mention_the_next_song(language: str) -> None:
    """Every shape ends on the next song, so the hand-over is never missing."""
    words = {"en": ("next song", "the song"), "fr": ("morceau suivant", "le morceau")}
    for shape in load_shapes(PROMPTS_DIR, language):
        assert any(word in shape.instruction for word in words[language]), shape.id
