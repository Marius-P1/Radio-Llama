"""Personas loaded from `prompts/<language>/personas.json`.

Each language has its own file so names and descriptions stay in that language (D003).
The voice id sits next to the persona because it is not a secret (D014).
"""

from pathlib import Path

from pydantic import TypeAdapter

from radio_llama.models import Persona

_PERSONA_LIST = TypeAdapter(list[Persona])


class UnknownPersonaError(LookupError):
    """No persona has the requested id. The message lists the known ids."""


def load_personas(prompts_dir: Path, language: str) -> list[Persona]:
    """Read and validate `prompts_dir / language / "personas.json"`."""
    path = prompts_dir / language / "personas.json"
    return _PERSONA_LIST.validate_json(path.read_text(encoding="utf-8"))


def get_persona(prompts_dir: Path, language: str, persona_id: str) -> Persona:
    """Return the persona with `persona_id`, or raise UnknownPersonaError."""
    personas = load_personas(prompts_dir, language)
    for persona in personas:
        if persona.id == persona_id:
            return persona
    known = ", ".join(persona.id for persona in personas)
    raise UnknownPersonaError(
        f"Unknown persona {persona_id!r} for language {language!r}. Known personas: {known}."
    )
