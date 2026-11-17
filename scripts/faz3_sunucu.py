"""Faz 3: Sayman MCP sunucusunu stdio üzerinden başlatır (bir MCP istemcisi bu komutu çalıştırır).

Önce scripts/faz2_ambar_kur.py ve scripts/faz3_anahtar_uret.py çalıştırılmış olmalı.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz3_sunucu.py [--ambar ...] [--saat ...] [--kayit ...] [--kapsam-klasoru ...]
        [--belge data\\belge] [--belge-yontemi anlam|bm25|karma|karma_sirala]

--belge verilmezse belge_ara aracı "kullanilamaz" ile reddedilir (Faz 6). Sıralayıcı (karma_sirala) daha güçlü bir
bilgisayar için bir ayardır.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sayman.araclar.sunucu import main  # noqa: E402

if __name__ == "__main__":
    main()
