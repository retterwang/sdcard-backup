"""媒体识别、拍摄日期推断、路径安全清洗、大小格式化。"""
from __future__ import annotations

import datetime
import os
import re

# 照片扩展名（含主流 RAW）
IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf",
    ".rw2", ".orf", ".pef", ".srw", ".raw",
}
# 视频扩展名（含 Insta360 的 .insv）
VIDEO_EXT = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".m2t",
    ".3gp", ".3g2", ".mpg", ".mpeg", ".wmv", ".flv", ".insv", ".ts",
}
# 相机卡上的伴随文件，默认跳过（缩略图/低码率代理文件等）
JUNK_EXT = {".thm", ".lrv", ".scr", ".tmp", ".exe", ".lnk"}

# 文件名中的日期：20240115 / 2024-01-15 / 2024_01_15 等
_DATE_RE = re.compile(r"(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


def ext_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def is_media(name: str) -> bool:
    e = ext_of(name)
    return e in IMAGE_EXT or e in VIDEO_EXT


def is_junk(name: str) -> bool:
    return ext_of(name) in JUNK_EXT


def guess_date(path: str, mtime: float):
    """推断文件拍摄日期，返回 (年, 月, 日)。

    优先级：文件名中的日期（最快，且相机命名普遍带日期）
            → EXIF（仅图片，需要 exifread）→ 文件修改时间（兜底）。
    """
    base = os.path.basename(path)

    # 1) 文件名日期
    m = _DATE_RE.search(base)
    if m:
        digits = re.sub(r"\D", "", m.group(0))
        try:
            y, mo, d = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
            datetime.date(y, mo, d)  # 合法性校验
            if 1990 <= y <= 2099:
                return y, mo, d
        except Exception:
            pass

    # 2) EXIF（仅图片；exifread 读取文件头，不解码整张图，速度可接受）
    if ext_of(base) in IMAGE_EXT:
        try:
            import exifread  # type: ignore

            with open(path, "rb") as f:
                tags = exifread.process_file(
                    f, details=False, stop_tag="EXIF DateTimeOriginal"
                )
            for key in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
                t = tags.get(key)
                if not t:
                    continue
                s = str(t)  # 形如 "2024:01:15 12:00:00"
                if len(s) >= 10 and s[4] == ":":
                    y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
                    datetime.date(y, mo, d)
                    if 1990 <= y <= 2099:
                        return y, mo, d
        except Exception:
            pass

    # 3) 文件修改时间兜底
    try:
        dt = datetime.datetime.fromtimestamp(mtime)
        return dt.year, dt.month, dt.day
    except Exception:
        return 1970, 1, 1


# ── 路径安全 ──────────────────────────────────────────────
# 存储卡内容不可信（可能包含恶意文件名），所有用于拼接目标路径的
# 字符串都必须先清洗，防止目录穿越（如 ../../x）。
_ILLEGAL = '/\\:*?"<>|'


def safe_component(name: str) -> str:
    """清洗单个路径片段。"""
    s = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    for ch in _ILLEGAL:
        s = s.replace(ch, "_")
    s = s.strip().strip(".").strip()
    return s[:150] or "_"


def safe_relpath(rel: str) -> str:
    """清洗相对路径：丢弃空段、. 与 ..，逐段清洗后重新拼接。"""
    parts = []
    for p in str(rel).replace("\\", "/").split("/"):
        p = p.strip()
        if p in ("", ".", ".."):
            continue
        parts.append(safe_component(p))
    return "/".join(parts) if parts else "_"


def slug_subdir(text: str) -> str:
    """把卡片别名转成安全的目录名（空值回退为 card）。"""
    s = safe_component((text or "").strip())
    return "card" if s in ("", "_") else s[:100]


def fmt_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
