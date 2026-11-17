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
yazılır. Testler her metriği bağımsız bir pandas hesaplamasıyla, elle hesaplanmış küçük bir veriyle ve Faz 1
hesaplamalarıyla karşılaştırır. Nakit akış tahmini yalnızca bilinen kalemleri içerir (açık alacaklar, çekler,
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

## Faz 5: belge arama ve alıntı kontrolü

Sayman sayıların yanında belgelere de dayanır. Kütüphanede şirket verisinden türetilen 81 sentetik belge
(40 tedarik ve 5 hizmet sözleşmesi, en büyük 30 müşterinin satış sözleşmesi, 6 finans politikası; 833 madde)
ve aramayı zorlaştırmak için üç kanun (Çek Kanunu, Türk Borçlar Kanunu, Türk Ticaret Kanunu; 2.170 madde)
vardır. Sözleşmelerdeki vade, kredi limiti ve çek koşulları şirket verisiyle tutarlıdır. Her madde bir arama
parçasıdır ve kimliği sabittir (`SOZ-S001#m3`).

Arama üç aşamadır: kelime araması (BM25, Türkçe küçük harf ve 5 harflik kök), anlam araması (bge-m3 vektörleri,
Qdrant yerel modu) ve ikisinin sıralarının birleştirilmesi; birleşik listenin ilk 20 adayı bir sıralayıcı modelden
(bge-reranker-v2-m3) geçer. Her parça belgesinin rollerini taşır; rol süzmesi her aşamada sıralamadan önce yapılır,
bir satış müdürü başka bir müdürün müşterisinin sözleşmesini göremez.

Model bir belgeden alıntı yaptığında alıntıyı tırnak içinde ve parça kimliğiyle verir. Alıntı kontrolü alıntının
o parçada gerçekten geçtiğine bakar; yalnızca boşluk, tırnak işareti ve Türkçe büyük/küçük harf farkını yok sayar,
rakamlara dokunmaz ("%12" ile "%1,2" farklıdır). Bilinen sınır: gerçek ama iddiayı desteklemeyen bir alıntı geçer.

```bash
ollama pull bge-m3
.venv/Scripts/python -I scripts/faz5_belge_uret.py
.venv/Scripts/python -I scripts/faz5_dizin_kur.py
.venv/Scripts/python -I scripts/faz5_arama_olc.py --ayrilmis
.venv/Scripts/python -I scripts/faz5_kaynak_olc.py
```

İlk betik belge kütüphanesini ve soru setini üretir (kanun PDF'leri `data/ham/mevzuat/` altında olmalıdır),
ikincisi bütün parçaları vektöre çevirip Qdrant dizinini kurar, üçüncüsü ayrılmış soru setini dört yöntemle
ölçüp `reports/faz5_arama.json` dosyasını yazar, dördüncüsü sohbet modeli, anlam modeli ve sıralayıcı birlikte
yüklüyken bellek ve süreyi ölçer. Sıralayıcı modeli `data/modeller/bge-reranker-v2-m3` altına Hugging Face'ten
(BAAI deposu) indirilir. Vektörler ve sıralama skorları `data/belge/onbellek/` altında saklanır; testler modelleri
çalıştırmadan bu önbellekten çalışır.

Ölçüm: belgelerin maddelerinden üretilen 322 soru (`tests/faz5_sorular.py`), aramadan önce yazıldı ve
donduruldu. Sorular ayar ve ayrılmış set olarak ikiye bölündü; iki set aynı belgeyi ya da aynı soru kalıbını
paylaşmaz. Her madde türünün bir "yakın" (maddenin kelimeleriyle) bir de "uzak" (günlük dille) kalıbı vardır.
Ayarlar yalnızca ayar setinde seçildi (`reports/faz5_ayar_denemeleri.json`), ayrılmış set bir kez çalıştırıldı
(`reports/faz5_arama.json`). Kabul ölçütü: ayrılmış sette doğru madde ilk 5 sonuçta en az %85 oranında.

| Yöntem | Ayar seti, ilk 5 | Ayrılmış set, ilk 5 | Ayrılmış set, ilk sırada |
| --- | --- | --- | --- |
| Kelime araması (BM25) | 0,685 | 0,769 (123/160) | 0,500 |
| Anlam araması (bge-m3) | 0,957 | 1,000 (160/160) | 0,919 |
| Kelime + anlam | 0,932 | 0,956 (153/160) | 0,700 |
| Kelime + anlam + sıralayıcı (kabul yöntemi) | 0,963 | 0,981 (157/160) | 0,950 |

Kelime araması uzak kalıplı sorularda şansa yakındır (ayrılmış sette 45/78 = 0,58): sözleşme soruları karşı
tarafın adını içerdiği için doğru sözleşmeyi bulup maddelerini rastgele sıralamak bile ilk 5'te yaklaşık 0,47
verir. Sıralayıcı ilk 5'i
değiştirmedi; ilk sıradaki isabette ayar setinde düşürdü (0,895 → 0,827), ayrılmış sette yükseltti
(0,919 → 0,950). Fark birkaç sorudur. Kaçan üç sorudan ikisi uzak kalıplı politika sorusu (ilk sıraları kanun
maddeleri alıyor), biri peşin çalışan bir müşterinin vade sorusu ("kaç gün içinde" diye soruluyor, madde gün
söylemiyor). Belgeleri ve soruları aynı kişi yazdı; gerçek belgeler ve kullanıcı soruları daha zor olabilir.

Bellek ve süre (`reports/faz5_kaynak.json`, 16 GB bellekli, ekran kartsız dizüstü bilgisayar): qwen3.5:9b
(5,8 GB) ve bge-m3 (1,2 GB) Ollama'da birlikte yüklü kalıyor, sıralayıcı yüklenince sohbet modeli bellekten
atılmıyor. Sıralayıcı süreci 2,9 GB büyütüyor; üçü birlikte yüklüyken 15,7 GB'ın 0,9 GB'ı boş kalıyor. Bir sorunun
arama kısmı sıralayıcıyla medyan 9,6 sn (en çok 23 sn) sürüyor; sıralayıcının 20 adayı işlemcide okuması bu sürenin
neredeyse tamamı. Sıralayıcısız arama soru başına yaklaşık 0,15 sn (sorunun vektöre çevrilmesi 0,1 sn). Bütün parçaların vektöre
çevrilmesi bir kez yapılır ve 26 dakika sürdü.

## Veri kaynakları

- Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
  https://doi.org/10.24432/C5CG6D. Lisans: CC BY 4.0. Fatura kalemleri kullanılır; tarihler
  ileri kaydırılır, tutarlar TCMB kurlarıyla Türk lirasına çevrilir, müşteri adları sentetiktir.
- TCMB Elektronik Veri Dağıtım Sistemi (EVDS): döviz alış kurları (TP.DK.USD.A, TP.DK.EUR.A,
  TP.DK.GBP.A) ve Sektör Bilançoları alacak devir hızı (TP.SEKBILAC02A.46, .G, .GENEL).
- Atradius Payment Practices Barometer, Türkiye 2026: geç ödenen fatura payı ve gecikme dağılımı.
  Rakamlar `scripts/faz1_kalibrasyon_kaynaklari.py` içinde, sayfa numarasıyla aktarılmıştır.
- Mevzuat Bilgi Sistemi (mevzuat.gov.tr): 5941 sayılı Çek Kanunu, 6098 sayılı Türk Borçlar Kanunu, 6102 sayılı
  Türk Ticaret Kanunu PDF metinleri. Kanun metinleri 5846 sayılı Kanun'un 31. maddesi gereği telif kapsamı
  dışındadır.
- Modeller: bge-m3 (Ollama kütüphanesi) ve BAAI/bge-reranker-v2-m3 (Hugging Face, Apache-2.0).

## Klasörler

| Klasör | İçerik |
| --- | --- |
| `src/sayman` | Projenin kodu (`veri`: Faz 1 üretici, `ambar`: Faz 2 ambar ve metrikler, `araclar`: Faz 3 MCP sunucusu, `izleme`: Faz 4 kurallar ve zamanlayıcı, `belge`: Faz 5 belgeler, arama ve alıntı kontrolü) |
| `tests` | Yazılım testleri ve kabul testleri |
| `scripts` | Ölçüm, indirme ve veri üretim betikleri |
| `reports` | Ölçüm sonuçları |
| `data/kaynak` | Kaynaklardan indirilen küçük dosyalar (kurlar, kalibrasyon bantları) |
| `data/denetim` | Araç çağrılarının denetim kaydı (git'e girmez) |
| `data/izleme` | Zamanlayıcının uyarı deposu (git'e girmez) |
| `data/izleme_veri` | Faz 4 ölçümü için ek veri setleri (git'e girmez) |
| `data/ham/mevzuat` | İndirilen kanun PDF'leri (git'e girmez) |
| `data/belge` | Belge kütüphanesi, soru seti, vektör ve sıralama önbelleği, Qdrant dizini (git'e girmez) |
| `data/modeller` | Hugging Face'ten indirilen sıralayıcı modeli (git'e girmez) |
