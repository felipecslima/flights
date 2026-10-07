from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    route_id TEXT NOT NULL,       -- ex.: GRU-SSA (rota, sem data)
    query_key TEXT NOT NULL,      -- rota + datas
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    airline TEXT,
    stops INTEGER,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_route ON prices(route_id, seen_at);
CREATE INDEX IF NOT EXISTS idx_prices_key ON prices(query_key, seen_at);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    query_key TEXT NOT NULL,
    price REAL NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(query_key, sent_at);

CREATE TABLE IF NOT EXISTS watches (      -- alertas criados pelo CLI
    id INTEGER PRIMARY KEY,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    depart_from TEXT NOT NULL,
    depart_to TEXT NOT NULL,
    step_days INTEGER NOT NULL DEFAULT 7,
    trip_days TEXT NOT NULL DEFAULT '',     -- "7,10"; vazio = só ida
    max_price REAL,
    max_stops INTEGER,
    allow_hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (        -- promoções do site
    link TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    price REAL,
    published TEXT,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    month TEXT PRIMARY KEY,       -- YYYY-MM
    searches INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = "flights.db"):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(watches)")]
        if "allow_hidden" not in cols:  # bancos criados antes dessa coluna
            self.conn.execute("ALTER TABLE watches ADD COLUMN allow_hidden INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()

    # --- cota ---------------------------------------------------------
    def searches_this_month(self) -> int:
        m = datetime.now(timezone.utc).strftime("%Y-%m")
        row = self.conn.execute("SELECT searches FROM usage WHERE month=?", (m,)).fetchone()
        return row[0] if row else 0

    def count_search(self) -> None:
        m = datetime.now(timezone.utc).strftime("%Y-%m")
        self.conn.execute(
            "INSERT INTO usage(month, searches) VALUES(?, 1) "
            "ON CONFLICT(month) DO UPDATE SET searches = searches + 1",
            (m,),
        )
        self.conn.commit()

    # --- preços -------------------------------------------------------
    def save_price(self, route_id, query_key, price, currency, airline, stops) -> None:
        self.conn.execute(
            "INSERT INTO prices(route_id, query_key, price, currency, airline, stops, seen_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (route_id, query_key, price, currency, airline, stops, now_iso()),
        )
        self.conn.commit()

    def last_seen(self, query_key: str) -> str:
        """Quando a busca foi checada pela última vez ('' se nunca) — usado para rodízio."""
        row = self.conn.execute(
            "SELECT MAX(seen_at) FROM prices WHERE query_key=?", (query_key,)
        ).fetchone()
        return row[0] or ""

    def route_history(self, route_id: str) -> list[float]:
        rows = self.conn.execute(
            "SELECT price FROM prices WHERE route_id=?", (route_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def route_median(self, route_id: str) -> tuple[float | None, int]:
        h = self.route_history(route_id)
        return (statistics.median(h) if h else None), len(h)

    def route_min(self, route_id: str) -> float | None:
        h = self.route_history(route_id)
        return min(h) if h else None

    def routes_with_history(self) -> list[tuple[str, int, float]]:
        """[(route_id, nº de preços, menor preço)] da rota mais coletada para a menos."""
        rows = self.conn.execute(
            "SELECT route_id, COUNT(*), MIN(price) FROM prices GROUP BY route_id ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def route_series(self, route_id: str, limit: int = 30) -> list[tuple[str, str, float, str]]:
        """Últimos preços da rota, do mais antigo ao mais novo: (seen_at, query_key, price, airline)."""
        rows = self.conn.execute(
            "SELECT seen_at, query_key, price, airline FROM prices WHERE route_id=? "
            "ORDER BY id DESC LIMIT ?",
            (route_id, limit),
        ).fetchall()
        return list(reversed(rows))

    # --- alertas criados pelo CLI --------------------------------------
    def add_watch(self, origin, destination, depart_from, depart_to, step_days=7,
                  trip_days=(), max_price=None, max_stops=None, allow_hidden=False) -> int:
        cur = self.conn.execute(
            "INSERT INTO watches(origin, destination, depart_from, depart_to, step_days, "
            "trip_days, max_price, max_stops, allow_hidden, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (origin, destination, str(depart_from), str(depart_to), step_days,
             ",".join(str(n) for n in trip_days), max_price, max_stops, int(allow_hidden), now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_watches(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM watches ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def delete_watch(self, watch_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- promoções (site) -----------------------------------------------
    def deals_seen_any(self) -> bool:
        return self.conn.execute("SELECT 1 FROM deals LIMIT 1").fetchone() is not None

    def deal_seen(self, link: str) -> bool:
        return self.conn.execute("SELECT 1 FROM deals WHERE link=?", (link,)).fetchone() is not None

    def save_deal(self, link, title, price, published) -> None:
        self.conn.execute("INSERT OR IGNORE INTO deals(link, title, price, published, seen_at) VALUES(?,?,?,?,?)",
                          (link, title, price, published, now_iso()))
        self.conn.commit()

    # --- alertas disparados ---------------------------------------------
    def last_alert_price(self, query_key: str) -> float | None:
        row = self.conn.execute(
            "SELECT price FROM alerts WHERE query_key=? ORDER BY sent_at DESC LIMIT 1",
            (query_key,),
        ).fetchone()
        return row[0] if row else None

    def save_alert(self, query_key: str, price: float) -> None:
        self.conn.execute(
            "INSERT INTO alerts(query_key, price, sent_at) VALUES(?,?,?)",
            (query_key, price, now_iso()),
        )
        self.conn.commit()
