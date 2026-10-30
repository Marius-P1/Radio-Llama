"""Script writing: prompts from files, word budgets, and the ScriptWriter on LLMClient."""

from radio_llama.script.prompts import PROMPT_NAMES, load_template, placeholders, render
from radio_llama.script.timing import SPEECH_SECONDS, word_budget
from radio_llama.script.writer import ScriptWriter, clean_script

__all__ = [
    "PROMPT_NAMES",
    "SPEECH_SECONDS",
    "ScriptWriter",
    "clean_script",
    "load_template",
    "placeholders",
    "render",
    "word_budget",
]
