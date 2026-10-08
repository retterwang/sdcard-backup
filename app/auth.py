"""账号、登录会话与用户管理。

设计要点
--------
- **密码存储**：``hashlib.pbkdf2_hmac``（PBKDF2-HMAC-SHA256，20 万次迭代 + 16 字节随机盐），
  仅使用标准库，不引入第三方依赖。落库格式 ``pbkdf2_sha256$<迭代>$<盐 hex>$<摘要 hex>``。
- **登录会话**：随机 token 存 SQLite ``sessions`` 表，浏览器侧只保存 HttpOnly Cookie；
  默认 7 天有效期，剩余不足一半时滑动续期（见 :func:`current_user`）。
- **初始管理员**：数据库内没有任何用户时自动创建（默认 ``admin/admin``）并置
  ``must_change=1``；在修改用户名与密码之前，后端会拦截其余所有接口。
- **用户管理**：支持增删、启停、重置密码；始终保证至少存在一个启用状态的管理员，
  且不允许删除/停用当前登录的账号。
- **登录限速**：同一来源 IP 在 5 分钟内失败 8 次即暂时拒绝（进程内存态，重启即清空）。

所有失败原因返回 ``(消息键, 参数)``，由 Web 层按请求语言渲染。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Optional

from . import config

log = logging.getLogger("auth")

PBKDF2_ITERATIONS = 200_000
HASH_PREFIX = "pbkdf2_sha256"

# 用户名允许：字母、数字、下划线、点、@、连字符，以及中日韩汉字（\w 在 Python3 已含汉字）
USERNAME_RE = re.compile(r"^[\w.@\-]+$", re.UNICODE)

# 登录限速（进程内存态）
MAX_FAILS = 8
FAIL_WINDOW = 300
_fails: dict = {}
_fails_lock = threading.Lock()


# ── 密码 ─────────────────────────────────────────────────────
def hash_password(password: str, salt: Optional[str] = None) -> str:
    """生成密码摘要（salt 仅用于测试注入，正式调用一律随机）。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"{HASH_PREFIX}${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码（常量时间比较；存储格式异常一律视为不通过）。"""
    try:
        algo, iterations, salt, digest = str(stored).split("$")
        if algo != HASH_PREFIX:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError, TypeError):
        return False


# ── 校验 ─────────────────────────────────────────────────────
def check_username(name: str) -> Optional[tuple]:
    """返回 (消息键, 参数) 表示不合法；None 表示通过。"""
    s = (name or "").strip()
    if not (config.MIN_USERNAME_LEN <= len(s) <= config.MAX_USERNAME_LEN):
        return ("auth.err.username_len",
                {"min": config.MIN_USERNAME_LEN, "max": config.MAX_USERNAME_LEN})
    if not USERNAME_RE.match(s):
        return ("auth.err.username_chars", {})
    return None


def check_password(password: str) -> Optional[tuple]:
    if not password or len(password) < config.MIN_PASSWORD_LEN:
        return ("auth.err.password_len", {"min": config.MIN_PASSWORD_LEN})
    return None


# ── 登录限速 ─────────────────────────────────────────────────
def blocked_seconds(ip: str) -> int:
    """该 IP 是否被临时拒绝登录；返回剩余秒数（0 = 允许）。"""
    if not ip:
        return 0
    now = time.time()
    with _fails_lock:
        stamps = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = stamps
        if len(stamps) >= MAX_FAILS:
            return int(FAIL_WINDOW - (now - stamps[0])) + 1
    return 0


def note_failure(ip: str):
    if not ip:
        return
    with _fails_lock:
        now = time.time()
        stamps = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        stamps.append(now)
        _fails[ip] = stamps


def clear_failures(ip: str):
    if not ip:
        return
    with _fails_lock:
        _fails.pop(ip, None)


# ── 初始管理员 ───────────────────────────────────────────────
def ensure_default_admin(db) -> None:
    """数据库内没有任何用户时创建初始管理员（幂等）。"""
    if db.users_count() > 0:
        return
    name = (config.INITIAL_ADMIN_USER or "admin").strip() or "admin"
    password = config.INITIAL_ADMIN_PASSWORD or "admin"
    db.user_create(name, hash_password(password), is_admin=True, must_change=True)
    log.warning("已创建初始管理员账号「%s」，请登录后立即修改用户名与密码", name)


# ── 对外视图（绝不外泄密码摘要） ──────────────────────────────
def public_user(u: dict) -> dict:
    return {
        "username": u["username"],
        "is_admin": bool(u["is_admin"]),
        "enabled": bool(u["enabled"]),
        "must_change": bool(u["must_change"]),
        "created_at": u.get("created_at"),
        "last_login_at": u.get("last_login_at"),
    }


# ── 会话 ─────────────────────────────────────────────────────
def create_session(db, username: str, ip: str = "", ua: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.sessions_purge(now)
    db.session_create(token, username, now, now + config.SESSION_TTL, ip, (ua or "")[:200])
    return token


def current_user(db, token: Optional[str]) -> Optional[dict]:
    """按 token 取当前用户；过期或账号失效时自动清理会话。"""
    if not token:
        return None
    row = db.session_get(token)
    if not row:
        return None
    now = time.time()
    if (row["expires_at"] or 0) < now:
        db.session_delete(token)
        return None
    u = db.user_get(row["username"])
    if not u or not u["enabled"]:
        db.session_delete(token)
        return None
    if (row["expires_at"] or 0) - now < config.SESSION_TTL / 2:   # 滑动续期
        db.session_touch(token, now + config.SESSION_TTL)
    return u


def login(db, username: str, password: str, ip: str = "", ua: str = "") -> dict:
    """登录。成功返回 {"ok": True, "token", "user"}，失败返回 {"ok": False, "key", "params"}。"""
    left = blocked_seconds(ip)
    if left > 0:
        return {"ok": False, "key": "auth.err.too_many",
                "params": {"minutes": max(1, left // 60 + 1)}}

    u = db.user_get((username or "").strip())
    if not u or not verify_password(password or "", u["pass_hash"]):
        note_failure(ip)
        return {"ok": False, "key": "auth.err.bad_credentials", "params": {}}
    if not u["enabled"]:
        return {"ok": False, "key": "auth.err.disabled", "params": {}}

    clear_failures(ip)
    now = time.time()
    db.user_update(u["username"], {"last_login_at": now, "last_login_ip": ip or ""})
    u = db.user_get(u["username"])
    token = create_session(db, u["username"], ip, ua)
    log.info("用户登录成功: %s (%s)", u["username"], ip or "-")
    return {"ok": True, "token": token, "user": public_user(u)}


def logout(db, token: Optional[str]):
    if token:
        db.session_delete(token)


# ── 修改自己的账号信息 ────────────────────────────────────────
def change_credentials(db, actor: str, current_password: str,
                       new_username: Optional[str] = None,
                       new_password: Optional[str] = None,
                       confirm_password: Optional[str] = None,
                       require_new_username: bool = False) -> dict:
    """修改当前登录账号的用户名 / 密码。

    ``require_new_username=True``（首次登录强制改密）时，必须同时更换用户名。
    成功后注销该用户的全部旧会话，返回新 token（调用方需重新下发 Cookie）。
    """
    u = db.user_get(actor)
    if not u:
        return {"ok": False, "key": "auth.err.user_not_found", "params": {}}
    if not (current_password and verify_password(current_password, u["pass_hash"])):
        return {"ok": False, "key": "auth.err.current_wrong", "params": {}}

    name = (new_username or "").strip() or u["username"]
    pw = new_password or ""
    if confirm_password is not None and pw != confirm_password:
        return {"ok": False, "key": "auth.err.passwords_mismatch", "params": {}}

    name_changed = name != u["username"]
    pw_changed = bool(pw)

    if require_new_username and not name_changed:
        return {"ok": False, "key": "auth.err.need_new_username", "params": {}}
    if not name_changed and not pw_changed:
        return {"ok": False, "key": "auth.err.nothing_changed", "params": {}}

    if name_changed:
        bad = check_username(name)
        if bad:
            return {"ok": False, "key": bad[0], "params": bad[1]}
        if db.user_get(name):
            return {"ok": False, "key": "auth.err.username_taken", "params": {}}
    if pw_changed:
        bad = check_password(pw)
        if bad:
            return {"ok": False, "key": bad[0], "params": bad[1]}
        if pw.lower() == name.lower():
            return {"ok": False, "key": "auth.err.password_same", "params": {}}
        if verify_password(pw, u["pass_hash"]):
            return {"ok": False, "key": "auth.err.password_unchanged", "params": {}}

    patch: dict = {"must_change": False}
    if name_changed:
        patch["username"] = name
    if pw_changed:
        patch["pass_hash"] = hash_password(pw)
    updated = db.user_update(u["username"], patch)

    db.sessions_delete_user(updated["username"])          # 旧会话全部失效
    token = create_session(db, updated["username"])
    log.info("账号信息已更新: %s → %s（改密=%s）", u["username"], updated["username"], pw_changed)
    return {"ok": True, "token": token, "user": public_user(updated)}


# ── 用户管理（仅管理员） ──────────────────────────────────────
def list_users(db) -> list:
    return [public_user(u) for u in db.users_all()]


def _last_admin_guard(db, target: dict, patch: dict) -> bool:
    """若本次操作会让「启用的管理员」归零，则拒绝。"""
    if not target["is_admin"] or not target["enabled"]:
        return True
    becoming = ("is_admin" in patch and not patch["is_admin"]) or \
               ("enabled" in patch and not patch["enabled"])
    if not becoming:
        return True
    return db.admins_enabled() > 1


def create_user(db, username: str, password: str, is_admin: bool = False) -> dict:
    name = (username or "").strip()
    bad = check_username(name)
    if bad:
        return {"ok": False, "key": bad[0], "params": bad[1]}
    bad = check_password(password or "")
    if bad:
        return {"ok": False, "key": bad[0], "params": bad[1]}
    if name.lower() == (password or "").lower():
        return {"ok": False, "key": "auth.err.password_same", "params": {}}
    if db.user_get(name):
        return {"ok": False, "key": "auth.err.username_taken", "params": {}}
    u = db.user_create(name, hash_password(password), is_admin=is_admin, must_change=True)
    log.info("已创建用户: %s（管理员=%s）", name, bool(is_admin))
    return {"ok": True, "user": public_user(u)}


def update_user(db, actor: str, username: str, patch: dict) -> dict:
    target = db.user_get(username)
    if not target:
        return {"ok": False, "key": "auth.err.user_not_found", "params": {}}

    new_patch: dict = {}
    if patch.get("is_admin") is not None:
        new_patch["is_admin"] = bool(patch["is_admin"])
    if patch.get("enabled") is not None:
        if not patch["enabled"] and username == actor:
            return {"ok": False, "key": "auth.err.self_disable", "params": {}}
        new_patch["enabled"] = bool(patch["enabled"])
    if patch.get("password"):
        bad = check_password(patch["password"])
        if bad:
            return {"ok": False, "key": bad[0], "params": bad[1]}
        if patch["password"].lower() == username.lower():
            return {"ok": False, "key": "auth.err.password_same", "params": {}}
        new_patch["pass_hash"] = hash_password(patch["password"])
        new_patch["must_change"] = True
        db.sessions_delete_user(username)                # 改密后强制重新登录
    if patch.get("must_change") is not None:
        new_patch["must_change"] = bool(patch["must_change"])
    if not new_patch:
        return {"ok": False, "key": "auth.err.nothing_changed", "params": {}}

    if not _last_admin_guard(db, target, new_patch):
        return {"ok": False, "key": "auth.err.last_admin", "params": {}}
    u = db.user_update(username, new_patch)
    log.info("用户已更新: %s -> %s", username, {k: v for k, v in new_patch.items()
                                               if k != "pass_hash"})
    return {"ok": True, "user": public_user(u)}


def delete_user(db, actor: str, username: str) -> dict:
    target = db.user_get(username)
    if not target:
        return {"ok": False, "key": "auth.err.user_not_found", "params": {}}
    if username == actor:
        return {"ok": False, "key": "auth.err.self_delete", "params": {}}
    if not _last_admin_guard(db, target, {"enabled": False}):
        return {"ok": False, "key": "auth.err.last_admin", "params": {}}
    db.user_delete(username)
    log.info("用户已删除: %s", username)
    return {"ok": True}
