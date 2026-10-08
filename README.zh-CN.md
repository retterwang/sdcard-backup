# 存储卡自动备份（sdcard-backup）

[English](README.md) | 简体中文

可以部署在任何一台 NAS 上的 Docker 容器：**存储卡插入即自动增量备份照片和视频**，
带图形化管理界面查看实时进度与历史任务。只有注册过的卡（白名单）才会触发备份。

## 功能一览

| 能力 | 说明 |
|---|---|
| 插卡即自动备份 | 后台轮询检测设备，识别到已注册的卡后自动开始，无需任何操作 |
| 按卡识别 | 内置卡槽按 SD 卡 CID 序列号识别；USB 读卡器按文件系统 UUID 识别，不会张冠李戴 |
| 增量备份 | SQLite 索引（路径+大小+mtime+哈希），只复制新增/变化的文件；拔卡中断后重插自动续传 |
| 完整性校验 | 复制时单次读取计算哈希，可选复制后重读校验，防拷贝静默损坏 |
| 安全只读 | 存储卡一律**只读挂载**，程序不向卡内写入或删除任何内容 |
| 图形界面 | 实时进度条 / 设备列表 / 白名单管理 / 任务历史 / 错误明细 / 运行设置 |
| 目录整理 | 按拍摄日期（2024/01/15/…）或保留卡内原结构，逐卡可配 |
| 完成通知 | Webhook 自动适配 PushPlus / 企业微信机器人 / Server 酱 / 钉钉 |
| 断点与重试 | 失败任务一键重试；目标空间不足自动预检中止 |

## 工作原理

```
插卡 → 检测线程（每 3 秒 lsblk 扫描） → 匹配白名单 → 任务队列
     → 只读挂载(ro) → 扫描文件 → 比对索引 → 复制+哈希 → 写入存储卡专属文件夹
     → 卸载 → 记录任务/发通知 → Web 界面实时刷新（SSE）
```

详细设计取舍见 [docs/DESIGN.md](docs/DESIGN.md)。

## 部署到 NAS

### 0. 前置条件

- NAS 上已安装 **Docker** 应用（各品牌入口略有差异，通常在「应用中心」）
- 准备照片目标共享文件夹，例如 `/volume1/photo/SD卡备份`
- 建议开启 SSH（方式 A 需要）；不开 SSH 可用方式 B

### 1. 部署（三选一）

**方式 A：在 NAS 上直接构建（推荐）**

1. 用「文件管理」把本项目整个文件夹上传到 `/volume1/docker/sdcard-backup`
2. 编辑 `docker-compose.yml`，把最后一行的备份目录改成你的实际路径
3. SSH 登录 NAS 执行：

```bash
cd /volume1/docker/sdcard-backup
sudo docker compose up -d --build
```

> **国内网络说明**：构建默认全部走国内源（基础镜像 DaoCloud 代理 + apt/pip 清华镜像），
> 无需任何额外配置即可构建；若你的网络能直连 Docker Hub，可把 compose 里的
> `BASE_IMAGE` 改回官方 `python:3.12-slim-bookworm`。

**方式 B：电脑构建镜像 → 导入 NAS**（NAS 无需构建环境）

1. 在装有 Docker 的电脑上运行 `scripts/build-export.sh`（Windows 用 `build-export.bat`），
   生成 `sdcard-backup-image.tar`
2. 在 NAS 的 Docker 应用 → 镜像 → 导入，上传该 tar
3. 编辑 `docker-compose.yml`：**删除 `build: .` 这一行**，其余不变
4. Docker 应用 → 项目 → 创建，粘贴修改后的 compose 内容，启动

**方式 C：Docker 应用界面创建项目**

镜像已在 NAS 上（方式 B 导入过）时，直接在「项目 → 创建」中粘贴 compose 文件即可。

> 端口默认 `8787`，被占用可在 compose 里改为 `"18787:8787"`。

### 2. 首次使用（一次性）

1. 浏览器打开 `http://NAS的IP:8787`
2. 插入存储卡（读卡器或 NAS 内置卡槽均可）
3. 概览页出现「检测到未注册存储卡」→ 点 **注册并启用这张卡**，
   填别名（如"佳能R6"）、选择整理方式 → 保存（立即开始首次备份）
4. 此后这张卡每次插入都会自动增量备份；**其他未注册的卡只提示，绝不执行备份**

### 3. 备份目录结构示例

```
/volume1/photo/SD卡备份/          ← compose 里配置的 /backup
└── 佳能R6/                       ← 卡片别名（每张卡独立子目录）
    ├── 2024/01/15/IMG_0001.JPG   ← 按日期模式（默认）
    └── DCIM/100CANON/IMG_0002.CR3  ← 原结构模式（逐卡可选）
```

同名但内容不同的文件**不会被覆盖**，自动追加 `__2`、`__3` 后缀。

### 4. 卡片身份是怎么识别的

| 接入方式 | 身份来源 | 稳定性 |
|---|---|---|
| NAS 内置 SD 卡槽（mmcblk） | SD 卡 CID 序列号（卡本身的唯一编号） | 极稳定 |
| USB 读卡器 | 分区的文件系统 UUID | 稳定（重新格式化会变，需重新注册） |
| 兜底 | PARTUUID → 卷标+容量 | 仅供参考 |

注意：USB 读卡器自身的序列号是**读卡器**的编号，不能用来代表卡，因此不作为身份依据。

### 5. 完成通知接入（可选）

在「设置 → 完成通知 Webhook」填入对应地址，任务完成/失败时自动推送；程序按地址自动识别服务类型：

| 推送服务 | 填写内容 | 获取方式 |
|---|---|---|
| **PushPlus**（微信） | `https://www.pushplus.plus/send?token=你的Token` | pushplus.plus 微信扫码登录 →「一对一推送」页复制 token |
| **mails.dev**（邮箱·无需服务器） | `https://api.mails.dev/v1/send?key=mk_你的Key&to=收件邮箱` | mails.dev 申领邮箱后复制 API Key；免费 100 封/月 |
| **SMTP 直发**（邮箱·无需服务器） | `smtp+ssl://发件邮箱:密码@SMTP服务器:465` | 例如企业邮自发：`smtp+ssl://you@example.com:客户端专用密码@smtp.exmail.qq.com:465`（默认收件人=发件人；密码含 `@` `#` `:` 需 URL 编码为 `%40` `%23` `%3A`） |
| 企业微信机器人 | 群机器人 Webhook 完整地址 | 群设置 → 群机器人 → 添加并复制 |
| Server 酱 | `https://sctapi.ftqq.com/你的SendKey.send` | sct.ftqq.com 登录后复制 SendKey |
| 钉钉机器人 | 机器人 Webhook 完整地址 | 安全设置选「自定义关键词」，关键词填 `备份`（消息标题含该词）；本程序不支持加签模式 |
| 其他服务 | 任意 http(s) 地址 | 通用 POST JSON：`{"title": "...", "text": "..."}` |

> 通知发送失败不影响备份本身；失败原因记录在 `docker logs sdcard-backup`（关键字「通知发送失败」）。

### 6. 常见问题排查

| 现象 | 处理 |
|---|---|
| 界面显示「未检测到存储卡」 | ① 点「存储卡」页右上角**「运行环境自检」**，把结果发给维护者；② 确认 compose 含 `privileged: true`、`/dev:/dev`、`/run/udev:/run/udev:ro`（改动后需重新部署）；③ 看容器日志 `sudo docker logs sdcard-backup` 中的「设备扫描」段落，会逐盘列出被跳过的原因 |
| 构建时报 `registry-1.docker.io ... context deadline exceeded` | 内网连不上 Docker Hub。本项目已默认改用国内代理镜像；若仍超时，把 compose 里 `BASE_IMAGE` 换成 Dockerfile 顶部注释中的其他源（1Panel / 华为云），或给 NAS 配置镜像加速器后重试 |
| 界面打不开 | `sudo docker logs sdcard-backup` 看启动日志；确认端口映射 |
| 检测不到卡 | 确认 compose 中有 `- /dev:/dev` 与 `privileged: true`；SSH 里执行 `lsblk` 确认宿主能看到设备 |
| 挂载失败提示 "already mounted" | NAS 系统已自动挂载该卡。方案一：在文件管理里安全弹出外部设备后再插；方案二：compose 中启用注释掉的 `/mnt/@usb:/mnt/@usb:ro` 映射后重启容器 |
| 任务失败"存储卡读取中断" | 卡接触不良或被拔出；重插即可断点续传 |
| 任务失败"目标存储空间不足" | 清理目标磁盘，或调整 compose 的备份目录 |
| 拔卡中断会不会丢数据 | 不会。已备份文件在索引里，重插后只补差集 |
| 系统弹出"检测到外部设备" | 可忽略（那是 NAS 自身的挂载提示） |

### 7. 运维要点

- `./data` 卷保存索引库与配置，**请不要删除**；丢了不会丢备份文件，只是下次插入需全量比对
- 查看运行日志：`sudo docker logs -f sdcard-backup`
- 升级：更新代码后 `sudo docker compose up -d --build`
- 备份策略建议：NAS 上的备份 ≠ 最终备份，重要照片建议再异地保存一份

### 8. 安全说明

- 容器需要 `privileged: true`，仅用于执行 `mount/umount` 只读挂载与读取设备；程序不写卡、不删源
- 管理界面仅供**局域网**使用，请勿做公网端口映射；如需远程访问请套反向代理并加认证

## 本地开发与测试

```bash
python tests/test_core.py        # 核心逻辑测试（无需第三方依赖）
pip install -r requirements.txt  # 本地运行需要
python -m app                    # Linux 上可完整体验（Windows 无 lsblk，设备检测会暂停）
```

## 项目结构

```
├── app/
│   ├── config.py      # 环境配置与默认设置
│   ├── db.py          # SQLite：白名单/文件索引/任务/设置
│   ├── detector.py    # 设备检测（lsblk 轮询）与卡片身份识别
│   ├── mounter.py     # 只读挂载与回退策略
│   ├── backup.py      # 增量备份引擎（计划/复制/校验）
│   ├── runner.py      # 任务队列、状态机、通知
│   ├── web.py         # FastAPI REST + SSE
│   ├── __main__.py    # 启动入口
│   └── static/        # 管理界面（index.html / app.js / style.css）
├── tests/test_core.py # 核心逻辑测试
├── scripts/           # 构建导出脚本
├── Dockerfile
├── docker-compose.yml
└── docs/DESIGN.md     # 设计文档
```
