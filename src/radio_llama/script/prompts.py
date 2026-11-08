"""Prompt templates read from `prompts/<language>/<name>.md` (D003).

Templates use `string.Template` syntax (`$name`). Both languages must declare
the same placeholders; a test checks it.
"""

from pathlib import Path
from string import Template

PROMPT_NAMES = ("system", "intro", "transition", "outro", "wish")


def load_template(prompts_dir: Path, language: str, name: str) -> Template:
    """Read `prompts_dir / language / f"{name}.md"` as a Template."""
    path = prompts_dir / language / f"{name}.md"
    return Template(path.read_text(encoding="utf-8"))


def placeholders(template: Template) -> set[str]:
    """The placeholder names a template declares."""
    return set(template.get_identifiers())


def render(template: Template, values: dict[str, str]) -> str:
    """Fill the template. A missing value is a bug, so KeyError propagates."""
    return template.substitute(values)
