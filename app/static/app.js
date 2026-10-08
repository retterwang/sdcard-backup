'use strict';
/* 存储卡备份控制台 —— 前端逻辑（SSE 实时刷新 + 事件委托，无第三方依赖）
 * 所有文案经 i18n.js 的 T()/trMsg() 输出，支持中英切换。
 */

const $ = (s, el) => (el || document).querySelector(s);

let SNAP = null;
let ES = null;
let ME = null;              // 当前登录用户
let USERS = [];             // 用户列表（仅管理员加载）
let settingsDirty = false;
let prevCurrentTaskId = null;
let connOk = null;
let lastSnapKey = null;

/* ────────────────────────── 工具 ────────────────────────── */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
function fmtNum(n) {
  return Number(n || 0).toLocaleString(getLang() === 'en' ? 'en-US' : 'zh-CN');
}
function fmtBytes(n) {
  n = Number(n || 0);
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
}
function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), p = x => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtDur(start, end) {
  if (!start) return '—';
  const s = Math.max(0, (end || Date.now() / 1000) - start);
  if (s < 60) return Math.round(s) + ' ' + T('unit.second');
  if (s < 3600) return (s / 60).toFixed(1) + ' ' + T('unit.minute');
  return (s / 3600).toFixed(1) + ' ' + T('unit.hour');
}
function fmtEta(s) {
  if (s == null) return '—';
  if (s < 60) return Math.round(s) + ' ' + T('unit.second');
  if (s < 3600) return Math.round(s / 60) + ' ' + T('unit.minute');
  return (s / 3600).toFixed(1) + ' ' + T('unit.hour');
}

async function api(path, opts = {}) {
  const url = path + (path.indexOf('?') >= 0 ? '&' : '?') + 'lang=' + getLang();
  const r = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  if (r.status === 401) {                       // 会话失效 → 回到登录页
    location.replace('/login');
    throw new Error(T('auth.err.login_required'));
  }
  let data = {};
  try { data = await r.json(); } catch (e) { /* 空响应 */ }
  if (!r.ok) {
    if (r.status === 403 && data && data.detail === T('auth.err.must_change')) {
      location.reload();                        // 首次登录未改密 → 刷新后弹出强制修改
    }
    throw new Error((data && data.detail) || T('toast.request_failed', { status: r.status }));
  }
  return data;
}

function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

const STATUS_CLS = {
  queued: 'muted', running: 'run', done: 'ok',
  failed: 'err', interrupted: 'warn', cancelled: 'muted',
};
function chip(status, errorCount) {
  let cls = STATUS_CLS[status] || 'muted';
  if (status === 'done' && errorCount > 0) cls = 'warn';
  const label = (status === 'done' && errorCount > 0)
    ? T('status.done_with_errors', { n: errorCount })
    : T('status.' + (status || 'unknown'));
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

/* ────────────────────────── 连接 ────────────────────────── */
function renderConn() {
  const el = $('#conn');
  if (!el) return;
  const ok = connOk === true;
  el.className = 'conn ' + (ok ? 'ok' : 'off');
  el.innerHTML = `<i></i>${esc(T(ok ? 'top.connected' : 'top.lost'))}`;
}
function setConn(ok) {
  if (connOk === ok) return;
  connOk = ok;
  renderConn();
}

function connect() {
  if (ES) ES.close();
  ES = new EventSource('/api/events');
  ES.onopen = () => setConn(true);
  ES.onmessage = ev => {
    setConn(true);
    let snap;
    try { snap = JSON.parse(ev.data); } catch (e) { return; }
    SNAP = snap;
    // 快照未变化时跳过重绘：避免每秒重建 DOM 造成的闪烁与点击落空
    const key = snapKey(snap);
    if (key === lastSnapKey) return;
    lastSnapKey = key;
    render();
  };
  ES.onerror = () => setConn(false);
}

function snapKey(snap) {
  const c = Object.assign({}, snap);
  delete c.now;               // 时间戳每秒都变，不参与比较
  return JSON.stringify(c);
}

/* ────────────────────────── 渲染 ────────────────────────── */
function render() {
  renderHero();
  renderStats();
  renderDevices();
  renderCards();
  renderTasks($('#dash-tasks'), 5);
  renderTasks($('#tasks-full'), 50);
  renderSettings();
}

function renderHero() {
  const el = $('#hero');
  if (!SNAP) {
    el.innerHTML = `<div class="hero-idle"><div class="big">${esc(T('hero.connecting'))}</div></div>`;
    return;
  }
  const cur = SNAP.current;
  const devices = SNAP.devices || [];
  const unreg = devices.filter(d => !d.registered);
  const regConnected = devices.filter(d => d.registered && d.enabled);
  let h = '';

  if (cur) {
    if (cur.task_id !== prevCurrentTaskId) {
      toast(T('toast.backup_started', { alias: cur.alias }), 'info');
      prevCurrentTaskId = cur.task_id;
    }
    const pct = cur.percent == null ? 0 : cur.percent;
    h = `
      <div class="hero-run">
        <div class="hero-head">
          <span class="chip run">${esc(T('hero.backing_up'))}</span>
          <span class="hero-title">${esc(cur.alias)}</span>
          <span class="muted">${esc(trMsg(cur.phase))}</span>
        </div>
        <div class="bar"><div class="bar-in" style="width:${Math.min(100, pct)}%"></div></div>
        <div class="hero-meta">
          <span>${esc(T('hero.files'))} <b>${cur.done_files}</b> / ${cur.total_files}</span>
          <span>${esc(T('hero.data'))} <b>${fmtBytes(cur.done_bytes)}</b> / ${fmtBytes(cur.total_bytes)}</span>
          <span>${esc(T('hero.speed'))} <b>${fmtBytes(cur.speed)}/s</b></span>
          <span>${esc(T('hero.eta'))} <b>${fmtEta(cur.eta)}</b></span>
          ${cur.error_count ? `<span class="err-text">${esc(T('hero.failed', { n: cur.error_count }))}</span>` : ''}
        </div>
        <div class="curfile mono">${esc(cur.current_file || '…')}</div>
        <div class="hero-actions">
          <button class="btn danger" data-action="cancel-task" data-id="${cur.task_id}">${esc(T('hero.cancel'))}</button>
        </div>
      </div>`;
  } else if (unreg.length) {
    const d = unreg[0];
    h = `
      <div class="hero-idle warnbox">
        <div class="big">${esc(T('hero.unregistered'))}</div>
        <div class="sub2">${esc(d.display)} · <span class="mono">${esc(d.node)}</span></div>
        <div class="hint">${esc(T('hero.unregistered_hint'))}</div>
        <div class="hero-actions">
          <button class="btn" data-action="register-dev" data-id="${esc(d.card_id)}">${esc(T('hero.register_enable'))}</button>
        </div>
      </div>`;
  } else if (regConnected.length) {
    const d = regConnected[0];
    h = `
      <div class="hero-idle okbox">
        <div class="big">${esc(T('hero.connected', { name: d.alias || d.display }))}</div>
        <div class="sub2">${esc(d.display)} · <span class="mono">${esc(d.node)}</span></div>
        <div class="hint">${esc(T('hero.auto_done'))}</div>
        <div class="hero-actions">
          <button class="btn" data-action="backup-now" data-id="${esc(d.card_id)}">${esc(T('hero.backup_now'))}</button>
          <button class="btn ghost" data-action="eject-card" data-id="${esc(d.card_id)}">${esc(T('hero.eject'))}</button>
        </div>
      </div>`;
  } else {
    const regDevices = devices.filter(d => d.registered);
    const n = (SNAP.settings && SNAP.settings.scan_interval) || 3;
    h = `
      <div class="hero-idle">
        <div class="big">${esc(T('hero.none'))}</div>
        <div class="hint">${esc(T('hero.none_hint', { n }))}</div>
        ${regDevices.length ? `<div class="sub2">${esc(T('hero.disabled_hint'))}</div>` : ''}
      </div>`;
  }
  el.innerHTML = h;
  if (!cur && prevCurrentTaskId) prevCurrentTaskId = null;
}

function renderStats() {
  const el = $('#stats');
  if (!SNAP) { el.innerHTML = ''; return; }
  const cards = SNAP.cards || [];
  let files = 0, bytes = 0;
  cards.forEach(c => { files += c.files || 0; bytes += c.bytes || 0; });
  el.innerHTML = `
    <div class="stat"><div class="k">${esc(T('stat.cards'))}</div><div class="v">${cards.length}</div></div>
    <div class="stat"><div class="k">${esc(T('stat.devices'))}</div><div class="v">${(SNAP.devices || []).length}</div></div>
    <div class="stat"><div class="k">${esc(T('stat.files'))}</div><div class="v">${fmtNum(files)}</div></div>
    <div class="stat"><div class="k">${esc(T('stat.bytes'))}</div><div class="v">${fmtBytes(bytes)}</div></div>`;
}

function renderDevices() {
  const el = $('#devices');
  if (!SNAP) return;
  const devices = SNAP.devices || [];
  if (!devices.length) {
    el.innerHTML = `<div class="empty">${esc(T('empty.devices'))}</div>`;
    return;
  }
  el.innerHTML = `
    <div class="table-wrap"><table><thead><tr>
      <th>${esc(T('th.device'))}</th><th>${esc(T('th.node'))}</th><th>${esc(T('th.fstype'))}</th>
      <th>${esc(T('th.size'))}</th><th>${esc(T('th.status'))}</th><th>${esc(T('th.ops'))}</th>
    </tr></thead><tbody>
    ${devices.map(d => `
      <tr>
        <td>${esc(d.display)}<div class="sub-line mono">${esc(d.card_id)}</div></td>
        <td class="mono">${esc(d.node)}</td>
        <td>${esc(d.fstype)}</td>
        <td>${fmtBytes(d.size)}</td>
        <td>${d.registered
          ? `<span class="chip ok">${esc(T('chip.registered'))}</span>${d.enabled ? '' : `<span class="chip muted">${esc(T('chip.disabled'))}</span>`}`
          : `<span class="chip warn">${esc(T('chip.unregistered'))}</span>`}</td>
        <td><div class="row-actions">
          ${d.registered
            ? `<button class="btn sm" data-action="backup-now" data-id="${esc(d.card_id)}" ${d.enabled ? '' : 'disabled'}>${esc(T('btn.backup'))}</button>`
            : `<button class="btn sm" data-action="register-dev" data-id="${esc(d.card_id)}">${esc(T('btn.register'))}</button>`}
        </div></td>
      </tr>`).join('')}
    </tbody></table></div>`;
}

function cardRow(c) {
  const ops = `<div class="row-actions">
        <button class="btn sm" data-action="backup-now" data-id="${esc(c.id)}" ${c.connected && c.enabled ? '' : 'disabled'}>${esc(T('btn.backup'))}</button>
        <button class="btn sm ghost" data-action="edit-card" data-id="${esc(c.id)}">${esc(T('btn.edit'))}</button>
        <button class="btn sm ghost" data-action="toggle-card" data-id="${esc(c.id)}">${esc(T(c.enabled ? 'btn.disable' : 'btn.enable'))}</button>
        <button class="btn sm ghost" data-action="reset-index" data-id="${esc(c.id)}">${esc(T('btn.reset_index'))}</button>
        <button class="btn sm danger" data-action="del-card" data-id="${esc(c.id)}">${esc(T('btn.delete'))}</button>
      </div>`;
  const state = c.enabled
    ? (c.connected ? `<span class="chip ok">${esc(T('chip.online'))}</span>` : `<span class="chip muted">${esc(T('chip.offline'))}</span>`)
    : `<span class="chip warn">${esc(T('chip.disabled'))}</span>`;
  return `
    <tr>
      <td>${esc(c.alias)}<div class="sub-line mono">${esc(c.id)}</div></td>
      <td class="mono">/backup/${esc(c.dest_subdir)}</td>
      <td>${esc(T(c.organize === 'mirror' ? 'organize.mirror' : 'organize.date'))}</td>
      <td>${state}</td>
      <td>${fmtBytes(c.bytes)}<div class="sub-line">${esc(T('files.count', { n: fmtNum(c.files || 0) }))}</div></td>
      <td>${fmtTime(c.last_backup_at)}</td>
      <td>${ops}</td>
    </tr>`;
}

function renderCards() {
  const sum = $('#cards-summary');
  const tab = $('#cards-table');
  if (!SNAP) return;
  const cards = SNAP.cards || [];
  if (!cards.length) {
    sum.innerHTML = `<div class="empty">${esc(T('empty.cards'))}</div>`;
    tab.innerHTML = `<div class="empty">${esc(T('empty.cards_short'))}</div>`;
    return;
  }
  sum.innerHTML = `
    <div class="table-wrap"><table><thead><tr>
      <th>${esc(T('th.card'))}</th><th>${esc(T('th.status'))}</th><th>${esc(T('th.backed_up'))}</th>
      <th>${esc(T('th.last_backup'))}</th><th>${esc(T('th.ops'))}</th>
    </tr></thead>
    <tbody>${cards.map(c => `
      <tr>
        <td>${esc(c.alias)}</td>
        <td>${c.enabled
          ? (c.connected ? `<span class="chip ok">${esc(T('chip.online'))}</span>` : `<span class="chip muted">${esc(T('chip.offline'))}</span>`)
          : `<span class="chip warn">${esc(T('chip.disabled'))}</span>`}</td>
        <td>${fmtBytes(c.bytes)} · ${esc(T('files.count', { n: fmtNum(c.files || 0) }))}</td>
        <td>${fmtTime(c.last_backup_at)}</td>
        <td><button class="btn sm ghost" data-action="backup-now" data-id="${esc(c.id)}" ${c.connected && c.enabled ? '' : 'disabled'}>${esc(T('btn.backup'))}</button></td>
      </tr>`).join('')}
    </tbody></table></div>`;
  tab.innerHTML = `
    <div class="table-wrap"><table><thead><tr>
      <th>${esc(T('th.card'))}</th><th>${esc(T('th.target'))}</th><th>${esc(T('th.organize'))}</th>
      <th>${esc(T('th.status'))}</th><th>${esc(T('th.backed_up'))}</th><th>${esc(T('th.last_backup'))}</th>
      <th>${esc(T('th.ops'))}</th>
    </tr></thead><tbody>${cards.map(c => cardRow(c)).join('')}</tbody></table></div>`;
}

function renderTasks(el, limit) {
  if (!el || !SNAP) return;
  const tasks = (SNAP.recent_tasks || []).slice(0, limit);
  const current = SNAP.current;
  let rows = tasks.map(t => {
    const isRunning = current && current.task_id === t.id;
    const doneFiles = isRunning ? current.done_files : t.done_files;
    const totalFiles = isRunning ? current.total_files : t.total_files;
    const doneBytes = isRunning ? current.done_bytes : t.done_bytes;
    const errCount = isRunning ? current.error_count : (t.error_count || 0);
    const result = isRunning
      ? (trMsg(current.phase) + '…')
      : (t.error || t.result ? trMsg(t.error || t.result) : '—');
    return `
      <tr>
        <td class="mono">#${t.id}</td>
        <td>${esc(t.card_alias || '')}</td>
        <td>${esc(trMsg(t.trigger || ''))}</td>
        <td>${isRunning ? chip('running') : chip(t.status, errCount)}</td>
        <td>${fmtTime(t.started_at)}</td>
        <td>${fmtDur(t.started_at, t.finished_at)}</td>
        <td>${doneFiles || 0} / ${totalFiles || 0}<div class="sub-line">${fmtBytes(doneBytes)}</div></td>
        <td class="result-text">${esc(result)}</td>
        <td><div class="row-actions">
          ${isRunning ? `<button class="btn sm danger" data-action="cancel-task" data-id="${t.id}">${esc(T('btn.cancel'))}</button>` : ''}
          ${!isRunning && (t.status === 'failed' || t.status === 'interrupted' || errCount > 0)
            ? `<button class="btn sm ghost" data-action="retry-task" data-id="${t.id}">${esc(T('btn.retry'))}</button>` : ''}
          ${!isRunning && errCount > 0 ? `<button class="btn sm ghost" data-action="task-errors" data-id="${t.id}">${esc(T('btn.errors'))}</button>` : ''}
        </div></td>
      </tr>`;
  }).join('');
  el.innerHTML = tasks.length
    ? `<div class="table-wrap"><table><thead><tr>
        <th>${esc(T('th.task'))}</th><th>${esc(T('th.card'))}</th><th>${esc(T('th.trigger'))}</th>
        <th>${esc(T('th.status'))}</th><th>${esc(T('th.started'))}</th><th>${esc(T('th.duration'))}</th>
        <th>${esc(T('th.files'))}</th><th>${esc(T('th.result'))}</th><th>${esc(T('th.action'))}</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`
    : `<div class="empty">${esc(T('empty.tasks'))}</div>`;
}

function renderSettings() {
  if (!SNAP || !SNAP.settings || settingsDirty) return;
  const s = SNAP.settings;
  $('#s-scan_interval').value = s.scan_interval;
  $('#s-mount_delay').value = s.mount_delay;
  $('#s-verify').checked = !!s.verify;
  $('#s-auto_unmount').checked = !!s.auto_unmount;
  $('#s-auto_accept').checked = !!s.auto_accept;
  $('#s-notify_url').value = s.notify_url || '';
  const nl = $('#s-notify_lang');
  if (nl) nl.value = s.notify_lang === 'en' ? 'en' : 'zh';
}

/* ────────────────────────── 账号与用户 ────────────────────────── */
async function boot() {
  try {
    const r = await fetch('/api/auth/me?lang=' + getLang());
    if (r.status === 401) { location.replace('/login'); return; }
    const d = await r.json();
    ME = d.user || null;
  } catch (e) { /* 后端未就绪：仍按未登录渲染 */ }
  renderUser();
  if (ME && ME.must_change) { showGate(); return; }   // 首次登录：先改用户名和密码
  if (ME && ME.is_admin && localStorage.getItem('sdbak.tab') === 'users') loadUsers();
  connect();
}

function renderUser() {
  const nameEl = $('#user-name');
  if (nameEl) nameEl.textContent = ME ? ME.username : T('auth.anonymous');
  const chip = $('#user-chip');
  if (chip) {
    chip.title = ME ? (ME.is_admin ? T('role.admin') : T('role.member')) : '';
    chip.classList.toggle('admin', !!(ME && ME.is_admin));
  }
  const tabBtn = $('#tab-users-btn');
  if (tabBtn) tabBtn.textContent = T(ME && ME.is_admin ? 'tab.users' : 'tab.account');
  const adminCard = $('#users-admin-card');
  if (adminCard) adminCard.hidden = !(ME && ME.is_admin);
  const uname = $('#a-username');
  if (uname && ME) uname.placeholder = ME.username;
}

function showGate() {
  const g = $('#gate');
  if (!g) return;
  g.hidden = false;
  if (ME) {
    const u = $('#g-username');
    if (u) { u.value = ''; u.placeholder = ME.username; }
  }
  const c = $('#g-current');
  if (c) c.focus();
}

async function loadUsers() {
  if (!ME || !ME.is_admin) return;
  try {
    const d = await api('/api/users');
    USERS = d.users || [];
    renderUsersTable();
  } catch (e) {
    toast(e.message || String(e), 'err');
  }
}

function userRow(u) {
  const isMe = ME && u.username === ME.username;
  const state = u.enabled
    ? `<span class="chip ok">${esc(T('chip.active'))}</span>`
    : `<span class="chip warn">${esc(T('chip.disabled'))}</span>`;
  return `
    <tr>
      <td>${esc(u.username)}${isMe ? ` <span class="chip muted">${esc(T('chip.you'))}</span>` : ''}</td>
      <td>${esc(T(u.is_admin ? 'role.admin' : 'role.member'))}</td>
      <td>${state}${u.must_change ? ` <span class="chip muted">${esc(T('chip.must_change'))}</span>` : ''}</td>
      <td>${fmtTime(u.created_at)}</td>
      <td>${fmtTime(u.last_login_at)}</td>
      <td><div class="row-actions">
        <button class="btn sm ghost" data-action="reset-pw" data-id="${esc(u.username)}">${esc(T('users.reset_pw'))}</button>
        <button class="btn sm ghost" data-action="toggle-user" data-id="${esc(u.username)}" ${isMe ? 'disabled' : ''}>${esc(T(u.enabled ? 'btn.disable' : 'btn.enable'))}</button>
        <button class="btn sm danger" data-action="del-user" data-id="${esc(u.username)}" ${isMe ? 'disabled' : ''}>${esc(T('btn.delete'))}</button>
      </div></td>
    </tr>`;
}

function renderUsersTable() {
  const el = $('#users-table');
  if (!el) return;
  if (!USERS.length) { el.innerHTML = `<div class="empty">—</div>`; return; }
  el.innerHTML = `
    <div class="table-wrap"><table><thead><tr>
      <th>${esc(T('th.username'))}</th><th>${esc(T('th.role'))}</th><th>${esc(T('th.userstatus'))}</th>
      <th>${esc(T('th.created'))}</th><th>${esc(T('th.last_login'))}</th><th>${esc(T('th.ops'))}</th>
    </tr></thead><tbody>${USERS.map(userRow).join('')}</tbody></table></div>`;
}

function openUserModal() {
  openModal(`
    <h3>${esc(T('modal.new_user'))}</h3>
    <div class="form-grid">
      <label>
        <span>${esc(T('modal.username'))}</span>
        <input type="text" id="u-name" autocomplete="off">
        <small>${esc(T('acct.username_hint'))}</small>
      </label>
      <label>
        <span>${esc(T('modal.password'))}</span>
        <input type="password" id="u-pass" autocomplete="new-password">
        <small>${esc(T('acct.password_hint'))}</small>
      </label>
      <label>
        <span>${esc(T('modal.confirm_password'))}</span>
        <input type="password" id="u-pass2" autocomplete="new-password">
      </label>
      <label class="switch-line">
        <span>${esc(T('modal.admin'))}</span>
        <span class="switch"><input type="checkbox" id="u-admin"><i></i></span>
      </label>
    </div>
    <div class="foot">
      <button class="btn ghost" data-action="modal-close">${esc(T('modal.cancel'))}</button>
      <button class="btn" data-action="user-save">${esc(T('modal.save'))}</button>
    </div>`);
  const f = $('#u-name');
  if (f) f.focus();
}

function openResetModal(name) {
  openModal(`
    <h3>${esc(T('modal.reset_pw', { name }))}</h3>
    <p class="hint">${esc(T('modal.reset_pw_hint'))}</p>
    <div class="form-grid">
      <label>
        <span>${esc(T('modal.password'))}</span>
        <input type="password" id="u-pass" autocomplete="new-password">
        <small>${esc(T('acct.password_hint'))}</small>
      </label>
      <label>
        <span>${esc(T('modal.confirm_password'))}</span>
        <input type="password" id="u-pass2" autocomplete="new-password">
      </label>
    </div>
    <div class="foot">
      <button class="btn ghost" data-action="modal-close">${esc(T('modal.cancel'))}</button>
      <button class="btn" data-action="user-pw-save" data-id="${esc(name)}">${esc(T('modal.save'))}</button>
    </div>`);
  const f = $('#u-pass');
  if (f) f.focus();
}

/* ────────────────────────── 弹窗 ────────────────────────── */
function openModal(html) {
  $('#modal-root').innerHTML = `<div class="overlay"><div class="modal">${html}</div></div>`;
  const ov = $('.overlay', $('#modal-root'));
  ov.addEventListener('click', e => { if (e.target === ov) closeModal(); });
}
function closeModal() { $('#modal-root').innerHTML = ''; }

function openCardModal(card, deviceCardId) {
  const isEdit = !!card;
  const c = card || { alias: '', dest_subdir: '', organize: 'date', enabled: true, all_files: false, include_globs: '', exclude_globs: '' };
  openModal(`
    <h3>${esc(isEdit ? T('modal.edit', { alias: c.alias }) : T('modal.register'))}</h3>
    <div class="form-grid">
      <label>
        <span>${esc(T('modal.alias'))}</span>
        <input type="text" id="m-alias" value="${esc(c.alias)}" placeholder="${esc(T('modal.alias_ph'))}">
      </label>
      <label>
        <span>${esc(T('modal.dest'))}</span>
        <input type="text" id="m-dest" value="${esc(c.dest_subdir)}" placeholder="${esc(T('modal.dest_ph'))}">
      </label>
      <label>
        <span>${esc(T('modal.organize'))}</span>
        <select id="m-organize">
          <option value="date" ${c.organize !== 'mirror' ? 'selected' : ''}>${esc(T('modal.organize_date'))}</option>
          <option value="mirror" ${c.organize === 'mirror' ? 'selected' : ''}>${esc(T('modal.organize_mirror'))}</option>
        </select>
      </label>
      <label class="switch-line">
        <span>${esc(T('modal.enabled'))}</span>
        <span class="switch"><input type="checkbox" id="m-enabled" ${c.enabled ? 'checked' : ''}><i></i></span>
      </label>
      <label class="switch-line">
        <span>${esc(T('modal.allfiles'))}</span>
        <span class="switch"><input type="checkbox" id="m-allfiles" ${c.all_files ? 'checked' : ''}><i></i></span>
      </label>
      <label>
        <span>${esc(T('modal.include'))}</span>
        <input type="text" id="m-include" value="${esc(c.include_globs || '')}">
      </label>
      <label>
        <span>${esc(T('modal.exclude'))}</span>
        <input type="text" id="m-exclude" value="${esc(c.exclude_globs || '')}">
      </label>
    </div>
    <div class="foot">
      <button class="btn ghost" data-action="modal-close">${esc(T('modal.cancel'))}</button>
      <button class="btn" data-action="modal-save" ${deviceCardId ? `data-dev="${esc(deviceCardId)}"` : ''} ${isEdit ? `data-edit="${esc(c.id)}"` : ''}>${esc(T('modal.save'))}</button>
    </div>`);
}

/* ────────────────────────── 动作 ────────────────────────── */
document.addEventListener('click', async e => {
  const langBtn = e.target.closest('.lang-btn');
  if (langBtn) { setLang(langBtn.dataset.lang); return; }
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const { action, id } = btn.dataset;
  try {
    if (action === 'rescan') {
      const r = await api('/api/rescan', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'diagnose') {
      const info = await api('/api/diagnose');
      openModal(`<h3>${esc(T('diag.title'))}</h3>
        <p class="hint">${esc(T('diag.hint'))}</p>
        <pre class="diag-pre">${esc(JSON.stringify(info, null, 2))}</pre>
        <div class="foot"><button class="btn ghost" data-action="modal-close">${esc(T('modal.close'))}</button></div>`);
    } else if (action === 'register-dev') {
      const dev = (SNAP.devices || []).find(d => d.card_id === id);
      openCardModal(null, id);
      if (dev) {
        $('#m-alias').value = dev.label || dev.display.split(' · ')[0] || 'SD Card';
      }
    } else if (action === 'edit-card') {
      const c = (SNAP.cards || []).find(x => x.id === id);
      if (c) openCardModal(c, null);
    } else if (action === 'modal-close') {
      closeModal();
    } else if (action === 'modal-save') {
      const dev = btn.dataset.dev, edit = btn.dataset.edit;
      const body = {
        alias: $('#m-alias').value.trim(),
        dest_subdir: $('#m-dest').value.trim() || undefined,
        organize: $('#m-organize').value,
        enabled: $('#m-enabled').checked,
        all_files: $('#m-allfiles').checked,
        include_globs: $('#m-include').value.trim(),
        exclude_globs: $('#m-exclude').value.trim(),
      };
      if (!body.alias) { toast(T('toast.alias_required'), 'err'); return; }
      if (edit) {
        await api('/api/cards/' + encodeURIComponent(edit), { method: 'PATCH', body: JSON.stringify(body) });
        toast(T('toast.saved'));
      } else {
        body.card_id = dev;
        const r = await api('/api/cards', { method: 'POST', body: JSON.stringify(body) });
        toast(r.message || T('toast.saved'));
      }
      closeModal();
    } else if (action === 'backup-now') {
      const r = await api('/api/cards/' + encodeURIComponent(id) + '/backup-now', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'cancel-task') {
      const r = await api('/api/tasks/' + id + '/cancel', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'retry-task') {
      const r = await api('/api/tasks/' + id + '/retry', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'toggle-card') {
      const c = (SNAP.cards || []).find(x => x.id === id);
      if (c) {
        await api('/api/cards/' + encodeURIComponent(id), { method: 'PATCH', body: JSON.stringify({ enabled: !c.enabled }) });
        toast(T(c.enabled ? 'toast.disabled' : 'toast.enabled'));
      }
    } else if (action === 'reset-index') {
      if (!confirm(T('confirm.reset_index'))) return;
      const r = await api('/api/cards/' + encodeURIComponent(id) + '/reset-index', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'del-card') {
      if (!confirm(T('confirm.del_card'))) return;
      const r = await api('/api/cards/' + encodeURIComponent(id), { method: 'DELETE' });
      toast(r.message, 'info');
    } else if (action === 'task-errors') {
      const t = await api('/api/tasks/' + id);
      const rows = (t.errors || []).map(x =>
        `<tr><td class="mono">${esc(x.relpath)}</td><td>${esc(trMsg(x.message))}</td></tr>`).join('');
      openModal(`<h3>${esc(T('errors.title', { id: t.id }))}</h3>
        ${rows ? `<div class="table-wrap"><table><thead><tr><th>${esc(T('th.file'))}</th><th>${esc(T('th.error'))}</th></tr></thead><tbody>${rows}</tbody></table></div>`
               : `<div class="empty">${esc(T('empty.errors'))}</div>`}
        <div class="foot"><button class="btn ghost" data-action="modal-close">${esc(T('modal.close'))}</button></div>`);
    } else if (action === 'eject-card') {
      const r = await api('/api/cards/' + encodeURIComponent(id) + '/unmount', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'logout') {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (err) { /* 已失效也照样退出 */ }
      location.replace('/login');
    } else if (action === 'account') {
      switchTab('users');
    } else if (action === 'new-user') {
      openUserModal();
    } else if (action === 'reset-pw') {
      openResetModal(id);
    } else if (action === 'user-save') {
      const username = $('#u-name').value.trim();
      const pw = $('#u-pass').value, pw2 = $('#u-pass2').value;
      if (!username) { toast(T('auth.err.username_required'), 'err'); return; }
      if (pw !== pw2) { toast(T('auth.err.passwords_mismatch'), 'err'); return; }
      const r = await api('/api/users', {
        method: 'POST',
        body: JSON.stringify({ username: username, password: pw, is_admin: $('#u-admin').checked }),
      });
      toast(r.message || T('toast.user_created'));
      closeModal();
      await loadUsers();
    } else if (action === 'user-pw-save') {
      const pw = $('#u-pass').value, pw2 = $('#u-pass2').value;
      if (pw !== pw2) { toast(T('auth.err.passwords_mismatch'), 'err'); return; }
      await api('/api/users/' + encodeURIComponent(btn.dataset.id), {
        method: 'PATCH', body: JSON.stringify({ password: pw }),
      });
      toast(T('toast.pw_reset'), 'info');
      closeModal();
      await loadUsers();
    } else if (action === 'toggle-user') {
      const u = USERS.find(x => x.username === id);
      if (!u) return;
      if (u.enabled && !confirm(T('confirm.disable_user', { name: id }))) return;
      const r = await api('/api/users/' + encodeURIComponent(id), {
        method: 'PATCH', body: JSON.stringify({ enabled: !u.enabled }),
      });
      toast(r.message || T('toast.user_updated'));
      await loadUsers();
    } else if (action === 'del-user') {
      if (!confirm(T('confirm.delete_user', { name: id }))) return;
      const r = await api('/api/users/' + encodeURIComponent(id), { method: 'DELETE' });
      toast(r.message || T('toast.user_deleted'), 'info');
      await loadUsers();
    }
  } catch (err) {
    toast(err.message || String(err), 'err');
  }
});

/* ────────────────────────── 语言 ────────────────────────── */
function updateLangButtons() {
  const cur = getLang();
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === cur);
  });
}

window.onLangChanged = () => {
  updateLangButtons();
  renderConn();
  renderUser();
  if (ME && ME.is_admin) renderUsersTable();
  if (SNAP) render();
  const st = $('#settings-status');
  if (st && st.dataset.state === 'dirty') st.textContent = T('set.dirty');
};

/* 设置表单 */
document.addEventListener('DOMContentLoaded', () => {
  applyStaticI18n();
  updateLangButtons();

  const form = $('#settings-form');
  form.addEventListener('input', () => {
    settingsDirty = true;
    const st = $('#settings-status');
    st.dataset.state = 'dirty';
    st.textContent = T('set.dirty');
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const body = {
        scan_interval: parseInt($('#s-scan_interval').value, 10),
        mount_delay: parseInt($('#s-mount_delay').value, 10),
        verify: $('#s-verify').checked,
        auto_unmount: $('#s-auto_unmount').checked,
        auto_accept: $('#s-auto_accept').checked,
        notify_url: $('#s-notify_url').value.trim(),
        notify_lang: $('#s-notify_lang').value,
      };
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
      settingsDirty = false;
      const st = $('#settings-status');
      st.dataset.state = 'saved';
      st.textContent = T('set.saved_at', { t: fmtTime(Date.now() / 1000) });
      toast(T('toast.settings_saved'));
    } catch (err) {
      toast(err.message || String(err), 'err');
    }
  });

  // 标签页
  document.querySelectorAll('.tab').forEach(b => {
    b.addEventListener('click', () => switchTab(b.dataset.tab));
  });
  switchTab(localStorage.getItem('sdbak.tab') || 'dash');

  // 修改自己的账号信息
  const acct = $('#account-form');
  acct.addEventListener('submit', async e => {
    e.preventDefault();
    const cur = $('#a-current').value;
    const newUser = $('#a-username').value.trim();
    const newPw = $('#a-password').value;
    const confirmPw = $('#a-confirm').value;
    const st = $('#account-status');
    if (newPw && newPw !== confirmPw) { toast(T('auth.err.passwords_mismatch'), 'err'); return; }
    if (!newPw && (!newUser || newUser === (ME && ME.username))) {
      toast(T('auth.err.nothing_changed'), 'err');
      return;
    }
    const body = { current_password: cur };
    if (newUser) body.new_username = newUser;
    if (newPw) { body.new_password = newPw; body.confirm_password = confirmPw; }
    try {
      const r = await api('/api/auth/change-credentials', { method: 'POST', body: JSON.stringify(body) });
      st.textContent = T('acct.saved');
      toast(r.message || T('acct.saved'));
      setTimeout(() => location.reload(), 800);   // 会话已轮换，重新加载更稳妥
    } catch (err) {
      st.textContent = '';
      toast(err.message || String(err), 'err');
    }
  });

  // 首次登录：强制修改用户名和密码
  const gate = $('#gate-form');
  gate.addEventListener('submit', async e => {
    e.preventDefault();
    const st = $('#gate-status');
    const btn = $('#gate-form button[type=submit]');
    const pw = $('#g-password').value, pw2 = $('#g-confirm').value;
    const newName = $('#g-username').value.trim();
    st.textContent = '';
    if (!newName) { st.textContent = T('auth.err.username_required'); return; }
    if (ME && newName === ME.username) { st.textContent = T('auth.err.need_new_username'); return; }
    if (pw !== pw2) { st.textContent = T('auth.err.passwords_mismatch'); return; }
    btn.disabled = true;
    try {
      await api('/api/auth/change-credentials', {
        method: 'POST',
        body: JSON.stringify({
          current_password: $('#g-current').value,
          new_username: newName,
          new_password: pw,
          confirm_password: pw2,
        }),
      });
      toast(T('acct.saved'));
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      st.textContent = err.message || String(err);
      btn.disabled = false;
    }
  });

  boot();
});

function switchTab(t) {
  localStorage.setItem('sdbak.tab', t);
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t));
  if (t === 'users' && ME && ME.is_admin) loadUsers();
}
