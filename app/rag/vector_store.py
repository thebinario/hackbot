from __future__ import annotations

from pathlib import Path


# Shared with app.writeups.store: where the local Chroma index lives on disk.
DEFAULT_CHROMA_PERSIST_DIRECTORY = Path("data") / "vectorstore" / "chroma"
