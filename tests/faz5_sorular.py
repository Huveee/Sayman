"""Faz 5 soru seti: sentetik belgelerin maddelerinden üretilen sorular ve doğru parçaları.

Soru seti arama kodundan önce yazıldı ve donduruldu (özeti reports/faz5_soru_seti.json'da). Sonradan eklenen tek
satır, kütüphaneye eklenen kanunları soru üretiminin dışında tutan süzmedir; setin özeti değişmedi. Arama kodu
(src/sayman/belge) bu dosyayı okumaz.

- Her soru tek bir maddeyi sorar; doğru cevap o maddenin parçasıdır (ör. "SOZ-S003#m5").
- Sözleşme soruları karşı tarafın adını içerir: onlarca sözleşmede aynı madde türleri vardır, soru hangisini
  sorduğunu söylemelidir. İki firmanın kısa adı aynıysa tam ad kullanılır.
- Politika soruları taraf adı içermez. Bir müşterinin sözleşmesindeki maddeyle karışabilecek olanlar ("genel kural
  olarak", "şirket içinde") diye sorulur; böylece cevabı yalnızca politika verir.
- Her madde türü için iki kalıp vardır: "yakın" kalıp maddenin kendi kelimelerini kullanır, "uzak" kalıp aynı şeyi
  günlük dille, maddenin ayırt edici kelimelerinden kaçınarak sorar.
- Ayar ve ayrılmış set iki yönden ayrıdır: belgeler ikiye bölünür (aynı belge iki sette olmaz) ve bir kalıp
  çiftinin bir kalıbı ayar setine, öteki ayrılmış sete gider. Kalıp çiftlerinin rastgele yarısında yakın kalıp ayar
  setine, öteki yarısında ayrılmış sete düşer; böylece iki setin yakın/uzak karışımı benzer olur ve ayar seti
  ayrılmış setten kolay kalmaz. Her sorunun "stil" alanı hangi kalıptan geldiğini söyler.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter

from sayman.belge.sentetik import Belge, arama_metni

SORU_PER_SOZLESME = 4

# alan -> (yakın kalıp, uzak kalıp). {taraf}: karşı tarafın adı.
ORTAK = {
    "sure": ("{taraf} ile yaptığımız sözleşme ne zaman başladı, kaç yıl geçerli?",
             "{taraf} anlaşmamızın bitiş tarihi ne, otomatik yenileniyor mu?"),
    "fesih": ("{taraf} sözleşmesini feshetmek için kaç gün önceden yazılı bildirim gerekir?",
              "{taraf} ile işbirliğini erkenden bitirmek istersek ne kadar önceden haber vermemiz gerekir?"),
    "uyusmazlik": ("{taraf} ile uyuşmazlık çıkarsa hangi mahkeme ya da kurum yetkili?",
                   "{taraf} ile aramızda sorun çıkarsa davayı nerede açarız?"),
}
TEDARIK = {
    **ORTAK,
    "odeme_vadesi": ("{taraf} faturalarını fatura tarihinden itibaren kaç gün içinde ödemeliyiz?",
                     "{taraf} firmasına olan borcumuzu en geç ne zaman kapatmalıyız?"),
    "erken_odeme": ("{taraf} sözleşmesinde erken ödeme iskontosu var mı?",
                    "{taraf} firmasına borcu günü gelmeden kapatırsak bize bir avantaj sağlıyorlar mı?"),
    "gecikme_faizi": ("{taraf} ödemesini vadesinde yapmazsak ne kadar temerrüt faizi işler?",
                      "{taraf} firmasına geç kalırsak her ay ne kadar ek bedel çıkar?"),
    "teslim": ("{taraf} siparişlerimizi kaç iş günü içinde teslim etmek zorunda?",
               "{taraf} firmasına ısmarladığımız ürünler kaç günde elimize ulaşmalı?"),
    "fiyat": ("{taraf} fiyatlarını hangi durumda güncelleyebilir?",
              "{taraf} zam yapmak isterse buna ne zaman hakkı var?"),
    "ayip": ("{taraf} firmasından gelen ayıplı malı kaç gün içinde bildirmeliyiz?",
             "{taraf} ürünleri bozuk çıkarsa itiraz için ne kadar vaktimiz var?"),
    "cezai_sart": ("{taraf} teslimatı geciktirirse ne kadar cezai şart öder?",
                   "{taraf} malları geç getirirse bize ödeyeceği tazminat nasıl belirleniyor?"),
    "gizlilik": ("{taraf} sözleşmesine göre gizlilik yükümlülüğü kaç yıl sürüyor?",
                 "{taraf} ile aramızdaki ticari sırları anlaşma bittikten sonra ne kadar saklamaları gerekiyor?"),
}
HIZMET = {
    **TEDARIK,
    "teslim": ("{taraf} gönderileri kaç iş gününde alıcısına ulaştırmak zorunda?",
               "{taraf} ile yolladığımız paketler müşteriye kaç günde varıyor?"),
    "kira_artisi": ("{taraf} ile kira sözleşmemizde kira bedeli ne zaman ve ne kadar artırılıyor?",
                    "{taraf} firmasına yerimiz için ödediğimiz para yılda bir hangi orana göre yükseliyor?"),
    "depozito": ("{taraf} firmasına kaç aylık depozito verdik?",
                 "Binadan çıkarken {taraf} bize hangi güvence parasını geri verecek?"),
    "okuma": ("{taraf} sayaçları ayın kaçında okuyor?",
              "{taraf} elektrik tüketimimizi her ay hangi gün ölçüyor?"),
    "kesinti": ("{taraf} planlı kesintiyi kaç saat önce bildirmek zorunda?",
                "{taraf} elektriği bilerek keseceği zaman bizi ne kadar önce uyarmalı?"),
    "hasar_limiti": ("{taraf} taşımada hasar ve kayıp olursa en fazla ne kadar öder?",
                     "{taraf} ile kargoda kırılan paketler için alabileceğimiz tazminatın üst sınırı ne?"),
    "sla": ("{taraf} yazılım hizmeti için hangi erişilebilirlik oranını taahhüt ediyor?",
            "{taraf} sistemi sık sık çökerse faturadan ne kadar düşülür?"),
    "destek": ("{taraf} kritik arızaya kaç saat içinde müdahale etmek zorunda?",
               "{taraf} yazılımı tamamen durursa ekipleri en geç ne zaman devreye girmeli?"),
    "taahhut": ("{taraf} ile taahhüt süremiz kaç ay?",
                "{taraf} hattını erken kapatırsak ne kadar ödememiz gerekir?"),
}
SATIS = {
    **ORTAK,
    "odeme_vadesi": ("{taraf} faturalarını fatura tarihinden itibaren kaç gün içinde ödüyor?",
                     "{taraf} aldığı malların parasını bize en geç ne zaman yatırmalı?"),
    "teslim_sekli": ("{taraf} ile satışlarda teslim şekli ve para birimi ne?",
                     "{taraf} siparişlerinde nakliye kimin üzerinde, hangi dövizle çalışıyoruz?"),
    "kredi_limiti": ("{taraf} için tanımlı kredi limiti ne kadar?",
                     "{taraf} bize en fazla ne kadar borçlu kalabilir?"),
    "cek": ("{taraf} çekle ödeme yapabilir mi?",
            "{taraf} parayı ileri tarihli bir kâğıtla ödemek isterse kabul ediyor muyuz?"),
    "gecikme_faizi": ("{taraf} vadesinde ödemezse ne kadar temerrüt faizi uygularız?",
                      "{taraf} borcunu gününde kapatmazsa ona her ay ne kadar ek bedel yansıtırız?"),
    "iade": ("{taraf} aldığı ürünleri kaç gün içinde iade edebilir?",
             "{taraf} beğenmediği malları ne kadar süre içinde geri gönderebilir?"),
    "teminat": ("{taraf} bize teminat mektubu vermiş mi, ne kadar?",
                "{taraf} bize karşı bir güvence belgesi vermek zorunda mı, tutarı ne?"),
}
POLITIKA = {
    "guvenlik_esigi": ("Ana TL hesabının en düşük bakiyesi ne olmalı?",
                       "Şirketin ana banka hesabında sürekli kalması gereken en az para ne kadar?"),
    "banka_dagilimi": ("Toplam nakdin ne kadarı tek bir bankada tutulabilir?",
                       "Paramızı farklı kuruluşlara yaymak zorunda mıyız, birinde en fazla ne kadarı durabilir?"),
    "fazla_nakit": ("Fazla nakit hangi araçlarda ve hangi vadeyle değerlendirilir?",
                    "Kullanmadığımız parayı borsada ya da başka bir yerde nemalandırabilir miyiz?"),
    "nakit_raporu": ("Günlük nakit raporu CFO'ya saat kaçta iletilmeli?",
                     "Her gün paranın durumunu gösteren özet yöneticiye kaça kadar ulaşmalı?"),
    "onay_limitleri": ("Finans müdürü en fazla ne kadarlık ödemeyi onaylayabilir?",
                       "Para çıkışlarına izin verme yetkisi kimde, tutara göre değişiyor mu?"),
    "cift_imza": ("Hangi tutarı aşan ödemeler çift imza gerektirir?",
                  "Çok büyük bir havalede tek yöneticinin izni yetmiyorsa kimler birlikte izin vermeli?"),
    "ilk_odeme": ("Daha önce ödeme yapılmamış bir alıcıya ilk ödemede ne yapılır?",
                  "Hiç çalışmadığımız bir firmaya ilk defa havale yapacaksak önce neyi doğrulamalıyız?"),
    "mukerrer": ("Aynı tedarikçi faturasına ikinci ödeme yapılmasını nasıl engelliyoruz?",
                 "Bir faturanın parası yanlışlıkla iki kere gönderilmek istenirse ne olur?"),
    "takip_adimlari": ("Vadesi geçen alacak için müşteriye ne zaman hatırlatma ve ihtar gönderilir?",
                       "Parasını geciktiren bir alıcıya ilk uyarıyı kaçıncı günde yaparız?"),
    "sevkiyat_durdurma": ("Alacağı geciken müşteriye sevkiyat ne zaman durdurulur?",
                          "Hangi gecikmeden sonra bir alıcının siparişlerini artık yollamayız?"),
    "hukuki_takip": ("Vadesi geçen alacak ne zaman hukuk birimine devredilir?",
                     "Hiç ödemeyen bir müşteriye karşı avukata ne zaman başvururuz?"),
    "limit_asimi": ("Şirket içinde kredi limiti aşımını hangi yönetici onaylar?",
                    "Bir alıcı kendisine tanınan borç tavanını aşmak isterse içeride kimin iznini almalıyız?"),
    "cek_vadesi": ("Genel kural olarak en uzun kaç gün vadeli çek kabul edilir?",
                   "Şirket olarak alabileceğimiz bir çek en fazla ne kadar ileri tarihli olabilir?"),
    "ciro": ("Genel kural olarak ciro edilmiş çek kabul ediyor muyuz?",
             "Kural olarak bir müşteri başkasından aldığı çeki bize verebilir mi?"),
    "tahsile_verme": ("Portföydeki çekler bankaya ne zaman tahsile verilir?",
                      "Müşterilerden aldığımız çekleri paraya çevirmek için ne zaman işleme koyarız?"),
    "karsiliksiz": ("Genel kural olarak çeki karşılıksız çıkan müşteriye nasıl davranılır?",
                    "Kural olarak bir alıcının verdiği çek bankadan dönerse ona sonra nasıl satış yaparız?"),
    "pozisyon_siniri": ("Net açık döviz pozisyonu en fazla ne kadar olabilir?",
                        "Yabancı para borcumuz alacağımızı en fazla ne kadar aşabilir?"),
    "korunma_orani": ("İhracat alacaklarının ne kadarı vadeli döviz satışıyla korunmalı?",
                      "Yurt dışındaki müşterilerden beklediğimiz paraların ne kadarını kur dalgalanmasına karşı "
                      "güvenceye almalıyız?"),
    "araclar": ("Kur riskinden korunmak için hangi araçlara izin veriliyor?",
                "Döviz dalgalanmasına karşı hangi finansal sözleşmeleri yapabiliriz?"),
    "kayit_belgeleri": ("Yeni tedarikçi kaydı için hangi belgeler istenir?",
                        "Bir firmayı satıcı olarak sisteme açmak için ne evrak lazım?"),
    "iban_degisikligi": ("Tedarikçinin banka hesabı değişikliği nasıl sisteme girilir?",
                         "Bir satıcı bize farklı bir IBAN yollarsa parayı hemen oraya gönderebilir miyiz?"),
    "yogunlasma": ("Tek bir tedarikçiden yapılan alımlar en fazla ne kadar olabilir?",
                   "Satın almalarımızı tek bir satıcıya ne kadar bağlayabiliriz?"),
}
KALIPLAR = {"tedarik_sozlesmesi": TEDARIK, "hizmet_sozlesmesi": HIZMET, "satis_sozlesmesi": SATIS,
            "politika": POLITIKA}
CIFTLER = sorted({c for grup in KALIPLAR.values() for c in grup.values()})


def ters_ciftler(tohum: int = 42) -> set[tuple[str, str]]:
    """Yakın kalıbı ayrılmış sete, uzak kalıbı ayar setine giden kalıp çiftleri (rastgele yarısı)."""
    ciftler = list(CIFTLER)
    random.Random(f"{tohum}:belge-kalip").shuffle(ciftler)
    return set(ciftler[: len(ciftler) // 2])


def soru_seti(belgeler: list[Belge], tohum: int = 42) -> dict[str, list[dict]]:
    """{"ayar": [...], "ayrilmis": [...]}: her soru {soru_id, soru, stil, dogru_parca, belge_id, tur, alan, deger,
    roller}."""
    belgeler = [b for b in belgeler if b.tur in KALIPLAR]  # kanunlar (karıştırıcı belgeler) soru almaz
    rng = random.Random(f"{tohum}:belge-soru")
    ters = ters_ciftler(tohum)
    kisa = Counter(b.kisa_ad for b in belgeler if b.kisa_ad)
    ayar_belgeleri = set()
    for tur in sorted({b.tur for b in belgeler}):
        grup = sorted(b.belge_id for b in belgeler if b.tur == tur)
        rng.shuffle(grup)
        ayar_belgeleri.update(grup[: (len(grup) + 1) // 2])

    sonuc: dict[str, list[dict]] = {"ayar": [], "ayrilmis": []}
    for b in sorted(belgeler, key=lambda b: b.belge_id):
        maddeler = [m for m in b.maddeler if m.alan]
        if b.tur != "politika":
            maddeler = rng.sample(maddeler, min(SORU_PER_SOZLESME, len(maddeler)))
        set_adi = "ayar" if b.belge_id in ayar_belgeleri else "ayrilmis"
        taraf = (b.kisa_ad if kisa[b.kisa_ad] == 1 else b.taraf_ad) if b.kisa_ad else None
        for m in sorted(maddeler, key=lambda m: m.no):
            cift = KALIPLAR[b.tur][m.alan]
            stil = "yakin" if (set_adi == "ayar") != (cift in ters) else "uzak"
            sonuc[set_adi].append({
                "soru_id": f"{'A' if set_adi == 'ayar' else 'H'}-{m.parca_id}",
                "soru": cift[0 if stil == "yakin" else 1].format(taraf=taraf), "stil": stil,
                "dogru_parca": m.parca_id, "belge_id": b.belge_id, "tur": b.tur, "alan": m.alan,
                "deger": m.deger, "roller": list(b.roller)})
    return sonuc


def ozet(sorular: dict[str, list[dict]]) -> str:
    metin = json.dumps(sorular, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(metin.encode("utf-8")).hexdigest()


def belge_ozeti(belgeler: list[Belge]) -> str:
    """Parça kimliği, madde metni ve aranan metin üzerinden özet: belge üretici değişirse bu da değişir."""
    satirlar = [[m.parca_id, m.metin, arama_metni(b, m)] for b in belgeler for m in b.maddeler]
    return hashlib.sha256(json.dumps(satirlar, ensure_ascii=False).encode("utf-8")).hexdigest()


def sans_tabani(sorular: list[dict], belgeler: list[Belge], k: int = 5) -> float | None:
    """Arama doğru sözleşmeyi bulup içindeki maddeleri rastgele sıralasa beklenen isabet@k (yalnızca sözleşmeler).

    Sözleşme soruları taraf adını içerdiği için kelime araması doğru belgeye kolayca ulaşır; bu taban, isabet@k'nın
    ne kadarının yalnızca belge adını bulmaktan geldiğini gösterir."""
    madde_sayisi = {b.belge_id: len(b.maddeler) for b in belgeler}
    s = [min(k, madde_sayisi[x["belge_id"]]) / madde_sayisi[x["belge_id"]] for x in sorular if x["tur"] != "politika"]
    return round(sum(s) / len(s), 4) if s else None
