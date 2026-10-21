# 📈 DiviTrack

**Gizlilik odaklı, tamamen lokal çalışan kişisel portföy, temettü ve bütçe takip uygulaması.**

BIST hisseleri, ABD hisse/ETF'leri, fonlar, kıymetli madenler ve nakit/BES varlıklarınızı tek panelde izleyin. Her varlığa bir görev verin (koruma, büyüme, temettü, deneme), hedef dağılımdan ne kadar saptığınızı ve enflasyonu yenip yenmediğinizi görün. Gelir ve giderlerinizi kaydedin; ay sonunda kalan parayı uygulama hedeflerinize göre bölsün. Tüm veriler **sizin bilgisayarınızda**, tek bir SQLite dosyasında durur. Hesap yok, bulut yok, telemetri yok.

[![CI](https://github.com/Murat-UGUR/divitrack/actions/workflows/ci.yml/badge.svg)](https://github.com/Murat-UGUR/divitrack/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Streamlit](https://img.shields.io/badge/UI-Streamlit-red) ![SQLite](https://img.shields.io/badge/DB-SQLite-lightgrey) ![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Özellikler

| Modül | Açıklama |
|---|---|
| **Portföy Yönetimi** | İşlem defteri; ağırlıklı ortalama maliyet (komisyon dahil), gerçekleşen ve gerçekleşmemiş K/Z, açığa satış koruması. TRY, USD, EUR. |
| **Kurumsal İşlemler** | Bedelsiz / bölünme (maliyetsiz adet), bedelli (rüçhan), temettüden alış (yeni para sayılmaz) |
| **Varlık Sınıfları** | BIST Hisse · ABD Hisse/ETF · Yatırım Fonu · Kıymetli Maden (gram/ons altın ve gümüş) · Nakit/BES (manuel fiyat) |
| **Amaç ve Hedefler** | Varlık başına amaç; amaç bazlı hedef dağılım, sapma toleransı, tek kalem sınırı, deneme bütçesi |
| **Analiz** | Amaçlara göre dağılım ve hedef, TÜFE ile reel getiri ("yatırdığın para bugün ne etmeli"), 100 üzerinden sağlık puanı ve puanın nereden geldiği, kur/fiyat şoklu "ya şöyle olursa?" senaryosu |
| **Dikkat Edilecekler** | Hedef sapması, yoğunlaşma, fiyatsız varlık, eski TÜFE, aşılan bütçe, yaklaşan temettü ve beyan sınırı uyarıları |
| **Bütçe** | Gelir/gider kategorileri, aylık kategori bütçeleri, tasarruf oranı, 12 aylık seyir, banka dökümünden CSV içe aktarma |
| **Aylık Plan** | Ayın net tasarrufundan yatırıma ayrılan tutarı satış yapmadan hedefe en çok yaklaştıracak şekilde amaçlara böler; aylık rutin listesi |
| **Temettü Takibi** | Ödenen/beklenen kayıtlar, stopaj (varsayılan BIST %15, ABD %20 — ayarlanabilir), TTM verim ve maliyete göre verim, 24 aylık takvim grafiği, Yahoo geçmişinden otomatik kayıt önerisi |
| **Canlı Veri** | `yfinance` ile fiyat, temettü geçmişi ve döviz kurları; SQLite önbelleği, ağ hatasında son bilinen fiyatla çalışma, tam çevrimdışı mod |
| **Dashboard** | Toplam değer + günlük değişim, K/Z, 12 aylık net temettü beklentisi, sağlık puanı, dikkat edilecekler, varlık dağılımı, zaman içindeki değer grafiği |
| **Vergi Ön İzleme** | Yurt dışı temettülerin yıllık brüt TL toplamı ve kendi girdiğiniz beyan sınırına göre doluluk |
| **Veri Taşınabilirliği** | JSON tam yedek (birleştir/geri yükle), işlem, temettü ve gelir/gider CSV'leri (Excel'in `;` ayracı desteklenir), tek tıkla `.db` yedeği |

## 🚀 Hızlı Başlangıç

```bash
git clone https://github.com/Murat-UGUR/divitrack.git
cd divitrack
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py             # → http://127.0.0.1:8501
```

İsteğe bağlı ayarlar için `cp .env.example .env`.

**Docker ile:**

```bash
docker build -t divitrack .
docker run -p 127.0.0.1:8501:8501 -v "$(pwd)/data:/app/data" divitrack
```

## 🧭 Kullanım

1. **Ayarlar & Veri → Hedefler ve kurallar**: amaç hedeflerinizi (toplam %100), tek kalem sınırını, tasarruf hedefinizi ve bu yılın yurt dışı temettü beyan sınırını girin. **TÜFE** bölümüne aylık endeksi ekleyin.
2. **Varlıklar & İşlemler → Varlıklar**: varlığı tanımlayın ve amacını seçin (ör. BIST `THYAO` → Büyüme, `XAU-GRAM` → Koruma).
3. **Yeni İşlem**: alış/satış ve kurumsal işlemleri girin. Ondalık için `12,5` veya `12.5` yazabilirsiniz.
4. **Bütçe**: ayın gelir ve giderlerini girin (veya bankadan aldığınız CSV'yi yükleyin), kategori bütçelerini belirleyin.
5. **Aylık Plan**: bütçeden kalan tutarın amaçlara nasıl bölüneceğini görün, alımları yapıp işlem olarak kaydedin.
6. **Portföy Özeti / Analiz**: sağlık puanı, uyarılar, reel getiri ve senaryolar. Değer grafiği için *Geçmişi işlemlerden yeniden oluştur*.
7. **Temettüler**: *Geçmişten Aktar* sekmesi, elde tuttuğunuz döneme denk gelen ödemeleri tek tıkla kaydeder; beyan sınırı paneli yurt dışı temettüleri izler.

### Sembol kuralları

| Varlık | Örnek | Not |
|---|---|---|
| BIST hisse | `THYAO`, `TUPRS` | `.IS` soneki otomatik eklenir; para birimi TRY |
| ABD hisse/ETF | `AAPL`, `SCHD`, `VOO` | |
| Gram altın / gümüş | `XAU-GRAM`, `XAG-GRAM` | `ons (GC=F / SI=F) × USDTRY ÷ 31,1035` ile türetilir (TRY) |
| Ons altın / gümüş | `XAU-OZ`, `XAG-OZ` | Vadeli kontrat fiyatı (USD), spot'a yakın |
| Nakit | `NAKIT-TRY` | Adet = tutar, fiyat = 1 |
| Yatırım fonu | `TTE` | Sınıf: Yatırım Fonu. TEFAS fiyatı çekilmez; manuel birim fiyat girin |
| BES | `BES-FON1` | Manuel birim fiyat girilir, istenince güncellenir |

### CSV formatı

```csv
date,symbol,asset_class,currency,type,quantity,price,fee,note
2024-01-15,THYAO,BIST_STOCK,TRY,BUY,100,285.50,12.5,
2024-02-01,SCHD,US_STOCK,USD,BUY,10,76.20,0,
```

`asset_class`: `BIST_STOCK | US_STOCK | PRECIOUS_METAL | FUND | CASH` · `type`: `BUY | SELL | BONUS | RIGHTS | REINVEST`

Gelir/gider CSV'si (`date;category;amount;note`, `kind` opsiyonel): banka dökümündeki eksi tutarlar gider sayılır, olmayan kategoriler oluşturulur.

```csv
date;category;amount;note
2026-01-05;Maaş;65.000;Ocak maaşı
2026-01-07;Market;-2.350,40;
```

## 🏛️ Mimari

Clean Architecture: bağımlılıklar **yalnızca içe doğru** akar. `core` saf Python'dur; Streamlit, SQLite veya yfinance bilmez.

```
┌───────────────────────────────────────────────────────────┐
│ UI (Streamlit)        ui/pages, ui/components, ui/tables  │
├───────────────────────────────────────────────────────────┤
│ Services              portfolio · dividend_engine ·       │
│                       snapshot · market · io/transfer ·   │
│                       budget · insights                   │
├───────────────────────────────────────────────────────────┤
│ Core (Domain)         entities · calculations · ports ·   │
│                       inflation · planning                │
├───────────────────────────────────────────────────────────┤
│ Infrastructure        db/ (sqlite3) · repositories/ ·     │
│                       market/yfinance_provider            │
└───────────────────────────────────────────────────────────┘
          bootstrap.py → tüm bağımlılıkları bağlar (DI)
```

Ayrıntılar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

```
divitrack/
├── app.py                       # streamlit run app.py
├── src/divitrack/
│   ├── config.py                # .env / ortam değişkenleri
│   ├── bootstrap.py             # composition root
│   ├── core/                    # enums, entities, calculations, inflation, planning, ports, money
│   ├── db/                      # sqlite3 bağlantısı, şema + migration (PRAGMA user_version)
│   ├── repositories/            # satır ↔ entity, CRUD
│   ├── services/
│   │   ├── market/              # PriceProvider adaptörü, sembol eşleme, önbellek, FX
│   │   ├── io/                  # JSON/CSV içe-dışa aktarma, DB yedeği
│   │   ├── portfolio_service.py
│   │   ├── dividend_engine.py
│   │   ├── snapshot_service.py
│   │   ├── budget_service.py    # gelir/gider, bütçe, tasarruf oranı
│   │   └── insights_service.py  # dağılım, reel getiri, puan, uyarılar, plan, beyan sınırı
│   └── ui/                      # Streamlit sayfaları, grafikler, tablolar, biçimlendirme
├── tests/                       # unit + integration + UI smoke testleri
├── docs/                        # mimari ve Git rehberi
└── data/                        # lokal veritabanı (git'e girmez)
```

## 🔒 Gizlilik

- Tüm veriler `data/divitrack.db` içinde kalır; `.gitignore` tüm `*.db`, `*.csv`, `data/` ve `.env` dosyalarını dışlar.
- Sunucu yalnızca `127.0.0.1`'e bağlanır; Streamlit telemetrisi kapalıdır.
- Dışarıya giden tek trafik Yahoo Finance'e **yalnızca sembol adlarıyla** yapılan fiyat sorgularıdır. Adet, maliyet, işlem bilgisi asla gönderilmez. `DIVITRACK_OFFLINE=1` ile bu da kapatılabilir.

## 🧪 Geliştirme

```bash
pip install -r requirements-dev.txt
make test     # pytest + coverage (ağ gerektirmez, sahte sağlayıcı kullanır)
make lint     # ruff + mypy
make format
```

Yeni bir veri sağlayıcısı eklemek için `core/ports.py` içindeki `PriceProvider` protokolünü uygulayan bir sınıf yazıp `bootstrap.default_provider` içinde bağlamanız yeterli.

## 🗺️ Yol Haritası

- [x] Mimari ve proje iskeleti
- [x] SQLite şeması, domain modelleri, repository katmanı
- [x] Fiyat servisi, kur çevrimi, temettü motoru
- [x] Streamlit arayüzü ve dashboard
- [x] CI (GitHub Actions)
- [x] İşlem tarihindeki kurla TL bazlı maliyet ve TÜFE ile reel getiri
- [x] Amaç bazlı hedef dağılım ve yeni parayla dengeleme önerisi
- [x] Gelir/gider ve bütçe modülü, aylık yatırım planı
- [ ] TEFAS entegrasyonu (BES/yatırım fonu fiyatları)
- [ ] TÜFE'nin resmi kaynaktan otomatik alınması
- [ ] Banka dökümü kolon eşleme sihirbazı

## ⚠️ Sorumluluk Reddi

Kişisel takip amaçlıdır; yatırım veya vergi tavsiyesi değildir. Yahoo Finance verileri gayriresmî bir istemciyle (`yfinance`) çekilir, gecikmeli veya hatalı olabilir. Stopaj oranları ve beyan sınırı mevzuatla değişir; kendi durumunuza göre doğrulayın. Beyan sınırı paneli bir ön izlemedir: resmi hesap TCMB kuruyla yapılır. Sağlık puanı ve aylık plan sizin girdiğiniz kurallara dayanan basit hesaplardır, öneri değildir.

## 📄 Lisans

MIT © Murat UĞUR
