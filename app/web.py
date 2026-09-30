"""FastAPI 应用：REST 接口 + SSE 实时状态 + 静态界面。"""
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

from . import config, detector, media

log = logging.getLogger("web")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


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


def create_app(db, state, runner) -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.VERSION)

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
    async def rescan():
        runner.rescan()
        return {"ok": True, "message": "已触发重新扫描"}

    @app.get("/api/diagnose")
    def diagnose():
        """运行环境自检（检测不到卡时用于排查）。"""
        return detector.diagnose()

    # ── 卡片白名单 ──
    @app.get("/api/cards")
    async def cards_list():
        return {"cards": state.snapshot()["cards"]}

    @app.post("/api/cards")
    async def card_create(p: CardPayload):
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
            raise HTTPException(400, "缺少卡片标识（card_id 或 fs_uuid）")
        if db.card_get(card_id):
            raise HTTPException(409, "该存储卡已注册过")

        alias = (p.alias or (dev.display.split(" · ")[0] if dev else "") or "存储卡").strip()[:60]
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
            triggered = runner.enqueue(dev, card, "注册后触发")
        return {
            "ok": True, "card": card, "triggered": triggered,
            "message": "已注册" + ("，并开始首次备份" if triggered else ""),
        }

    @app.patch("/api/cards/{cid}")
    async def card_patch(cid: str, p: CardPayload):
        if not db.card_get(cid):
            raise HTTPException(404, "卡片不存在")
        patch = {}
        if p.alias is not None:
            a = p.alias.strip()
            if not a:
                raise HTTPException(400, "别名不能为空")
            patch["alias"] = a[:60]
        if p.dest_subdir is not None:
            patch["dest_subdir"] = runner._unique_subdir(media.slug_subdir(p.dest_subdir))
        if p.organize is not None:
            if p.organize not in ("date", "mirror"):
                raise HTTPException(400, "organize 仅支持 date / mirror")
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
    async def card_delete(cid: str):
        if not db.card_get(cid):
            raise HTTPException(404, "卡片不存在")
        db.card_delete(cid)
        return {"ok": True, "message": "已移除卡片记录（已备份的文件仍保留在磁盘上）"}

    @app.post("/api/cards/{cid}/backup-now")
    async def card_backup_now(cid: str):
        ok, msg = runner.manual_backup(cid)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}

    @app.post("/api/cards/{cid}/reset-index")
    async def card_reset_index(cid: str):
        if not db.card_get(cid):
            raise HTTPException(404, "卡片不存在")
        db.card_reset_index(cid)
        return {"ok": True, "message": "索引已清除，下次插入将重新全量比对（不会覆盖已有文件）"}

    @app.post("/api/cards/{cid}/unmount")
    async def card_unmount(cid: str):
        ok, msg = runner.unmount_card(cid)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}

    # ── 任务 ──
    @app.get("/api/tasks")
    async def tasks_list(limit: int = 50):
        limit = max(1, min(limit, 200))
        return {"tasks": db.tasks_recent(limit)}

    @app.get("/api/tasks/{tid}")
    async def task_detail(tid: int):
        t = db.task_get(tid)
        if not t:
            raise HTTPException(404, "任务不存在")
        t["errors"] = db.task_errors(tid)
        return t

    @app.post("/api/tasks/{tid}/cancel")
    async def task_cancel(tid: int):
        ok, msg = runner.cancel_task(tid)
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}

    @app.post("/api/tasks/{tid}/retry")
    async def task_retry(tid: int):
        t = db.task_get(tid)
        if not t:
            raise HTTPException(404, "任务不存在")
        ok, msg = runner.manual_backup(t["card_id"])
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}

    # ── 设置 ──
    @app.get("/api/settings")
    async def settings_get():
        return db.settings_all()

    @app.put("/api/settings")
    async def settings_put(p: SettingsPayload):
        patch = {}
        if p.scan_interval is not None:
            if not 1 <= p.scan_interval <= 120:
                raise HTTPException(400, "扫描间隔需在 1-120 秒之间")
            patch["scan_interval"] = int(p.scan_interval)
        if p.mount_delay is not None:
            if not 0 <= p.mount_delay <= 300:
                raise HTTPException(400, "挂载延迟需在 0-300 秒之间")
            patch["mount_delay"] = int(p.mount_delay)
        for k in ("verify", "auto_unmount", "auto_accept"):
            v = getattr(p, k)
            if v is not None:
                patch[k] = bool(v)
        if p.notify_url is not None:
            u = p.notify_url.strip()
            if u and not u.startswith(("http://", "https://", "smtp+ssl://", "smtp://")):
                raise HTTPException(400, "通知地址需以 http(s):// 或 smtp+ssl:// 开头")
            patch["notify_url"] = u[:500]
        if patch:
            db.settings_set_many(patch)
        return {"ok": True, "settings": db.settings_all()}

    return app
