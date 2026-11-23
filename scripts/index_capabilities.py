from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime import ensure_supported_python

ensure_supported_python()

from app.writeups import build_embedding_client
from app.orchestrator.capability_store import index_capabilities
from app.rag.vector_store import DEFAULT_CHROMA_PERSIST_DIRECTORY

PERSIST_DIRECTORY = REPO_ROOT / "data" / "vectorstore" / "chroma"


def main() -> None:
    client = build_embedding_client()
    count = index_capabilities(client, persist_directory=PERSIST_DIRECTORY)
    print(f"[capabilities] indexed {count} recon capabilities into {PERSIST_DIRECTORY}")


if __name__ == "__main__":
    main()
