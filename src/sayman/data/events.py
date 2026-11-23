"""Olay katmanı: temel verinin kopyasına, sayısı ayarda yazılı olaylar ekler.

Her olayın doğru cevabı (ne oldu, hangi kayıtlar etkilendi) cevap anahtarına yazılır; anahtar
araçların okuduğu tablolara girmez. Her türün son olayı "şimdi"den sonraya düşer: as-of görünümü
bunları göstermez, saat ileri sarılınca ortaya çıkarlar.

Sıra: önce nakit dışı olaylar, sonra kalıcı para çıkışları, en son geçici nakit sıkışmaları. Çıkış ve
sıkışmalar o anki defterden boyutlanır, böylece hiçbir hesap eksiye düşmez. Nakit sıkışması kalıcı çıkış
değildir: tedarikçi ödemeleri öne çekilir, eski vadeleri geçince bakiye temel veriye döner.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

from sayman.data.money import kurusa, kurustan
from sayman.data.generator import AD_ILK, AD_TUR, GUN, akis, is_gunune_al, yeni_id

KARSI_TARAF = ["Vega", "Orion", "Delta", "Lima", "Sigma", "Kappa", "Nova", "Polar"]
CIKIS_NEDENI = ["DANIŞMANLIK BEDELİ", "MAKİNE AVANSI", "GAYRİMENKUL KAPORASI", "ORTAK CARİ HESAP"]
NAKIT_DISI = ("buyuk_musteri_gecikmesi", "karsiliksiz_cek", "mukerrer_odeme", "eslesmeyen_tahsilat_birikimi")
NAKIT = ("olagandisi_buyuk_cikis", "nakit_sikismasi")
CIKIS_KATI = (Decimal("2.0"), Decimal("3.0"))  # olağandışı çıkış, tedarikçi ödemelerinin 95. yüzdeliğinin bu katları arası
CIKIS_TAVANI = Decimal("0.8")  # olağandışı çıkış, o günden sonraki en düşük A_TRY bakiyesinin bu kadarını geçemez
SIKISMA_PENCERESI = 14  # gün: nakit sıkışmasının bakiyeyi eşiğin altına indirdiği süre
ONE_CEKME_UFKU = 120  # gün: öne çekilebilecek tedarikçi ödemelerinin en uzak tarihi


class AdayYok(RuntimeError):
    pass


def esik_kurus(u) -> int:
    """Nakit sıkışması eşiği: ortalama aylık satışın %5'i."""
    return kurusa(kurustan(u.ortalama_aylik_satis) * Decimal("0.05"))


def _gun_sec(u, rng, sonra: bool) -> tuple[dt.date, dt.date, dt.date]:
    """(olay günü, aramanın en erken günü, aramanın en geç günü)."""
    if sonra:
        alt, ust = u.a.simdi + 7 * GUN, u.bitis - 30 * GUN
        en_erken, sinir = u.a.simdi + GUN, u.bitis
    else:
        alt, ust = u.baslangic + 90 * GUN, u.a.simdi - 45 * GUN
        en_erken, sinir = u.baslangic + 60 * GUN, u.a.simdi - GUN
    ust = max(ust, alt)
    return is_gunune_al(alt + rng.randint(0, (ust - alt).days) * GUN), en_erken, sinir


def a_try_gunluk(u, kayitlar, acilis) -> dict[dt.date, tuple[int, int]]:
    """A_TRY'nin her gün (gün içi en düşük, gün sonu) bakiyesi. Gün içi en düşük, günün açılışını
    (bir önceki günün kapanışını) da kapsar."""
    satirlar = [s for s in u.defter(kayitlar["tahsilatlar"], kayitlar["odemeler"], acilis) if s["hesap_id"] == "A_TRY"]
    gunluk, i, kapanis, gun = {}, 0, acilis["A_TRY"], u.baslangic
    while gun <= u.bitis:
        en_dusuk = kapanis
        while i < len(satirlar) and satirlar[i]["tarih"] == gun:
            kapanis = satirlar[i]["bakiye_kurus"]
            en_dusuk = min(en_dusuk, kapanis)
            i += 1
        gunluk[gun] = (en_dusuk, kapanis)
        gun += GUN
    return gunluk


def a_try_gunluk_en_dusuk(u, kayitlar, acilis) -> dict[dt.date, int]:
    return {g: v[0] for g, v in a_try_gunluk(u, kayitlar, acilis).items()}


def _cakisir(kullanilan: set, bas: dt.date, bit: dt.date) -> bool:
    """Para çıkışı olaylarının etki aralıkları üst üste binmesin: her sıkışma tek bir olayın sonucu olsun."""
    return any(isinstance(k, tuple) and k[0] == "nakit" and k[1] <= bit and bas <= k[2] for k in kullanilan)


def _tedarikci_p95(kayitlar) -> int:
    tutarlar = sorted(o["tutar_kurus"] for o in kayitlar["odemeler"] if o["tur"] == "tedarikci")
    return tutarlar[len(tutarlar) * 95 // 100]


# ---------------------------------------------------------------- nakit dışı olaylar


def buyuk_musteri_gecikmesi(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    for bas in (gun, en_erken):
        bit = min(bas + 90 * GUN, sinir)
        adaylar = {}
        for t in kayitlar["tahsilatlar"]:
            if (bas <= t["tarih"] <= bit and t["yontem"] in ("havale", "swift") and t["eslesme"] == "eslesti"
                    and t["musteri_id"] not in kullanilan and not u.musteri_ada[t["musteri_id"]]["_batik"]):
                adaylar.setdefault(t["musteri_id"], []).append(t)
        adaylar = {m: v for m, v in adaylar.items() if len(v) >= 3}
        if adaylar:
            break
    else:
        raise AdayYok("büyük müşteri gecikmesi için aday yok")
    sirali = sorted(adaylar, key=lambda m: (-u.musteri_toplam[m], m))
    musteri = sirali[rng.randrange(min(3, len(sirali)))]
    kullanilan.add(musteri)
    gecikme = rng.randint(45, 90)
    etkilenen, silinen = [], set()
    for t in sorted(adaylar[musteri], key=lambda t: (t["tarih"], t["tahsilat_id"])):
        eski, yeni = t["tarih"], is_gunune_al(t["tarih"] + gecikme * GUN)
        etkilenen.append({"tahsilat_id": t["tahsilat_id"], "fatura_id": t["fatura_id"], "eski_tarih": eski.isoformat(),
                          "yeni_tarih": yeni.isoformat() if yeni <= u.bitis else None})
        if yeni > u.bitis:
            silinen.add(t["tahsilat_id"])
        else:
            t["tarih"] = yeni
    kayitlar["tahsilatlar"] = [t for t in kayitlar["tahsilatlar"] if t["tahsilat_id"] not in silinen]
    return {
        "tarih": etkilenen[0]["eski_tarih"], "musteri_id": musteri, "gecikme_gun": gecikme, "tahsilatlar": etkilenen,
        "aciklama": f"Büyük bir müşteri ödemelerini {gecikme} gün geciktirmeye başladı.",
        "etkilenen": {"tahsilatlar": [e["tahsilat_id"] for e in etkilenen]},
    }


def karsiliksiz_cek(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    tutarlar = sorted(c["tutar_kurus"] for c in kayitlar["cekler"])
    if not tutarlar:
        raise AdayYok("çek yok")
    esik = tutarlar[len(tutarlar) * 3 // 4]
    cek_ada = {c["cek_id"]: c for c in kayitlar["cekler"]}
    for bas in (gun, en_erken):
        adaylar = sorted(
            (h for h in kayitlar["cek_hareketleri"]
             if h["durum"] == "tahsil_edildi" and bas <= h["tarih"] <= sinir and h["cek_id"] not in kullanilan
             and cek_ada[h["cek_id"]]["tutar_kurus"] >= esik),
            key=lambda h: (h["tarih"], h["cek_id"]))
        if adaylar:
            break
    else:
        raise AdayYok("karşılıksız çek için aday yok")
    hareket = adaylar[rng.randrange(min(5, len(adaylar)))]
    cek = cek_ada[hareket["cek_id"]]
    kullanilan.add(cek["cek_id"])
    hareket["durum"] = "karsiliksiz"
    silinen = [t["tahsilat_id"] for t in kayitlar["tahsilatlar"] if t["cek_id"] == cek["cek_id"]]
    kayitlar["tahsilatlar"] = [t for t in kayitlar["tahsilatlar"] if t["cek_id"] != cek["cek_id"]]
    return {
        "tarih": hareket["tarih"].isoformat(), "cek_id": cek["cek_id"], "musteri_id": cek["musteri_id"],
        "tutar_kurus": cek["tutar_kurus"],
        "aciklama": "Büyük tutarlı bir müşteri çeki vadesinde karşılıksız çıktı; fatura açık kaldı.",
        "etkilenen": {"cek_hareketleri": [hareket["cek_hareket_id"]], "tahsilatlar": silinen},
    }


def mukerrer_odeme(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    tutarlar = sorted(o["tutar_kurus"] for o in kayitlar["odemeler"] if o["tur"] == "tedarikci")
    orta = tutarlar[len(tutarlar) // 2]
    for bas in (gun, en_erken):
        adaylar = sorted(
            (o for o in kayitlar["odemeler"]
             if o["tur"] == "tedarikci" and o["yontem"] == "havale" and bas <= o["tarih"] <= sinir
             and o["tutar_kurus"] >= orta and o["odeme_id"] not in kullanilan),
            key=lambda o: (o["tarih"], o["odeme_id"]))
        if adaylar:
            break
    else:
        raise AdayYok("mükerrer ödeme için aday yok")
    asil = adaylar[rng.randrange(min(10, len(adaylar)))]
    kullanilan.add(asil["odeme_id"])
    kopya = copy.deepcopy(asil)
    kopya["odeme_id"] = yeni_id("O", rng_kimlik, {o["odeme_id"] for o in kayitlar["odemeler"]})
    yeni_gun = is_gunune_al(asil["tarih"] + rng.randint(0, 2) * GUN)
    kopya["tarih"] = yeni_gun if yeni_gun <= sinir else asil["tarih"]
    kayitlar["odemeler"].append(kopya)
    return {
        "tarih": kopya["tarih"].isoformat(), "asil_odeme_id": asil["odeme_id"], "mukerrer_odeme_id": kopya["odeme_id"],
        "tedarikci_id": asil["tedarikci_id"], "alis_fatura_id": asil["alis_fatura_id"], "tutar_kurus": asil["tutar_kurus"],
        "aciklama": "Aynı tedarikçi faturası iki kez ödendi.",
        "etkilenen": {"odemeler": [kopya["odeme_id"]]},
    }


def eslesmeyen_tahsilat_birikimi(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    for bas in (gun, en_erken):
        bit = min(bas + 21 * GUN, sinir)
        adaylar = {}
        for t in kayitlar["tahsilatlar"]:
            if (bas <= t["tarih"] <= bit and t["yontem"] == "havale" and t["eslesme"] == "eslesti"
                    and t["musteri_id"] not in kullanilan):
                adaylar.setdefault(t["musteri_id"], []).append(t)
        if sum(len(v) for v in adaylar.values()) >= 4:
            break
    else:
        raise AdayYok("eşleşmeyen tahsilat birikimi için aday yok")
    musteriler = sorted(adaylar, key=lambda m: (-len(adaylar[m]), m))[:3]
    secilen = sorted((t for m in musteriler for t in adaylar[m]), key=lambda t: (t["tarih"], t["tahsilat_id"]))[:8]
    for t in secilen:
        t["fatura_id"] = None
        t["eslesme"] = "bilerek_eslesmemis"
    kullanilan.update(musteriler)
    return {
        "tarih": secilen[0]["tarih"].isoformat(), "bitis": secilen[-1]["tarih"].isoformat(),
        "tahsilatlar": [{"tahsilat_id": t["tahsilat_id"], "gercek_fatura_id": t["_gercek_fatura"]} for t in secilen],
        "aciklama": "Kısa sürede, açıklamasında fatura bilgisi olmayan çok sayıda havale geldi.",
        "etkilenen": {"tahsilatlar": [t["tahsilat_id"] for t in secilen]},
    }


# ---------------------------------------------------------------- para çıkışı yaratan olaylar


def olagandisi_buyuk_cikis(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    """Kalıcı çıkış: tutar, o günden veri sonuna kadarki en düşük A_TRY bakiyesinin %80'ini geçemez."""
    p95 = _tedarikci_p95(kayitlar)
    kat = Decimal(rng.randint(int(CIKIS_KATI[0] * 100), int(CIKIS_KATI[1] * 100))) / 100
    tutar = kurusa(kurustan(p95) * kat)
    if _cakisir(kullanilan, gun - SIKISMA_PENCERESI * GUN, gun + SIKISMA_PENCERESI * GUN):
        raise AdayYok("başka bir para çıkışı olayına çok yakın")
    gunluk = a_try_gunluk_en_dusuk(u, kayitlar, acilis)
    if tutar > kurusa(kurustan(min(b for g, b in gunluk.items() if g >= gun)) * CIKIS_TAVANI):
        raise AdayYok("olağandışı çıkış için bakiye yetersiz")
    karsi = f"{rng.choice(KARSI_TARAF)} {rng.choice(AD_ILK)} {rng.choice(AD_TUR)}"
    odeme = {
        "odeme_id": yeni_id("O", rng_kimlik, {o["odeme_id"] for o in kayitlar["odemeler"]}), "tur": "diger",
        "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None, "iade_fatura_id": None, "tarih": gun,
        "tutar_kurus": tutar, "yontem": "havale", "banka_hesap_id": "A_TRY",
        "aciklama": f"EFT GİDEN {karsi} {rng.choice(CIKIS_NEDENI)}",
    }
    kayitlar["odemeler"].append(odeme)
    kullanilan.add(("nakit", gun, gun))
    return {
        "tarih": gun.isoformat(), "odeme_id": odeme["odeme_id"], "tutar_kurus": tutar,
        "tedarikci_odemesi_p95_kurus": p95, "p95_kati": str(kat),
        "aciklama": f"Daha önce görülmemiş bir karşı tarafa, tedarikçi ödemelerinin 95. yüzdeliğinin {kat} katı "
                    "tutarında faturasız para çıkışı.",
        "etkilenen": {"odemeler": [odeme["odeme_id"]]},
    }


def nakit_sikismasi(u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik):
    """Geçici sıkışma: ileri vadeli tedarikçi ödemeleri olay gününe çekilir (erken ödeme). Pencere boyunca
    A_TRY eşiğin altına iner; öne çekilen ödemelerin eski tarihleri geçince bakiye temel veriye döner."""
    esik = esik_kurus(u)
    gunluk_iki = a_try_gunluk(u, kayitlar, acilis)
    gunluk = {g: v[0] for g, v in gunluk_iki.items()}
    pencere_son = min(gun + SIKISMA_PENCERESI * GUN, u.bitis)
    pencere_en_dusuk = min(b for g, b in gunluk.items() if gun <= g <= pencere_son)
    if pencere_en_dusuk < esik:
        raise AdayYok("bakiye olaydan önce zaten eşiğin altında")
    # Ödemeler en yakın vadeliden başlayarak tek tek öne çekilir. Öne çekilen ödeme, olay günüyle eski tarihi
    # arasındaki her günü kendi tutarı kadar düşürür (eski günü de, temkinli olarak). Bir günü eksiye indirecek
    # ödeme atlanır; pencere içindeki en düşük bakiye eşiğin altına inince durulur. Olay gününde öne çekilen
    # ödemeler o gün gelen paralardan sonra düşer: olay gününün açılışı değil kapanışı azalır.
    ufuk = min(gun + ONE_CEKME_UFKU * GUN, u.bitis)
    gunler = [g for g in sorted(gunluk) if gun <= g <= ufuk]
    dusus = {g: 0 for g in gunler}

    def pencere_degeri(g):
        return (gunluk_iki[g][1] if g == gun else gunluk[g]) - dusus[g]

    # Başka bir olaya ait ödeme aday değildir: asıl ödemeler kullanılanlarda düz kimlikle, olayların etkilediği
    # ödemeler (ör. mükerrer ödemenin kopyası) ("olay_odemesi", kimlik) etiketiyle durur. Öne çekilselerdi o olayın
    # cevap anahtarındaki tarihi verideki tarihle uyuşmazdı.
    adaylar = sorted((o for o in kayitlar["odemeler"]
                      if o["tur"] == "tedarikci" and pencere_son < o["tarih"] <= ufuk and o["odeme_id"] not in kullanilan
                      and ("olay_odemesi", o["odeme_id"]) not in kullanilan),
                     key=lambda o: (o["tarih"], o["odeme_id"]))
    secilen, toplam = [], 0
    for o in adaylar:
        etkilenen_gunler = [g for g in gunler if g <= o["tarih"]]
        if any(gunluk[g] - dusus[g] - o["tutar_kurus"] < 0 for g in etkilenen_gunler):
            continue
        for g in etkilenen_gunler:
            dusus[g] += o["tutar_kurus"]
        secilen.append(o)
        toplam += o["tutar_kurus"]
        if min(pencere_degeri(g) for g in gunler if g <= pencere_son) < esik:
            break
    else:
        raise AdayYok("öne çekilecek tedarikçi ödemesi yetmiyor")
    biter = max(o["tarih"] for o in secilen)  # son öne çekilen ödemenin eski tarihi: sıkışma en geç burada biter
    if _cakisir(kullanilan, gun - SIKISMA_PENCERESI * GUN, biter + SIKISMA_PENCERESI * GUN):
        raise AdayYok("başka bir para çıkışı olayıyla çakışıyor")
    tasinan = [{"odeme_id": o["odeme_id"], "eski_tarih": o["tarih"].isoformat()} for o in secilen]
    for o in secilen:
        o["tarih"] = gun
        kullanilan.add(o["odeme_id"])
    kullanilan.add(("nakit", gun, biter))
    sonra = a_try_gunluk_en_dusuk(u, kayitlar, acilis)
    altinda = sorted(g for g, b in sonra.items() if gun <= g <= biter and b < esik)
    return {
        "tarih": gun.isoformat(), "bitis": biter.isoformat(), "esik_kurus": esik, "one_cekilen_toplam_kurus": toplam,
        "odemeler": tasinan,
        "olay_oncesi_pencere_en_dusuk_kurus": pencere_en_dusuk,
        "pencere_en_dusuk_kurus": min(b for g, b in sonra.items() if gun <= g <= pencere_son),
        "esik_alti_ilk_gun": altinda[0].isoformat() if altinda else None,
        "esik_alti_son_gun": altinda[-1].isoformat() if altinda else None,
        "aciklama": "İleri vadeli tedarikçi ödemeleri erken yapıldı; A_TRY bakiyesi ortalama aylık satışın %5'inin "
                    "altına indi.",
        "etkilenen": {"odemeler": [o["odeme_id"] for o in secilen]},
    }


OLAY_FONKSIYONLARI = {
    "buyuk_musteri_gecikmesi": buyuk_musteri_gecikmesi,
    "karsiliksiz_cek": karsiliksiz_cek,
    "mukerrer_odeme": mukerrer_odeme,
    "eslesmeyen_tahsilat_birikimi": eslesmeyen_tahsilat_birikimi,
    "olagandisi_buyuk_cikis": olagandisi_buyuk_cikis,
    "nakit_sikismasi": nakit_sikismasi,
}


def uygula(u, kayitlar: dict, acilis: dict[str, int]) -> dict:
    sayilar = dict(u.a.olay_sayilari)
    if set(sayilar) - set(OLAY_FONKSIYONLARI):
        raise ValueError(f"bilinmeyen olay türü: {set(sayilar) - set(OLAY_FONKSIYONLARI)}")
    rng, rng_kimlik = akis(u.a, "olay"), akis(u.a, "kimlik:olay")
    kullanilan: set = set()
    sonuclar = []

    def planla(turler):
        planlar = []
        for tur in turler:
            adet = sayilar.get(tur, 0)
            for i in range(adet):
                sonra = tur in u.a.simdiden_sonra_olay and i == adet - 1
                planlar.append((tur, sonra, _gun_sec(u, rng, sonra)))
        return planlar

    # Sıra: nakit dışı olaylar (tür sırasıyla), kalıcı çıkışlar, geçici sıkışmalar (ikisi de tarih sırasıyla).
    # Sıkışmalar en sonda: bilerek düşürdükleri bakiye, sonradan eklenen kalıcı bir çıkışın tavanını belirlemesin.
    sirali = planla(NAKIT_DISI)
    for tur in NAKIT:
        sirali += sorted(planla([tur]), key=lambda p: p[2][0])
    for tur, sonra, (gun, en_erken, sinir) in sirali:
        for deneme in range(20):  # aday bulunamazsa başka bir gün denenir
            if deneme:
                gun, en_erken, sinir = _gun_sec(u, rng, sonra)
            try:
                sonuc = OLAY_FONKSIYONLARI[tur](u, kayitlar, acilis, rng, gun, en_erken, sinir, kullanilan, rng_kimlik)
                sonuclar.append((tur, sonuc))
                # Olayın etkilediği ödemeler etiketle işaretlenir; sonraki nakit sıkışmaları onları öne çekmez.
                # Düz kimlik değil etiket: düz kimlik, sonraki mükerrer ödeme olaylarının aday listesini de daraltırdı.
                kullanilan.update(("olay_odemesi", i) for i in sonuc["etkilenen"].get("odemeler", []))
                break
            except AdayYok:
                if deneme == 19:
                    raise
    for i, (tur, sonuc) in enumerate(sorted(sonuclar, key=lambda x: (x[1]["tarih"], x[0])), start=1):
        u.anahtar["olaylar"].append({"olay_id": f"E{i:02d}", "tur": tur,
                                     "simdiden_sonra": dt.date.fromisoformat(sonuc["tarih"]) > u.a.simdi, **sonuc})
    return kayitlar
