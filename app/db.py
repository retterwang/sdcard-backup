"""SQLite 存储层。

五类数据：
- cards     卡片白名单（按卡的身份 ID 匹配，只有注册过的卡才自动备份）
- files     文件索引（增量备份的核心依据：路径 + 大小 + mtime + 哈希）
- tasks     备份任务与结果
- settings  业务设置（键值对，界面可改）
- users/sessions  账号与登录会话（密码摘要 + 会话 token）
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
    note TEXT DEFAULT '',
    target_root TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    pass_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    must_change INTEGER NOT NULL DEFAULT 0,
    created_at REAL,
    last_login_at REAL,
    last_login_ip TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at REAL,
    expires_at REAL,
    ip TEXT DEFAULT '',
    ua TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    card_alias TEXT,
    status TEXT DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    detail TEXT DEFAULT '',
    at REAL
);
CREATE INDEX IF NOT EXISTS idx_files_card ON files(card_id);
CREATE INDEX IF NOT EXISTS idx_errors_task ON task_errors(task_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_login_ip ON login_attempts(ip, ts);
"""

# 兼容旧库的增量迁移（ALTER TABLE 在列已存在时会报错，逐条容错）
MIGRATIONS = (
    "ALTER TABLE cards ADD COLUMN target_root TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN avg_speed REAL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN peak_speed REAL DEFAULT 0",
)

# PATCH 接口允许修改的卡片字段
CARD_PATCHABLE = {
    "alias", "dest_subdir", "organize", "enabled", "all_files",
    "include_globs", "exclude_globs", "note", "target_root",
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
            for sql in MIGRATIONS:
                try:
                    c.execute(sql)
                except sqlite3.OperationalError:
                    pass          # 列已存在（新库或已迁移）
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

    def close(self):
        """关闭当前线程持有的连接（测试或进程退出时使用）。"""
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            finally:
                self._local.conn = None

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
               card_size,created_at,last_seen_at,note,target_root)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                d["id"], d["alias"], d["dest_subdir"], d.get("organize", "date"),
                1 if d.get("enabled", True) else 0,
                1 if d.get("all_files") else 0,
                d.get("include_globs", ""), d.get("exclude_globs", ""),
                d.get("fs_uuid", ""), d.get("fs_label", ""), d.get("fs_type", ""),
                d.get("reader_serial", ""), int(d.get("card_size") or 0),
                now, now, d.get("note", ""), (d.get("target_root") or "").strip(),
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

    def card_stats_all(self) -> dict:
        """一次查询取回全部卡片的 (文件数, 字节数)。

        避免 snapshot() 对每张卡各发一次 COUNT/SUM 的 N+1 查询。
        返回 {card_id: (files, bytes)}。
        """
        rows = self.query(
            "SELECT card_id, COUNT(*) n, COALESCE(SUM(size),0) b FROM files "
            "WHERE status='done' GROUP BY card_id"
        )
        return {r["card_id"]: (int(r["n"]), int(r["b"])) for r in rows}

    def cards_lookup(self, ids_lists: list) -> dict:
        """批量按候选 ID 列表查找卡片，避免逐设备多次单条查询。

        ids_lists: [[card_id, 'fs:uuid', ...], ...]（每个设备的候选 ID）
        返回 {设备的候选 ID 元组下标: card}，键为该设备在入参中的索引。
        卡片匹配优先级与 card_lookup 一致：按候选顺序取第一个命中。
        """
        want = {}
        for idx, ids in enumerate(ids_lists):
            for i in ids:
                if i:
                    want.setdefault(i, idx)
        if not want:
            return {}
        out: dict = {}
        # SQLite 参数上限约 999，分批查询
        keys = list(want)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            ph = ",".join("?" * len(chunk))
            for r in self.query(f"SELECT * FROM cards WHERE id IN ({ph})", chunk):
                out[r["id"]] = r
        # 按每个设备自身的候选顺序取第一个命中
        result = {}
        for idx, ids in enumerate(ids_lists):
            for i in ids:
                if i and i in out:
                    result[idx] = out[i]
                    break
        return result

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

    def task_interrupted_mark(self) -> int:
        """把上次进程退出时仍处于 running 的任务标记为 interrupted。

        用于重启后清理"僵尸任务"——运行态任务不可能跨进程存活。
        返回被标记的记录数。
        """
        rows = self.query("SELECT id FROM tasks WHERE status='running'")
        if not rows:
            return 0
        now = time.time()
        with self._lock:
            c = self._conn()
            c.execute(
                "UPDATE tasks SET status='interrupted', finished_at=?, "
                "phase=? WHERE status='running'",
                (now, json.dumps({"k": "status.interrupted", "p": {}},
                                 ensure_ascii=False, separators=(",", ":"))),
            )
            c.commit()
        return len(rows)

    def task_resumable(self) -> list:
        """可恢复的任务：被中断、且该卡仍存在（供界面提示「断点续传」）。"""
        return self.query(
            "SELECT * FROM tasks WHERE status='interrupted' "
            "ORDER BY id DESC LIMIT 20"
        )

    # ── 通知历史 ──────────────────────────────────────────
    def notify_add(self, task_id, alias: str, status: str, ok: bool, detail: str):
        self.execute(
            "INSERT INTO notifications(task_id,card_alias,status,ok,detail,at) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, alias, status, 1 if ok else 0, (detail or "")[:500], time.time()),
        )

    def notifications_recent(self, limit: int = 20) -> list:
        return self.query(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
        )

    def notifications_cleanup(self, keep: int = 100):
        r = self.query_one(
            "SELECT MIN(id) m FROM (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        if r and r["m"]:
            self.execute("DELETE FROM notifications WHERE id < ?", (r["m"],))

    # ── 用户 ──────────────────────────────────────────────
    def users_all(self) -> list:
        return self.query("SELECT * FROM users ORDER BY created_at, username")

    def users_count(self) -> int:
        r = self.query_one("SELECT COUNT(*) n FROM users")
        return int(r["n"]) if r else 0

    def admins_enabled(self) -> int:
        r = self.query_one("SELECT COUNT(*) n FROM users WHERE is_admin=1 AND enabled=1")
        return int(r["n"]) if r else 0

    def user_get(self, username: str):
        return self.query_one("SELECT * FROM users WHERE username=?", (username or "",))

    def user_create(self, username: str, pass_hash: str, is_admin: bool = False,
                    must_change: bool = False) -> dict:
        self.execute(
            "INSERT INTO users(username,pass_hash,is_admin,enabled,must_change,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (username, pass_hash, 1 if is_admin else 0, 1, 1 if must_change else 0, time.time()),
        )
        return self.user_get(username)

    # 允许修改的用户字段（其余字段由 auth.py 之外的地方禁止触碰）
    USER_PATCHABLE = {"username", "pass_hash", "is_admin", "enabled",
                      "must_change", "last_login_at", "last_login_ip"}

    def user_update(self, username: str, patch: dict) -> dict:
        fields, args = [], []
        for k, v in patch.items():
            if k not in self.USER_PATCHABLE:
                continue
            if k in ("is_admin", "enabled", "must_change"):
                v = 1 if v else 0
            fields.append(f"{k}=?")
            args.append(v)
        if not fields:
            return self.user_get(username)
        args.append(username)
        with self._lock:
            c = self._conn()
            c.execute(f"UPDATE users SET {', '.join(fields)} WHERE username=?", args)
            if "username" in patch:      # 改名后同步会话归属，避免旧会话丢失身份
                c.execute("UPDATE sessions SET username=? WHERE username=?",
                          (patch["username"], username))
            c.commit()
        return self.user_get(patch.get("username", username))

    def user_delete(self, username: str):
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM sessions WHERE username=?", (username,))
            c.execute("DELETE FROM users WHERE username=?", (username,))
            c.commit()

    # ── 会话 ──────────────────────────────────────────────
    def session_create(self, token: str, username: str, created_at: float,
                       expires_at: float, ip: str = "", ua: str = ""):
        self.execute(
            "INSERT INTO sessions(token,username,created_at,expires_at,ip,ua) "
            "VALUES(?,?,?,?,?,?)",
            (token, username, created_at, expires_at, ip, ua),
        )

    def session_get(self, token: str):
        return self.query_one("SELECT * FROM sessions WHERE token=?", (token or "",))

    def session_touch(self, token: str, expires_at: float):
        self.execute("UPDATE sessions SET expires_at=? WHERE token=?", (expires_at, token))

    def session_delete(self, token: str):
        self.execute("DELETE FROM sessions WHERE token=?", (token or "",))

    def sessions_delete_user(self, username: str):
        self.execute("DELETE FROM sessions WHERE username=?", (username,))

    def sessions_purge(self, now: float = None):
        self.execute("DELETE FROM sessions WHERE expires_at < ?", (now or time.time(),))

    # ── 登录限速（落库，重启不清零） ──────────────────────
    def login_failures(self, ip: str, since: float) -> list:
        """返回该 IP 在 since 之后的失败时间戳（升序）。"""
        if not ip:
            return []
        return [
            float(r["ts"]) for r in self.query(
                "SELECT ts FROM login_attempts WHERE ip=? AND ts>=? ORDER BY ts",
                (ip, since),
            )
        ]

    def login_failure_add(self, ip: str, ts: float = None):
        if not ip:
            return
        self.execute("INSERT INTO login_attempts(ip,ts) VALUES(?,?)",
                     (ip, ts or time.time()))

    def login_failures_clear(self, ip: str):
        if not ip:
            return
        self.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))

    def login_failures_purge(self, before: float):
        self.execute("DELETE FROM login_attempts WHERE ts < ?", (before,))
