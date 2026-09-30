"""任务通知：根据填入的地址自动适配推送渠道（全部无需自建服务器）。

支持（按地址/协议自动识别）：
- PushPlus            https://www.pushplus.plus/send?token=你的Token
- mails.dev（邮箱）    https://api.mails.dev/v1/send?key=mk_...&to=收件邮箱[&from=发件邮箱]
- SMTP 直发（邮箱）     smtp+ssl://发件邮箱:密码@SMTP服务器:465[?to=收件邮箱]
                       密码含特殊字符需 URL 编码（@→%40  #→%23  :→%3A）
- 企业微信机器人       https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
- 钉钉机器人           https://oapi.dingtalk.com/robot/send?access_token=...
                       （安全设置选「自定义关键词」，建议填 备份，消息标题含该词）
- Server 酱           https://sctapi.ftqq.com/<SendKey>.send
- 其他任意 http(s)     通用 POST JSON {"title": "...", "text": "..."}
"""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate

log = logging.getLogger("notify")

# Cloudflare 会拦截默认的 Python-urllib User-Agent（error 1010），
# mails.dev 等走 Cloudflare 的服务必须带浏览器 UA。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def build_request(url: str, title: str, content: str):
    """生成 HTTP 请求参数：(最终URL, 请求体 bytes, Content-Type, 额外请求头)。纯函数，便于测试。"""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    text = f"{title}\n{content}".strip()

    if "pushplus" in host:
        token = (urllib.parse.parse_qs(u.query).get("token") or [""])[0]
        if not token:
            raise ValueError(
                "PushPlus 地址缺少 token 参数"
                "（应形如 https://www.pushplus.plus/send?token=你的Token）"
            )
        body = {"token": token, "title": title, "content": content, "template": "txt"}
        return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), \
            "application/json; charset=utf-8", {}

    if "mails.dev" in host:
        q = urllib.parse.parse_qs(u.query)
        key = (q.get("key") or [""])[0]
        to = (q.get("to") or [""])[0]
        if not key:
            raise ValueError(
                "mails.dev 地址缺少 key 参数"
                "（应形如 https://api.mails.dev/v1/send?key=mk_...&to=收件邮箱）"
            )
        if not to:
            raise ValueError("mails.dev 地址缺少 to 参数（收件邮箱）")
        body = {"to": [to], "subject": title, "text": content}
        frm = (q.get("from") or [""])[0]
        if frm:
            body["from"] = frm
        headers = {"Authorization": "Bearer " + key, "User-Agent": _UA}
        return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), \
            "application/json; charset=utf-8", headers

    if "qyapi.weixin.qq.com" in host or "oapi.dingtalk.com" in host:
        body = {"msgtype": "text", "text": {"content": text}}
        return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), \
            "application/json; charset=utf-8", {}

    if host.endswith("ftqq.com"):  # Server 酱（Turbo / ³）
        body = {"title": title, "desp": content}
        return url, urllib.parse.urlencode(body).encode("utf-8"), \
            "application/x-www-form-urlencoded", {}

    body = {"title": title, "text": content}
    return url, json.dumps(body, ensure_ascii=False).encode("utf-8"), \
        "application/json; charset=utf-8", {}


def parse_smtp_url(url: str) -> dict:
    """解析 smtp+ssl:// 直发地址。

    格式：smtp+ssl://发件邮箱:密码@SMTP服务器:465[?to=收件邮箱&from=发件邮箱]
    默认：端口 465、收件人=发件人（自寄）；密码含特殊字符须 URL 编码。
    """
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("smtp+ssl", "smtp"):
        raise ValueError("不是 SMTP 直发地址")
    host = u.hostname or ""
    user = urllib.parse.unquote(u.username or "")
    password = urllib.parse.unquote(u.password or "")
    q = urllib.parse.parse_qs(u.query)
    to = (q.get("to") or [""])[0] or user
    frm = (q.get("from") or [""])[0] or user
    port = u.port or 465
    if not (host and user and password and to):
        raise ValueError(
            "SMTP 地址不完整（需要 发件邮箱:密码@SMTP服务器:端口，"
            "密码含 @ # : 请编码为 %40 %23 %3A）"
        )
    return {"host": host, "port": port, "user": user,
            "password": password, "to": to, "from": frm}


def send_smtp(url: str, title: str, content: str):
    p = parse_smtp_url(url)
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = p["from"]
    msg["To"] = p["to"]
    msg["Date"] = formatdate(localtime=True)
    with smtplib.SMTP_SSL(p["host"], p["port"], timeout=25) as s:
        s.login(p["user"], p["password"])
        s.sendmail(p["from"], [p["to"]], msg.as_string())


def send(url: str, title: str, content: str, timeout: int = 10):
    """发送通知，返回 (是否成功, 结果或错误信息)。"""
    if url.startswith(("smtp+ssl://", "smtp://")):
        try:
            send_smtp(url, title, content)
            return True, "SMTP 已发送"
        except Exception as e:
            return False, str(e)
    try:
        final_url, data, ctype, headers = build_request(url, title, content)
    except ValueError as e:
        return False, str(e)
    try:
        req = urllib.request.Request(
            final_url, data=data, headers={"Content-Type": ctype, **headers})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read(600).decode("utf-8", "ignore").strip()
        # 部分服务 HTTP 200 但业务码表示失败（PushPlus 200 为成功 / Server 酱 0 为成功）
        if body.startswith("{"):
            try:
                d = json.loads(body)
                c = d.get("code")
                if c is not None and c not in (0, 200):
                    return False, f"HTTP {code}，服务返回失败: {body[:200]}"
            except Exception:
                pass
        return True, f"HTTP {code} {body[:160]}"
    except Exception as e:
        return False, str(e)
