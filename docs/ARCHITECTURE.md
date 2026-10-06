# Mimari

## Katmanlar

| Katman | Paket | Sorumluluk | Bağımlı olduğu |
|---|---|---|---|
| Core | `divitrack.core` | Entity'ler, enum'lar, saf hesaplamalar (`compute_position`, temettü projeksiyonu), port arayüzleri | — |
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

**Kur çevrimi.** Değerleme varlığın kendi para biriminde yapılır, raporlama para birimine güncel kurla çevrilir. K/Z yüzdesi bu yüzden varlığın kendi para birimindeki getiriyi gösterir (kur etkisi ayrıştırılmaz — yol haritasında).

**Temettü projeksiyonu.** Son 12 ayın ödemeleri bir yıl ileri kaydırılır, mevcut adetle çarpılır. Kullanıcı bir varlık için gelecek tarihli "Beklenen" kayıt girmişse o varlık için tahmin üretilmez. Stopaj oranları `settings` tablosunda, varlık sınıfı bazında tutulur.

**Değer geçmişi.** Dashboard her açıldığında o günün toplamı `portfolio_snapshots` tablosuna yazılır (upsert). `SnapshotService.backfill` işlem defteri + Yahoo geçmiş kapanışları + geçmiş kurlarla ilk işlemden bugüne günlük seriyi yeniden oluşturur.

## Veri modeli

```
assets ──< transactions
   └────< dividends
price_cache (symbol PK)          — hisseler, GC=F/SI=F, USDTRY=X…
portfolio_snapshots (date, ccy)  — büyüme grafiği
settings (key, value)            — raporlama para birimi, stopaj oranları
```

## Test stratejisi

- `tests/unit`: saf hesaplamalar, sembol eşleme, önbellek/çevrimdışı davranış, biçimlendirme.
- `tests/integration`: geçici SQLite dosyası + `FakeProvider` ile uçtan uca servis senaryoları (özet, kur, temettü, geri doldurma, JSON/CSV).
- `tests/test_app_smoke.py`: Streamlit `AppTest` ile her sayfanın boş ve dolu veritabanıyla hatasız açıldığı (CI'da).
