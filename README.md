# Sayman

Yapay Zekâ Destekli Finans Asistanı. Entertech Yapay Zekâ Kariyer Atölyesi final projesi.

Hazırlayan: Hüveyda Başyurtlu

## Kurulum

Python 3.11 gerekir.

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

API anahtarları gerektiğinde `.env.example` dosyası `.env` adıyla kopyalanıp doldurulur. `.env` git'e eklenmez.

## Testler

```bash
.venv/Scripts/python -m pytest
```

Testler internete ve Ollama'ya gitmez; ölçüm betiklerinin kaydettiği dosyaları okur.

## Faz 0: model hız ölçümü

Ollama çalışırken ve aday modeller indirilmişken:

```bash
.venv/Scripts/python scripts/faz0_hiz_olcumu.py
```

Sonuç `reports/faz0_hiz.json` dosyasına yazılır.

## Faz 1: sentetik şirket verisi

Gerçek bir iskeletin (UCI fatura kalemleri, TCMB kurları) üzerine, tohumla birebir tekrarlanabilen
sentetik bir Türk toptancı şirket kurulur. EVDS anahtarı yalnızca kur ve kalibrasyon kaynaklarını
yeniden indirmek için gerekir; bunların sonuçları `data/kaynak/` klasöründe hazırdır.

```bash
.venv/Scripts/python -I scripts/faz1_uci_indir.py
.venv/Scripts/python -I scripts/faz1_uci_profil.py
.venv/Scripts/python -I scripts/faz1_veri_uret.py
```

Tablolar `data/uretilen/`, olayların cevap anahtarı ayrı olarak `data/anahtar/`, simülasyon saati
`data/saat.json`, özet ve denetim sonuçları `reports/faz1_veri.json` dosyasına yazılır.

## Faz 2: veri ambarı ve metrikler

Üretilen tablolar tek bir DuckDB dosyasına yüklenir. Yedi metrik SQL ile, verilen gün itibarıyla
hesaplanır: nakit pozisyonu, alacak yaşlandırma, DSO, geciken alacaklar, yaklaşan ödemeler, kur riski ve
13 haftalık nakit akış tahmini. Her metrik yalnızca o güne kadarki kayıtları kullanır.

```bash
.venv/Scripts/python -I scripts/faz2_ambar_kur.py
```

Ambar `data/ambar/sayman.duckdb`, "şimdi" için metrik özetleri `reports/faz2_metrikler.json` dosyasına
yazılır. Testler her metriği bağımsız bir pandas hesabıyla, elle hesaplanmış küçük bir veriyle ve Faz 1
hesaplarıyla karşılaştırır. Nakit akış tahmini yalnızca bilinen kalemleri içerir (açık alacaklar, çekler,
POS valörleri, ödenmemiş alış faturaları); maaş, vergi ve henüz kesilmemiş satışlar dahil değildir.

## Faz 3: araçlar ve yetki

Model veriye yalnızca 13 salt okunur araçla ulaşır; araçlar bir MCP sunucusunda durur. Her çağrı tek bir
kapıdan geçer: imzalı kimlik kartı, rolün araç izni, argüman şeması, "bugün"den sonraki tarih kontrolü.
Satış müdürleri yalnızca kendi müşterilerinin satırlarının bulunduğu ayrı, salt okunur bir veritabanında
çalışır. Reddedilenler dahil her çağrı, satırları birbirine zincirlenmiş bir denetim kaydına yazılır.

```bash
.venv/Scripts/python -I scripts/faz3_anahtar_uret.py
.venv/Scripts/python -I scripts/faz3_araclar_rapor.py
```

İlk betik kimlik kartlarını imzalayan anahtarı `.env` dosyasına yazar (ekrana yazmaz). İkincisi 60 yetkisiz
denemeyi gerçek veride çalıştırır, cevap boyutlarını ölçer ve `reports/faz3_araclar.json` dosyasını yazar;
Ollama açıksa `--token` ile token sayısını da ölçer. Sunucuyu bir MCP istemcisi
`.venv/Scripts/python -I scripts/faz3_sunucu.py` komutuyla başlatır. Roller ve araç izinleri
`src/sayman/araclar/roller.json` dosyasındadır; kullanıcılar kurgusaldır.

## Faz 4: izleme ve uyarılar

Uyarıyı kurallar verir; model (Faz 6) yalnızca uyarıyı açıklar. Altı kural her gün yalnızca o güne kadar
görünen kayıtlara bakar ve bir sorun başladığında bir kez uyarır:

| Kural | Ne zaman uyarır |
| --- | --- |
| `nakit_esigi` | Ana TL hesabının gün içi en düşük bakiyesi, son 12 ayın ortalama aylık satışının %10'unun altına indi |
| `buyuk_cikis` | Daha önce ödeme yapılmamış bir karşı tarafa, faturasız ve son 12 ayın tedarikçi ödemelerinin %95'inden büyük ödeme |
| `mukerrer_odeme` | Bir tedarikçi faturasına tutarından fazla ödendi |
| `karsiliksiz_cek` | Bir müşteri çeki karşılıksız çıktı (tutarı son 12 ayın çeklerinin üst çeyreğindeyse uyarı, değilse bilgi) |
| `eslesmeyen_birikim` | Son 14 günde faturası belli olmayan tahsilat sayısı, geçmişe göre şansla açıklanamayacak kadar yüksek |
| `musteri_gecikmesi` | En büyük 20 müşteriden birinin havale ile ödenen faturalarından, kendi alışkanlığına göre şansla açıklanamayacak kadar çoğu açık kaldı |

Her uyarı kuralın adını, ölçülen değeri, eşiği, dayandığı kayıtların numaralarını ve Türkçe bir açıklamayı
taşır. Zamanlayıcı saatin gösterdiği güne kadar tarar ve yeni uyarıları `data/izleme/` deposuna ekler;
aynı saatle ikinci çalışma hiçbir şey eklemez, saat geri alınamaz.

```bash
.venv/Scripts/python -I scripts/faz4_veri_uret.py
.venv/Scripts/python -I scripts/faz4_izleme_olc.py --ayrilmis
.venv/Scripts/python -I scripts/faz4_izle.py --gun 2026-10-15
```

İlk betik ölçüm için ek veri setleri üretir: projenin şirketinin olaysız ikizi ve kuralların ayarında hiç
kullanılmayan iki tohum (43, 44). İkincisi kuralları olaylı ve olaysız veride çalıştırıp
`reports/faz4_izleme.json` dosyasını yazar. Üçüncüsü zamanlayıcıyı çalıştırır. Kural ayarının her denemesi
`reports/faz4_ayar_denemeleri.json` dosyasındadır. Olaylar ve kurallar aynı kişi tarafından yazıldı; yüksek
yakalama oranı kuralların olay türlerini bildiğini gösterir, gerçek dünyadaki başarısını göstermez.

## Veri kaynakları

- Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
  https://doi.org/10.24432/C5CG6D. Lisans: CC BY 4.0. Fatura kalemleri kullanılır; tarihler
  ileri kaydırılır, tutarlar TCMB kurlarıyla Türk lirasına çevrilir, müşteri adları sentetiktir.
- TCMB Elektronik Veri Dağıtım Sistemi (EVDS): döviz alış kurları (TP.DK.USD.A, TP.DK.EUR.A,
  TP.DK.GBP.A) ve Sektör Bilançoları alacak devir hızı (TP.SEKBILAC02A.46, .G, .GENEL).
- Atradius Payment Practices Barometer, Türkiye 2026: geç ödenen fatura payı ve gecikme dağılımı.
  Rakamlar `scripts/faz1_kalibrasyon_kaynaklari.py` içinde, sayfa numarasıyla aktarılmıştır.

## Klasörler

| Klasör | İçerik |
| --- | --- |
| `src/sayman` | Projenin kodu (`veri`: Faz 1 üretici, `ambar`: Faz 2 ambar ve metrikler, `araclar`: Faz 3 MCP sunucusu, `izleme`: Faz 4 kurallar ve zamanlayıcı) |
| `tests` | Yazılım testleri ve kabul testleri |
| `scripts` | Ölçüm, indirme ve veri üretim betikleri |
| `reports` | Ölçüm sonuçları |
| `data/kaynak` | Kaynaklardan indirilen küçük dosyalar (kurlar, kalibrasyon bantları) |
| `data/denetim` | Araç çağrılarının denetim kaydı (git'e girmez) |
| `data/izleme` | Zamanlayıcının uyarı deposu (git'e girmez) |
| `data/izleme_veri` | Faz 4 ölçümü için ek veri setleri (git'e girmez) |
