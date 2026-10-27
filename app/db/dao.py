"""SQLite 访问层。

刻意做得很薄：不引入 ORM，不隐藏 SQL。
理由是这个项目里"数据长什么样"本身就是面试要讲的内容，SQL 应该肉眼可读。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """打开连接并设置合理的 PRAGMA。

    - WAL：读写并发（Agent 跑着的时候还能写 trace）
    - foreign_keys：chunks -> documents 的级联删除要生效
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    """with 块内自动提交 / 异常回滚 / 关闭。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection, schema_path: Path = SCHEMA_PATH) -> None:
    """执行 schema.sql（幂等）。"""
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def upsert_many(
    conn: sqlite3.Connection,
    table: str,
    rows: Sequence[dict[str, Any]],
    batch: int = 500,
) -> int:
    """批量 INSERT OR REPLACE。

    以第一行的键作为列集合 —— 调用方需保证所有行键一致
    （抓取脚本里天然一致，因为都是同一个 dataclass 转出来的 dict）。
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"

    written = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        values = [tuple(r.get(c) for c in cols) for r in chunk]
        conn.executemany(sql, values)
        written += len(chunk)
    conn.commit()
    return written


def fetch_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict]:
    cur = conn.execute(sql, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def fetch_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict | None:
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    return dict(row) if row else None


def count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"]) if row else 0


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
