"""Domain hataları. UI katmanı bunları yakalayıp kullanıcıya anlamlı mesaj gösterir."""


class DivitrackError(Exception):
    """Tüm uygulama hatalarının tabanı."""


class ValidationError(DivitrackError):
    """Geçersiz girdi (negatif adet, boş sembol vb.)."""


class NotFoundError(DivitrackError):
    """İstenen kayıt bulunamadı."""


class DuplicateError(DivitrackError):
    """Aynı benzersiz anahtarla kayıt zaten var."""


class InsufficientQuantityError(DivitrackError):
    """Elde bulunandan fazla satış denemesi."""


class ImportFormatError(DivitrackError):
    """İçe aktarılan dosya beklenen formatta değil."""
