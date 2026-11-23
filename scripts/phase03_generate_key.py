"""Phase 3: generates the key that signs the identity cards and writes it to the .env file.

An existing key is left untouched. The key is never printed; .env is not committed to git. The other lines in
the file (e.g. EVDS_API_KEY) are kept as they are.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase03_generate_key.py
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.tools.identity import KEY_ENV_VAR, read_key  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    lines = text.splitlines()
    existing = [s for s in lines if s.split("=", 1)[0].strip() == KEY_ENV_VAR]
    if any(s.split("=", 1)[1].strip() for s in existing if "=" in s):
        from dotenv import dotenv_values

        read_key(dotenv_values(env))  # is it valid? (the value is not printed)
        print(f"{KEY_ENV_VAR} zaten .env dosyasında; değiştirilmedi.")
        return 0
    new_line = f"{KEY_ENV_VAR}={secrets.token_hex(32)}"
    if existing:  # fill in the line that was left empty
        lines = [new_line if s in existing else s for s in lines]
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        with open(env, "a", encoding="utf-8", newline="\n") as f:
            f.write(("" if not text or text.endswith("\n") else "\n") + new_line + "\n")
    print(f"{KEY_ENV_VAR} .env dosyasına yazıldı (32 bayt, onaltılık; değer ekrana yazılmadı).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
