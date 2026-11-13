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
| `src/sayman` | Projenin kodu (`veri`: Faz 1 üretici, `ambar`: Faz 2 ambar ve metrikler, `araclar`: Faz 3 MCP sunucusu) |
| `tests` | Yazılım testleri ve kabul testleri |
| `scripts` | Ölçüm, indirme ve veri üretim betikleri |
| `reports` | Ölçüm sonuçları |
| `data/kaynak` | Kaynaklardan indirilen küçük dosyalar (kurlar, kalibrasyon bantları) |
| `data/denetim` | Araç çağrılarının denetim kaydı (git'e girmez) |
