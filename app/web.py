"""FastAPI 应用：REST 接口 + SSE 实时状态 + 静态界面。

所有面向用户的消息均通过 app/i18n.py 输出，语言由请求参数 ?lang= 或
Accept-Language 头决定（默认中文）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, detector, i18n, media

log = logging.getLogger("web")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _lang(request: Request) -> str:
    """解析请求语言：?lang= 优先，其次 Accept-Language。"""
    return i18n.from_request(
        request.query_params.get("lang"),
        request.headers.get("accept-language"),
    )


class CardPayload(BaseModel):
    card_id: Optional[str] = None      # 来自检测到的设备（推荐）
    fs_uuid: Optional[str] = None      # 或手动按文件系统 UUID 预注册
    alias: Optional[str] = None
    dest_subdir: Optional[str] = None
    organize: Optional[str] = None     # date | mirror
    enabled: Optional[bool] = None
    all_files: Optional[bool] = None
    include_globs: Optional[str] = None
    exclude_globs: Optional[str] = None
    note: Optional[str] = None


class SettingsPayload(BaseModel):
    scan_interval: Optional[int] = None
    mount_delay: Optional[int] = None
    verify: Optional[bool] = None
    auto_unmount: Optional[bool] = None
    auto_accept: Optional[bool] = None
    notify_url: Optional[str] = None
    notify_lang: Optional[str] = None


def create_app(db, state, runner) -> FastAPI:
    app = FastAPI(title="SD Card Backup Console", version=config.VERSION)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p in ("/", "/index.html") or p.endswith((".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ── 页面与静态资源 ──
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    # ── 状态 ──
    @app.get("/api/health")
    async def health():
        return {"ok": True, "time": time.time(), "version": config.VERSION}

    @app.get("/api/status")
    async def status():
        return state.snapshot()

    @app.get("/api/events")
    async def events(request: Request):
        async def gen():
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = state.snapshot()
                    yield "data: " + json.dumps(snap, ensure_ascii=False) + "\n\n"
                except Exception as e:
                    log.warning("生成状态快照失败: %s", e)
                    yield "event: error\ndata: {}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/rescan")
    async def rescan(request: Request):
        runner.rescan()
        return {"ok": True, "message": i18n.t("web.rescan_done", _lang(request))}

    @app.get("/api/diagnose")
    def diagnose():
        """运行环境自检（检测不到卡时用于排查）。诊断信息面向维护者，保持中文原文。"""
        return detector.diagnose()

    # ── 卡片白名单 ──
    @app.get("/api/cards")
    async def cards_list():
        return {"cards": state.snapshot()["cards"]}

    @app.post("/api/cards")
    async def card_create(p: CardPayload, request: Request):
        lang = _lang(request)
        dev = None
        card_id = None
        if p.card_id:
            with state.lock:
                info = state.devices.get(p.card_id)
            if info:
                dev = info["device"]
                card_id = dev.card_id
            else:
                card_id = p.card_id
        elif p.fs_uuid:
            card_id = f"fs:{p.fs_uuid}"
        if not card_id:
            raise HTTPException(400, i18n.t("web.card_id_required", lang))
        if db.card_get(card_id):
            raise HTTPException(409, i18n.t("web.card_exists", lang))

        alias = (p.alias or (dev.display.split(" · ")[0] if dev else "") or "SD Card").strip()[:60]
        organize = p.organize if p.organize in ("date", "mirror") else "date"
        subdir = runner._unique_subdir(media.slug_subdir(p.dest_subdir or alias))
        record = {
            "id": card_id, "alias": alias, "dest_subdir": subdir,
            "organize": organize,
            "enabled": True if p.enabled is None else bool(p.enabled),
            "all_files": bool(p.all_files),
            "include_globs": (p.include_globs or "").strip(),
            "exclude_globs": (p.exclude_globs or "").strip(),
            "note": p.note or "",
        }
        if dev:
            record.update({
                "fs_uuid": dev.fs_uuid, "fs_label": dev.label,
                "fs_type": dev.fstype, "reader_serial": dev.reader_serial,
                "card_size": dev.size,
            })
        card = db.card_create(record)
        triggered = False
        if dev and card["enabled"]:
            triggered = runner.enqueue(dev, card, "trigger.after_register")
        return {
            "ok": True, "card": card, "triggered": triggered,
            "message": i18n.t("web.card_registered_started" if triggered
                              else "web.card_registered", lang),
        }

    @app.patch("/api/cards/{cid}")
    async def card_patch(cid: str, p: CardPayload, request: Request):
        lang = _lang(request)
        if not db.card_get(cid):
            raise HTTPException(404, i18n.t("web.card_not_found", lang))
        patch = {}
        if p.alias is not None:
            a = p.alias.strip()
            if not a:
                raise HTTPException(400, i18n.t("web.alias_required", lang))
            patch["alias"] = a[:60]
        if p.dest_subdir is not None:
            patch["dest_subdir"] = runner._unique_subdir(media.slug_subdir(p.dest_subdir))
        if p.organize is not None:
            if p.organize not in ("date", "mirror"):
                raise HTTPException(400, i18n.t("web.organize_invalid", lang))
            patch["organize"] = p.organize
        for k in ("enabled", "all_files"):
            v = getattr(p, k)
            if v is not None:
                patch[k] = bool(v)
        for k in ("include_globs", "exclude_globs", "note"):
            v = getattr(p, k)
            if v is not None:
                patch[k] = v.strip()
        card = db.card_update(cid, patch)
        return {"ok": True, "card": card}

    @app.delete("/api/cards/{cid}")
    async def card_delete(cid: str, request: Request):
        lang = _lang(request)
        if not db.card_get(cid):
            raise HTTPException(404, i18n.t("web.card_not_found", lang))
        db.card_delete(cid)
        return {"ok": True, "message": i18n.t("web.card_removed", lang)}

    @app.post("/api/cards/{cid}/backup-now")
    async def card_backup_now(cid: str, request: Request):
        lang = _lang(request)
        ok, msg = runner.manual_backup(cid)
        if not ok:
            raise HTTPException(400, i18n.t(msg, lang))
        return {"ok": True, "message": i18n.t(msg, lang)}

    @app.post("/api/cards/{cid}/reset-index")
    async def card_reset_index(cid: str, request: Request):
        lang = _lang(request)
        if not db.card_get(cid):
            raise HTTPException(404, i18n.t("web.card_not_found", lang))
        db.card_reset_index(cid)
        return {"ok": True, "message": i18n.t("web.index_reset", lang)}

    @app.post("/api/cards/{cid}/unmount")
    async def card_unmount(cid: str, request: Request):
        lang = _lang(request)
        ok, msg = runner.unmount_card(cid)
        if not ok:
            raise HTTPException(400, i18n.t(msg, lang))
        return {"ok": True, "message": i18n.t(msg, lang)}

    # ── 任务 ──
    @app.get("/api/tasks")
    async def tasks_list(request: Request, limit: int = 50):
        lang = _lang(request)
        limit = max(1, min(limit, 200))
        rows = db.tasks_recent(limit)
        for r in rows:
            r["result"] = i18n.render_stored(r.get("result"), lang)
            r["error"] = i18n.render_stored(r.get("error"), lang)
            r["phase"] = i18n.render_stored(r.get("phase"), lang)
        return {"tasks": rows}

    @app.get("/api/tasks/{tid}")
    async def task_detail(tid: int, request: Request):
        lang = _lang(request)
        t = db.task_get(tid)
        if not t:
            raise HTTPException(404, i18n.t("web.task_not_found", lang))
        t["result"] = i18n.render_stored(t.get("result"), lang)
        t["error"] = i18n.render_stored(t.get("error"), lang)
        t["phase"] = i18n.render_stored(t.get("phase"), lang)
        t["errors"] = [
            dict(e, message=i18n.render_stored(e.get("message"), lang))
            for e in db.task_errors(tid)
        ]
        return t

    @app.post("/api/tasks/{tid}/cancel")
    async def task_cancel(tid: int, request: Request):
        lang = _lang(request)
        ok, msg = runner.cancel_task(tid)
        if not ok:
            raise HTTPException(400, i18n.t(msg, lang))
        return {"ok": True, "message": i18n.t(msg, lang)}

    @app.post("/api/tasks/{tid}/retry")
    async def task_retry(tid: int, request: Request):
        lang = _lang(request)
        t = db.task_get(tid)
        if not t:
            raise HTTPException(404, i18n.t("web.task_not_found", lang))
        ok, msg = runner.manual_backup(t["card_id"])
        if not ok:
            raise HTTPException(400, i18n.t(msg, lang))
        return {"ok": True, "message": i18n.t(msg, lang)}

    # ── 设置 ──
    @app.get("/api/settings")
    async def settings_get():
        return db.settings_all()

    @app.put("/api/settings")
    async def settings_put(p: SettingsPayload, request: Request):
        lang = _lang(request)
        patch = {}
        if p.scan_interval is not None:
            if not 1 <= p.scan_interval <= 120:
                raise HTTPException(400, i18n.t("web.scan_interval_range", lang))
            patch["scan_interval"] = int(p.scan_interval)
        if p.mount_delay is not None:
            if not 0 <= p.mount_delay <= 300:
                raise HTTPException(400, i18n.t("web.mount_delay_range", lang))
            patch["mount_delay"] = int(p.mount_delay)
        for k in ("verify", "auto_unmount", "auto_accept"):
            v = getattr(p, k)
            if v is not None:
                patch[k] = bool(v)
        if p.notify_url is not None:
            u = p.notify_url.strip()
            if u and not u.startswith(("http://", "https://", "smtp+ssl://", "smtp://")):
                raise HTTPException(400, i18n.t("web.notify_url_invalid", lang))
            patch["notify_url"] = u[:500]
        if p.notify_lang is not None:
            v = str(p.notify_lang).strip().lower()
            if v not in i18n.LANGS:
                raise HTTPException(400, i18n.t("web.notify_lang_invalid", lang))
            patch["notify_lang"] = v
        if patch:
            db.settings_set_many(patch)
        return {"ok": True, "settings": db.settings_all()}

    return app
