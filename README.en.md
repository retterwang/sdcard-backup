# SD Card Auto Backup (sdcard-backup)

**English** | [简体中文](README.md)

A Docker application for UGREEN NAS (UGOS Pro): **insert a storage card and your photos and videos are backed up automatically and incrementally**, with a web UI for live progress and task history. Only registered cards (whitelist) trigger a backup.

## Features

| Capability | Description |
|---|---|
| Auto-backup on insertion | A background poller watches for devices and starts as soon as a registered card is detected — no user action required |
| Per-card identity | Internal card slots are identified by the SD card CID serial number; USB readers by the partition's filesystem UUID. Cards are never mixed up |
| Incremental backup | A SQLite index (path + size + mtime + hash) copies only new and changed files; if a card is unplugged mid-run, re-inserting it resumes automatically |
| Integrity verification | The hash is computed from a single read pass while copying, with an optional post-copy re-read verification to guard against silent corruption |
| Strictly read-only | Storage cards are always mounted **read-only**; the program never writes to or deletes anything on the card |
| Web UI | Live progress bars / device list / whitelist management / task history / error details / runtime settings |
| Folder organization | Organize by capture date (2024/01/15/…) or keep the card's original structure — configurable per card |
| Completion notifications | Webhook auto-detects PushPlus / WeCom (WeChat Work) group bot / ServerChan / DingTalk |
| Resume & retry | One-click retry for failed tasks; the destination free-space check aborts early when there is not enough room |

## How it works

```
Card inserted → detector thread (lsblk scan every 3 s) → whitelist match → task queue
     → read-only mount (ro) → scan files → compare against index → copy + hash → write to the card's own folder
     → unmount → record task / send notification → Web UI refreshes live (SSE)
```

See [docs/DESIGN.md](docs/DESIGN.md) for the detailed design rationale.

## Deploying to a UGREEN NAS

### 0. Prerequisites

- The **Docker** app is installed on the NAS (App Center → Docker)
- Pick a shared folder as the backup destination, for example `/volume1/photo/sdcard-backup`
- SSH is recommended for Option A (enable it under Control Panel → Terminal); Option B works without SSH

### 1. Deployment (choose one of three)

**Option A: Build directly on the NAS (recommended)**

1. Using File Manager, upload the entire project folder to `/volume1/docker/sdcard-backup`
2. Edit `docker-compose.yml` and change the backup directory on the last line to your actual path
3. Log in to the NAS over SSH and run:

```bash
cd /volume1/docker/sdcard-backup
sudo docker compose up -d --build
```

> **China network note**: the build uses domestic mirrors by default (DaoCloud proxy for the base image,
> Tsinghua mirrors for apt/pip), so it builds without any extra configuration. If your network can reach
> Docker Hub directly, you can revert `BASE_IMAGE` in the compose file to the official `python:3.12-slim-bookworm`.

**Option B: Build the image on your PC, then import it into the NAS** (no build environment needed on the NAS)

1. On a machine with Docker, run `scripts/build-export.sh` (on Windows use `build-export.bat`) to produce
   `sdcard-backup-image.tar`
2. In the UGREEN Docker app → Images → Import, upload that tar file
3. Edit `docker-compose.yml`: **delete the `build: .` line**, leave everything else unchanged
4. Docker app → Project → Create, paste the modified compose content, and start it

**Option C: Create the project from the Docker app UI**

If the image is already on the NAS (imported via Option B), simply paste the compose file into
"Project → Create".

> The default port is `8787`. If it is already in use, change the mapping in the compose file to `"18787:8787"`.

### 2. First-time setup (one-off)

1. Open `http://<NAS-IP>:8787` in a browser
2. Insert a storage card (a USB card reader or the NAS's built-in slot both work)
3. The overview page shows "Unregistered storage card detected" → click **Register and enable this card**,
   fill in an alias (for example "Canon-R6"), choose the organization mode → Save (the first backup starts immediately)
4. From then on this card is backed up incrementally every time it is inserted; **other unregistered cards only raise a
   notification and are never backed up**

### 3. Example backup directory layout

```
/volume1/photo/sdcard-backup/     ← the /backup path configured in the compose file
└── Canon-R6/                     ← card alias (each card gets its own subfolder)
    ├── 2024/01/15/IMG_0001.JPG   ← date mode (default)
    └── DCIM/100CANON/IMG_0002.CR3  ← original-structure mode (per card)
```

Files with the same name but different content are **never overwritten** — a `__2`, `__3` suffix is appended instead.

### 4. How card identity is determined

| Connection type | Identity source | Stability |
|---|---|---|
| NAS built-in SD slot (mmcblk) | SD card CID serial number (unique to the card itself) | Very stable |
| USB card reader | The partition's filesystem UUID | Stable (changes if reformatted; re-registration required) |
| Fallback | PARTUUID → volume label + capacity | For reference only |

Note: a USB reader's own serial number belongs to the **reader**, not the card, so it is not used as an identity source.

### 5. Completion notifications (optional)

Enter the target URL under "Settings → Completion notification webhook" and the program pushes a message when a task
finishes or fails. The service type is detected automatically from the URL:

| Push service | What to enter | How to obtain it |
|---|---|---|
| **PushPlus** (WeChat) | `https://www.pushplus.plus/send?token=YOUR_TOKEN` | Sign in to pushplus.plus with WeChat, then copy the token from the "One-to-one push" page |
| **mails.dev** (email, no server required) | `https://api.mails.dev/v1/send?key=mk_YOUR_KEY&to=RECIPIENT` | Claim a mailbox on mails.dev and copy the API key; 100 free emails per month |
| **Direct SMTP** (email, no server required) | `smtp+ssl://SENDER:PASSWORD@SMTP_HOST:465` | For example, from a corporate mailbox: `smtp+ssl://you@example.com:app-password@smtp.exmail.qq.com:465` (the recipient defaults to the sender; passwords containing `@`, `#` or `:` must be URL-encoded as `%40`, `%23`, `%3A`) |
| WeCom group bot | The full group-bot webhook URL | Group settings → Group bot → Add, then copy |
| ServerChan | `https://sctapi.ftqq.com/YOUR_SENDKEY.send` | Sign in at sct.ftqq.com and copy the SendKey |
| DingTalk bot | The full bot webhook URL | Choose "Custom keyword" under security settings and use `备份` as the keyword (the message title contains it). Signed mode is not supported |
| Any other service | Any http(s) URL | Generic JSON POST: `{"title": "...", "text": "..."}` |

> A failed notification never affects the backup itself; the reason is logged in `docker logs sdcard-backup`
> (search for "通知发送失败").

### 6. Troubleshooting

| Symptom | Action |
|---|---|
| The UI shows "No storage card detected" | ① Click **Run environment self-check** in the top-right corner of the "Storage cards" page and send the result to the maintainer; ② confirm the compose file contains `privileged: true`, `/dev:/dev` and `/run/udev:/run/udev:ro` (redeploy after changing it); ③ check the "设备扫描" section in `sudo docker logs sdcard-backup`, which lists every disk and the reason it was skipped |
| Build fails with `registry-1.docker.io ... context deadline exceeded` | The network cannot reach Docker Hub. This project uses domestic mirror proxies by default; if it still times out, switch `BASE_IMAGE` in the compose file to another source listed in the comments at the top of the Dockerfile (1Panel / Huawei Cloud), or configure a registry mirror on the NAS and retry |
| The UI does not open | Check the startup log with `sudo docker logs sdcard-backup`; verify the port mapping |
| No card is detected | Confirm the compose file has `- /dev:/dev` and `privileged: true`; run `lsblk` over SSH to confirm the host sees the device |
| Mount fails with "already mounted" | UGOS has already auto-mounted the card. Option 1: File Manager → External devices → safely eject, then re-insert. Option 2: enable the commented-out `/mnt/@usb:/mnt/@usb:ro` mapping in the compose file and restart the container |
| Task fails with "storage card read interrupted" | Poor contact or the card was removed; re-insert it to resume from where it stopped |
| Task fails with "insufficient destination space" | Free up space on the destination disk, or adjust the backup directory in the compose file |
| Will removing a card mid-run lose data? | No. Already-copied files are recorded in the index, so only the difference is transferred after re-insertion |
| UGOS pops up "external device detected" | Safe to ignore — that is UGOS's own mount notification |

### 7. Operations notes

- The `./data` volume holds the index database and configuration. **Do not delete it**; losing it does not lose backup
  files, but the next insertion will require a full comparison
- View runtime logs: `sudo docker logs -f sdcard-backup`
- Upgrade: update the code, then `sudo docker compose up -d --build`
- Backup strategy advice: a copy on the NAS is not a final backup. Keep an off-site copy of important photos

### 8. Security notes

- The container requires `privileged: true`, used only to perform read-only `mount`/`umount` and read device information.
  The program never writes to the card and never deletes the source
- The management UI is intended for **LAN use only**. Do not expose the port to the public internet; if remote access
  is required, put it behind a reverse proxy with authentication

## Local development and testing

```bash
python tests/test_core.py        # Core logic tests (no third-party dependencies)
pip install -r requirements.txt  # Required to run locally
python -m app                    # Full experience on Linux (Windows has no lsblk, so device detection is paused)
```

## Project structure

```
├── app/
│   ├── config.py      # Environment configuration and default settings
│   ├── db.py          # SQLite: whitelist / file index / tasks / settings
│   ├── detector.py    # Device detection (lsblk polling) and card identity resolution
│   ├── mounter.py     # Read-only mounting and fallback strategies
│   ├── backup.py      # Incremental backup engine (planning / copying / verification)
│   ├── runner.py      # Task queue, state machine, notifications
│   ├── web.py         # FastAPI REST + SSE
│   ├── __main__.py    # Entry point
│   └── static/        # Web UI (index.html / app.js / style.css)
├── tests/test_core.py # Core logic tests
├── scripts/           # Build and export scripts
├── Dockerfile
├── docker-compose.yml
└── docs/DESIGN.md     # Design document
```
