"""SQLite bağlantı yönetimi (stdlib `sqlite3`, ek bağımlılık yok)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from divitrack.db.schema import MIGRATIONS, SCHEMA_VERSION

log = logging.getLogger(__name__)

MEMORY = ":memory:"


class Database:
    """Her işlem için kısa ömürlü bağlantı açar; `transaction()` atomik birim sağlar.

    `:memory:` için tek bir paylaşılan bağlantı tutulur (testler).
    """

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._shared: sqlite3.Connection | None = None
        if self.path != MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # -- bağlantı -------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, detect_types=0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != MEMORY:
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.path == MEMORY:
            if self._shared is None:
                self._shared = self._open()
            conn = self._shared
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- şema -----------------------------------------------------------------
    def schema_version(self) -> int:
        with self.transaction() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        current = self.schema_version()
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Veritabanı sürümü ({current}) uygulamadan yeni ({SCHEMA_VERSION}). Uygulamayı güncelleyin."
            )
        for version in range(current, SCHEMA_VERSION):
            log.info("Migration v%s uygulanıyor", version + 1)
            with self.transaction() as conn:
                conn.executescript(MIGRATIONS[version])
                conn.execute(f"PRAGMA user_version = {version + 1}")

    # -- yedek ----------------------------------------------------------------
    def backup_bytes(self) -> bytes:
        """Tutarlı bir anlık yedeği (online backup API) bayt olarak döndürür."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            target_path = Path(tmp) / "backup.db"
            target = sqlite3.connect(target_path)
            try:
                with self.transaction() as conn:
                    conn.backup(target)
            finally:
                target.close()
            return target_path.read_bytes()

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
