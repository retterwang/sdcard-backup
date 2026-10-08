'use strict';
/* 界面国际化（i18n）：中英双语文案表 + 翻译与渲染工具。
 *
 * - 语言保存在 localStorage，默认中文；
 * - 静态文案用 HTML 属性标注：data-i18n / data-i18n-ph / data-i18n-title / data-i18n-html；
 * - 后端落库消息为 {"k":键,"p":参数} 的 JSON，由 trMsg() 按当前语言渲染（兼容历史纯文本）。
 */

const LANG_KEY = 'sdbak.lang';

const I18N = {
  zh: {
    'app.title': '存储卡备份控制台',
    'app.subtitle': '插卡即自动增量备份',

    'top.connecting': '连接中…',
    'top.connected': '已连接',
    'top.lost': '连接断开，重试中…',
    'top.rescan': '重新扫描设备',
    'top.lang': '切换语言',

    'tab.dash': '概览',
    'tab.cards': '存储卡',
    'tab.tasks': '任务记录',
    'tab.settings': '设置',

    'hero.connecting': '正在连接后端…',
    'hero.backing_up': '备份中',
    'hero.files': '文件',
    'hero.data': '数据',
    'hero.speed': '速度',
    'hero.eta': '预计剩余',
    'hero.failed': '失败 {n}',
    'hero.cancel': '取消任务',
    'hero.unregistered': '检测到未注册存储卡',
    'hero.unregistered_hint': '默认只备份已注册的卡。注册后，这张卡每次插入都会自动开始增量备份。',
    'hero.register_enable': '注册并启用这张卡',
    'hero.connected': '已连接：{name}',
    'hero.auto_done': '插入自动备份已触发完成；也可以手动发起一次增量检查（无新增时几秒内结束）。',
    'hero.backup_now': '立即备份',
    'hero.eject': '卸载（安全拔出）',
    'hero.none': '未检测到存储卡',
    'hero.none_hint': '把卡接入读卡器或 NAS 卡槽。程序每 {n} 秒扫描一次，识别到已注册的卡后会自动开始增量备份，无需任何操作。',
    'hero.disabled_hint': '提示：检测到已注册但停用的卡',

    'stat.cards': '已注册卡片',
    'stat.devices': '在线外接设备',
    'stat.files': '累计已备份文件',
    'stat.bytes': '累计备份数据',

    'sec.cards_summary': '卡片备份汇总',
    'sec.recent_tasks': '最近任务',
    'sec.devices': '当前检测到的设备',
    'sec.whitelist': '已注册的存储卡（白名单）',
    'sec.tasks': '任务记录',
    'sec.settings': '运行设置',
    'sec.about': '关于本程序',
    'sec.notifications': '通知发送记录',
    'btn.diagnose': '运行环境自检',
    'btn.refresh': '刷新',
    'hint.devices': '实时列出外接存储设备（USB 读卡器 / 内置 SD 槽）。检测不到时，点右上角「运行环境自检」获取诊断信息。',
    'hint.whitelist': '只有注册且启用的卡插入时才自动备份；每张卡按身份 ID 识别，同名不同卡也不会混淆。',
    'hint.tasks': '保留最近 200 条任务。失败/部分失败的任务可查看具体文件错误，或插入卡后一键重试。',

    'set.scan_interval': '设备扫描间隔（秒）',
    'set.scan_interval_hint': '检测插卡/拔卡的轮询频率，1-120 秒。',
    'set.mount_delay': '插卡后等待时间（秒）',
    'set.mount_delay_hint': '识别到卡后等待若干秒再挂载，避免系统识别未完成，0-300 秒。',
    'set.verify': '复制完成后校验哈希（推荐）',
    'set.verify_hint': '重读目标文件比对哈希，防止拷贝静默损坏；会略微增加耗时。',
    'set.auto_unmount': '备份完成后自动卸载',
    'set.auto_unmount_hint': '完成后卸载存储卡，可安全拔出；关闭则保持挂载（可在概览页手动卸载）。',
    'set.auto_accept': '自动接受未注册的卡（谨慎）',
    'set.auto_accept_hint': '开启后任何卡插入都会自动注册并备份；默认关闭 = 只备份白名单内的卡。',
    'set.notify_url': '完成通知 Webhook（可选）',
    'set.notify_url_hint': '自动适配多种渠道（详见 README）：PushPlus（微信）/ 邮件 API / SMTP 直发（邮箱，无需服务器）/ 企业微信·钉钉机器人 / Server 酱。示例：https://api.example.com/v1/send?key=YOUR_KEY&to=you@example.com',
    'set.notify_lang': '完成通知语言',
    'set.notify_lang_hint': '推送内容的语言，与浏览器界面语言无关。',
    'set.copy_workers': '并行复制线程数',
    'set.copy_workers_hint': '同时复制的文件数，1-8。机械硬盘/低速卡建议 1-2，SSD 或 NVMe 目标盘可调高。',
    'set.adaptive_scan': '自动降低空闲时的扫描频率（省电）',
    'set.adaptive_scan_hint': '无设备变化时逐步延长扫描间隔（最长约 15 秒），检测到插拔后立即恢复高频扫描。',
    'set.lang.zh': '中文',
    'set.lang.en': 'English',
    'set.save': '保存设置',
    'set.dirty': '有未保存的修改',
    'set.saved_at': '已保存 {t}',
    'set.error': '保存失败',

    'about.l1': '备份目录结构：/backup/卡片别名/…（按日期分目录或保留卡内原结构，逐卡可选）',
    'about.l2': '存储卡以只读方式挂载，程序不会向卡内写入、删除任何内容',
    'about.l3': '增量依据：本地 SQLite 索引（路径 + 大小 + 修改时间 + 内容哈希），拔卡中断后重插会自动续传',
    'about.l4': '管理界面仅供局域网使用，请勿直接暴露到公网',

    'th.device': '设备', 'th.node': '节点', 'th.fstype': '文件系统', 'th.size': '容量',
    'th.status': '状态', 'th.ops': '操作', 'th.card': '卡片', 'th.target': '目标目录',
    'th.organize': '整理方式', 'th.backed_up': '已备份', 'th.last_backup': '最近备份',
    'th.task': '任务', 'th.trigger': '触发', 'th.started': '开始时间', 'th.duration': '耗时',
    'th.files': '文件 / 数据', 'th.result': '结果', 'th.action': '操作',
    'task.peak': '峰值',
    'th.file': '文件', 'th.error': '错误',

    'chip.registered': '已注册',
    'chip.unregistered': '未注册',
    'chip.disabled': '已停用',
    'chip.online': '在线',
    'chip.offline': '未连接',
    'organize.date': '按日期',
    'organize.mirror': '原结构',
    'files.count': '{n} 个文件',

    'btn.backup': '立即备份',
    'btn.register': '注册',
    'btn.edit': '编辑',
    'btn.enable': '启用',
    'btn.disable': '停用',
    'btn.reset_index': '清除索引',
    'btn.delete': '删除',
    'btn.retry': '重试',
    'btn.cancel': '取消',
    'btn.errors': '错误详情',

    'empty.devices': '未检测到外接存储设备。插入存储卡后自动出现在这里。',
    'empty.cards': '还没有注册任何存储卡。插入卡后在「存储卡」页注册。',
    'empty.cards_short': '还没有注册任何存储卡。',
    'empty.tasks': '暂无任务记录。',
    'empty.errors': '没有记录到错误明细。',
    'empty.notifications': '还没有通知发送记录。',
    'hint.notifications': '最近 20 条完成通知的发送结果。通知失败不会影响备份任务，仅在此处留痕便于排查。',
    'notify.sent': '已发送',
    'notify.failed': '发送失败',
    'notify.skipped': '未配置通知',
    'notify.col.send': '通知发送',

    'modal.register': '注册存储卡',
    'modal.edit': '编辑存储卡：{alias}',
    'modal.alias': '卡片别名（用于生成备份目录名）',
    'modal.alias_ph': '例如：Canon-R6 / DJI-Action',
    'modal.dest': '目标子目录（备份至 /backup/ 下的哪个文件夹）',
    'modal.dest_ph': '留空则用别名',
    'modal.organize': '目录整理方式',
    'modal.organize_date': '按拍摄日期（2024/01/15/IMG_0001.JPG）',
    'modal.organize_mirror': '保留卡内原结构（DCIM/100CANON/IMG_0001.JPG）',
    'modal.enabled': '启用（插入时自动备份）',
    'modal.allfiles': '备份全部文件（默认只备份照片和视频）',
    'modal.include': '包含规则（可选，分号分隔的文件通配符，如 DCIM/*;*.jpg）',
    'modal.exclude': '排除规则（可选，如 *.LRV;*_thumb*）',
    'modal.cancel': '取消',
    'modal.save': '保存',
    'modal.close': '关闭',
    'modal.target_root': '自定义备份目标根目录（可选）',
    'modal.target_root_ph': '留空 = 使用全局备份目录',
    'modal.target_root_hint': '为这张卡单独指定备份根目录（必须是容器内可写路径）。适合把不同卡备份到不同磁盘。',

    'resume.title': '上次备份被中断，可以继续',
    'resume.detail': '「{alias}」上次备份到 {files}/{total} 个文件时中断。重新插入这张卡即会自动续传，已复制的部分不会重复。',
    'resume.resume_now': '重新插入后自动续传',

    'diag.title': '运行环境自检',
    'diag.hint': '检测不到卡时，把以下内容完整复制（鼠标全选 / Ctrl+A → Ctrl+C）发给维护者，即可定位问题。',
    'errors.title': '任务 #{id} 错误详情',

    'toast.alias_required': '请填写卡片别名',
    'toast.saved': '已保存',
    'toast.backup_started': '开始备份：{alias}',
    'toast.disabled': '已停用（插入时不再自动备份）',
    'toast.enabled': '已启用',
    'toast.settings_saved': '设置已保存',
    'toast.refreshed': '已刷新',
    'toast.request_failed': '请求失败 HTTP {status}',

    'confirm.reset_index': '清除该卡的增量索引？\n\n下次插入时会重新逐文件比对（不会删除或覆盖已备份的文件），耗时较长。',
    'confirm.del_card': '移除这张卡的注册记录？\n\n已备份的文件会保留在磁盘上；再次插入该卡将不再自动备份。',

    'unit.second': '秒',
    'unit.minute': '分钟',
    'unit.hour': '小时',

    /* 与后端 i18n.py 对齐的落库消息键 */
    'status.queued': '排队中',
    'status.running': '备份中',
    'status.done': '完成',
    'status.failed': '失败',
    'status.interrupted': '已中断',
    'status.cancelled': '已取消',
    'status.unknown': '未知',
    'status.done_with_errors': '完成（{n} 个失败）',

    'phase.queued': '排队中',
    'phase.waiting': '等待设备就绪（{seconds}s）',
    'phase.mounting': '只读挂载',
    'phase.scanning': '扫描文件',
    'phase.comparing': '比对索引',
    'phase.copying': '备份中',
    'phase.unmounting': '卸载',

    'trigger.insert': '插入触发',
    'trigger.manual': '手动触发',
    'trigger.after_register': '注册后触发',

    'task.summary.base': '新增 {copied} 个（{bytes}）',
    'task.summary.reused': '目标已存在 {n} 个',
    'task.summary.skipped': '索引跳过 {n} 个',
    'task.summary.failed': '失败 {n} 个',
    'task.summary.empty': '卡内无可备份的媒体文件',
    'task.summary.nonew': '无新增文件',
    'task.summary.sep': '，',

    'err.card_removed': '存储卡已拔出',
    'err.user_cancelled': '用户取消',
    'err.read_interrupted': '存储卡读取中断（可能已被拔出）',
    'err.mount_lost': '存储卡不可读（可能已被拔出）',
    'err.verify_failed': '校验失败：目标文件哈希与源文件不一致',
    'err.space_low': '目标存储空间不足：需要 {need}，剩余 {free}',
    'err.space_full': '目标存储空间不足，任务已终止',
    'err.mount_point': '无法创建挂载点 {path}：{msg}',
    'err.mount_failed': '无法挂载存储卡。若 NAS 系统已自动挂载该卡，请先在「文件管理 → 外部设备」里安全弹出，或把宿主挂载点（如 /mnt/@usb）映射进容器后重试。原始错误：{detail}',
    'err.lsblk_missing': 'lsblk 命令不存在（设备检测仅在 Linux 宿主机可用）',

    /* ── 登录与账号 ── */
    'auth.title': '登录 · 存储卡备份控制台',
    'auth.signin_hint': '请登录后使用',
    'auth.username': '用户名',
    'auth.username_ph': '请输入用户名',
    'auth.password': '密码',
    'auth.password_ph': '请输入密码',
    'auth.signin': '登录',
    'auth.signing_in': '登录中…',
    'auth.foot_hint': '初始账号为 admin / admin，首次登录后会要求修改用户名和密码。',
    'auth.anonymous': '未登录',

    'top.account': '账号设置',
    'top.logout': '退出登录',

    'tab.users': '用户管理',
    'tab.account': '账号',

    'sec.my_account': '我的账号',
    'sec.users': '用户列表',
    'hint.my_account': '修改后需重新登录；首次登录必须先修改用户名和密码才能使用控制台。',
    'hint.users': '管理员可以为其他成员创建账号、重置密码或停用账号。新账号与重置后的密码在首次登录时同样需要修改用户名和密码。',
    'acct.current_password': '当前密码',
    'acct.new_username': '新的用户名',
    'acct.new_password': '新密码（留空表示不修改）',
    'acct.confirm_password': '确认新密码',
    'acct.username_hint': '2-32 个字符，可用字母、数字、汉字、下划线、点、@ 和连字符。',
    'acct.password_hint': '至少 6 个字符，且不能与用户名或原密码相同。',
    'acct.save': '保存修改',
    'acct.saved': '账号信息已更新',
    'acct.you': '（当前登录）',

    'first.title': '请修改初始用户名和密码',
    'first.hint': '检测到这是首次登录（或密码刚被重置）。默认账号一旦暴露在局域网内，任何人都能查看并操作备份，请立即设置你自己的用户名和密码。',
    'first.submit': '保存并进入控制台',

    'users.new': '新建用户',
    'users.reset_pw': '重置密码',
    'users.you': '当前账号',
    'chip.active': '正常',
    'chip.must_change': '待改密',
    'th.username': '用户名', 'th.role': '角色', 'th.userstatus': '状态', 'th.created': '创建时间',
    'th.last_login': '最近登录',
    'role.admin': '管理员', 'role.member': '普通用户',
    'chip.you': '当前账号', 'chip.me_disabled': '已停用',

    'modal.new_user': '新建用户',
    'modal.reset_pw': '重置密码：{name}',
    'modal.reset_pw_hint': '重置后该用户的现有会话立即失效，下次登录需重新设置用户名和密码。',
    'modal.username': '用户名',
    'modal.password': '初始密码',
    'modal.confirm_password': '确认密码',
    'modal.admin': '设为管理员（可管理用户与所有设置）',
    'modal.saving': '保存中…',

    'confirm.delete_user': '删除用户「{name}」？\n\n该账号将立即失效并退出登录，已备份的文件不受影响。',
    'confirm.disable_user': '停用用户「{name}」？\n\n停用后该账号无法登录（已有会话立即失效）。',

    'toast.user_created': '用户已创建',
    'toast.user_updated': '用户已更新',
    'toast.user_deleted': '用户已删除',
    'toast.pw_reset': '密码已重置，该用户下次登录需重新设置用户名和密码',
    'toast.logout': '已退出登录',
    'toast.gate_required': '请先完成初始账号的修改',
    'auth.err.passwords_mismatch': '两次输入的密码不一致',
    'auth.err.username_required': '请填写用户名',
    'auth.err.need_new_username': '请设置一个新的用户名（不能与当前的相同）',
  },

  en: {
    'app.title': 'SD Card Backup Console',
    'app.subtitle': 'Automatic incremental backup on card insertion',

    'top.connecting': 'Connecting…',
    'top.connected': 'Connected',
    'top.lost': 'Disconnected, retrying…',
    'top.rescan': 'Rescan devices',
    'top.lang': 'Switch language',

    'tab.dash': 'Overview',
    'tab.cards': 'Cards',
    'tab.tasks': 'Tasks',
    'tab.settings': 'Settings',

    'hero.connecting': 'Connecting to backend…',
    'hero.backing_up': 'Backing up',
    'hero.files': 'Files',
    'hero.data': 'Data',
    'hero.speed': 'Speed',
    'hero.eta': 'ETA',
    'hero.failed': '{n} failed',
    'hero.cancel': 'Cancel task',
    'hero.unregistered': 'Unregistered storage card detected',
    'hero.unregistered_hint': 'Only registered cards are backed up. After registering, this card is backed up automatically every time it is inserted.',
    'hero.register_enable': 'Register and enable this card',
    'hero.connected': 'Connected: {name}',
    'hero.auto_done': 'The insertion-triggered backup has finished; you can also run an incremental check manually (takes seconds when nothing is new).',
    'hero.backup_now': 'Back up now',
    'hero.eject': 'Unmount (safe to remove)',
    'hero.none': 'No storage card detected',
    'hero.none_hint': 'Connect a card through a reader or the NAS slot. The program scans every {n} seconds and starts an incremental backup automatically once a registered card is detected.',
    'hero.disabled_hint': 'Note: a registered but disabled card was detected',

    'stat.cards': 'Registered cards',
    'stat.devices': 'Online devices',
    'stat.files': 'Files backed up',
    'stat.bytes': 'Total backed up',

    'sec.cards_summary': 'Card backup summary',
    'sec.recent_tasks': 'Recent tasks',
    'sec.devices': 'Detected devices',
    'sec.whitelist': 'Registered cards (whitelist)',
    'sec.tasks': 'Task history',
    'sec.settings': 'Runtime settings',
    'sec.about': 'About',
    'sec.notifications': 'Notification delivery log',
    'btn.diagnose': 'Run environment self-check',
    'btn.refresh': 'Refresh',
    'hint.devices': 'Lists external storage devices in real time (USB readers / built-in SD slot). When nothing is detected, click "Run environment self-check" in the top-right corner for diagnostics.',
    'hint.whitelist': 'Only registered and enabled cards are backed up automatically. Each card is identified by its identity ID, so cards that share a name are never mixed up.',
    'hint.tasks': 'The most recent 200 tasks are kept. For failed or partially failed tasks you can inspect per-file errors, or retry with one click after inserting the card.',

    'set.scan_interval': 'Device scan interval (s)',
    'set.scan_interval_hint': 'Polling frequency for detecting insertion/removal, 1-120 seconds.',
    'set.mount_delay': 'Wait after insertion (s)',
    'set.mount_delay_hint': 'Wait a few seconds after detection before mounting, so the system has finished recognizing the device. 0-300 seconds.',
    'set.verify': 'Verify hash after copying (recommended)',
    'set.verify_hint': 'Re-read the target file and compare hashes to guard against silent corruption; slightly slower.',
    'set.auto_unmount': 'Auto unmount when finished',
    'set.auto_unmount_hint': 'Unmount the card when the backup finishes so it can be removed safely. When off, the mount is kept (you can unmount manually on the overview page).',
    'set.auto_accept': 'Auto-accept unregistered cards (caution)',
    'set.auto_accept_hint': 'When enabled, any inserted card is registered and backed up automatically. Default off = only whitelisted cards are backed up.',
    'set.notify_url': 'Completion notification webhook (optional)',
    'set.notify_url_hint': 'Multiple channels are auto-detected (see README): PushPlus (WeChat) / mail API / direct SMTP (email, no server needed) / WeCom & DingTalk bots / ServerChan. Example: https://api.example.com/v1/send?key=YOUR_KEY&to=you@example.com',
    'set.notify_lang': 'Notification language',
    'set.notify_lang_hint': 'Language of the pushed message; independent of the browser UI language.',
    'set.copy_workers': 'Parallel copy workers',
    'set.copy_workers_hint': 'Number of files copied simultaneously, 1-8. Use 1-2 for HDDs / slow cards; raise it for SSD or NVMe targets.',
    'set.adaptive_scan': 'Slow down scanning when idle (power saving)',
    'set.adaptive_scan_hint': 'Gradually lengthens the scan interval when nothing changes (up to about 15s), and returns to frequent scanning immediately after any insertion/removal.',
    'set.lang.zh': '中文',
    'set.lang.en': 'English',
    'set.save': 'Save settings',
    'set.dirty': 'Unsaved changes',
    'set.saved_at': 'Saved {t}',
    'set.error': 'Save failed',

    'about.l1': 'Backup layout: /backup/<card alias>/… (by date or keeping the card structure, per card)',
    'about.l2': 'Cards are always mounted read-only; the program never writes to or deletes anything on the card',
    'about.l3': 'Incremental basis: a local SQLite index (path + size + mtime + content hash); re-inserting a card after an interruption resumes automatically',
    'about.l4': 'The console is intended for LAN use only. Do not expose it directly to the internet',

    'th.device': 'Device', 'th.node': 'Node', 'th.fstype': 'Filesystem', 'th.size': 'Size',
    'th.status': 'Status', 'th.ops': 'Actions', 'th.card': 'Card', 'th.target': 'Destination',
    'th.organize': 'Organization', 'th.backed_up': 'Backed up', 'th.last_backup': 'Last backup',
    'th.task': 'Task', 'th.trigger': 'Trigger', 'th.started': 'Started', 'th.duration': 'Duration',
    'th.files': 'Files / Data', 'th.result': 'Result', 'th.action': 'Actions',
    'task.peak': 'peak',
    'th.file': 'File', 'th.error': 'Error',

    'chip.registered': 'Registered',
    'chip.unregistered': 'Unregistered',
    'chip.disabled': 'Disabled',
    'chip.online': 'Online',
    'chip.offline': 'Not connected',
    'organize.date': 'By date',
    'organize.mirror': 'Original structure',
    'files.count': '{n} files',

    'btn.backup': 'Back up now',
    'btn.register': 'Register',
    'btn.edit': 'Edit',
    'btn.enable': 'Enable',
    'btn.disable': 'Disable',
    'btn.reset_index': 'Clear index',
    'btn.delete': 'Delete',
    'btn.retry': 'Retry',
    'btn.cancel': 'Cancel',
    'btn.errors': 'Error details',

    'empty.devices': 'No external storage device detected. A card appears here once inserted.',
    'empty.cards': 'No storage card registered yet. Insert a card and register it on the "Cards" page.',
    'empty.cards_short': 'No storage card registered yet.',
    'empty.tasks': 'No task record yet.',
    'empty.errors': 'No error details were recorded.',
    'empty.notifications': 'No notification deliveries yet.',
    'hint.notifications': 'Results of the last 20 completion notifications. A failed notification never affects the backup task; it is logged here only to help troubleshooting.',
    'notify.sent': 'Sent',
    'notify.failed': 'Failed',
    'notify.skipped': 'Not configured',
    'notify.col.send': 'Notification',

    'modal.register': 'Register storage card',
    'modal.edit': 'Edit card: {alias}',
    'modal.alias': 'Card alias (used as the backup folder name)',
    'modal.alias_ph': 'e.g. Canon-R6 / DJI-Action',
    'modal.dest': 'Destination subfolder (folder under /backup/)',
    'modal.dest_ph': 'Leave empty to use the alias',
    'modal.organize': 'Folder organization',
    'modal.organize_date': 'By capture date (2024/01/15/IMG_0001.JPG)',
    'modal.organize_mirror': 'Keep the card structure (DCIM/100CANON/IMG_0001.JPG)',
    'modal.enabled': 'Enabled (back up automatically on insertion)',
    'modal.allfiles': 'Back up all files (by default only photos and videos)',
    'modal.include': 'Include rules (optional, semicolon-separated wildcards, e.g. DCIM/*;*.jpg)',
    'modal.exclude': 'Exclude rules (optional, e.g. *.LRV;*_thumb*)',
    'modal.cancel': 'Cancel',
    'modal.save': 'Save',
    'modal.close': 'Close',
    'modal.target_root': 'Custom backup root (optional)',
    'modal.target_root_ph': 'Empty = use the global backup directory',
    'modal.target_root_hint': 'Set a dedicated backup root for this card (must be a writable path inside the container). Useful for backing up different cards to different disks.',

    'resume.title': 'Last backup was interrupted and can be resumed',
    'resume.detail': 'Backup of "{alias}" was interrupted at {files}/{total} files. Re-inserting this card resumes automatically; already copied data is not repeated.',
    'resume.resume_now': 'Auto-resumes on re-insertion',

    'diag.title': 'Environment self-check',
    'diag.hint': 'When no card is detected, copy everything below (Ctrl+A → Ctrl+C) and send it to the maintainer to locate the problem.',
    'errors.title': 'Task #{id} error details',

    'toast.alias_required': 'Please enter a card alias',
    'toast.saved': 'Saved',
    'toast.backup_started': 'Backup started: {alias}',
    'toast.disabled': 'Disabled (no automatic backup on insertion)',
    'toast.enabled': 'Enabled',
    'toast.settings_saved': 'Settings saved',
    'toast.refreshed': 'Refreshed',
    'toast.request_failed': 'Request failed HTTP {status}',

    'confirm.reset_index': 'Clear the incremental index for this card?\n\nThe next insertion compares every file again (already backed-up files are not deleted or overwritten); this takes longer.',
    'confirm.del_card': 'Remove the registration record for this card?\n\nAlready backed-up files remain on disk; inserting this card again will not start a backup automatically.',

    'unit.second': 's',
    'unit.minute': 'min',
    'unit.hour': 'h',

    'status.queued': 'Queued',
    'status.running': 'Running',
    'status.done': 'Done',
    'status.failed': 'Failed',
    'status.interrupted': 'Interrupted',
    'status.cancelled': 'Cancelled',
    'status.unknown': 'Unknown',
    'status.done_with_errors': 'Done ({n} failed)',

    'phase.queued': 'Queued',
    'phase.waiting': 'Waiting for device ({seconds}s)',
    'phase.mounting': 'Mounting read-only',
    'phase.scanning': 'Scanning files',
    'phase.comparing': 'Comparing index',
    'phase.copying': 'Backing up',
    'phase.unmounting': 'Unmounting',

    'trigger.insert': 'Card inserted',
    'trigger.manual': 'Manual',
    'trigger.after_register': 'After registration',

    'task.summary.base': '{copied} new ({bytes})',
    'task.summary.reused': '{n} already at destination',
    'task.summary.skipped': '{n} skipped by index',
    'task.summary.failed': '{n} failed',
    'task.summary.empty': 'No backupable media on card',
    'task.summary.nonew': 'No new files',
    'task.summary.sep': ', ',

    'err.card_removed': 'Storage card removed',
    'err.user_cancelled': 'Cancelled by user',
    'err.read_interrupted': 'Card read interrupted (the device may have been unplugged)',
    'err.mount_lost': 'Storage card is not readable (it may have been unplugged)',
    'err.verify_failed': 'Verification failed: the copied file hash does not match the source',
    'err.space_low': 'Not enough space at the destination: need {need}, free {free}',
    'err.space_full': 'Destination ran out of space, task aborted',
    'err.mount_point': 'Cannot create mount point {path}: {msg}',
    'err.mount_failed': 'Failed to mount the card. If the NAS has already auto-mounted it, eject it under "File Manager → External devices" first, or map the host mount point (e.g. /mnt/@usb) into the container and retry. Raw error: {detail}',
    'err.lsblk_missing': 'lsblk not found (device detection is only available on a Linux host)',

    /* ── Sign-in & accounts ── */
    'auth.title': 'Sign in · SD Card Backup Console',
    'auth.signin_hint': 'Sign in to continue',
    'auth.username': 'Username',
    'auth.username_ph': 'Enter your username',
    'auth.password': 'Password',
    'auth.password_ph': 'Enter your password',
    'auth.signin': 'Sign in',
    'auth.signing_in': 'Signing in…',
    'auth.foot_hint': 'The initial account is admin / admin. You will be asked to change the username and password on first sign-in.',
    'auth.anonymous': 'Not signed in',

    'top.account': 'Account',
    'top.logout': 'Sign out',

    'tab.users': 'Users',
    'tab.account': 'Account',

    'sec.my_account': 'My account',
    'sec.users': 'Users',
    'hint.my_account': 'You will be signed out after the change. On first sign-in you must change the username and password before using the console.',
    'hint.users': 'Administrators can create accounts for other members, reset passwords, or disable them. New accounts and reset passwords must also be personalised on first sign-in.',
    'acct.current_password': 'Current password',
    'acct.new_username': 'New username',
    'acct.new_password': 'New password (leave empty to keep)',
    'acct.confirm_password': 'Confirm new password',
    'acct.username_hint': '2-32 characters: letters, digits, CJK, underscore, dot, @ and hyphen.',
    'acct.password_hint': 'At least 6 characters, different from the username and the current password.',
    'acct.save': 'Save changes',
    'acct.saved': 'Account updated',
    'acct.you': '(signed in)',

    'first.title': 'Change the initial username and password',
    'first.hint': 'This looks like a first sign-in (or a password that was just reset). Anyone on the LAN could use the default account to browse and control backups, so please set your own username and password now.',
    'first.submit': 'Save and enter the console',

    'users.new': 'New user',
    'users.reset_pw': 'Reset password',
    'users.you': 'You',
    'chip.active': 'Active',
    'chip.must_change': 'Must change',
    'th.username': 'Username', 'th.role': 'Role', 'th.userstatus': 'Status', 'th.created': 'Created',
    'th.last_login': 'Last sign-in',
    'role.admin': 'Administrator', 'role.member': 'Member',
    'chip.you': 'Signed in', 'chip.me_disabled': 'Disabled',

    'modal.new_user': 'New user',
    'modal.reset_pw': 'Reset password: {name}',
    'modal.reset_pw_hint': 'The user is signed out immediately; they must set a new username and password on their next sign-in.',
    'modal.username': 'Username',
    'modal.password': 'Initial password',
    'modal.confirm_password': 'Confirm password',
    'modal.admin': 'Make administrator (can manage users and all settings)',
    'modal.saving': 'Saving…',

    'confirm.delete_user': 'Delete user "{name}"?\n\nThe account stops working immediately and is signed out; backed-up files are untouched.',
    'confirm.disable_user': 'Disable user "{name}"?\n\nThey will no longer be able to sign in (existing sessions are dropped immediately).',

    'toast.user_created': 'User created',
    'toast.user_updated': 'User updated',
    'toast.user_deleted': 'User deleted',
    'toast.pw_reset': 'Password reset; the user must set a new username and password on next sign-in',
    'toast.logout': 'Signed out',
    'toast.gate_required': 'Please finish updating the initial credentials first',
    'auth.err.passwords_mismatch': 'The two passwords do not match',
    'auth.err.username_required': 'Please enter a username',
    'auth.err.need_new_username': 'Please set a new username (it must differ from the current one)',
  },
};

/* ── 语言状态 ── */
function getLang() {
  const v = localStorage.getItem(LANG_KEY);
  return v === 'en' ? 'en' : 'zh';
}

function setLang(lang) {
  const l = lang === 'en' ? 'en' : 'zh';
  localStorage.setItem(LANG_KEY, l);
  applyStaticI18n();
  if (typeof window.onLangChanged === 'function') window.onLangChanged();
}

/* ── 翻译 ── */
function T(key, params) {
  const dict = I18N[getLang()] || I18N.zh;
  let s = dict[key];
  if (s === undefined) s = I18N.zh[key];
  if (s === undefined) return key;
  if (params) {
    s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] === undefined || params[k] === null ? m : String(params[k])));
  }
  return s;
}

/* 任务结果摘要（与后端 i18n.summary_text 的拼接顺序保持一致） */
function fmtSummary(p) {
  p = p || {};
  const parts = [T('task.summary.base', { copied: p.copied || 0, bytes: p.bytes || '0 B' })];
  if (p.reused) parts.push(T('task.summary.reused', { n: p.reused }));
  parts.push(T('task.summary.skipped', { n: p.skipped || 0 }));
  if (p.failed) parts.push(T('task.summary.failed', { n: p.failed }));
  if (p.empty) parts.push(T('task.summary.empty'));
  else if (p.nonew) parts.push(T('task.summary.nonew'));
  return parts.join(T('task.summary.sep'));
}

/* 渲染落库消息：{"k":键,"p":参数} 的 JSON → 当前语言；其余（历史纯文本 / 消息键）原样或按字典翻译 */
function trMsg(raw) {
  if (raw === null || raw === undefined) return '';
  const s = String(raw);
  if (s.trim().charAt(0) === '{') {
    try {
      const o = JSON.parse(s);
      if (o && o.k) {
        return o.k === 'task.summary' ? fmtSummary(o.p) : T(o.k, o.p);
      }
    } catch (e) { /* 不是 JSON，按纯文本处理 */ }
  }
  return T(s);
}

/* ── 静态文案 ── */
function applyStaticI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = T(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-ph]').forEach(el => { el.setAttribute('placeholder', T(el.dataset.i18nPh)); });
  document.querySelectorAll('[data-i18n-title]').forEach(el => { el.setAttribute('title', T(el.dataset.i18nTitle)); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = T(el.dataset.i18nHtml); });
  document.title = T('app.title');
  document.documentElement.lang = getLang() === 'en' ? 'en' : 'zh-CN';
}
