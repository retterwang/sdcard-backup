"""SQLite 存储层。

四类数据：
- cards     卡片白名单（按卡的身份 ID 匹配，只有注册过的卡才自动备份）
- files     文件索引（增量备份的核心依据：路径 + 大小 + mtime + 哈希）
- tasks     备份任务与结果
- settings  业务设置（键值对，界面可改）
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    dest_subdir TEXT NOT NULL,
    organize TEXT NOT NULL DEFAULT 'date',
    enabled INTEGER NOT NULL DEFAULT 1,
    all_files INTEGER NOT NULL DEFAULT 0,
    include_globs TEXT NOT NULL DEFAULT '',
    exclude_globs TEXT NOT NULL DEFAULT '',
    fs_uuid TEXT DEFAULT '',
    fs_label TEXT DEFAULT '',
    fs_type TEXT DEFAULT '',
    reader_serial TEXT DEFAULT '',
    card_size INTEGER DEFAULT 0,
    created_at REAL,
    last_seen_at REAL,
    last_backup_at REAL,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS files (
    card_id TEXT NOT NULL,
    relpath TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    hash TEXT DEFAULT '',
    dest_rel TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    backed_up_at REAL,
    PRIMARY KEY (card_id, relpath)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT,
    card_alias TEXT,
    trigger TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    phase TEXT DEFAULT '',
    started_at REAL,
    finished_at REAL,
    total_files INTEGER DEFAULT 0,
    done_files INTEGER DEFAULT 0,
    total_bytes INTEGER DEFAULT 0,
    done_bytes INTEGER DEFAULT 0,
    skipped_files INTEGER DEFAULT 0,
    skipped_bytes INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    result TEXT DEFAULT '',
    error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS task_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    relpath TEXT,
    message TEXT,
    at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_card ON files(card_id);
CREATE INDEX IF NOT EXISTS idx_errors_task ON task_errors(task_id);
"""

# PATCH 接口允许修改的卡片字段
CARD_PATCHABLE = {
    "alias", "dest_subdir", "organize", "enabled", "all_files",
    "include_globs", "exclude_globs", "note",
}


class DB:
    """线程安全的轻量 SQLite 封装（每线程独立连接 + WAL）。"""

    def __init__(self, path: str):
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init()

    # ── 基础 ──────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def _init(self):
        with self._lock:
            c = self._conn()
            c.executescript(SCHEMA)
            c.commit()

    def execute(self, sql, args=()):
        with self._lock:
            c = self._conn()
            cur = c.execute(sql, args)
            c.commit()
            return cur

    def query(self, sql, args=()) -> list:
        return [dict(r) for r in self._conn().execute(sql, args).fetchall()]

    def query_one(self, sql, args=()):
        r = self._conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    # ── 设置 ──────────────────────────────────────────────
    def settings_all(self) -> dict:
        out = dict(config.DEFAULT_SETTINGS)
        for r in self._conn().execute("SELECT k, v FROM settings").fetchall():
            try:
                out[r["k"]] = json.loads(r["v"])
            except Exception:
                pass
        return out

    def setting_get(self, key, default=None):
        r = self.query_one("SELECT v FROM settings WHERE k=?", (key,))
        if r is None:
            return config.DEFAULT_SETTINGS.get(key, default)
        try:
            return json.loads(r["v"])
        except Exception:
            return default

    def settings_set_many(self, d: dict):
        with self._lock:
            c = self._conn()
            for k, v in d.items():
                c.execute(
                    "INSERT INTO settings(k,v) VALUES(?,?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                    (k, json.dumps(v, ensure_ascii=False)),
                )
            c.commit()

    # ── 卡片 ──────────────────────────────────────────────
    def cards_all(self) -> list:
        return self.query("SELECT * FROM cards ORDER BY created_at")

    def card_get(self, cid: str):
        return self.query_one("SELECT * FROM cards WHERE id=?", (cid,))

    def card_lookup(self, ids: list):
        """按候选 ID 列表（[card_id, fs:uuid, ...]）查找已注册卡片。"""
        for i in ids:
            if not i:
                continue
            r = self.card_get(i)
            if r:
                return r
        return None

    def card_create(self, d: dict) -> dict:
        now = time.time()
        self.execute(
            """INSERT INTO cards(id,alias,dest_subdir,organize,enabled,all_files,
               include_globs,exclude_globs,fs_uuid,fs_label,fs_type,reader_serial,
               card_size,created_at,last_seen_at,note)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d["id"], d["alias"], d["dest_subdir"], d.get("organize", "date"),
                1 if d.get("enabled", True) else 0,
                1 if d.get("all_files") else 0,
                d.get("include_globs", ""), d.get("exclude_globs", ""),
                d.get("fs_uuid", ""), d.get("fs_label", ""), d.get("fs_type", ""),
                d.get("reader_serial", ""), int(d.get("card_size") or 0),
                now, now, d.get("note", ""),
            ),
        )
        return self.card_get(d["id"])

    def card_update(self, cid: str, patch: dict) -> dict:
        fields, args = [], []
        for k, v in patch.items():
            if k not in CARD_PATCHABLE:
                continue
            if k in ("enabled", "all_files"):
                v = 1 if v else 0
            fields.append(f"{k}=?")
            args.append(v)
        if fields:
            args.append(cid)
            self.execute(f"UPDATE cards SET {', '.join(fields)} WHERE id=?", args)
        return self.card_get(cid)

    def card_touch(self, cid: str, backup_done: bool = False):
        now = time.time()
        if backup_done:
            self.execute(
                "UPDATE cards SET last_seen_at=?, last_backup_at=? WHERE id=?",
                (now, now, cid),
            )
        else:
            self.execute("UPDATE cards SET last_seen_at=? WHERE id=?", (now, cid))

    def card_delete(self, cid: str):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM files WHERE card_id=?", (cid,))
            c.execute("DELETE FROM cards WHERE id=?", (cid,))
            c.commit()

    # ── 文件索引 ──────────────────────────────────────────
    def file_index(self, card_id: str) -> dict:
        rows = self.query(
            "SELECT relpath,size,mtime,hash,dest_rel,status FROM files WHERE card_id=?",
            (card_id,),
        )
        return {r["relpath"]: r for r in rows}

    def file_upsert_many(self, rows: list):
        """rows: [(card_id, relpath, size, mtime, hash, dest_rel, status, backed_up_at)]"""
        if not rows:
            return
        with self._lock:
            c = self._conn()
            c.executemany(
                """INSERT INTO files(card_id,relpath,size,mtime,hash,dest_rel,status,backed_up_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(card_id,relpath) DO UPDATE SET
                     size=excluded.size, mtime=excluded.mtime, hash=excluded.hash,
                     dest_rel=excluded.dest_rel, status=excluded.status,
                     backed_up_at=excluded.backed_up_at""",
                rows,
            )
            c.commit()

    def card_stats(self, card_id: str):
        r = self.query_one(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM files "
            "WHERE card_id=? AND status='done'",
            (card_id,),
        )
        return (int(r["n"]), int(r["b"])) if r else (0, 0)

    def card_reset_index(self, card_id: str):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM files WHERE card_id=?", (card_id,))
            c.commit()

    # ── 任务 ──────────────────────────────────────────────
    def task_create(self, card_id: str, alias: str, trigger: str) -> int:
        cur = self.execute(
            "INSERT INTO tasks(card_id,card_alias,trigger,status,started_at) "
            "VALUES(?,?,?,'running',?)",
            (card_id, alias, trigger, time.time()),
        )
        return int(cur.lastrowid)

    def task_finish(self, tid: int, status: str, **fields):
        fields["status"] = status
        fields["finished_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE tasks SET {cols} WHERE id=?", list(fields.values()) + [tid])

    def task_error_add(self, tid: int, relpath: str, message: str):
        self.execute(
            "INSERT INTO task_errors(task_id,relpath,message,at) VALUES(?,?,?,?)",
            (tid, relpath[:500], message[:500], time.time()),
        )

    def tasks_recent(self, limit: int = 20) -> list:
        return self.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))

    def task_get(self, tid: int):
        return self.query_one("SELECT * FROM tasks WHERE id=?", (tid,))

    def task_errors(self, tid: int, limit: int = 500) -> list:
        return self.query(
            "SELECT relpath,message,at FROM task_errors WHERE task_id=? ORDER BY id LIMIT ?",
            (tid, limit),
        )

    def tasks_cleanup(self, keep: int = 200):
        r = self.query_one(
            "SELECT MIN(id) m FROM (SELECT id FROM tasks ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        if r and r["m"]:
            with self._lock:
                c = self._conn()
                c.execute("DELETE FROM task_errors WHERE task_id < ?", (r["m"],))
                c.execute("DELETE FROM tasks WHERE id < ?", (r["m"],))
                c.commit()
