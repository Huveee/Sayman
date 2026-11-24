// Defne ERP demo + Sayman chat window. Plain JS, no dependencies.
"use strict";

const $ = (id) => document.getElementById(id);
const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const DURUM_ETIKET = { ret: "Yetki yok", uretemedim: "Doğrulanamadı", sure_siniri: "Süre sınırı", baglam_siniri: "Çok uzun", adim_siniri: "Adım sınırı" };
// The cash forecast counts only items already on record, so its balance is not the balance to come: answers built
// on it carry this fixed note in the "i" tooltip
const TAHMIN_ARACI = "nakit_akis_tahmini";
const TAHMIN_NOTU = "Bu tahmin yalnızca bilinen kalemleri içerir; vergi, SGK, maaş ve yeni satışlar dahil değildir; gerçek bakiye farklı olabilir.";
// The icon of each status chip under an answer (templates in index.html); other statuses get "ikon-dur"
const DURUM_IKON = { ret: "ikon-ret", sure_siniri: "ikon-sure" };
// The whole company's scope label (SCOPE_LABELS["hepsi"] in src/sayman/tools/server.py): any other scope gets a note
const TUM_SIRKET = "tüm şirket";
// On wide screens Sayman is docked beside the page and open from the start (the same width as in style.css)
const GENIS_EKRAN = matchMedia("(min-width: 1101px)");

let durum = null;          // last /api/durum
let kullanici = null;      // /api/ben
let meşgul = false;        // is Sayman waiting for an answer
let yoklamaZamani = null;
let ornekYuklendi = false;
let toastZamani = null;

// ---- Helpers ----
function el(etiket, sinif, metin) {
  const e = document.createElement(etiket);
  if (sinif) e.className = sinif;
  if (metin !== undefined) e.textContent = metin;
  return e;
}

function uzunTarih(iso) {
  const [y, a, g] = String(iso).split("-").map(Number);
  if (!y || !a || !g) return String(iso);
  return `${g} ${AYLAR[a - 1]} ${y}`;
}

// "2026-10-26" -> "26.10.2026"
function kisaTarih(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
}

// "46.484.612,86 TL" -> 4648461286n (kuruş). Exact integers, for proportions only; null if it is not an amount
function kurusa(metin) {
  const m = /^(-?)(\d{1,3}(?:\.\d{3})*),(\d{2}) TL$/.exec(String(metin).trim());
  if (!m) return null;
  const k = BigInt(m[2].replace(/\./g, "") + m[3]);
  return m[1] ? -k : k;
}

function ikon(sablon) {
  return $(sablon).content.firstElementChild.cloneNode(true);
}

class Yetkisiz extends Error {}
class OturumBitti extends Yetkisiz {}  // the request belonged to a session that has ended: show nothing

// Every request belongs to the session that sent it. When the user logs out or another user logs in, the requests
// still waiting are cancelled and their answers dropped: an answer to the CFO must never appear on the next user's
// screen (the server also refuses to hand it to a closed session).
let oturumKesici = new AbortController();

function oturumuBitir() {
  oturumKesici.abort();
  oturumKesici = new AbortController();
}

// All requests go through here; 401 -> login screen
async function api(yol, govde) {
  const sinyal = oturumKesici.signal;
  const secenek = { credentials: "same-origin", signal: sinyal };
  if (govde !== undefined) {
    secenek.method = "POST";
    secenek.headers = { "Content-Type": "application/json" };
    secenek.body = JSON.stringify(govde);
  }
  let r;
  try {
    r = await fetch(yol, secenek);
  } catch (e) {
    if (sinyal.aborted) throw new OturumBitti();
    throw e;
  }
  let veri = null;
  try { veri = await r.json(); } catch (_) { /* no body */ }
  if (sinyal.aborted) throw new OturumBitti();
  if (r.status === 401 && yol !== "/api/giris") {
    if (kullanici) girisGoster();
    throw new Yetkisiz();
  }
  if (!r.ok) {
    const hata = new Error((veri && veri.detail) || "İstek başarısız.");
    hata.status = r.status;
    throw hata;
  }
  return veri;
}

function toast(metin) {
  const t = $("toast");
  t.textContent = metin;
  t.classList.remove("gizli");
  clearTimeout(toastZamani);
  toastZamani = setTimeout(() => t.classList.add("gizli"), 5000);
}

// ---- Login / logout ----
async function kullanicilariYukle() {
  const kutu = $("kullanici-kartlari");
  kutu.replaceChildren();
  try {
    const v = await api("/api/kullanicilar");
    for (const k of v.kullanicilar) {
      const b = el("button", "kullanici-kart");
      b.type = "button";
      const bas = k.ad.split(/\s+/).map((s) => s[0] || "").join("").slice(0, 2).toLocaleUpperCase("tr");
      b.append(el("span", "bas", bas), el("b", "", k.ad), el("div", "rol", k.rol_adi), el("div", "kapsam", k.kapsam_adi));
      b.addEventListener("click", () => girisYap(k.kullanici));
      kutu.append(b);
    }
  } catch (e) {
    $("giris-hata").textContent = "Kullanıcılar yüklenemedi.";
  }
}

function girisGoster() {
  oturumuBitir();
  kullanici = null;
  clearInterval(yoklamaZamani);
  yoklamaZamani = null;
  sohbetiSifirla();
  $("kabuk").classList.add("gizli");
  $("giris").classList.remove("gizli");
  $("giris-hata").textContent = "";
  kullanicilariYukle();
}

async function girisYap(ad) {
  try {
    const ben = await api("/api/giris", { kullanici: ad });
    await kabukGoster(ben);
  } catch (e) {
    $("giris-hata").textContent = e.message || "Giriş yapılamadı.";
  }
}

async function cikis() {
  oturumuBitir(); // an answer still on its way must not arrive while logging out
  try { await api("/api/cikis", {}); } catch (_) { /* log out anyway */ }
  girisGoster();
}

async function kabukGoster(ben) {
  oturumuBitir(); // requests of the previous user are cancelled
  const sinyal = oturumKesici.signal;
  sohbetiSifirla(); // clear the history when the user changes
  kullanici = ben;
  $("giris").classList.add("gizli");
  $("kabuk").classList.remove("gizli");
  $("kullanici-bilgi").textContent = `${ben.ad} · ${ben.rol_adi}`;
  raporuTemizle(); // the previous user's figures must not stay on the page while the new report loads
  kapsamGoster(ben.kapsam_adi);
  $("sohbet").classList.toggle("gizli", !GENIS_EKRAN.matches);
  $("sohbet-ac").classList.toggle("gizli", GENIS_EKRAN.matches);
  belgeAdlari = {};
  await Promise.all([durumYenile(), raporYukle(), uyarilariYukle(), ornekleriYukle(), belgeAdlariniYukle()]);
  if (sinyal.aborted) return; // logged out while loading
  clearInterval(yoklamaZamani);
  yoklamaZamani = setInterval(durumYenile, 10000);
}

// ---- Status ----
async function durumYenile() {
  if (!kullanici) return;
  try {
    durum = await api("/api/durum");
  } catch (_) { return; }
  $("bugun").textContent = "Bugün: " + uzunTarih(durum.bugun);
  $("ileri-yazi").textContent = `Zamanı ileri sar (+${durum.adim_gun} gün)`;
  // "Hazır" only when a question would start now: warmed up and not answering another question
  const bos = !!durum.model_hazir && !durum.mesgul;
  $("model-nokta").classList.toggle("hazir", bos);
  $("model-yazi").textContent = !durum.model_hazir ? "Isınıyor" : durum.mesgul ? "Meşgul" : "Hazır";
}

// ---- Scope ----
// The whole company gets a chip next to the title; a narrower scope gets a note above the report
function kapsamGoster(kapsamAdi) {
  const hepsi = kapsamAdi === TUM_SIRKET;
  $("kapsam-cip-yazi").textContent = `Kapsam: ${kapsamAdi}`;
  $("kapsam-cip").classList.toggle("gizli", !hepsi || !kapsamAdi);
  $("kapsam-not-baslik").textContent = `Kapsamınız: ${kapsamAdi}`;
  $("kapsam-not").classList.toggle("gizli", hepsi || !kapsamAdi);
}

// ---- Report ----
// Every section of /api/rapor is drawn the same way, whatever the role sees:
// - a figure card: the title, the summary and the count rows ("Çek sayısı: 61");
// - a detail card for the other rows, next to the alerts card. A section with one other row keeps it on the figure
//   card instead. A section that could not be read ("—" and a "Durum" row) shows its message on the figure card.
const SAYI = /^\d+$/;

function raporuTemizle() {
  $("sayfa-alt").textContent = "";
  $("gostergeler").replaceChildren();
  $("detaylar").replaceChildren();
  $("uyarilar").replaceChildren();
}

function raporCiz(r) {
  $("sayfa-alt").textContent = `${uzunTarih(r.tarih)} itibarıyla günlük finans özeti`;
  kapsamGoster(r.kapsam_adi);
  const gostergeler = $("gostergeler");
  gostergeler.replaceChildren();
  const n = Math.max(1, (r.bolumler || []).length);
  gostergeler.style.setProperty("--sutun", n);
  gostergeler.style.setProperty("--sutun-dar", Math.min(n, 2));
  const detaylar = $("detaylar");
  detaylar.replaceChildren();
  for (const b of r.bolumler || []) {
    const satirlar = b.satirlar || [];
    const okunamadi = b.ozet === "—";
    const sayilar = satirlar.filter(([, d]) => SAYI.test(d));
    const digerleri = satirlar.filter(([, d]) => !SAYI.test(d));
    const detayli = !okunamadi && digerleri.length > 1;
    const alt = okunamadi ? satirlar.map(([, d]) => d)
      : [...sayilar, ...(detayli ? [] : digerleri)].map(([a, d]) => `${a}: ${d}`);
    const g = el("div", "gosterge");
    g.append(el("span", "gosterge-ad", b.baslik), el("span", "gosterge-deger", b.ozet));
    if (alt.length) g.append(el("span", "gosterge-alt", alt.join(" · ")));
    gostergeler.append(g);
    if (detayli) detaylar.append(detayKarti(b, digerleri));
  }
}

function detayKarti(b, satirlar) {
  const kart = el("section", "kart rapor-kart");
  const ust = el("div", "kart-baslik");
  ust.append(el("h2", "", b.baslik), el("span", "soluk", b.ozet));
  kart.append(ust);
  // The overdue part of the total as a bar; the page prints no number the tools did not return
  const gecmis = satirlar.find(([a]) => a === "Vadesi geçmiş");
  const toplam = kurusa(b.ozet), kisim = gecmis ? kurusa(gecmis[1]) : null;
  if (toplam !== null && kisim !== null && toplam > 0n && kisim >= 0n && kisim <= toplam) {
    const oran = el("div", "oran");
    oran.setAttribute("role", "img");
    oran.setAttribute("aria-label", `${b.baslik} ${b.ozet}, içinde vadesi geçmiş ${gecmis[1]}`);
    const dolu = el("div", "oran-gecmis");
    dolu.style.width = `${Number((kisim * 1000n) / toplam) / 10}%`;
    oran.append(dolu);
    const acikla = el("div", "oran-acikla");
    const g = el("span");
    g.append(el("span", "renk gecmis"), document.createTextNode("Vadesi geçmiş"), el("b", "", gecmis[1]));
    const k = el("span");
    k.append(el("span", "renk"), document.createTextNode("Vadesi gelmemiş"));
    acikla.append(g, k);
    kart.append(oran, acikla);
    satirlar = satirlar.filter((s) => s !== gecmis);
  }
  const tablo = el("table", "tablo");
  const govde = el("tbody");
  for (const [ad, deger] of satirlar) {
    // "Banka A · TRY", "<müşteri> · 2026-10-28": the part after the last "·" goes under the name
    const i = ad.lastIndexOf(" · ");
    const th = el("th", "", i < 0 ? ad : ad.slice(0, i));
    th.scope = "row";
    if (i >= 0) th.append(el("small", "", kisaTarih(ad.slice(i + 3))));
    const tr = el("tr");
    tr.append(th, el("td", "", deger));
    govde.append(tr);
  }
  tablo.append(govde);
  kart.append(tablo);
  return kart;
}

async function raporYukle() {
  try { raporCiz(await api("/api/rapor")); } catch (_) { /* 401 is handled above */ }
}

// ---- Alerts ----
function uyarilariCiz(liste, yeniIdler) {
  const kutu = $("uyarilar");
  kutu.replaceChildren();
  if (!liste.length) {
    kutu.append(el("p", "soluk", "Bu kapsamda uyarı yok."));
    return;
  }
  const sirali = [...liste].sort((a, b) => String(b.gun).localeCompare(String(a.gun)));
  for (const u of sirali) {
    const yeni = yeniIdler.has(u.uyari_id);
    const d = el("article", "uyari" + (yeni ? " yeni" : ""));
    const ust = el("div", "uyari-ust");
    const uyariMi = u.onem === "uyari";
    ust.append(el("span", "", kisaTarih(u.gun)), el("span", "chip " + (uyariMi ? "uyari" : "bilgi"), uyariMi ? "Uyarı" : "Bilgi"));
    if (yeni) ust.append(el("span", "chip yeni-rozet", "Yeni"));
    d.append(ust, el("h3", "", u.baslik), el("p", "", u.aciklama));
    kutu.append(d);
  }
}

async function uyarilariYukle(yeniIdler = new Set()) {
  try {
    const v = await api("/api/uyarilar");
    uyarilariCiz(v.uyarilar || [], yeniIdler);
  } catch (_) { /* ignore */ }
}

// ---- Fast-forward time ----
async function ileriSar() {
  const btn = $("ileri");
  btn.disabled = true;
  $("ileri-spin").classList.remove("gizli");
  try {
    const v = await api("/api/ileri_sar", {});
    const yeni = v.yeni_uyarilar || [];
    toast(`Bugün artık ${uzunTarih(v.bugun)} · ${yeni.length} yeni uyarı · model yeni tarihe hazırlanıyor (~1,5 dk)`);
    if (v.rapor) raporCiz(v.rapor); else await raporYukle();
    await Promise.all([durumYenile(), uyarilariYukle(new Set(yeni.map((u) => u.uyari_id)))]);
  } catch (e) {
    if (!(e instanceof Yetkisiz)) toast(e.status ? e.message : "Sunucuya ulaşılamadı.");
  } finally {
    btn.disabled = false;
    $("ileri-spin").classList.add("gizli");
  }
}

// ---- Chat ----
function sohbetiSifirla() {
  ipucuGizle();
  $("mesajlar").replaceChildren();
  $("soru").value = "";
  meşgul = false;
  girdiKilidi(false);
}

function girdiKilidi(kilit) {
  $("soru").disabled = kilit;
  $("gonder").disabled = kilit;
  document.querySelectorAll(".ornek").forEach((b) => { b.disabled = kilit; });
}

async function ornekleriYukle() {
  const kutu = $("ornekler");
  kutu.replaceChildren();
  try {
    const v = await api("/api/ornek_sorular");
    for (const s of v.sorular || []) {
      const b = el("button", "ornek", s);
      b.type = "button";
      b.addEventListener("click", () => gonder(s));
      kutu.append(b);
    }
  } catch (_) { /* ignore */ }
}

// A citation "[POL-CEK#m2]": the same pattern as _CHUNK_ID in src/sayman/documents/citation.py
const KAYNAK = /\[([A-Za-z0-9_\-]+)#(m\d+)\]/g;

// Readable document names for citation links (only the documents this user may open)
let belgeAdlari = {};

async function belgeAdlariniYukle() {
  try {
    belgeAdlari = (await api("/belge/adlar")).adlar || {};
  } catch (e) {
    if (!(e instanceof OturumBitti)) belgeAdlari = {};
  }
}

// Text with citations: each citation becomes a link that opens the document's PDF at the article in a new tab.
// The link shows the document's readable name ("Çek Kabul Politikası, Madde 2"); the raw id stays in the tooltip.
function kaynakliCiz(hedef, metin) {
  let son = 0;
  for (const m of metin.matchAll(KAYNAK)) {
    if (m.index > son) hedef.append(document.createTextNode(metin.slice(son, m.index)));
    const ad = belgeAdlari[m[1]];
    const a = el("a", "kaynak", ad ? `${ad}, Madde ${m[2].slice(1)}` : m[0]);
    a.href = `/belge/${encodeURIComponent(m[1])}/${m[2]}`;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = `${m[0]} · Belgeyi PDF olarak yeni sekmede aç`;
    // A readable name gets its own parentheses unless the answer already wrote them: "([POL-CEK#m2])"
    const parantezli = metin[m.index - 1] === "(" && metin[m.index + m[0].length] === ")";
    if (ad && !parantezli) hedef.append(document.createTextNode("("), a, document.createTextNode(")"));
    else hedef.append(a);
    son = m.index + m[0].length;
  }
  if (son < metin.length) hedef.append(document.createTextNode(metin.slice(son)));
}

// Only **bold**, line breaks and citation links; everything is added with textContent
function metinCiz(hedef, metin) {
  String(metin).split("\n").forEach((satir, i) => {
    if (i) hedef.append(document.createElement("br"));
    satir.split("**").forEach((parca, j) => {
      if (!parca) return;
      if (j % 2) { const s = el("strong", ""); kaynakliCiz(s, parca); hedef.append(s); }
      else kaynakliCiz(hedef, parca);
    });
  });
}

// The tools and the time of an answer: an "i" in the bubble's corner, shown in a tooltip on hover or focus. The
// tooltip is one fixed element on the page, so the scrolling message list cannot clip it.
const ipucu = el("div", "ipucu gizli");
ipucu.id = "ipucu";
ipucu.setAttribute("role", "tooltip");
document.body.append(ipucu);

function bilgiEkle(balon, metin) {
  const b = el("button", "bilgi", "i");
  b.type = "button";
  b.dataset.ipucu = metin;
  b.setAttribute("aria-label", metin.replace(/\n+/g, " · "));
  balon.classList.add("bilgili");
  balon.append(b);
}

function ipucuGoster(hedef) {
  ipucu.textContent = hedef.dataset.ipucu;
  ipucu.style.left = ipucu.style.top = "0px"; // measure at full width, not squeezed at the last tooltip's place
  ipucu.classList.remove("gizli");
  const r = hedef.getBoundingClientRect();
  const w = ipucu.offsetWidth, h = ipucu.offsetHeight;
  const x = Math.max(8, Math.min(r.right - w, innerWidth - w - 8));
  const y = r.bottom + 6 + h > innerHeight - 8 ? r.top - h - 6 : r.bottom + 6;
  ipucu.style.left = `${x}px`;
  ipucu.style.top = `${y}px`;
}

function ipucuGizle() {
  ipucu.classList.add("gizli");
}

for (const [olay, goster] of [["mouseover", true], ["focusin", true], ["mouseout", false], ["focusout", false]]) {
  $("mesajlar").addEventListener(olay, (e) => {
    const hedef = e.target.closest(".bilgi");
    if (!hedef) return;
    if (goster) ipucuGoster(hedef); else ipucuGizle();
  });
}
$("mesajlar").addEventListener("scroll", ipucuGizle);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") ipucuGizle(); });

function balonEkle(tur, metin) {
  const b = el("div", "balon " + tur);
  if (tur === "sayman") metinCiz(b, metin); else b.textContent = metin;
  const liste = $("mesajlar");
  liste.append(b);
  liste.scrollTop = liste.scrollHeight;
  return b;
}

async function gonder(metin) {
  metin = (metin || "").trim();
  if (!metin || meşgul) return;
  const sinyal = oturumKesici.signal; // the session that asks: the answer is shown only while it lasts
  meşgul = true;
  girdiKilidi(true);
  $("soru").value = "";
  balonEkle("kullanici", metin);
  const bekle = balonEkle("sayman", "");
  const sure = el("span", "", "Sayman düşünüyor… 0 sn");
  bekle.append(sure);
  const bas = Date.now();
  const sayac = setInterval(() => {
    sure.textContent = `Sayman düşünüyor… ${Math.floor((Date.now() - bas) / 1000)} sn`;
  }, 1000);
  try {
    // After the clock advances the model reads the whole prompt again (~1.5 min); say so instead of looking stuck
    await durumYenile();
    if (sinyal.aborted) throw new OturumBitti(); // logged out before the question left
    if (durum && !durum.model_hazir) {
      bekle.append(el("div", "isinma-notu", "Model yeni tarihe hazırlanıyor; cevap 1–2 dakika gecikebilir."));
    }
    const v = await api("/api/sor", { soru: metin });
    clearInterval(sayac);
    bekle.remove();
    const b = balonEkle("sayman", v.cevap || "");
    const araclar = (v.araclar || []).map((a) => a.arac);
    // Only a verified answer whose forecast call returned a result shows a forecast (refusals and fixed texts don't)
    const tahminli = v.durum === "tamam" && (v.araclar || []).some((a) => a.arac === TAHMIN_ARACI && !a.hata);
    bilgiEkle(b, `Kullanılan araçlar: ${araclar.length ? araclar.join(", ") : "yok"}\nSüre: ${Math.round(v.sure_s)} sn`
      + (tahminli ? `\n\n${TAHMIN_NOTU}` : ""));
    // Under the text: a verified answer says so, a refused or stopped one says why
    if (v.durum) {
      const meta = el("div", "meta");
      const dogru = v.durum === "tamam";
      const cip = el("span", "chip " + (dogru ? "dogrulandi" : "durum"));
      cip.append(ikon(dogru ? "ikon-dogrulandi" : DURUM_IKON[v.durum] || "ikon-dur"),
        document.createTextNode(dogru ? "Doğrulandı" : DURUM_ETIKET[v.durum] || v.durum));
      if (dogru) cip.title = "Cevaptaki sayılar ve alıntılar doğrulayıcıdan geçti.";
      meta.append(cip);
      b.append(meta);
    }
  } catch (e) {
    clearInterval(sayac);
    bekle.remove();
    if (!(e instanceof Yetkisiz)) {
      balonEkle("sayman", e.status ? e.message : "Sunucuya ulaşılamadı. Lütfen tekrar deneyin.");
    }
  } finally {
    if (!sinyal.aborted) { // after a user change the new session's chat state is its own
      meşgul = false;
      girdiKilidi(false);
      $("soru").focus();
      durumYenile(); // the indicator shows at once whether Sayman is free (or why it was busy)
    }
  }
}

function sohbetAc(ac) {
  $("sohbet").classList.toggle("gizli", !ac);
  $("sohbet-ac").classList.toggle("gizli", ac);
  if (ac) $("soru").focus();
}

// ---- Startup ----
$("cikis").addEventListener("click", cikis);
$("ileri").addEventListener("click", ileriSar);
$("sohbet-ac").addEventListener("click", () => sohbetAc(true));
$("sohbet-kapat").addEventListener("click", () => sohbetAc(false));
$("sor-form").addEventListener("submit", (e) => { e.preventDefault(); gonder($("soru").value); });
$("soru").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); gonder($("soru").value); }
});

(async function basla() {
  try {
    const ben = await api("/api/ben");
    await kabukGoster(ben);
  } catch (_) {
    girisGoster();
  }
})();
