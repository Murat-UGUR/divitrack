# Değişiklik Günlüğü

## [0.2.0] - 2026-10-21

### Eklendi
- Varlık amaçları (koruma, büyüme, temettü geliri, deneme) ve amaç bazlı hedef dağılım
- Yatırım fonu varlık sınıfı
- Kurumsal işlemler: bedelsiz / bölünme, bedelli (rüçhan), temettüden alış
- Bütçe sayfası: gelir/gider kategorileri, aylık kategori bütçeleri, tasarruf oranı, 12 aylık seyir, CSV içe/dışa aktarma
- Aylık Plan sayfası: ayın net tasarrufunu satış yapmadan hedefe göre amaçlara böler; aylık rutin listesi
- Analiz sayfası: amaç dağılımı ve hedef, TÜFE ile reel getiri, sağlık puanı dökümü, fiyat/kur senaryosu
- Portföy Özeti'nde sağlık puanı ve "dikkat edilecekler" uyarıları
- Temettüler sayfasında yurt dışı temettü beyan sınırı paneli
- Ayarlar: hedefler ve kurallar, tasarruf hedefi, TÜFE girişi (endeks veya aylık %)

### Değişti
- Veritabanı şeması v2; v1 veritabanları açılışta otomatik yükseltilir
- JSON dışa aktarım biçimi v2 (amaç, bütçe ve TÜFE dahil); v1 dosyaları içe aktarılmaya devam eder
- Geçmiş fiyat sorguları oturum içinde önbelleğe alınır

## [0.1.0] - 2026-10-06

İlk sürüm: işlem defteri, BIST/ABD hisse, kıymetli maden ve nakit takibi, temettü motoru, değer geçmişi, JSON/CSV aktarımı.
