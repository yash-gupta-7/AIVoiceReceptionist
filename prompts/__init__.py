"""Versioned prompt loader.

Prompts are plain text files under prompts/<version>/, versioned by directory
name and by git. Set PROMPT_VERSION to switch versions atomically.
"""
from functools import lru_cache
from pathlib import Path

from packages.shared.config import get_settings

PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    version = get_settings().prompt_version
    path = PROMPTS_DIR / version / f"{name}.txt"
    if not path.exists():  # fall back to any version that has this prompt (newest first)
        candidates = sorted(PROMPTS_DIR.glob(f"v*/{name}.txt"), reverse=True)
        if not candidates:
            raise FileNotFoundError(f"prompt {name!r} not found in any version")
        path = candidates[0]
    return path.read_text().strip()


def render_prompt(name: str, **values: str) -> str:
    """Substitute {placeholders}. Plain replace, so literal JSON braces in
    prompt text are safe (str.format would choke on them)."""
    text = load_prompt(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text
