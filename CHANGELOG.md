# Değişiklik Günlüğü

## [0.5.0] - 2026-10-21

### Eklendi
- Birikim hedefleri: hedef tutar ve tarih, bağlı varlıklar, ilerleme, aylık gereken tutar, mevcut tasarruf hızıyla "yolunda / geride" durumu ve uyarıları
- Fon eklerken adın TEFAS'tan otomatik doldurulması; BES fonları da TEFAS'tan fiyatlanır

### Değişti
- Veritabanı şeması v4, JSON dışa aktarım biçimi v4 (hedefler dahil)

## [0.4.0] - 2026-10-21

### Eklendi
- TEFAS entegrasyonu: yatırım fonlarının güncel fiyatı ve geçmişi otomatik (yeni `fonFiyatBilgiGetir` API'si); fon pozisyonları değer geçmişine ve reel getiriye dahil
- Sembol önekine göre sağlayıcı yönlendirme (`TEFAS:` → TEFAS, diğerleri → Yahoo); bir kaynağın hatası diğerini etkilemez
- TÜFE'nin TCMB EVDS'den tek tıkla alınması (kişisel API anahtarıyla; anahtar yedeğe yazılmaz)
- Banka dökümlerinde Excel (.xlsx) desteği

### Değişti
- Yatırım fonu varlıkları her zaman TL cinsinden tutulur
- `openpyxl` bağımlılığı eklendi

## [0.3.0] - 2026-10-21

### Eklendi
- Banka dökümü içe aktarma: kolon eşleme (tek tutar veya Borç/Alacak, kredi kartı için "giderler artı" seçeneği), baştaki hesap bilgisi satırlarını ve toplam satırlarını atlama, Windows-1254 dosya desteği
- Döküm eşleme kuralları: açıklamada geçen metne göre otomatik kategori; önizlemede elle düzeltme; mükerrer kayıt koruması
- Varlık karnesi: kalem başına ağırlık, getiri, reel getiri, temettü verimi ve kural işaretleri
- Sağlık puanının aylık geçmişi ve grafiği

### Değişti
- Sayı okuma İngilizce biçimi de tanır (`1,234.56`, `1,234,567`)
- Veritabanı şeması v3, JSON dışa aktarım biçimi v3 (kurallar dahil)
- CI eylemleri güncel sürümlere taşındı

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
