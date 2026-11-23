"""Phase 3: starts the Sayman MCP server over stdio (an MCP client runs this command).

scripts/phase02_build_warehouse.py and scripts/phase03_generate_key.py must have been run first.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase03_server.py [--ambar ...] [--saat ...] [--kayit ...] [--kapsam-klasoru ...]
        [--belge data\\belge] [--belge-yontemi anlam|bm25|karma|karma_sirala]

If --belge is not given, the `belge_ara` tool is refused with "kullanilamaz" (Phase 6). The reranker (`karma_sirala`)
is a setting for a more powerful computer.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sayman.tools.server import main  # noqa: E402

if __name__ == "__main__":
    main()
