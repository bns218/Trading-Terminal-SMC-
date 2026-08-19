"""SQLite (WAL mode) tick/candle store. The ingestion service is the only writer;
everything else (dashboard, strategies) reads from here.

Schema:
  ticks(instrument_token, ltp, volume, exchange_ts, received_at)
  candles(instrument_token, timeframe, open_time, close_time, open, high, low, close,
          volume, is_backfilled, is_closed)
  data_health_events(event_type, detail, occurred_at) — reconnects, rejected bars, backfills

All timestamps are stored as ISO-8601 UTC strings. Prices are stored as TEXT to
preserve Decimal precision exactly (SQLite has no native Decimal type; storing
as float would silently corrupt money values, which the ground rules forbid).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_token TEXT NOT NULL,
    ltp TEXT NOT NULL,
    volume INTEGER,
    exchange_ts TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_token_ts ON ticks(instrument_token, exchange_ts);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_token TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume INTEGER NOT NULL,
    is_backfilled INTEGER NOT NULL DEFAULT 0,
    is_closed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(instrument_token, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_candles_token_tf_time ON candles(instrument_token, timeframe, open_time);

CREATE TABLE IF NOT EXISTS data_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,  -- 'reconnect' | 'rejected_bar' | 'backfill' | 'disconnect'
    detail TEXT,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_events_type_time ON data_health_events(event_type, occurred_at);

CREATE TABLE IF NOT EXISTS trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_token TEXT NOT NULL,
    instrument_symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price TEXT NOT NULL,
    exit_price TEXT NOT NULL,
    stop_loss TEXT NOT NULL,
    target TEXT NOT NULL,
    pnl_gross TEXT NOT NULL,
    pnl_net TEXT NOT NULL,
    charges_total TEXT NOT NULL,
    confidence REAL NOT NULL,
    strategy TEXT NOT NULL,
    entry_reason TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_entry_time ON trade_journal(entry_time);
"""


class TickStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --- ticks ---

    def insert_tick(self, instrument_token: str, ltp: Decimal, volume: Optional[int], exchange_ts: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ticks (instrument_token, ltp, volume, exchange_ts, received_at) VALUES (?, ?, ?, ?, ?)",
                (
                    instrument_token,
                    str(ltp),
                    volume,
                    exchange_ts.astimezone(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def latest_tick_time(self, instrument_token: str) -> Optional[datetime]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT exchange_ts FROM ticks WHERE instrument_token = ? ORDER BY exchange_ts DESC LIMIT 1",
                (instrument_token,),
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0])

    # --- candles ---

    def upsert_candle(
        self,
        instrument_token: str,
        timeframe: str,
        open_time: datetime,
        close_time: datetime,
        open_: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: int,
        is_backfilled: bool = False,
        is_closed: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO candles
                    (instrument_token, timeframe, open_time, close_time, open, high, low, close, volume, is_backfilled, is_closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_token, timeframe, open_time) DO UPDATE SET
                    close_time=excluded.close_time, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume,
                    is_backfilled=excluded.is_backfilled, is_closed=excluded.is_closed
                """,
                (
                    instrument_token,
                    timeframe,
                    open_time.astimezone(timezone.utc).isoformat(),
                    close_time.astimezone(timezone.utc).isoformat(),
                    str(open_),
                    str(high),
                    str(low),
                    str(close),
                    volume,
                    int(is_backfilled),
                    int(is_closed),
                ),
            )

    def bulk_upsert_candles(self, instrument_token: str, timeframe: str, rows: list[dict]) -> None:
        """Same semantics as upsert_candle, but writes every row in `rows` in a
        single transaction/connection. upsert_candle's one-connection-per-row
        pattern is fine for live ingestion (a handful of rows/sec), but is
        orders of magnitude too slow for bulk historical backfill (each row
        pays a full WAL commit) — this exists for that path.

        Each row dict needs: open_time, close_time, open, high, low, close,
        volume, is_backfilled, is_closed.
        """
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO candles
                    (instrument_token, timeframe, open_time, close_time, open, high, low, close, volume, is_backfilled, is_closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_token, timeframe, open_time) DO UPDATE SET
                    close_time=excluded.close_time, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume,
                    is_backfilled=excluded.is_backfilled, is_closed=excluded.is_closed
                """,
                [
                    (
                        instrument_token,
                        timeframe,
                        r["open_time"].astimezone(timezone.utc).isoformat(),
                        r["close_time"].astimezone(timezone.utc).isoformat(),
                        str(r["open"]),
                        str(r["high"]),
                        str(r["low"]),
                        str(r["close"]),
                        r["volume"],
                        int(r["is_backfilled"]),
                        int(r["is_closed"]),
                    )
                    for r in rows
                ],
            )

    def get_candles(self, instrument_token: str, timeframe: str, limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT open_time, close_time, open, high, low, close, volume, is_backfilled, is_closed
                FROM candles WHERE instrument_token = ? AND timeframe = ?
                ORDER BY open_time DESC LIMIT ?
                """,
                (instrument_token, timeframe, limit),
            ).fetchall()
        columns = ["open_time", "close_time", "open", "high", "low", "close", "volume", "is_backfilled", "is_closed"]
        return [dict(zip(columns, row)) for row in reversed(rows)]

    def latest_and_prev_close(self, instrument_token: str, timeframe: str, lookback: int = 1500) -> Optional[dict]:
        """LTP (latest stored candle's close), the previous trading day's
        close, and the latest trading day's high/low, for a watchlist-style
        quote card. Groups the last `lookback` candles by their UTC calendar
        date — safe as a stand-in for the IST trading day here because the
        whole NSE session (09:15-15:30 IST = 03:45-10:00 UTC) sits inside one
        UTC date, never crossing midnight. Returns None if there's no data at
        all for this token/timeframe.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT open_time, high, low, close FROM candles WHERE instrument_token = ? AND timeframe = ? "
                "ORDER BY open_time DESC LIMIT ?",
                (instrument_token, timeframe, lookback),
            ).fetchall()
        if not rows:
            return None
        latest_time = rows[0][0]
        latest_close = rows[0][3]
        latest_date = latest_time[:10]
        day_high = float("-inf")
        day_low = float("inf")
        prev_close = None
        for open_time, high, low, close in rows:
            if open_time[:10] != latest_date:
                prev_close = close
                break
            day_high = max(day_high, float(high))
            day_low = min(day_low, float(low))
        day_range_pct = ((day_high - day_low) / day_low * 100) if day_low > 0 else None
        return {
            "ltp": float(latest_close),
            "latest_time": latest_time,
            "prev_close": float(prev_close) if prev_close is not None else None,
            "day_high": day_high,
            "day_low": day_low,
            "day_range_pct": day_range_pct,
        }

    def find_gaps(self, instrument_token: str, timeframe: str, expected_step_seconds: int) -> list[tuple[datetime, datetime]]:
        """Return (gap_start, gap_end) pairs where consecutive stored candles are
        further apart than expected_step_seconds, i.e. missing bars."""
        candles = self.get_candles(instrument_token, timeframe, limit=100000)
        gaps: list[tuple[datetime, datetime]] = []
        for prev, curr in zip(candles, candles[1:]):
            prev_close = datetime.fromisoformat(prev["close_time"])
            curr_open = datetime.fromisoformat(curr["open_time"])
            if (curr_open - prev_close).total_seconds() > expected_step_seconds:
                gaps.append((prev_close, curr_open))
        return gaps

    # --- health events ---

    def record_health_event(self, event_type: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO data_health_events (event_type, detail, occurred_at) VALUES (?, ?, ?)",
                (event_type, detail, datetime.now(timezone.utc).isoformat()),
            )

    def count_health_events(self, event_type: str, since: Optional[datetime] = None) -> int:
        with self._connect() as conn:
            if since is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM data_health_events WHERE event_type = ?", (event_type,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM data_health_events WHERE event_type = ? AND occurred_at >= ?",
                    (event_type, since.astimezone(timezone.utc).isoformat()),
                ).fetchone()
        return row[0]

    # --- trade journal ---

    def insert_journal_entry(self, entry: "TradeJournalEntry") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_journal
                    (instrument_token, instrument_symbol, direction, quantity, entry_price, exit_price,
                     stop_loss, target, pnl_gross, pnl_net, charges_total, confidence, strategy,
                     entry_reason, exit_reason, entry_time, exit_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.instrument_token, entry.instrument_symbol, entry.direction.value, entry.quantity,
                    str(entry.entry_price), str(entry.exit_price), str(entry.stop_loss), str(entry.target),
                    str(entry.pnl_gross), str(entry.pnl_net), str(entry.charges_total), entry.confidence,
                    entry.strategy, entry.entry_reason, entry.exit_reason,
                    entry.entry_time.astimezone(timezone.utc).isoformat(),
                    entry.exit_time.astimezone(timezone.utc).isoformat(),
                ),
            )

    def get_journal_entries(self, limit: int = 500) -> list["TradeJournalEntry"]:
        from data.models import SignalDirection, TradeJournalEntry

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT instrument_token, instrument_symbol, direction, quantity, entry_price, exit_price,
                       stop_loss, target, pnl_gross, pnl_net, charges_total, confidence, strategy,
                       entry_reason, exit_reason, entry_time, exit_time
                FROM trade_journal ORDER BY entry_time DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

        entries = []
        for row in rows:
            entries.append(
                TradeJournalEntry(
                    instrument_token=row[0], instrument_symbol=row[1], direction=SignalDirection(row[2]),
                    quantity=row[3], entry_price=Decimal(row[4]), exit_price=Decimal(row[5]),
                    stop_loss=Decimal(row[6]), target=Decimal(row[7]), pnl_gross=Decimal(row[8]),
                    pnl_net=Decimal(row[9]), charges_total=Decimal(row[10]), confidence=row[11],
                    strategy=row[12], entry_reason=row[13], exit_reason=row[14],
                    entry_time=datetime.fromisoformat(row[15]), exit_time=datetime.fromisoformat(row[16]),
                )
            )
        return entries
