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
    # --- v2: amaçlar, kurumsal işlemler, fon sınıfı, bütçe, TÜFE ---------------
    # SQLite CHECK kısıtını ALTER ile değiştiremediği için assets ve transactions yeniden kurulur.
    # (Migration foreign_keys=ON bağlantısında çalışır; DROP öncesi veriler yeni tabloya taşınır,
    # transactions/dividends önce yedeklenip sonra geri yazılır.)
    """
    CREATE TEMP TABLE _tx_backup AS SELECT * FROM transactions;
    CREATE TEMP TABLE _div_backup AS SELECT * FROM dividends;
    DELETE FROM dividends;
    DELETE FROM transactions;

    CREATE TABLE assets_v2 (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT    NOT NULL,
        name         TEXT    NOT NULL DEFAULT '',
        asset_class  TEXT    NOT NULL
                     CHECK (asset_class IN ('BIST_STOCK','US_STOCK','PRECIOUS_METAL','FUND','CASH')),
        currency     TEXT    NOT NULL CHECK (currency IN ('TRY','USD','EUR')),
        manual_price TEXT,
        purpose      TEXT    NOT NULL DEFAULT 'UNASSIGNED'
                     CHECK (purpose IN ('DEFENSIVE','GROWTH','DIVIDEND','EXPERIMENT','UNASSIGNED')),
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (symbol, asset_class)
    );
    INSERT INTO assets_v2(id, symbol, name, asset_class, currency, manual_price, created_at)
        SELECT id, symbol, name, asset_class, currency, manual_price, created_at FROM assets;
    DROP TABLE assets;
    ALTER TABLE assets_v2 RENAME TO assets;

    DROP TABLE transactions;
    CREATE TABLE transactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        tx_type     TEXT    NOT NULL CHECK (tx_type IN ('BUY','SELL','BONUS','RIGHTS','REINVEST')),
        quantity    TEXT    NOT NULL,
        price       TEXT    NOT NULL,
        fee         TEXT    NOT NULL DEFAULT '0',
        trade_date  TEXT    NOT NULL,
        note        TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_transactions_asset ON transactions(asset_id, trade_date);
    INSERT INTO transactions SELECT * FROM _tx_backup;

    DROP TABLE dividends;
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
    INSERT INTO dividends SELECT * FROM _div_backup;

    DROP TABLE _tx_backup;
    DROP TABLE _div_backup;

    -- Gelir/gider kategorileri; monthly_budget giderler için aylık üst sınır
    CREATE TABLE categories (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind           TEXT NOT NULL CHECK (kind IN ('INCOME','EXPENSE')),
        monthly_budget TEXT
    );

    CREATE TABLE cash_flows (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
        flow_date   TEXT    NOT NULL,
        amount      TEXT    NOT NULL,
        note        TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_cash_flows_date ON cash_flows(flow_date);

    -- TÜFE endeksi: ay ("YYYY-AA") → endeks değeri
    CREATE TABLE cpi_monthly (
        month TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    INSERT INTO categories(name, kind) VALUES
        ('Maaş', 'INCOME'), ('Ek gelir', 'INCOME'), ('Diğer gelir', 'INCOME'),
        ('Kira / aidat', 'EXPENSE'), ('Market', 'EXPENSE'), ('Faturalar', 'EXPENSE'),
        ('Ulaşım', 'EXPENSE'), ('Sağlık', 'EXPENSE'), ('Eğitim', 'EXPENSE'),
        ('Yeme-içme / eğlence', 'EXPENSE'), ('Giyim', 'EXPENSE'), ('Diğer gider', 'EXPENSE');
    """,
    # --- v3: döküm eşleme kuralları, aylık puan geçmişi -------------------------
    """
    -- Banka dökümü açıklamasında geçen metin → kategori
    CREATE TABLE category_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE
    );

    -- Ay başına portföy sağlık puanı (ayın son kaydı tutulur)
    CREATE TABLE score_history (
        month      TEXT PRIMARY KEY,
        score      TEXT NOT NULL,
        components TEXT NOT NULL DEFAULT '{}'
    );
    """,
    # --- v4: birikim hedefleri ---------------------------------------------------
    """
    CREATE TABLE goals (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
        target_amount TEXT NOT NULL,
        target_date   TEXT NOT NULL,
        extra_saved   TEXT NOT NULL DEFAULT '0',
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- Bir varlık en fazla bir hedefe bağlanır (değeri iki kez sayılmasın)
    CREATE TABLE goal_assets (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
        goal_id  INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)

DATA_TABLES = (
    "assets",
    "transactions",
    "dividends",
    "price_cache",
    "portfolio_snapshots",
    "settings",
    "categories",
    "cash_flows",
    "cpi_monthly",
    "category_rules",
    "score_history",
    "goals",
    "goal_assets",
)
