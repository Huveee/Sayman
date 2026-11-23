# Sayman: ölçüm raporu

Bu rapor `scripts/faz11_rapor.py` ile `reports/` altındaki ölçüm dosyalarından üretilir; hiçbir ölçüm yeniden çalıştırılmaz. Her tablonun altında okunan dosyalar yazılıdır; hangi tablonun hangi dosya ve anahtardan okunduğu betikteki MANIFEST'tedir. Tablolardaki ve sonuç maddelerindeki sayılar dosyalardan okunur.

## Faz 0 · Model hız ölçümü

### İki aday modelin hızı (bu bilgisayar, ekran kartsız)

Kural: ön bellek ısındıktan sonra her araç çağırma turu eşik süreyi aşmayacak, geçerli çağrı sayısı yeterli olacak.

| Ölçüt | Aday 1 | Aday 2 |
| --- | --- | --- |
| Model | qwen3.5:4b | qwen3.5:9b |
| Boyut (GB) | 3,32 | 6,55 |
| Bellekteki boyut (GB) | 2,99 | 5,79 |
| Soğuk tur (sn) | 39,5 | 86,3 |
| İstem işleme (token/sn) | 61,01 | 27,24 |
| Üretim (token/sn) | 8,48 | 5,11 |
| Ortanca tur (sn) | 6,1 | 10,2 |
| Geçerli araç çağrısı | 5 | 5 |
| Kural sağlandı | evet | evet |

Kaynak: `faz0_hiz.json`

### Seçim

| Ölçüt | Değer |
| --- | --- |
| Seçilen model | qwen3.5:9b |
| Tur süresi eşiği (sn) | 30 |
| Gereken geçerli çağrı | 5 |

Kaynak: `faz0_hiz.json`

## Faz 2 · Veri ambarı ve metrikler

### Ambarın hesapladığı metrikler

Bu değerler ambarın çıktısıdır; her metriğin bağımsız pandas hesaplamasıyla karşılaştırması testlerdedir.

| Metrik | Değer |
| --- | --- |
| Gün | 2026-09-01 |
| Nakit pozisyonu (TL) | 75.826.754,65 |
| Alacak yaşlandırma toplamı (TL) | 75.580.245,68 |
| Yaşlandırmadaki müşteri sayısı | 1.290 |
| DSO (gün) | 50,20 |
| DSO, 2025 yıl sonu (gün) | 52,44 |
| Geciken fatura sayısı | 1.194 |
| Geciken alacak (TL) | 37.800.862,95 |
| Yaklaşan ödemelerin faturası | 22 |
| Yaklaşan ödemeler (TL) | 43.510.100,56 |
| Kur riski (TL) | 26.564.190,74 |
| Nakit akış tahmini, hafta sayısı | 13 |
| Nakit akış tahmini, açılış (TL) | 75.826.754,65 |

Kaynak: `faz2_metrikler.json`

## Faz 3 · Araçlar ve yetki

### Yetkisiz denemeler ve denetim kaydı

| Ölçüt | Değer |
| --- | --- |
| Araç sayısı | 13 |
| Yetkisiz deneme | 60 |
| Beklenen sonucu veren deneme | 60 |
| Sızıntı | 0 |
| Denetim kaydı satırı | 114 |
| Zincir hatası | 0 |
| Araç listesi (token) | 2.187 |
| En büyük cevap (token) | 1.783 |
| Kapıda en yavaş çağrı (ms) | 154,1 |

Kaynak: `faz3_araclar.json`

## Faz 4 · İzleme ve uyarılar

### Gömülü olayları yakalama ve olaysız veride yanlış alarm

Olayları ve kuralları aynı kişi yazdı; yüksek yakalama oranı gerçek dünyadaki başarıyı göstermez.

| Tohum | Ayrılmış mı | Olay | Yakalanan | Geri çağırma | Aylık yanlış alarm |
| --- | --- | --- | --- | --- | --- |
| Tohum 42 | hayır | 12 | 12 | 1,000 | 1,458 |
| Tohum 43 | evet | 12 | 12 | 1,000 | 1,843 |
| Tohum 44 | evet | 12 | 12 | 1,000 | 1,286 |
| Kabul eşiği |  |  |  | 0,9 | 2 |

Kaynak: `faz4_izleme.json`

## Faz 5 · Belge arama

### Arama yöntemleri (ilk 5 sonuçta doğru madde)

| Yöntem | Ayar seti, ilk 5 | Ayrılmış set, ilk 5 | Ayrılmış, ilk sırada | Ayrılmış soru | Ayrılmış, kaçan |
| --- | --- | --- | --- | --- | --- |
| Kelime araması (BM25) | 0,685 | 0,769 | 0,500 | 160 | 37 |
| Anlam araması (bge-m3) | 0,957 | 1,000 | 0,919 | 160 | 0 |
| Kelime + anlam | 0,932 | 0,956 | 0,700 | 160 | 7 |
| Kelime + anlam + sıralayıcı (kabul yöntemi) | 0,963 | 0,981 | 0,950 | 160 | 3 |
| Kabul ölçütü sağlandı |  | evet |  |  |  |

Kaynak: `faz5_arama.json`

### Bellek ve süre (sohbet modeli, anlam modeli ve sıralayıcı birlikte)

| Ölçüt | Değer |
| --- | --- |
| Toplam bellek (GB) | 15,7 |
| Üçü yüklüyken boş bellek (GB) | 0,86 |
| Sıralayıcı süreci, arama sonrası (GB) | 2,92 |
| Sıralayıcılı arama, soru başına medyan (sn) | 9,6 |
| Sıralayıcılı arama, en çok (sn) | 23,1 |
| Sohbet modeli bellekten atıldı mı | hayır |

Kaynak: `faz5_kaynak.json`

## Faz 6 · Doğrulayıcı ve orkestratör (duman testi)

### Duman testinin bitiş durumları

Bu bir kalite ölçümü değildir; sorular azdır ve düzeltmeler aynı sorularla yapıldı.

| Çalıştırma | tamam | ret | uretemedim | sure_siniri | baglam_siniri |
| --- | --- | --- | --- | --- | --- |
| İlk çalıştırma | 6 | 1 | 1 | 5 | 0 |
| İkinci çalıştırma (doğrulayıcı düzeltmelerinden sonra) | 9 | 1 | 1 | 1 | 1 |
| Seçili sorular, yeniden | 2 | 0 | 0 | 0 | 0 |

Kaynak: `faz6_duman_ilk.json`, `faz6_duman.json`, `faz6_duman_secili.json`

### İkinci çalıştırmanın ölçümleri

| Ölçüt | Değer |
| --- | --- |
| Soru | 13 |
| İlk taslağı reddedilen soru | 3 |
| İlk taslaklarda denetlenen sayı | 115 |
| İlk taslaklarda kaynaksız sayı | 4 |
| Süre medyanı (sn) | 97,1 |
| Araç listesi (token) | 2.338 |
| En uzun belge sonucu, seçili çalıştırma (token) | 2.580 |
| En büyük istem, seçili çalıştırma (token) | 4.954 |

Kaynak: `faz6_duman.json`, `faz6_duman_secili.json`

## Faz 7 · Değerlendirme düzeneği ve ilk ölçüm

### Ön yazılı hedefler

Hedefler ölçümden önce yazıldı; eşik, değer ve sonuç raporların kendi 'hedefler' listesindendir.

| Hedef | Eşik | Geliştirme | Saklanan | Saklanan sonuç |
| --- | --- | --- | --- | --- |
| Yetkisiz sızıntı (zorunlu) | 0 | 0 | 0 | Geçti |
| Kapsam dışı kayıt (zorunlu) | 0 | 0 | 0 | Geçti |
| Araç seçimi | 0,85 | 0,917 | 0,873 | Geçti |
| Rakam doğruluğu | 0,75 | 0,875 | 0,820 | Geçti |
| Doğrulanmış yanlış sayı | 0,03 | 0,042 | 0,113 | Kaldı |
| Belge isabeti | 0,85 | 0,700 | 0,575 | Kaldı |
| Belge cevabı | 0,7 | 0,500 | 0,325 | Kaldı |
| Belge alıntısı | 0,8 | 0,500 | 0,400 | Kaldı |
| Süre medyanı (sn) | 120 | 56,0 | 64,3 | Geçti |
| Süre p90 (sn) | 240 | 123,0 | 103,4 | Geçti |
| Soru sayısı |  | 68 | 250 |  |

Kaynak: `faz7_olcum_saklanan.json`, `faz7_olcum_gelistirme.json`

### Saklanan set ayrıntısı

| Ölçü | Değer |
| --- | --- |
| Soru | 250 |
| Araç sorusu, cevap gösterilen | 143 |
| Araç sorusu, doğru | 123 |
| Yetkisiz deneme, kapıya ulaşan | 49/60 |
| Belge sorusu, belge_ara çağrılan | 0,700 |
| Doğrulanabilir cevap üretilemedi | 27 |
| Tekrar ölçümü: aynı durum | 10/10 |
| Tekrar ölçümü: birebir aynı cevap | 4/10 |

Kaynak: `faz7_olcum_saklanan.json`

## Faz 8 · İnce ayar (QLoRA)

### Saklanan set: kütüphane modeli, kendi temel modelimiz, ince ayarlı model

Belge ve araç davranışları Faz 7'de saklanan set okunduktan sonra bilerek öğretildi; bu yüzden iyileşme tam genelleşme sayılmaz.

| Hedef | Eşik | Kütüphane (Faz 7) | Kendi temel | İnce ayarlı |
| --- | --- | --- | --- | --- |
| Yetkisiz sızıntı (zorunlu) | 0 | 0 | 0 | 0 |
| Araç seçimi | 0,85 | 0,873 | 0,880 | 0,960 |
| Rakam doğruluğu | 0,75 | 0,820 | 0,847 | 0,907 |
| Doğrulanmış yanlış sayı | 0,03 | 0,113 | 0,087 | 0,067 |
| Belge isabeti | 0,85 | 0,575 | 0,575 | 0,950 |
| Belge cevabı | 0,7 | 0,325 | 0,300 | 0,875 |
| Belge alıntısı | 0,8 | 0,400 | 0,450 | 0,950 |
| Süre medyanı (sn) | 120 | 64,3 | 52,7 | 44,9 |
| Süre p90 (sn) | 240 | 103,4 | 91,0 | 61,7 |
| "Doğrulanabilir cevap üretemedim" |  | 27 | 31 | 4 |
| Soru sayısı |  | 250 | 250 | 250 |

Kaynak: `faz7_olcum_saklanan.json`, `faz8_temel_kendi_olcum_saklanan.json`, `faz8_ayarli_olcum_saklanan.json`

### Eğitimin etkisi, eşli karşılaştırma (kendi temel → ince ayarlı)

| Metrik | İyileşen soru | Kötüleşen soru | p |
| --- | --- | --- | --- |
| Araç seçimi (araç soruları) | 13 | 1 | 0,0018 |
| Parametreler (araç) | 18 | 1 | 0,0001 |
| Rakam doğruluğu (araç) | 16 | 7 | 0,0931 |
| Doğrulanmış yanlış sayı (araç) | 11 | 8 | 0,6476 |
| Doğru madde bulundu (belge) | 15 | 0 | 0,0001 |
| Belge cevabı | 23 | 0 | 0,0000 |
| Geçerli alıntı (belge) | 20 | 0 | 0,0000 |
| Yetkisiz denemede ret | 7 | 2 | 0,1797 |

Kaynak: `faz8_karsilastirma_egitim_saklanan.json`

### Unutma ölçümü (Belebele Türkçe)

| Karşılaştırma | Soru | Önceki doğruluk (%) | Sonraki doğruluk (%) | Fark | Yalnız ilki doğru | Yalnız ikincisi doğru | p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Kütüphane, iki ölçüm | 200 | 84 | 84 | 0 | 0 | 0 | – |
| Kütüphane → kendi temel | 200 | 84 | 84 | 0 | 1 | 1 | 1,0000 |
| Kendi temel → ince ayarlı | 200 | 84 | 83,5 | -0,5 | 5 | 4 | 1,0000 |
| Kütüphane → ince ayarlı | 200 | 84 | 83,5 | -0,5 | 4 | 3 | 1,0000 |

Kaynak: `faz8_unutma_tekrar.json`, `faz8_unutma_donusturme.json`, `faz8_unutma.json`, `faz8_unutma_kutuphane_ayarli.json`

### Eğitim sorularının Faz 7 setleriyle sızıntı denetimi

| Ölçü | Değer |
| --- | --- |
| Eğitim ve doğrulama sorusu | 474 |
| En yakın kalıp benzerliği | 0,556 |
| En yakın soru benzerliği | 0,791 |
| İhlal: kalip | 0 |
| İhlal: soru | 0 |
| İhlal: kimlik | 0 |
| İhlal: ad | 0 |
| İhlal: madde | 0 |
| İhlal: kur | 0 |

Kaynak: `faz8_sizinti.json`

## Faz 8b · Doğrulayıcıya üç kural ve yeni test seti

### Ön kayıtlı ölçüler (canlı koşu)

| Ölçü | Sonuç | Tuttu mu |
| --- | --- | --- |
| Kaybedilen doğru | 0 | evet |
| Yanlış bayrak | 0 | evet |
| Müşteriye özel sorularda gösterilen doğrulanmış yanlış (eski → yeni) | 9 → 2 | evet |
| Süre medyanı, araç soruları (sn, eski → yeni) | 44,3 → 50,2 | hayır |
| Süre medyanı oranı (yeni / eski) | 1,133 |  |
| Süre p90 (sn) | 74,2 | evet |
| Tüm şirket sızıntısı | 0 | evet |

Kaynak: `faz8b_analiz.json`

### Eski düzen ↔ yeni düzen (araç soruları)

Eski düzen ayrı bir koşu değil, aynı koşunun kayıtlarından çıkarıldı; tek koşu.

| Ölçü | Eski düzen | Yeni düzen |
| --- | --- | --- |
| Soru sayısı | 86 | 86 |
| Rakam doğruluğu | 0,849 | 0,930 |
| Doğrulanmış yanlış | 9 | 2 |
| S grubu (müşteriye özel) doğruluğu | 0,733 | 0,967 |
| Eğitimde olan niyetler | 0,968 | 0,968 |
| Eğitimde olmayan niyetler | 0,542 | 0,833 |
| Tüm şirket, elle: ret | 2 | 2 |
| Tüm şirket, elle: kapsamlı | 0 | 9 |
| Tüm şirket, elle: yanıltıcı | 9 | 0 |
| Kazanılan / kaybedilen soru (McNemar) |  | 7 / 0 |
| McNemar p |  | 0,0156 |
| Kapsam düzeltmesi: düzelen / tur |  | 26/26 |

Kaynak: `faz8b_analiz.json`

### Faz 7 hedefleri bu sette

| Hedef | Eşik | Değer | Sonuç |
| --- | --- | --- | --- |
| Yetkisiz sızıntı (zorunlu) | 0 | 0 | Geçti |
| Kapsam dışı kayıt (zorunlu) | 0 | 0 | Geçti |
| Araç seçimi | 0,85 | 0,930 | Geçti |
| Rakam doğruluğu | 0,75 | 0,930 | Geçti |
| Doğrulanmış yanlış sayı | 0,03 | 0,023 | Geçti |
| Süre medyanı (sn) | 120 | 50,2 | Geçti |
| Süre p90 (sn) | 240 | 74,2 | Geçti |

Kaynak: `faz8b_ayarli_olcum_yeni.json`

### Kimlik ölçümü: gösterilen cevaplardaki numaralar

| Tür | Yeni düzen | Eski düzen |
| --- | --- | --- |
| Uydurma | 1 | 1 |
| Yanlış kayıt | 6 | 11 |
| Doğru numara | 11 | 11 |
| Numarasız | 18 | 13 |

Kaynak: `faz8b_kimlik_analiz.json`

### Kimlik ölçümü: uydurma kimlik kuralı

| Ölçü | Değer |
| --- | --- |
| Soru | 36 |
| Taslak | 45 |
| Desteksiz numaralı taslak | 6 |
| Eski kuralların durdurduğu | 5 |
| Durdurulmayan | 1 |
| Yeni kuralın tek başına katkısı | yok: desteksiz numaraların hiçbirini yalnızca yeni kural durdurmadı |
| Yeni kuralın yanlış bayrağı | 0 |
| Doğru numara sorularında doğru (yeni düzen) | 8/10 |
| Doğru numara sorularında doğru (eski düzen) | 8/10 |
| Eğitim ↔ yeni set en yakın kalıp benzerliği | 0,538 |
| Eğitim ↔ yeni set en yakın soru benzerliği | 0,736 |

Kaynak: `faz8b_kimlik_analiz.json`, `faz8b_soru_seti.json`

## Faz 9 · Araç deneyi (elle araçlar ↔ otomatik üretilen araçlar)

### Üç kol, aynı sorular

Üçüncü kol keşif amaçlıdır: düzeltmeler Faz 8b sonuçlarına bakılarak seçildi, p değeri yoktur.

| Ölçü | Elle araçlar | Otomatik araçlar | Elle + iki düzeltme (keşif) |
| --- | --- | --- | --- |
| Tüm set, gösterilen doğru | 65/86 | 18/86 | 68/86 |
| Doğrudan sınıflar (tek_cagri + zincir), gösterilen doğru | 25/37 | 16/37 | 28/37 |
| Gösterilen yanlış (ön kayıttaki tanım) | 9 | 12 | 11 |
| baglam_siniri ile biten | 0 | 30 | 0 |
| sure_siniri ile biten | 0 | 4 | 0 |
| Süre medyanı / p90 (sn) | 55,4 / 130 | 74,5 / 201,6 | 52,3 / 106,2 |
| İstem tokenı medyanı / en büyük | 3.288 / 4.884 | 3.027 / 6.378 | 3.256,5 / 4.915 |
| Sızıntı | 0 | 0 | 0 |
| Kâhin tavanı | 85/86 | 37/86 |  |

Kaynak: `faz9_karsilastirma.json`

### Soru sınıflarına göre gösterilen doğru

| Sınıf | Soru | Elle | Otomatik | Elle + düzeltme |
| --- | --- | --- | --- | --- |
| tek_cagri | 34 | 22 | 16 | 25 |
| zincir | 3 | 3 | 0 | 3 |
| engelli | 3 | 3 | 2 | 3 |
| toplama | 40 | 33 | 0 | 33 |
| yok | 6 | 4 | 0 | 4 |

Kaynak: `faz9_karsilastirma.json`

### Birincil ölçü: doğrudan sınıflarda eşli karşılaştırma

| Ölçü | Değer |
| --- | --- |
| Soru | 37 |
| İkisi de doğru | 12 |
| Yalnızca elle doğru | 13 |
| Yalnızca otomatik doğru | 4 |
| İkisi de yanlış | 8 |
| Kesin McNemar p (iki yönlü) | 0,0490 |
| Ön kayıtlı karar | fark var |
| Bir çift ters dönseydi p | 0,0768 |
| Doğrulayıcı kör nokta soruları çıkarılırsa p | 0,1185 |
| Negatif kontrol: yakalanan | 1 |
| Üçüncü kol: yalnızca elle doğru | 4 |
| Üçüncü kol: yalnızca düzeltmeli doğru | 7 |

Kaynak: `faz9_karsilastirma.json`

### Araç listesinin bağlamdaki yeri

| Ölçü | Elle araçlar | Otomatik araçlar |
| --- | --- | --- |
| Araç sayısı | 14 | 14 |
| Karakter | 7.986 | 10.836 |
| Token | 2.336 | 2.209 |
| Soru ve sonuçlara kalan token | 4.960 | 5.087 |

Kaynak: `faz9_boyut.json`

## Sonuçlar ve sınırlamalar

- Yetkisiz erişimde sızıntı yok: Faz 3 kapı denemelerinde 0 sızıntı (60 denemede); Faz 7 saklanan sette 0, Faz 8 ince ayarlı modelde 0, Faz 8b tüm şirket sorularında 0. Faz 7 saklanan sette denemelerin 49 tanesi kapıya ulaştı; kalanlar kapıyı değil modelin kendi reddini ölçer.
- Belge arama: ayrılmış sette anlam araması ilk 5 sonuçta 1,000, kabul yöntemi (kelime + anlam + sıralayıcı) 0,981 isabet verdi; kelime araması tek başına 0,769. Belgeleri ve soruları aynı kişi yazdı, gerçek belgeler daha zor olabilir.
- İzleme kuralları: üç tohumda geri çağırmanın en düşüğü 1,000, aylık yanlış alarmın en yükseği 1,843 (üst sınır 2); olayları ve kuralları aynı kişi yazdığı için bu oran gerçek dünyadaki başarıyı göstermez.
- Faz 7 ilk ölçümünde (saklanan set) doğrulanmış yanlış sayı 0,113 (sınır 0,03), belge cevabı 0,325 ve geçerli alıntı 0,400 hedefi tutmadı; araç seçimi 0,873, rakam doğruluğu 0,820 geçti.
- İnce ayarda belge cevabı 0,300 → 0,875, araç seçimi 0,880 → 0,960 oldu; ama bu davranışlar Faz 7 saklanan seti okunduktan sonra bilerek öğretildi, yeni soru türüne genelleşme sayılmaz. Doğrulanmış yanlış sayı 0,067 ile sınırın (0,03) üstünde kaldı; rakam doğruluğundaki artışın p değeri 0,0931, anlamlı değil.
- Genel Türkçe korundu: Belebele doğruluğu kendi temel modelde 84, ince ayarlıda 83,5; fark -0,5 puan (yalnız ilki doğru 5, yalnız ikincisi doğru 4, p = 1,0000).
- Faz 8b sahiplik kuralı müşteriye özel sorularda gösterilen doğrulanmış yanlışı 9 → 2 indirdi; ama düzeltme turları süre medyanını 44,3 → 50,2 sn çıkardı (oran 1,133) ve süre ölçütü tutmadı. Kazanım tek şablondan ve tek hata biçiminden geliyor, tek koşu.
- Uydurma kimlik kuralı 36 soruluk zorlayıcı sette 6 desteksiz taslaktan hiçbirini tek başına durdurmadı (dosyadaki ifade: "yok: desteksiz numaraların hiçbirini yalnızca yeni kural durdurmadı"). Asıl açık uydurma değil yanlış kayıt: gösterilen yanlış kayıt yeni düzende 6, eski düzende 11. Bağımsız olay sayısı çok küçük.
- Araç deneyi: aynı modelle elle araçlar 65/86 soruyu, otomatik üretilen araçlar 18/86 soruyu doğru cevapladı. Ön kayıtlı birincil testin p değeri 0,0490, doğrulayıcının kör noktası çıkarılınca 0,1185: yön net, anlamlılık kırılgan. Üçüncü kol yalnızca betimleyici.
- Hız sınırı: bu bilgisayarda (ekran kartsız) seçilen modelin üretim hızı saniyede 5,11 token; bu yüzden uzun cevaplar sürenin çoğunu alıyor ve süre sınırları buna göre konuldu. Bütün ölçümler tek makinede ve çoğunlukla tek koşuyla yapıldı.
