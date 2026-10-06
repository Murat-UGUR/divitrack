"""Uygulama yapılandırması.

Harici bağımlılık yok: değerler önce ortam değişkenlerinden, sonra proje kökündeki
`.env` dosyasından okunur. Tüm anahtarlar `DIVITRACK_` önekini kullanır.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "evet"}


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    base_currency: str = "TRY"
    price_cache_ttl_min: int = 15
    offline: bool = False
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        file_values = _load_dotenv(env_file or PROJECT_ROOT / ".env")

        def get(key: str, default: str) -> str:
            full = f"DIVITRACK_{key}"
            return os.environ.get(full, file_values.get(full, default))

        db_path = Path(get("DB_PATH", str(PROJECT_ROOT / "data" / "divitrack.db")))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        return cls(
            db_path=db_path,
            base_currency=get("BASE_CURRENCY", "TRY").upper(),
            price_cache_ttl_min=int(get("PRICE_CACHE_TTL_MIN", "15")),
            offline=_truthy(get("OFFLINE", "0")),
            log_level=get("LOG_LEVEL", "INFO").upper(),
        )
