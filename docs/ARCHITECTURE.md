# Mimari

## Katmanlar

| Katman | Paket | Sorumluluk | Bağımlı olduğu |
|---|---|---|---|
| Core | `divitrack.core` | Entity'ler, enum'lar, saf hesaplamalar (`compute_position`, temettü projeksiyonu, `inflation`, `planning`), port arayüzleri | — |
| Infrastructure | `divitrack.db`, `divitrack.repositories`, `services.market.yfinance_provider` | SQLite erişimi, Yahoo adaptörü | Core |
| Services | `divitrack.services` | İş kuralları ve orkestrasyon | Core, repository'ler, port'lar |
| UI | `divitrack.ui` | Streamlit sayfaları, grafik/tablo dönüşümleri | Services |
| Composition root | `divitrack.bootstrap` | Tüm nesneleri oluşturup birbirine bağlar | Hepsi |

## Temel kararlar

**İşlem defteri (ledger) modeli.** Pozisyonlar saklanmaz; her açılışta `transactions` tablosundan ağırlıklı ortalama maliyetle hesaplanır. Böylece geçmişe dönük düzeltme, gerçekleşen K/Z ve "X tarihindeki adet" (temettü hakkı, değer geçmişi) tutarlı kalır. Açığa satış ve geçmişi bozan silmeler servis katmanında engellenir.

**Decimal + TEXT.** Para/miktar alanları SQLite'ta `TEXT` olarak tutulur ve `Decimal`'e çevrilir; `REAL` kuruş hatası üretebilir. Float yalnızca grafik/tablo sınırında kullanılır.

**ORM yok, stdlib `sqlite3`.** Şema küçük ve stabil; ek bağımlılık getirmemek, repository katmanını şeffaf tutmak ve testleri hızlı çalıştırmak için. Migration'lar `db/schema.py` içindeki sıralı listede, sürüm `PRAGMA user_version` ile izlenir. Yeni sürüm = listeye yeni SQL bloğu eklemek.

**Port/adaptör ile veri sağlayıcı.** `PriceProvider` protokolü üç metot ister: `fetch_quotes`, `fetch_dividends`, `fetch_history`. `MarketDataService`:
- önbelleği (`price_cache`, TTL varsayılan 15 dk) önce kullanır,
- sağlayıcı hata verirse son bilinen fiyatı `stale` işaretiyle döndürür,
- gram altın/gümüşü ons × USDTRY'den türetir,
- kurları doğrudan (`USDTRY=X`) veya ters (`TRYUSD=X`) sembolden bulur.

**Kur çevrimi.** Değerleme varlığın kendi para biriminde yapılır, raporlama para birimine güncel kurla çevrilir. K/Z yüzdesi bu yüzden varlığın kendi para birimindeki getiriyi gösterir. Kur etkisini de içeren TL bazlı bakış Analiz sayfasındadır (aşağıda).

**Temettü projeksiyonu.** Son 12 ayın ödemeleri bir yıl ileri kaydırılır, mevcut adetle çarpılır. Kullanıcı bir varlık için gelecek tarihli "Beklenen" kayıt girmişse o varlık için tahmin üretilmez. Stopaj oranları `settings` tablosunda, varlık sınıfı bazında tutulur.

**Kurumsal işlemler.** İşlem tipleri `BUY`, `SELL`, `BONUS` (bedelsiz/bölünme), `RIGHTS` (bedelli/rüçhan) ve `REINVEST` (temettüden alış). `BONUS` adet ekler, maliyeti değiştirmez (fiyatı her zaman 0'dır); diğerleri alış gibi işlenir. `TxType.is_new_money` yalnızca `BUY` ve `RIGHTS` için doğrudur; yatırılan para ve enflasyon hesapları buna dayanır.

**Amaçlar ve plan.** Her varlığın bir amacı vardır (`Purpose`: koruma, büyüme, temettü, deneme, atanmadı). Hedef paylar ve kurallar (`PlanRules`: tek kalem sınırı, sapma toleransı, beyan sınırı) `settings` tablosunda tutulur. `core.planning.allocate` yeni parayı satış yapmadan böler: önce hedefin altındaki amaçların açığı, para yetmezse açıklar oranında; kalan para hedef oranlarına göre.

**Enflasyon.** `cpi_monthly` tablosu ay → TÜFE endeksi tutar (taban önemsiz, yalnızca oranlar kullanılır; eksik ay için önceki en yakın ay). `InsightsService.real_returns` her yeni para girişini işlem günü kuruyla TL'ye çevirip son TÜFE ayına taşır, satışları düşer ve güncel TL değerle karşılaştırır. Kur geçmişi alınamazsa güncel kur kullanılır ve satır işaretlenir. Uygulama TÜFE'yi internetten çekmez; kullanıcı girer.

**Sağlık puanı.** Altı bileşen (amaç dağılımı 25, tek kalem 20, deneme bütçesi 10, enflasyon 20, çeşitlilik 10, tasarruf oranı 15). Her biri `linear_score` ile iyi/kötü eşikleri arasında doğrusal puanlanır; verisi olmayan bileşen toplama katılmaz ve puan kalanların üzerinden 100'e ölçeklenir. Her bileşen bir açıklama ve "puan nasıl artar" önerisi taşır.

**Bütçe.** `categories` (gelir/gider, giderlerde opsiyonel aylık bütçe) ve `cash_flows` (her zaman pozitif tutar; yön kategoriden gelir). `BudgetService` ay özeti, 12 aylık seyir, tasarruf oranı ve yatırılabilir tutarı (net tasarruf × yatırım payı) üretir; Aylık Plan sayfası bu tutarı `allocate`'e verir. Tutarlar TL'dir.

**Banka dökümü.** `core.statement` dökümü okur (UTF-8 veya Windows-1254, `;`/`,`/sekme ayracı, baştaki hesap bilgisi satırlarını atlar), kullanıcının seçtiği kolonlardan işaretli tutar üretir (tek kolon veya Borç/Alacak; Türk bankalarının `15.000` biçimindeki binlik ayırıcısı dahil) ve tarihi okunamayan satırları (devreden bakiye, toplam) atlar. `category_rules` tablosundaki metin kuralları açıklamada Türkçe büyük/küçük harf duyarsız aranır, uzun kural önce denenir. Aynı tarih, tutar ve açıklamayla kayıtlı satır mükerrer sayılır. Kategori önceliği: önizlemede elle seçilen > kural > varsayılan; türü tutarın işaretiyle uyuşmayan seçim varsayılana düşer.

**Puan geçmişi ve varlık karnesi.** Portföy Özeti her açıldığında o ayın puanı `score_history` tablosuna yazılır (aynı ay üzerine yazar). Varlık karnesi her açık kalem için ağırlık, nominal/reel getiri ve temettü verimini gösterir; tek kalem sınırı, atanmamış amaç, enflasyonun gerisinde kalma ve "temettü amacında ama temettü yok" durumlarını işaretler.

**Yurt dışı temettü beyan sınırı.** TL dışı, "Ödendi" işaretli temettülerin yıllık brüt toplamı ödeme günü kuruyla TL'ye çevrilir ve kullanıcının girdiği sınırla karşılaştırılır. Sınır her yıl değiştiği için varsayılan değer yoktur.

**Değer geçmişi.** Dashboard her açıldığında o günün toplamı `portfolio_snapshots` tablosuna yazılır (upsert). `SnapshotService.backfill` işlem defteri + Yahoo geçmiş kapanışları + geçmiş kurlarla ilk işlemden bugüne günlük seriyi yeniden oluşturur.

## Veri modeli

```
assets (purpose) ──< transactions (BUY/SELL/BONUS/RIGHTS/REINVEST)
   └────────────< dividends
categories ──< cash_flows        — gelir/gider ve aylık bütçe
   └────────< category_rules    — döküm açıklaması → kategori
score_history (month PK)         — aylık sağlık puanı
cpi_monthly (month PK)           — TÜFE endeksi
price_cache (symbol PK)          — hisseler, GC=F/SI=F, USDTRY=X…
portfolio_snapshots (date, ccy)  — büyüme grafiği
settings (key, value)            — para birimi, stopaj, hedefler, kurallar, tasarruf hedefi
```

Şema v2, SQLite `CHECK` kısıtları `ALTER` ile değiştirilemediği için `assets`, `transactions` ve `dividends` tablolarını veriyi taşıyarak yeniden kurar. v3 döküm kurallarını ve puan geçmişini ekler. Eski veritabanları açılışta otomatik yükseltilir (`tests/integration/test_budget_insights.py::test_v1_database_upgrades_with_data`).

## Test stratejisi

- `tests/unit`: saf hesaplamalar, sembol eşleme, önbellek/çevrimdışı davranış, biçimlendirme.
- `tests/integration`: geçici SQLite dosyası + `FakeProvider` ile uçtan uca servis senaryoları (özet, kur, temettü, geri doldurma, JSON/CSV, bütçe, reel getiri, puan, uyarılar, plan, migration).
- `tests/test_app_smoke.py`: Streamlit `AppTest` ile her sayfanın boş ve dolu veritabanıyla hatasız açıldığı, bütçe formunun kayıt eklediği ve planın hesaplandığı (CI'da).
