// Defne ERP demo + Sayman chat window. Plain JS, no dependencies.
"use strict";

const $ = (id) => document.getElementById(id);
const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const DURUM_ETIKET = { ret: "Yetki yok", uretemedim: "Doğrulanamadı", sure_siniri: "Süre sınırı", baglam_siniri: "Çok uzun", adim_siniri: "Adım sınırı" };

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

class Yetkisiz extends Error {}

// All requests go through here; 401 -> login screen
async function api(yol, govde) {
  const secenek = { credentials: "same-origin" };
  if (govde !== undefined) {
    secenek.method = "POST";
    secenek.headers = { "Content-Type": "application/json" };
    secenek.body = JSON.stringify(govde);
  }
  const r = await fetch(yol, secenek);
  let veri = null;
  try { veri = await r.json(); } catch (_) { /* no body */ }
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
      b.append(el("b", "", k.ad), el("div", "rol", k.rol_adi), el("div", "kapsam", k.kapsam_adi));
      b.addEventListener("click", () => girisYap(k.kullanici));
      kutu.append(b);
    }
  } catch (e) {
    $("giris-hata").textContent = "Kullanıcılar yüklenemedi.";
  }
}

function girisGoster() {
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
  try { await api("/api/cikis", {}); } catch (_) { /* log out anyway */ }
  girisGoster();
}

async function kabukGoster(ben) {
  sohbetiSifirla(); // clear the history when the user changes
  kullanici = ben;
  $("giris").classList.add("gizli");
  $("kabuk").classList.remove("gizli");
  $("kullanici-bilgi").textContent = `${ben.ad} · ${ben.rol_adi}`;
  $("sohbet").classList.add("gizli");
  $("sohbet-ac").classList.remove("gizli");
  await Promise.all([durumYenile(), raporYukle(), uyarilariYukle(), ornekleriYukle()]);
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
  $("model-nokta").classList.toggle("hazir", !!durum.model_hazir);
  $("model-yazi").textContent = durum.model_hazir ? "Hazır" : "Isınıyor";
}

// ---- Report ----
function raporCiz(r) {
  $("rapor-ust").textContent = `${uzunTarih(r.tarih)} · ${r.kapsam_adi}`;
  const kutu = $("rapor");
  kutu.replaceChildren();
  for (const b of r.bolumler || []) {
    const d = el("div", "bolum");
    d.append(el("div", "bolum-baslik", b.baslik), el("div", "bolum-ozet", b.ozet));
    for (const [ad, deger] of b.satirlar || []) {
      const s = el("div", "satir");
      s.append(el("span", "", ad), el("span", "", deger));
      d.append(s);
    }
    kutu.append(d);
  }
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
    const d = el("div", "uyari" + (yeni ? " yeni" : ""));
    const ust = el("div", "uyari-ust");
    const uyariMi = u.onem === "uyari";
    ust.append(el("span", "", uzunTarih(u.gun)), el("span", "chip " + (uyariMi ? "uyari" : "bilgi"), uyariMi ? "Uyarı" : "Bilgi"));
    if (yeni) ust.append(el("span", "chip yeni-rozet", "Yeni"));
    d.append(ust, el("strong", "", u.baslik), el("p", "", u.aciklama));
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
    toast(`Bugün artık ${uzunTarih(v.bugun)} · ${yeni.length} yeni uyarı`);
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

// Text with citations: each citation becomes a link that opens the document's PDF at the article in a new tab
function kaynakliCiz(hedef, metin) {
  let son = 0;
  for (const m of metin.matchAll(KAYNAK)) {
    if (m.index > son) hedef.append(document.createTextNode(metin.slice(son, m.index)));
    const a = el("a", "kaynak", m[0]);
    a.href = `/belge/${encodeURIComponent(m[1])}/${m[2]}`;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = "Belgeyi PDF olarak yeni sekmede aç";
    hedef.append(a);
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
  meşgul = true;
  girdiKilidi(true);
  $("soru").value = "";
  balonEkle("kullanici", metin);
  const bekle = balonEkle("sayman", "Sayman düşünüyor… 0 sn");
  const bas = Date.now();
  const sayac = setInterval(() => {
    bekle.textContent = `Sayman düşünüyor… ${Math.floor((Date.now() - bas) / 1000)} sn`;
  }, 1000);
  try {
    const v = await api("/api/sor", { soru: metin });
    clearInterval(sayac);
    bekle.remove();
    const b = balonEkle("sayman", v.cevap || "");
    const meta = el("div", "meta");
    const araclar = (v.araclar || []).map((a) => a.arac);
    meta.append(el("span", "", `Kullanılan araçlar: ${araclar.length ? araclar.join(", ") : "yok"} · ${Math.round(v.sure_s)} sn`));
    if (v.durum && v.durum !== "tamam") meta.append(el("span", "chip durum", DURUM_ETIKET[v.durum] || v.durum));
    b.append(meta);
    if (v.bugun && durum && v.bugun !== durum.bugun) durumYenile();
  } catch (e) {
    clearInterval(sayac);
    bekle.remove();
    if (!(e instanceof Yetkisiz)) {
      balonEkle("sayman", e.status ? e.message : "Sunucuya ulaşılamadı. Lütfen tekrar deneyin.");
    }
  } finally {
    if (kullanici) {
      meşgul = false;
      girdiKilidi(false);
      $("soru").focus();
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
