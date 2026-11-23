"""Faz 4 puanlaması: izleme uyarılarını cevap anahtarındaki olaylarla eşleştirir.

Bu dosya kurallardan önce yazıldı ve kurallar ayarlanırken değiştirilmedi. İzleme kodu (src/sayman/izleme)
cevap anahtarını hiç okumaz; anahtarı yalnızca bu puanlama okur.

Sayım birimi bir uyarıdır: izleme motoru aynı durum sürdükçe her gün değil, durumun başladığı gün bir kez uyarır.
Yalnızca önemi "uyari" olanlar alarm sayılır; "bilgi" kayıtları ne olay yakalar ne yanlış alarm sayılır.

Bir olay yakalanmış sayılır, eğer şu üçünü birden sağlayan bir uyarı varsa:
1. Olayın türüne karşılık gelen kuraldan gelir (OLAY_KURALI).
2. Uyarının kimliklerinden biri olayın kimliklerinden biridir (ör. aynı çek, aynı müşteri, aynı ödeme).
3. Uyarının günü olayın penceresi içindedir (PENCERE).
Bir uyarı en çok bir olayı yakalayabilir; olaylar tarih sırasıyla, her biri en erken uygun uyarıyla eşleşir.
"""

from __future__ import annotations

import datetime as dt

G = dt.date.fromisoformat
AY_GUN = 30.4375

OLAY_KURALI = {
    "olagandisi_buyuk_cikis": "buyuk_cikis",
    "mukerrer_odeme": "mukerrer_odeme",
    "karsiliksiz_cek": "karsiliksiz_cek",
    "nakit_sikismasi": "nakit_esigi",
    "eslesmeyen_tahsilat_birikimi": "eslesmeyen_birikim",
    "buyuk_musteri_gecikmesi": "musteri_gecikmesi",
}


def olay_kimlikleri(o: dict) -> set[str]:
    tur = o["tur"]
    if tur == "olagandisi_buyuk_cikis":
        return {o["odeme_id"]}
    if tur == "mukerrer_odeme":
        return {o["mukerrer_odeme_id"], o["asil_odeme_id"], o["alis_fatura_id"]}
    if tur == "karsiliksiz_cek":
        return {o["cek_id"]}
    if tur == "nakit_sikismasi":
        return {"A_TRY"}
    if tur == "eslesmeyen_tahsilat_birikimi":
        return {t["tahsilat_id"] for t in o["tahsilatlar"]}
    if tur == "buyuk_musteri_gecikmesi":
        return {o["musteri_id"]}
    raise ValueError(tur)


def pencere(o: dict, veri_bitis: dt.date) -> tuple[dt.date, dt.date]:
    """Uyarının olay için sayılacağı günler (iki uç dahil)."""
    bas, tur = G(o["tarih"]), o["tur"]
    if tur in ("olagandisi_buyuk_cikis", "karsiliksiz_cek"):
        son = bas + dt.timedelta(days=3)
    elif tur == "mukerrer_odeme":
        son = bas + dt.timedelta(days=14)  # fatura ödemeden sonra görünürse uyarı o gün gelir
    elif tur == "nakit_sikismasi":
        son = G(o["bitis"])
    elif tur == "eslesmeyen_tahsilat_birikimi":
        son = G(o["bitis"]) + dt.timedelta(days=14)
    else:  # buyuk_musteri_gecikmesi: gecikme sürdüğü sürece
        son = bas + dt.timedelta(days=o["gecikme_gun"])
    return bas, min(son, veri_bitis)


def uyari_kimlikleri(u: dict) -> set[str]:
    kimlikler = {u["varlik"]["id"]}
    for liste in u.get("kaynak", {}).values():
        kimlikler.update(liste)
    return kimlikler


def alarmlar(uyarilar: list[dict]) -> list[dict]:
    return [u for u in uyarilar if u["onem"] == "uyari"]


def puanla(uyarilar: list[dict], olaylar: list[dict], veri_bitis: dt.date) -> dict:
    """Olaylı veride: hangi olay yakalandı, kaç günde, hangi alarmlar hiçbir olaya denk gelmedi."""
    adaylar = sorted(alarmlar(uyarilar), key=lambda u: (u["gun"], u["uyari_id"]))
    kullanilan, sonuc = set(), []
    for o in sorted(olaylar, key=lambda o: (o["tarih"], o["olay_id"])):
        bas, son = pencere(o, veri_bitis)
        kimlikler = olay_kimlikleri(o)
        eslesen = next((u for u in adaylar if u["uyari_id"] not in kullanilan and u["kural"] == OLAY_KURALI[o["tur"]]
                        and bas <= G(u["gun"]) <= son and uyari_kimlikleri(u) & kimlikler), None)
        if eslesen:
            kullanilan.add(eslesen["uyari_id"])
        sonuc.append({"olay_id": o["olay_id"], "tur": o["tur"], "tarih": o["tarih"], "pencere": [str(bas), str(son)],
                      "yakalandi": eslesen is not None, "uyari_id": eslesen["uyari_id"] if eslesen else None,
                      "gun_sonra": (G(eslesen["gun"]) - bas).days if eslesen else None})
    eslesmeyen = [u for u in adaylar if u["uyari_id"] not in kullanilan]
    return {
        "olay": len(sonuc), "yakalanan": sum(r["yakalandi"] for r in sonuc),
        "geri_cagirma": round(sum(r["yakalandi"] for r in sonuc) / len(sonuc), 4) if sonuc else None,
        "olaylar": sonuc, "alarm": len(adaylar), "olaya_denk_gelmeyen_alarm": len(eslesmeyen),
        "olaya_denk_gelmeyen_kurala_gore": _say(eslesmeyen),
    }


def yanlis_alarm(uyarilar: list[dict], baslangic: dt.date, bitis: dt.date) -> dict:
    """Olaysız (temiz) veride bütün alarmlar yanlış alarmdır."""
    a = alarmlar(uyarilar)
    ay = ((bitis - baslangic).days + 1) / AY_GUN
    return {"alarm": len(a), "ay": round(ay, 2), "aylik": round(len(a) / ay, 3), "kurala_gore": _say(a),
            "bilgi": len(uyarilar) - len(a)}


def _say(uyarilar: list[dict]) -> dict[str, int]:
    sayi: dict[str, int] = {}
    for u in uyarilar:
        sayi[u["kural"]] = sayi.get(u["kural"], 0) + 1
    return dict(sorted(sayi.items()))
