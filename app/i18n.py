# -*- coding: utf-8 -*-
"""国际化（i18n）：中英双语文案表与翻译工具。

设计要点：
- 语言来源优先级：请求参数 ``?lang=`` → ``Accept-Language`` 头 → 默认 ``zh``；
- 任务结果 / 失败原因等落库消息统一存为 ``{"k": 键, "p": 参数}`` 的 JSON 文本，
  由界面按当前语言渲染；历史遗留的纯文本消息原样返回，老数据依旧可读；
- 完成通知的语言由设置项 ``notify_lang`` 决定（与浏览器语言无关）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# ── 双语文案表 ─────────────────────────────────────────────
MESSAGES: dict = {
    # 任务状态
    "status.queued": {"zh": "排队中", "en": "Queued"},
    "status.running": {"zh": "备份中", "en": "Running"},
    "status.done": {"zh": "完成", "en": "Done"},
    "status.failed": {"zh": "失败", "en": "Failed"},
    "status.interrupted": {"zh": "已中断", "en": "Interrupted"},
    "status.cancelled": {"zh": "已取消", "en": "Cancelled"},
    "status.unknown": {"zh": "未知", "en": "Unknown"},
    "status.done_with_errors": {"zh": "完成（{n} 个失败）", "en": "Done ({n} failed)"},

    # 任务阶段
    "phase.queued": {"zh": "排队中", "en": "Queued"},
    "phase.waiting": {"zh": "等待设备就绪（{seconds}s）", "en": "Waiting for device ({seconds}s)"},
    "phase.mounting": {"zh": "只读挂载", "en": "Mounting read-only"},
    "phase.scanning": {"zh": "扫描文件", "en": "Scanning files"},
    "phase.comparing": {"zh": "比对索引", "en": "Comparing index"},
    "phase.copying": {"zh": "备份中", "en": "Backing up"},
    "phase.unmounting": {"zh": "卸载", "en": "Unmounting"},

    # 触发方式
    "trigger.insert": {"zh": "插入触发", "en": "Card inserted"},
    "trigger.manual": {"zh": "手动触发", "en": "Manual"},
    "trigger.after_register": {"zh": "注册后触发", "en": "After registration"},

    # 任务结果摘要（拼接片段）
    "task.summary.base": {"zh": "新增 {copied} 个（{bytes}）", "en": "{copied} new ({bytes})"},
    "task.summary.reused": {"zh": "目标已存在 {n} 个", "en": "{n} already at destination"},
    "task.summary.skipped": {"zh": "索引跳过 {n} 个", "en": "{n} skipped by index"},
    "task.summary.failed": {"zh": "失败 {n} 个", "en": "{n} failed"},
    "task.summary.empty": {"zh": "卡内无可备份的媒体文件", "en": "No backupable media on card"},
    "task.summary.nonew": {"zh": "无新增文件", "en": "No new files"},
    "task.summary.sep": {"zh": "，", "en": ", "},

    # 任务失败 / 中断原因
    "err.card_removed": {"zh": "存储卡已拔出", "en": "Storage card removed"},
    "err.user_cancelled": {"zh": "用户取消", "en": "Cancelled by user"},
    "err.read_interrupted": {
        "zh": "存储卡读取中断（可能已被拔出）",
        "en": "Card read interrupted (the device may have been unplugged)",
    },
    "err.mount_lost": {
        "zh": "存储卡不可读（可能已被拔出）",
        "en": "Storage card is not readable (it may have been unplugged)",
    },
    "err.verify_failed": {
        "zh": "校验失败：目标文件哈希与源文件不一致",
        "en": "Verification failed: the copied file hash does not match the source",
    },
    "err.space_low": {
        "zh": "目标存储空间不足：需要 {need}，剩余 {free}",
        "en": "Not enough space at the destination: need {need}, free {free}",
    },
    "err.space_full": {
        "zh": "目标存储空间不足，任务已终止",
        "en": "Destination ran out of space, task aborted",
    },
    "err.mount_failed": {
        "zh": ("无法挂载存储卡。若 NAS 系统已自动挂载该卡，请先在「文件管理 → 外部设备」里"
               "安全弹出，或把宿主挂载点（如 /mnt/@usb）映射进容器后重试。原始错误：{detail}"),
        "en": ("Failed to mount the card. If the NAS has already auto-mounted it, eject it under "
               "\"File Manager → External devices\" first, or map the host mount point "
               "(e.g. /mnt/@usb) into the container and retry. Raw error: {detail}"),
    },
    "err.mount_point": {
        "zh": "无法创建挂载点 {path}：{msg}",
        "en": "Cannot create mount point {path}: {msg}",
    },
    "err.lsblk_missing": {
        "zh": "lsblk 命令不存在（设备检测仅在 Linux 宿主机可用）",
        "en": "lsblk not found (device detection is only available on a Linux host)",
    },

    # 接口返回消息
    "web.rescan_done": {"zh": "已触发重新扫描", "en": "Rescan triggered"},
    "web.card_id_required": {
        "zh": "缺少卡片标识（card_id 或 fs_uuid）",
        "en": "Missing card identifier (card_id or fs_uuid)",
    },
    "web.card_exists": {"zh": "该存储卡已注册过", "en": "This card is already registered"},
    "web.card_registered": {"zh": "已注册", "en": "Registered"},
    "web.card_registered_started": {
        "zh": "已注册，并开始首次备份",
        "en": "Registered, first backup started",
    },
    "web.card_not_found": {"zh": "卡片不存在", "en": "Card not found"},
    "web.alias_required": {"zh": "别名不能为空", "en": "Alias cannot be empty"},
    "web.organize_invalid": {
        "zh": "organize 仅支持 date / mirror",
        "en": "organize only accepts date / mirror",
    },
    "web.card_removed": {
        "zh": "已移除卡片记录（已备份的文件仍保留在磁盘上）",
        "en": "Card record removed (already backed-up files remain on disk)",
    },
    "web.index_reset": {
        "zh": "索引已清除，下次插入将重新全量比对（不会覆盖已有文件）",
        "en": "Index cleared; the next insertion triggers a full comparison (existing files are not overwritten)",
    },
    "web.task_not_found": {"zh": "任务不存在", "en": "Task not found"},
    "web.scan_interval_range": {
        "zh": "扫描间隔需在 1-120 秒之间",
        "en": "Scan interval must be between 1 and 120 seconds",
    },
    "web.mount_delay_range": {
        "zh": "挂载延迟需在 0-300 秒之间",
        "en": "Mount delay must be between 0 and 300 seconds",
    },
    "web.notify_url_invalid": {
        "zh": "通知地址需以 http(s):// 或 smtp+ssl:// 开头",
        "en": "Notification URL must start with http(s):// or smtp+ssl://",
    },
    "web.notify_lang_invalid": {
        "zh": "通知语言仅支持 zh / en",
        "en": "Notification language only supports zh / en",
    },

    # 任务操作（Runner 返回给界面）
    "run.card_disabled": {"zh": "该卡已停用，请先启用", "en": "This card is disabled, enable it first"},
    "run.card_offline": {"zh": "该卡当前未连接", "en": "This card is not connected"},
    "run.enqueued": {"zh": "已加入队列", "en": "Added to queue"},
    "run.already_running": {"zh": "该卡已有进行中的任务", "en": "A task for this card is already running"},
    "run.cancelling": {"zh": "正在取消", "en": "Cancelling"},
    "run.task_not_running": {"zh": "该任务未在执行", "en": "This task is not running"},
    "run.unmounted": {"zh": "已卸载，可安全拔出", "en": "Unmounted, safe to remove"},
    "run.no_held_mount": {
        "zh": "当前没有保持挂载的存储卡（任务进行中或卡未连接）",
        "en": "No card is currently kept mounted (a task is running or the card is not connected)",
    },

    # 完成通知
    "notify.title": {"zh": "[存储卡备份] {alias} {status}", "en": "[Card Backup] {alias} {status}"},
    "notify.line.card": {"zh": "卡片：{v}", "en": "Card: {v}"},
    "notify.line.status": {"zh": "状态：{v}", "en": "Status: {v}"},
    "notify.line.detail": {"zh": "详情：{v}", "en": "Detail: {v}"},
    "notify.line.time": {"zh": "时间：{v}", "en": "Time: {v}"},
    "notify.empty": {"zh": "—", "en": "—"},
    "notify.sent_smtp": {"zh": "SMTP 已发送", "en": "Sent via SMTP"},

    # 通知地址解析错误（写入日志 / 失败原因）
    "notify.err.pushplus_token": {
        "zh": "PushPlus 地址缺少 token 参数（应形如 https://www.pushplus.plus/send?token=你的Token）",
        "en": "PushPlus URL is missing the token parameter (expected https://www.pushplus.plus/send?token=YOUR_TOKEN)",
    },
    "notify.err.mails_key": {
        "zh": "该邮件服务地址缺少 key 参数",
        "en": "The mail service URL is missing the key parameter",
    },
    "notify.err.mails_to": {
        "zh": "该邮件服务地址缺少 to 参数（收件邮箱）",
        "en": "The mail service URL is missing the to parameter (recipient address)",
    },
    "notify.err.mail_scheme": {
        "zh": "该邮件服务地址缺少 key 参数（应形如 https://api.<服务域名>/v1/send?key=...&to=收件邮箱）",
        "en": "The mail service URL is missing the key parameter (expected https://api.<service>/v1/send?key=...&to=RECIPIENT)",
    },
    "notify.err.smtp_scheme": {"zh": "不是 SMTP 直发地址", "en": "Not a direct SMTP URL"},
    "notify.err.smtp_incomplete": {
        "zh": "SMTP 地址不完整（需要 发件邮箱:密码@SMTP服务器:端口，密码含 @ # : 请编码为 %40 %23 %3A）",
        "en": ("Incomplete SMTP URL (expected sender:password@SMTP-host:port; encode @ # : in the "
               "password as %40 %23 %3A)"),
    },

    # ── 登录与账号 ──
    "auth.err.login_required": {"zh": "请先登录", "en": "Please sign in first"},
    "auth.err.must_change": {
        "zh": "首次登录需先修改用户名和密码",
        "en": "Change your username and password before using the console",
    },
    "auth.err.admin_only": {"zh": "需要管理员权限", "en": "Administrator privileges required"},
    "auth.err.bad_credentials": {"zh": "用户名或密码错误", "en": "Incorrect username or password"},
    "auth.err.disabled": {
        "zh": "该账号已停用，请联系管理员",
        "en": "This account is disabled; contact an administrator",
    },
    "auth.err.too_many": {
        "zh": "登录失败次数过多，请约 {minutes} 分钟后重试",
        "en": "Too many failed attempts; try again in about {minutes} minute(s)",
    },
    "auth.err.user_not_found": {"zh": "用户不存在", "en": "User not found"},
    "auth.err.username_len": {
        "zh": "用户名长度需为 {min}-{max} 个字符",
        "en": "Username must be {min}-{max} characters long",
    },
    "auth.err.username_chars": {
        "zh": "用户名只能包含字母、数字、汉字、下划线、点、@ 和连字符",
        "en": "Username may only contain letters, digits, CJK characters, _ . @ and -",
    },
    "auth.err.username_taken": {"zh": "该用户名已被占用", "en": "That username is already taken"},
    "auth.err.password_len": {
        "zh": "密码长度至少 {min} 位",
        "en": "Password must be at least {min} characters",
    },
    "auth.err.password_same": {
        "zh": "密码不能与用户名相同",
        "en": "The password cannot be the same as the username",
    },
    "auth.err.password_unchanged": {
        "zh": "新密码不能与当前密码相同",
        "en": "The new password must differ from the current one",
    },
    "auth.err.passwords_mismatch": {
        "zh": "两次输入的密码不一致",
        "en": "The two passwords do not match",
    },
    "auth.err.current_wrong": {"zh": "当前密码不正确", "en": "Current password is incorrect"},
    "auth.err.need_new_username": {
        "zh": "请设置一个新的用户名（不能与当前的相同）",
        "en": "Please set a new username (it must differ from the current one)",
    },
    "auth.err.nothing_changed": {"zh": "没有需要修改的内容", "en": "Nothing to update"},
    "auth.err.self_delete": {
        "zh": "不能删除当前登录的账号",
        "en": "You cannot delete the account you are signed in with",
    },
    "auth.err.self_disable": {
        "zh": "不能停用当前登录的账号",
        "en": "You cannot disable the account you are signed in with",
    },
    "auth.err.last_admin": {
        "zh": "至少需要保留一个启用状态的管理员账号",
        "en": "At least one enabled administrator must remain",
    },

    "web.login_ok": {"zh": "登录成功", "en": "Signed in"},
    "web.logout_ok": {"zh": "已退出登录", "en": "Signed out"},
    "web.credentials_changed": {"zh": "账号信息已更新", "en": "Account updated"},
    "web.user_created": {"zh": "用户已创建", "en": "User created"},
    "web.user_updated": {"zh": "用户已更新", "en": "User updated"},
    "web.user_deleted": {"zh": "用户已删除", "en": "User deleted"},
}

# 兼容旧调用：无别名键
MESSAGES.setdefault("web.settings_saved", {"zh": "已保存", "en": "Saved"})


def normalize_lang(value: Optional[str]) -> str:
    """把任意语言标签归一化为 zh / en（未知一律回落到默认中文）。"""
    s = (value or "").strip().lower().replace("_", "-")
    if not s:
        return DEFAULT_LANG
    if s == "cn" or s == "chs" or s.startswith("zh"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return DEFAULT_LANG


def from_request(query_lang: Optional[str] = None,
                 accept_language: Optional[str] = None) -> str:
    """解析请求语言：?lang= 优先，其次 Accept-Language 的第一个语言标签。"""
    if query_lang:
        return normalize_lang(query_lang)
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip()
        if tag and tag != "*":
            return normalize_lang(tag)
    return DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **params: Any) -> str:
    """翻译：键不存在时原样返回键，参数缺失时返回未格式化的原文。"""
    item = MESSAGES.get(key)
    if not item:
        return key
    text = item.get(lang) or item.get(DEFAULT_LANG) or key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def pack(key: str, **params: Any) -> str:
    """把消息打包为可落库的 JSON 文本（界面按当前语言渲染）。"""
    return json.dumps({"k": key, "p": params}, ensure_ascii=False,
                      separators=(",", ":"))


def summary_text(p: dict, lang: str = DEFAULT_LANG) -> str:
    """渲染备份结果摘要（与前端 static/i18n.js 的拼接逻辑保持一致）。"""
    p = p or {}
    parts = [t("task.summary.base", lang,
               copied=p.get("copied", 0), bytes=p.get("bytes", "0 B"))]
    if p.get("reused"):
        parts.append(t("task.summary.reused", lang, n=p["reused"]))
    parts.append(t("task.summary.skipped", lang, n=p.get("skipped", 0)))
    if p.get("failed"):
        parts.append(t("task.summary.failed", lang, n=p["failed"]))
    if p.get("empty"):
        parts.append(t("task.summary.empty", lang))
    elif p.get("nonew"):
        parts.append(t("task.summary.nonew", lang))
    return t("task.summary.sep", lang).join(parts)


def _render(key: str, p: dict, lang: str) -> str:
    if key == "task.summary":
        return summary_text(p, lang)
    return t(key, lang, **(p or {}))


def render_stored(value: Optional[str], lang: str = DEFAULT_LANG) -> str:
    """把落库消息渲染为指定语言的文本（兼容历史纯文本）。"""
    if not value:
        return ""
    s = str(value)
    if s.lstrip().startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return s
        if isinstance(obj, dict) and obj.get("k"):
            return _render(obj["k"], obj.get("p") or {}, lang)
    return s
