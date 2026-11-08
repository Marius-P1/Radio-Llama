"""Break shapes: what kind of break the host does between two songs, and how long (D033).

The shapes live in `prompts/<language>/shapes.json` because their instructions are prompt
text. Code only picks one per break: never the same twice in a row, and a shape that
needs facts only when the next track has some. The pick is seeded like the track pick,
so a seed reproduces the whole show.
"""

import random
from pathlib import Path

from pydantic import BaseModel, TypeAdapter


class Shape(BaseModel):
    id: str  # same ids in every language, checked by a test
    seconds: float  # target speech length; the word budget follows it
    needs_facts: bool = False  # only picked when the next track has facts
    instruction: str  # the sentence pasted into the transition prompt


_SHAPE_LIST = TypeAdapter(list[Shape])


def load_shapes(prompts_dir: Path, language: str) -> list[Shape]:
    path = prompts_dir / language / "shapes.json"
    return _SHAPE_LIST.validate_json(path.read_text(encoding="utf-8"))


def plan_shapes(
    shapes: list[Shape], facts_available: list[bool], rng: random.Random
) -> list[Shape]:
    """One shape per break, given whether each break's next track has facts."""
    plan: list[Shape] = []
    previous: str | None = None
    for has_facts in facts_available:
        options = [
            shape
            for shape in shapes
            if shape.id != previous and (has_facts or not shape.needs_facts)
        ]
        shape = rng.choice(options)
        plan.append(shape)
        previous = shape.id
    return plan
