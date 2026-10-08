"""核心逻辑本地测试：设备解析、卡片识别、日期推断、路径安全、增量计划、复制与校验。

无第三方依赖，直接运行：  python tests/test_core.py
"""
import datetime
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import backup, detector, media, notify  # noqa: E402

PASSED = 0


def ok(cond, msg):
    global PASSED
    if not cond:
        raise AssertionError(msg)
    PASSED += 1


# ── 模拟 lsblk 输出（USB 读卡器 + 内置 MMC 卡槽 + 内部 NVMe 盘） ──
LSBLK_SAMPLE = {
    "blockdevices": [
        {
            "name": "sda", "type": "disk", "tran": "usb", "rm": True,
            "size": 64000000000, "model": "MassStorageClass", "vendor": "Generic",
            "serial": "0123456789",
            "children": [
                {"name": "sda1", "type": "part", "fstype": "exfat", "label": "EOS_DIGITAL",
                 "uuid": "AAAA-BBBB", "partuuid": "aaa-01", "size": 63990000000,
                 "mountpoints": [None]},
            ],
        },
        {
            "name": "sdb", "type": "disk", "tran": "usb", "rm": True,
            "size": 32000000000, "children": [
                {"name": "sdb1", "type": "part", "fstype": "ext4", "uuid": "E1",
                 "size": 1000000000, "mountpoints": [None]},
                {"name": "sdb2", "type": "part", "fstype": "exfat", "uuid": "E2",
                 "size": 31000000000, "mountpoints": [None]},
            ],
        },
        {
            "name": "nvme0n1", "type": "disk", "tran": "nvme", "rm": False,
            "size": 500000000000, "children": [
                {"name": "nvme0n1p1", "type": "part", "fstype": "ext4",
                 "uuid": "ROOT", "mountpoints": ["/"]},
            ],
        },
        {
            "name": "mmcblk0", "type": "disk", "tran": "mmc", "rm": True,
            "size": 64000000000, "children": [
                {"name": "mmcblk0p1", "type": "part", "fstype": "vfat", "label": "SDCARD",
                 "uuid": "1234-ABCD", "size": 63900000000,
                 "mountpoints": ["/mnt/@usb/mmcblk0p1"]},
            ],
        },
    ]
}


def test_detector():
    # 1) 无 sysfs（USB 场景）：应排除 nvme；USB 设备用 fs:uuid；mmc 无序列号时回退
    devs = detector.build_devices(LSBLK_SAMPLE, sysfs=lambda p: "",
                                  probe=lambda n, s=0: {}, realpath=lambda p: "")
    ids = sorted(d.card_id for d in devs)
    ok(ids == ["fs:1234-ABCD", "fs:AAAA-BBBB", "fs:E2"], f"设备识别异常: {ids}")
    ok(all(d.tran != "nvme" for d in devs), "内部 NVMe 盘不应出现在候选设备中")
    mmc = [d for d in devs if d.is_mmc][0]
    ok(mmc.card_id == "fs:1234-ABCD", f"mmc 无序列号时应回退 fs:uuid，实际 {mmc.card_id}")

    # 2) 注入 sysfs（内置卡槽场景）：使用 SD 卡 CID 序列号
    def fake_sysfs(path):
        if path.endswith("/device/serial"):
            return "0xabc123"
        if path.endswith("/device/name"):
            return "SD64G"
        return ""

    devs2 = detector.build_devices(LSBLK_SAMPLE, sysfs=fake_sysfs,
                                   probe=lambda n, s=0: {}, realpath=lambda p: "")
    mmc2 = [d for d in devs2 if d.is_mmc][0]
    ok(mmc2.card_id == "mmc:0xabc123:SD64G", f"mmc 身份识别异常: {mmc2.card_id}")
    ok("fs:1234-ABCD" in mmc2.id_candidates, "候选 ID 应包含 fs:uuid 以便白名单匹配")
    usb = [d for d in devs2 if not d.is_mmc and d.fs_uuid == "E2"][0]
    ok(usb.other_parts == ["sdb1"], f"次要分区应被记录: {usb.other_parts}")

    # 3) 容器环境模拟（无 udev 数据）：lsblk 丢失文件系统信息 → blkid 探测回退补齐
    sample3 = {"blockdevices": [{
        "name": "sdc", "type": "disk", "tran": "", "rm": True, "size": 32000000000,
        "children": [
            {"name": "sdc1", "type": "part", "size": 31000000000, "mountpoints": [None]},
            {"name": "sdc2", "type": "part", "fstype": "swap", "size": 1000000000,
             "mountpoints": [None]},
        ]}]}
    probed = []

    def fake_probe(node, size=0):
        probed.append(node)
        if node == "/dev/sdc1":
            return {"fstype": "exfat", "fs_uuid": "DEAD-BEEF", "label": "SD32"}
        return {}

    devs3 = detector.build_devices(sample3, sysfs=lambda p: "",
                                   probe=fake_probe, realpath=lambda p: "")
    ok(len(devs3) == 1 and devs3[0].card_id == "fs:DEAD-BEEF",
       f"udev 缺失时应由 blkid 探测补齐: {[d.to_dict() for d in devs3]}")
    ok(probed == ["/dev/sdc1"], f"应只探测信息缺失的非排除类型分区: {probed}")

    # 4) 探测也拿不到信息时仍应可见（宽容策略，避免卡"静默消失"）
    devs4 = detector.build_devices(sample3, sysfs=lambda p: "",
                                   probe=lambda n, s=0: {}, realpath=lambda p: "")
    ok(len(devs4) == 1 and devs4[0].fstype == "",
       "未知文件系统的分区也应可见（便于诊断）")

    # 5) analyze 应记录每个磁盘的判定原因（供日志与界面自检）
    _, notes = detector.analyze({"blockdevices": [
        {"name": "nvme9n1", "type": "disk", "tran": "nvme", "rm": False, "size": 1,
         "children": [{"name": "nvme9n1p1", "type": "part", "fstype": "ext4", "size": 1}]}]},
        sysfs=lambda p: "", probe=lambda n, s=0: {}, realpath=lambda p: "")
    ok(notes and notes[0]["decision"].startswith("跳过"), f"analyze 应记录跳过原因: {notes}")

    # 6) 内置 eMMC 系统盘（mmcblk 但不可移动）不应被当作存储卡（绿联 NAS 实测场景：
    #    mmcblk0 = 系统 eMMC，含 /boot 等系统分区）
    sample_emmc = {"blockdevices": [{
        "name": "mmcblk0", "type": "disk", "tran": "mmc", "rm": False,
        "size": 31000000000,
        "children": [
            {"name": "mmcblk0p1", "type": "part", "fstype": "vfat", "size": 256000000},
            {"name": "mmcblk0p3", "type": "part", "fstype": "ext4", "size": 10000000},
        ]}]}
    devs5 = detector.build_devices(sample_emmc, sysfs=lambda p: "",
                                   probe=lambda n, s=0: {}, realpath=lambda p: "")
    ok(len(devs5) == 0, f"内置 eMMC 不应被识别为存储卡: {[d.to_dict() for d in devs5]}")

    # 7) 真正的 SD 卡槽（mmcblk + device/type=SD）即使 rm=0 也应识别
    def _sd_sysfs(path):
        return "SD" if path.endswith("/device/type") else ""

    devs6 = detector.build_devices(sample_emmc, sysfs=_sd_sysfs,
                                   probe=lambda n, s=0: {}, realpath=lambda p: "")
    ok(len(devs6) == 1, f"device/type=SD 的 mmcblk 应可识别: {devs6}")
    print("  [1/7] 设备解析与卡片识别  ✓")


def test_media():
    cases = {
        "IMG_20240115_123456.jpg": (2024, 1, 15),
        "PXL_20240302_101010.jpg": (2024, 3, 2),
        "VID_2024-03-02.mp4": (2024, 3, 2),
        "20241231_235959.MOV": (2024, 12, 31),
        "DSC_2024_07_01_0001.NEF": (2024, 7, 1),
    }
    for name, expect in cases.items():
        got = media.guess_date(name, 0)
        ok(got == expect, f"{name} 日期推断应为 {expect}，实际 {got}")

    # 非法日期与无日期文件名 → 回退 mtime
    mtime = 946684800  # 2000-01-01 UTC
    import datetime
    fallback = datetime.datetime.fromtimestamp(mtime)
    want = (fallback.year, fallback.month, fallback.day)
    ok(media.guess_date("IMG_20241340_000000.jpg", mtime) == want, "非法日期应回退 mtime")
    ok(media.guess_date("GOPR0001.JPG", mtime) == want, "无日期文件应回退 mtime")

    ok(media.is_media("a.JPG") and media.is_media("b.insv") and not media.is_media("readme.txt"),
       "媒体类型识别异常")
    ok(media.is_junk("MVI_0001.THM") and not media.is_junk("MVI_0001.MP4"), "垃圾伴随文件识别异常")
    print("  [2/7] 日期推断与媒体识别  ✓")


def test_path_safety():
    ok(media.safe_relpath("../../etc/passwd") == "etc/passwd", "目录穿越未被拦截")
    r = media.safe_relpath("DCIM/100CANON/IMG_0001.JPG")
    ok(r == "DCIM/100CANON/IMG_0001.JPG", f"正常路径不应被改变: {r}")
    bad = media.safe_relpath('a<b>:c*d?.jpg')
    ok(not any(ch in bad for ch in '<>:*?'), f"非法字符未清洗: {bad}")
    ok("/" not in media.safe_component("x/y") and "\\" not in media.safe_component("x\\y"),
       "分隔符未清洗")
    ok(media.slug_subdir("") == "card", "空别名应回退 card")
    ok(media.fmt_bytes(1536) == "1.5 KB", f"fmt_bytes 异常: {media.fmt_bytes(1536)}")
    print("  [3/7] 路径安全与格式化  ✓")


def test_plan():
    files = [
        ("src/a/IMG_20240115_120000.jpg", "a/IMG_20240115_120000.jpg", 100, 1000.0),
        ("src/DCIM/b.MP4", "DCIM/b.MP4", 200, 1704067200.0),   # 2024-01-01 UTC
        ("src/readme.txt", "readme.txt", 10, 3000.0),
        ("src/c/IMG_20240115_120000.jpg", "c/IMG_20240115_120000.jpg", 100, 4000.0),
    ]
    card = {"organize": "date", "all_files": 0, "include_globs": "", "exclude_globs": ""}
    index = {"a/IMG_20240115_120000.jpg": {"status": "done", "size": 100, "mtime": 1000.0}}

    items, skipped, sb, pb = backup.build_plan(files, index, card)
    ok(len(items) == 2, f"计划应为 2 个文件（跳过已备份 1、过滤 txt 1），实际 {len(items)}")
    ok(skipped == 1 and sb == 100, f"跳过统计异常: {skipped}/{sb}")
    dests = {it.rel: it.dest_rel for it in items}
    ok(dests["c/IMG_20240115_120000.jpg"] == "2024/01/15/IMG_20240115_120000.jpg",
       f"日期归类路径异常: {dests}")
    fb = datetime.datetime.fromtimestamp(1704067200.0)
    want_mp4 = f"{fb.year:04d}/{fb.month:02d}/{fb.day:02d}/b.MP4"
    ok(dests["DCIM/b.MP4"] == want_mp4, f"无日期文件名应按 mtime 归类（期望 {want_mp4}）: {dests}")

    # mirror 模式
    card2 = dict(card, organize="mirror")
    items2, _, _, _ = backup.build_plan(files, index, card2)
    ok(any(it.dest_rel == "DCIM/b.MP4" for it in items2), "mirror 模式应保留原结构")

    # 包含/排除规则
    card3 = dict(card, include_globs="*.MP4")
    items3, _, _, _ = backup.build_plan(files, index, card3)
    ok(len(items3) == 1 and items3[0].rel == "DCIM/b.MP4", "include 规则过滤异常")
    card4 = dict(card, exclude_globs="*.MP4")
    items4, _, _, _ = backup.build_plan(files, index, card4)
    ok(all(not x.rel.endswith(".MP4") for x in items4), "exclude 规则过滤异常")
    print("  [4/7] 增量计划与过滤规则  ✓")


def test_copy():
    class Ctx:
        task_id = 7

        def check_cancel(self):
            pass

        def add_bytes(self, n):
            pass

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src")
        dst = os.path.join(td, "dst")
        os.makedirs(src)
        os.makedirs(dst)
        p = os.path.join(src, "IMG_20240115_120000.jpg")
        data = os.urandom(3000)
        with open(p, "wb") as f:
            f.write(data)
        mtime = os.path.getmtime(p)
        item = backup.Item(p, "IMG_20240115_120000.jpg", len(data), mtime,
                           "2024/01/15/IMG_20240115_120000.jpg")

        r1 = backup.copy_one(item, dst, 7, True, Ctx())
        ok(r1["kind"] == "copied", f"首次复制应成功: {r1}")
        target = os.path.join(dst, item.dest_rel)
        ok(os.path.exists(target), "目标文件不存在")
        with open(target, "rb") as f:
            got = f.read()
        ok(got == data, "内容不一致")
        import hashlib
        expect_hash = hashlib.blake2b(data, digest_size=16).hexdigest()
        ok(r1["hash"] == expect_hash, "哈希不一致")

        # 再次复制同一文件（目标已存在且大小一致）→ 复用跳过
        r2 = backup.copy_one(item, dst, 7, True, Ctx())
        ok(r2["kind"] == "reused", f"应识别为已备份: {r2}")

        # 同名不同内容（大小不同）→ 追加 __2 后缀，不覆盖
        item2 = backup.Item(p, item.rel, len(data) + 5, mtime + 10, item.dest_rel)
        item2.size = len(data) + 5
        r3 = backup.copy_one(item2, dst, 7, True, Ctx())
        ok(r3["kind"] == "copied" and r3["dest_rel"].endswith("__2.jpg"),
           f"冲突文件应加后缀: {r3}")

        # 校验失败检测
        try:
            backup._verify(target, "0" * 32)
            ok(False, "错误哈希未被检出")
        except OSError:
            ok(True, "")
    print("  [5/7] 复制、冲突与校验  ✓")


def test_notify():
    # PushPlus：token 从 URL 提取，载荷转为 token/content
    u = "https://www.pushplus.plus/send?token=abc123"
    final, data, ctype, headers = notify.build_request(u, "标题T", "内容C")
    body = json.loads(data.decode("utf-8"))
    ok(final == u and body["token"] == "abc123" and body["title"] == "标题T"
       and body["content"] == "内容C", f"PushPlus 载荷异常: {body}")
    ok("application/json" in ctype, "PushPlus 应为 JSON 请求")

    # PushPlus 缺少 token → 明确报错而不是静默发空
    try:
        notify.build_request("https://www.pushplus.plus/send", "t", "c")
        ok(False, "PushPlus 缺少 token 未报错")
    except ValueError:
        ok(True, "")

    # mails.dev：key/to 从 URL 提取，带 Authorization 头 + 浏览器 UA
    u = "https://api.mails.dev/v1/send?key=mk_test123&to=me@example.com"
    _, data, ctype, headers = notify.build_request(u, "标题T", "内容C")
    body = json.loads(data.decode("utf-8"))
    ok(body["to"] == ["me@example.com"] and body["subject"] == "标题T"
       and body["text"] == "内容C", f"mails.dev 载荷异常: {body}")
    ok(headers.get("Authorization") == "Bearer mk_test123" and "User-Agent" in headers,
       "mails.dev 请求头异常（需 Bearer + UA）")

    # mails.dev 缺 key / 缺 to → 明确报错
    for bad in ("https://api.mails.dev/v1/send?to=x@y.z",
                "https://api.mails.dev/v1/send?key=mk_1"):
        try:
            notify.build_request(bad, "t", "c")
            ok(False, f"mails.dev 参数缺失未报错: {bad}")
        except ValueError:
            ok(True, "")

    # SMTP 直发：URL 解析（特殊字符百分比解码；默认收件人=发件人）
    p = notify.parse_smtp_url("smtp+ssl://me%40example.com:p%23w%40rd@smtp.exmail.qq.com:465")
    ok(p["user"] == "me@example.com" and p["password"] == "p#w@rd"
       and p["host"] == "smtp.exmail.qq.com" and p["to"] == "me@example.com",
       f"SMTP 解析异常: {p}")

    # SMTP 发送（mock smtplib：验证登录参数与收发件人）
    from unittest import mock
    with mock.patch("app.notify.smtplib.SMTP_SSL") as m:
        srv = m.return_value.__enter__.return_value
        okk, msg = notify.send(
            "smtp+ssl://me%40example.com:pw@smtp.exmail.qq.com:465?to=box%40x.com",
            "标题", "内容")
        ok(okk, f"SMTP 发送（mock）应成功: {msg}")
        srv.login.assert_called_once_with("me@example.com", "pw")
        srv.sendmail.assert_called_once()
        args = srv.sendmail.call_args[0]
        ok(args[0] == "me@example.com" and args[1] == ["box@x.com"],
           f"SMTP 收发件人异常: {args}")

    # 企业微信机器人
    _, data, _, _ = notify.build_request(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k1", "标题", "内容")
    body = json.loads(data.decode("utf-8"))
    ok(body["msgtype"] == "text" and "标题" in body["text"]["content"], f"企微载荷异常: {body}")

    # 钉钉机器人
    _, data, _, _ = notify.build_request(
        "https://oapi.dingtalk.com/robot/send?access_token=t1", "标题", "内容")
    ok(json.loads(data.decode("utf-8"))["msgtype"] == "text", "钉钉载荷异常")

    # Server 酱：表单编码 title/desp
    _, data, ctype, _ = notify.build_request("https://sctapi.ftqq.com/SCT123.send", "标题", "内容")
    s = data.decode("utf-8")
    ok("title=" in s and "desp=" in s and "x-www-form-urlencoded" in ctype,
       f"Server酱载荷异常: {s}")

    # 通用回退：JSON {title, text}
    _, data, _, _ = notify.build_request("https://example.com/hook", "标题", "内容")
    ok(json.loads(data.decode("utf-8")) == {"title": "标题", "text": "内容"}, "通用载荷异常")
    print("  [6/7] 通知载荷适配  ✓")


def test_i18n():
    """界面语言：语言解析、翻译、落库消息渲染（兼容历史纯文本）。"""
    from app import i18n

    # 语言归一化与优先级：?lang= > Accept-Language > 默认中文
    ok(i18n.normalize_lang("en-US") == "en" and i18n.normalize_lang("zh_CN") == "zh",
       "语言标签归一化异常")
    ok(i18n.normalize_lang("fr") == "zh", "未知语言应回落到中文")
    ok(i18n.from_request("en", "zh-CN,zh;q=0.9") == "en", "?lang 应优先于请求头")
    ok(i18n.from_request(None, "en-GB,en;q=0.8") == "en", "Accept-Language 解析异常")
    ok(i18n.from_request(None, None) == "zh", "缺少语言信息时应默认中文")

    # 翻译与参数插值
    ok(i18n.t("status.done", "zh") == "完成" and i18n.t("status.done", "en") == "Done",
       "状态翻译异常")
    ok(i18n.t("err.space_low", "en", need="2 GB", free="1 GB")
       == "Not enough space at the destination: need 2 GB, free 1 GB", "带参数翻译异常")
    ok(i18n.t("no.such.key", "zh") == "no.such.key", "未知键应原样返回")

    # 落库消息（键 + 参数）按语言渲染
    stored = i18n.pack("err.card_removed")
    ok(i18n.render_stored(stored, "zh") == "存储卡已拔出", "落库消息中文渲染异常")
    ok(i18n.render_stored(stored, "en") == "Storage card removed", "落库消息英文渲染异常")

    # 结果摘要两种语言的拼接
    s = i18n.pack("task.summary", copied=3, bytes="12.0 MB", reused=1,
                  skipped=5, failed=0, empty=False, nonew=False)
    zh, en = i18n.render_stored(s, "zh"), i18n.render_stored(s, "en")
    ok("新增 3 个（12.0 MB）" in zh and "索引跳过 5 个" in zh, f"中文摘要异常: {zh}")
    ok("3 new (12.0 MB)" in en and "5 skipped by index" in en, f"英文摘要异常: {en}")

    # 历史纯文本（老数据）原样返回，不受语言影响
    ok(i18n.render_stored("新增 2 个（1.0 MB）", "en") == "新增 2 个（1.0 MB）",
       "历史纯文本应原样返回")
    ok(i18n.render_stored("", "en") == "", "空消息渲染异常")
    print("  [7/7] 界面语言与消息渲染  ✓")


def main():
    print("运行核心逻辑测试：")
    test_detector()
    test_media()
    test_path_safety()
    test_plan()
    test_copy()
    test_notify()
    test_i18n()
    print(f"\n全部通过：{PASSED} 项断言 ✓")


if __name__ == "__main__":
    main()
