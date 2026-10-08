"""容器入口：python -m app"""
from __future__ import annotations

import logging
import os

import uvicorn

from . import auth, config
from .db import DB
from .runner import AppState, Runner
from .web import create_app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log = logging.getLogger("main")

    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        os.makedirs(config.BACKUP_ROOT, exist_ok=True)
    except OSError as e:
        log.warning("无法创建备份目录 %s: %s（请检查 compose 中的目录映射）",
                    config.BACKUP_ROOT, e)

    db = DB(config.DB_PATH)
    auth.ensure_default_admin(db)
    state = AppState(db)
    runner = Runner(db, state)
    runner.start()

    app = create_app(db, state, runner)
    pending = [u["username"] for u in db.users_all() if u["must_change"]]
    if pending:
        log.warning("以下账号尚未修改初始密码：%s（界面登录后会要求修改）", "、".join(pending))
    log.info("%s v%s 启动 | 端口 %s | 备份根目录 %s",
             config.APP_NAME, config.VERSION, config.PORT, config.BACKUP_ROOT)
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
