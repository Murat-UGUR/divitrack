"""SQLite şeması ve sürüm tabanlı migration'lar.

Para/miktar alanları TEXT olarak saklanır ve Decimal'e çevrilir; SQLite'ın REAL tipi
kuruş hassasiyetini kaybettirebilir. Tarihler ISO-8601 TEXT'tir.
Şema sürümü `PRAGMA user_version` ile izlenir.
"""

from __future__ import annotations

MIGRATIONS: list[str] = [
    # --- v1: ilk şema ---------------------------------------------------------
    """
    CREATE TABLE assets (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT    NOT NULL,
        name         TEXT    NOT NULL DEFAULT '',
        asset_class  TEXT    NOT NULL
                     CHECK (asset_class IN ('BIST_STOCK','US_STOCK','PRECIOUS_METAL','CASH')),
        currency     TEXT    NOT NULL CHECK (currency IN ('TRY','USD','EUR')),
        manual_price TEXT,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (symbol, asset_class)
    );

    CREATE TABLE transactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        tx_type     TEXT    NOT NULL CHECK (tx_type IN ('BUY','SELL')),
        quantity    TEXT    NOT NULL,
        price       TEXT    NOT NULL,
        fee         TEXT    NOT NULL DEFAULT '0',
        trade_date  TEXT    NOT NULL,
        note        TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_transactions_asset ON transactions(asset_id, trade_date);

    CREATE TABLE dividends (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id         INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        ex_date          TEXT,
        pay_date         TEXT    NOT NULL,
        amount_per_share TEXT    NOT NULL,
        quantity         TEXT    NOT NULL,
        currency         TEXT    NOT NULL CHECK (currency IN ('TRY','USD','EUR')),
        withholding_rate TEXT    NOT NULL DEFAULT '0',
        status           TEXT    NOT NULL CHECK (status IN ('EXPECTED','PAID')),
        note             TEXT    NOT NULL DEFAULT '',
        created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_dividends_pay_date ON dividends(pay_date);
    CREATE INDEX ix_dividends_asset ON dividends(asset_id);

    -- Sağlayıcı fiyat önbelleği (hisseler, vadeli metaller ve döviz kurları: USDTRY=X vb.)
    CREATE TABLE price_cache (
        symbol         TEXT PRIMARY KEY,
        price          TEXT NOT NULL,
        previous_close TEXT,
        fetched_at     TEXT NOT NULL
    );

    -- Günlük portföy değeri (büyüme grafiği)
    CREATE TABLE portfolio_snapshots (
        snapshot_date TEXT NOT NULL,
        currency      TEXT NOT NULL CHECK (currency IN ('TRY','USD','EUR')),
        total_value   TEXT NOT NULL,
        total_cost    TEXT NOT NULL,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (snapshot_date, currency)
    );

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)

DATA_TABLES = ("assets", "transactions", "dividends", "price_cache", "portfolio_snapshots", "settings")
