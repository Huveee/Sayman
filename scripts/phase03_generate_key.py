"""Faz 3: kimlik kartlarını imzalayan anahtarı üretir ve .env dosyasına yazar.

Anahtar zaten varsa dokunulmaz. Anahtar ekrana yazılmaz; .env git'e girmez. Dosyadaki diğer satırlar
(ör. EVDS_API_KEY) olduğu gibi kalır.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz3_anahtar_uret.py
"""

import secrets
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.tools.identity import ANAHTAR_DEGISKENI, anahtar_oku  # noqa: E402


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    env = KOK / ".env"
    metin = env.read_text(encoding="utf-8") if env.exists() else ""
    satirlar = metin.splitlines()
    mevcut = [s for s in satirlar if s.split("=", 1)[0].strip() == ANAHTAR_DEGISKENI]
    if any(s.split("=", 1)[1].strip() for s in mevcut if "=" in s):
        from dotenv import dotenv_values

        anahtar_oku(dotenv_values(env))  # geçerli mi? (değer yazdırılmaz)
        print(f"{ANAHTAR_DEGISKENI} zaten .env dosyasında; değiştirilmedi.")
        return 0
    yeni = f"{ANAHTAR_DEGISKENI}={secrets.token_hex(32)}"
    if mevcut:  # boş bırakılmış satırı doldur
        satirlar = [yeni if s in mevcut else s for s in satirlar]
        env.write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    else:
        with open(env, "a", encoding="utf-8", newline="\n") as f:
            f.write(("" if not metin or metin.endswith("\n") else "\n") + yeni + "\n")
    print(f"{ANAHTAR_DEGISKENI} .env dosyasına yazıldı (32 bayt, onaltılık; değer ekrana yazılmadı).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
