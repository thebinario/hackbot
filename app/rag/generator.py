from __future__ import annotations

import re


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_GENERATE_MODEL = "qwen2.5:7b-instruct"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 300

_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think_tags(text: str) -> str:
    return _THINK_PATTERN.sub("", text).strip()
