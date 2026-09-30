'use strict';
/* 存储卡备份控制台 —— 前端逻辑（SSE 实时刷新 + 事件委托，无第三方依赖） */

const $ = (s, el) => (el || document).querySelector(s);

let SNAP = null;
let ES = null;
let settingsDirty = false;
let prevCurrentTaskId = null;

/* ────────────────────────── 工具 ────────────────────────── */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
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
  if (s < 60) return Math.round(s) + ' 秒';
  if (s < 3600) return (s / 60).toFixed(1) + ' 分钟';
  return (s / 3600).toFixed(1) + ' 小时';
}
function fmtEta(s) {
  if (s == null) return '—';
  if (s < 60) return Math.round(s) + ' 秒';
  if (s < 3600) return Math.round(s / 60) + ' 分钟';
  return (s / 3600).toFixed(1) + ' 小时';
}

async function api(path, opts = {}) {
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  let data = {};
  try { data = await r.json(); } catch (e) { /* 空响应 */ }
  if (!r.ok) throw new Error((data && data.detail) || ('请求失败 HTTP ' + r.status));
  return data;
}

function toast(msg, type = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

const STATUS_MAP = {
  queued: ['排队中', 'muted'], running: ['备份中', 'run'],
  done: ['完成', 'ok'], failed: ['失败', 'err'],
  interrupted: ['已中断', 'warn'], cancelled: ['已取消', 'muted'],
};
function chip(status, errorCount) {
  const m = STATUS_MAP[status] || [status || '未知', 'muted'];
  let cls = m[1];
  if (status === 'done' && errorCount > 0) cls = 'warn';
  const label = status === 'done' && errorCount > 0 ? `完成（${errorCount} 个失败）` : m[0];
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

/* ────────────────────────── 连接 ────────────────────────── */
function setConn(ok) {
  const el = $('#conn');
  el.className = 'conn ' + (ok ? 'ok' : 'off');
  el.innerHTML = `<i></i>${ok ? '已连接' : '连接断开，重试中…'}`;
}

function connect() {
  if (ES) ES.close();
  ES = new EventSource('/api/events');
  ES.onopen = () => setConn(true);
  ES.onmessage = ev => {
    setConn(true);
    try { SNAP = JSON.parse(ev.data); } catch (e) { return; }
    render();
  };
  ES.onerror = () => setConn(false);
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
  if (!SNAP) { el.innerHTML = '<div class="hero-idle"><div class="big">正在连接后端…</div></div>'; return; }
  const cur = SNAP.current;
  const devices = SNAP.devices || [];
  const unreg = devices.filter(d => !d.registered);
  const regConnected = devices.filter(d => d.registered && d.enabled);
  let h = '';

  if (cur) {
    if (cur.task_id !== prevCurrentTaskId) { toast(`开始备份：${cur.alias}`, 'info'); prevCurrentTaskId = cur.task_id; }
    const pct = cur.percent == null ? 0 : cur.percent;
    h = `
      <div class="hero-run">
        <div class="hero-head">
          <span class="chip run">备份中</span>
          <span class="hero-title">${esc(cur.alias)}</span>
          <span class="muted">${esc(cur.phase)}</span>
        </div>
        <div class="bar"><div class="bar-in" style="width:${Math.min(100, pct)}%"></div></div>
        <div class="hero-meta">
          <span>文件 <b>${cur.done_files}</b> / ${cur.total_files}</span>
          <span>数据 <b>${fmtBytes(cur.done_bytes)}</b> / ${fmtBytes(cur.total_bytes)}</span>
          <span>速度 <b>${fmtBytes(cur.speed)}/s</b></span>
          <span>预计剩余 <b>${fmtEta(cur.eta)}</b></span>
          ${cur.error_count ? `<span class="err-text">失败 ${cur.error_count}</span>` : ''}
        </div>
        <div class="curfile mono">${esc(cur.current_file || '…')}</div>
        <div class="hero-actions">
          <button class="btn danger" data-action="cancel-task" data-id="${cur.task_id}">取消任务</button>
        </div>
      </div>`;
  } else if (unreg.length) {
    const d = unreg[0];
    h = `
      <div class="hero-idle warnbox">
        <div class="big">检测到未注册存储卡</div>
        <div class="sub2">${esc(d.display)} · <span class="mono">${esc(d.node)}</span></div>
        <div class="hint">默认只备份已注册的卡。注册后，这张卡每次插入都会自动开始增量备份。</div>
        <div class="hero-actions">
          <button class="btn" data-action="register-dev" data-id="${esc(d.card_id)}">注册并启用这张卡</button>
        </div>
      </div>`;
  } else if (regConnected.length) {
    const d = regConnected[0];
    h = `
      <div class="hero-idle okbox">
        <div class="big">已连接：${esc(d.alias || d.display)}</div>
        <div class="sub2">${esc(d.display)} · <span class="mono">${esc(d.node)}</span></div>
        <div class="hint">插入自动备份已触发完成；也可以手动发起一次增量检查（无新增时几秒内结束）。</div>
        <div class="hero-actions">
          <button class="btn" data-action="backup-now" data-id="${esc(d.card_id)}">立即备份</button>
          <button class="btn ghost" data-action="eject-card" data-id="${esc(d.card_id)}">卸载（安全拔出）</button>
        </div>
      </div>`;
  } else {
    const regDevices = devices.filter(d => d.registered);
    h = `
      <div class="hero-idle">
        <div class="big">未检测到存储卡</div>
        <div class="hint">把 SD 卡接入读卡器或 NAS 内置卡槽。程序每 ${(SNAP.settings && SNAP.settings.scan_interval) || 3} 秒扫描一次，
        识别到已注册的卡后会自动开始增量备份，无需任何操作。</div>
        ${regDevices.length ? '<div class="sub2">提示：检测到已注册但停用的卡</div>' : ''}
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
  const summary = SNAP.last_result || {};
  el.innerHTML = `
    <div class="stat"><div class="k">已注册卡片</div><div class="v">${cards.length}</div></div>
    <div class="stat"><div class="k">在线外接设备</div><div class="v">${(SNAP.devices || []).length}</div></div>
    <div class="stat"><div class="k">累计已备份文件</div><div class="v">${files.toLocaleString()}</div></div>
    <div class="stat"><div class="k">累计备份数据</div><div class="v">${fmtBytes(bytes)}</div></div>`;
}

function renderDevices() {
  const el = $('#devices');
  if (!SNAP) return;
  const devices = SNAP.devices || [];
  if (!devices.length) {
    el.innerHTML = '<div class="empty">未检测到外接存储设备。插入存储卡后自动出现在这里。</div>';
    return;
  }
  el.innerHTML = `
    <table><thead><tr>
      <th>设备</th><th>节点</th><th>文件系统</th><th>容量</th><th>状态</th><th>操作</th>
    </tr></thead><tbody>
    ${devices.map(d => `
      <tr>
        <td>${esc(d.display)}<div class="sub-line mono">${esc(d.card_id)}</div></td>
        <td class="mono">${esc(d.node)}</td>
        <td>${esc(d.fstype)}</td>
        <td>${fmtBytes(d.size)}</td>
        <td>${d.registered
          ? `<span class="chip ok">已注册</span>${d.enabled ? '' : '<span class="chip muted">已停用</span>'}`
          : '<span class="chip warn">未注册</span>'}</td>
        <td><div class="row-actions">
          ${d.registered
            ? `<button class="btn sm" data-action="backup-now" data-id="${esc(d.card_id)}" ${d.enabled ? '' : 'disabled'}>立即备份</button>`
            : `<button class="btn sm" data-action="register-dev" data-id="${esc(d.card_id)}">注册</button>`}
        </div></td>
      </tr>`).join('')}
    </tbody></table>`;
}

function cardRow(c) {
  const ops = `<div class="row-actions">
        <button class="btn sm" data-action="backup-now" data-id="${esc(c.id)}" ${c.connected && c.enabled ? '' : 'disabled'}>立即备份</button>
        <button class="btn sm ghost" data-action="edit-card" data-id="${esc(c.id)}">编辑</button>
        <button class="btn sm ghost" data-action="toggle-card" data-id="${esc(c.id)}">${c.enabled ? '停用' : '启用'}</button>
        <button class="btn sm ghost" data-action="reset-index" data-id="${esc(c.id)}">清除索引</button>
        <button class="btn sm danger" data-action="del-card" data-id="${esc(c.id)}">删除</button>
      </div>`;
  const state = c.enabled
    ? (c.connected ? '<span class="chip ok">在线</span>' : '<span class="chip muted">未连接</span>')
    : '<span class="chip warn">已停用</span>';
  return `
    <tr>
      <td>${esc(c.alias)}<div class="sub-line mono">${esc(c.id)}</div></td>
      <td class="mono">/backup/${esc(c.dest_subdir)}</td>
      <td>${c.organize === 'mirror' ? '原结构' : '按日期'}</td>
      <td>${state}</td>
      <td>${fmtBytes(c.bytes)}<div class="sub-line">${(c.files || 0).toLocaleString()} 个文件</div></td>
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
    sum.innerHTML = '<div class="empty">还没有注册任何存储卡。插入卡后在「存储卡」页注册。</div>';
    tab.innerHTML = '<div class="empty">还没有注册任何存储卡。</div>';
    return;
  }
  sum.innerHTML = `
    <table><thead><tr><th>卡片</th><th>状态</th><th>已备份</th><th>最近备份</th><th>操作</th></tr></thead>
    <tbody>${cards.map(c => `
      <tr>
        <td>${esc(c.alias)}</td>
        <td>${c.enabled
          ? (c.connected ? '<span class="chip ok">在线</span>' : '<span class="chip muted">未连接</span>')
          : '<span class="chip warn">已停用</span>'}</td>
        <td>${fmtBytes(c.bytes)} · ${(c.files || 0).toLocaleString()} 个</td>
        <td>${fmtTime(c.last_backup_at)}</td>
        <td><button class="btn sm ghost" data-action="backup-now" data-id="${esc(c.id)}" ${c.connected && c.enabled ? '' : 'disabled'}>立即备份</button></td>
      </tr>`).join('')}
    </tbody></table>`;
  tab.innerHTML = `
    <table><thead><tr>
      <th>卡片</th><th>目标目录</th><th>整理方式</th><th>状态</th><th>已备份</th><th>最近备份</th><th>操作</th>
    </tr></thead><tbody>${cards.map(c => cardRow(c)).join('')}</tbody></table>`;
}

function renderTasks(el, limit) {
  if (!el) return;
  if (!SNAP) return;
  const tasks = (SNAP.recent_tasks || []).slice(0, limit);
  const current = SNAP.current;
  let rows = tasks.map(t => {
    const isRunning = current && current.task_id === t.id;
    const doneFiles = isRunning ? current.done_files : t.done_files;
    const totalFiles = isRunning ? current.total_files : t.total_files;
    const doneBytes = isRunning ? current.done_bytes : t.done_bytes;
    const errCount = isRunning ? current.error_count : (t.error_count || 0);
    const result = isRunning ? (current.phase + '…') : (t.error || t.result || '—');
    return `
      <tr>
        <td class="mono">#${t.id}</td>
        <td>${esc(t.card_alias || '')}</td>
        <td>${esc(t.trigger || '')}</td>
        <td>${isRunning ? chip('running') : chip(t.status, errCount)}</td>
        <td>${fmtTime(t.started_at)}</td>
        <td>${fmtDur(t.started_at, t.finished_at)}</td>
        <td>${doneFiles || 0} / ${totalFiles || 0}<div class="sub-line">${fmtBytes(doneBytes)}</div></td>
        <td class="result-text">${esc(result)}</td>
        <td><div class="row-actions">
          ${isRunning ? `<button class="btn sm danger" data-action="cancel-task" data-id="${t.id}">取消</button>` : ''}
          ${!isRunning && (t.status === 'failed' || t.status === 'interrupted' || errCount > 0)
            ? `<button class="btn sm ghost" data-action="retry-task" data-id="${t.id}">重试</button>` : ''}
          ${!isRunning && errCount > 0 ? `<button class="btn sm ghost" data-action="task-errors" data-id="${t.id}">错误详情</button>` : ''}
        </div></td>
      </tr>`;
  }).join('');
  el.innerHTML = tasks.length
    ? `<table><thead><tr>
        <th>任务</th><th>卡片</th><th>触发</th><th>状态</th><th>开始时间</th><th>耗时</th><th>文件 / 数据</th><th>结果</th><th>操作</th>
      </tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="empty">暂无任务记录。</div>';
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
    <h3>${isEdit ? '编辑存储卡：' + esc(c.alias) : '注册存储卡'}</h3>
    <div class="form-grid">
      <label>卡片别名（用于生成备份目录名）
        <input type="text" id="m-alias" value="${esc(c.alias)}" placeholder="例如：佳能R6 / DJI-Action">
      </label>
      <label>目标子目录（备份至 /backup/ 下的哪个文件夹）
        <input type="text" id="m-dest" value="${esc(c.dest_subdir)}" placeholder="留空则用别名">
      </label>
      <label>目录整理方式
        <select id="m-organize">
          <option value="date" ${c.organize !== 'mirror' ? 'selected' : ''}>按拍摄日期（2024/01/15/IMG_0001.JPG）</option>
          <option value="mirror" ${c.organize === 'mirror' ? 'selected' : ''}>保留卡内原结构（DCIM/100CANON/IMG_0001.JPG）</option>
        </select>
      </label>
      <label class="switch-line">启用（插入时自动备份）
        <span class="switch"><input type="checkbox" id="m-enabled" ${c.enabled ? 'checked' : ''}><i></i></span>
      </label>
      <label class="switch-line">备份全部文件（默认只备份照片和视频）
        <span class="switch"><input type="checkbox" id="m-allfiles" ${c.all_files ? 'checked' : ''}><i></i></span>
      </label>
      <label>包含规则（可选，分号分隔的文件通配符，如 DCIM/*;*.jpg）
        <input type="text" id="m-include" value="${esc(c.include_globs || '')}">
      </label>
      <label>排除规则（可选，如 *.LRV;*_thumb*）
        <input type="text" id="m-exclude" value="${esc(c.exclude_globs || '')}">
      </label>
    </div>
    <div class="foot">
      <button class="btn ghost" data-action="modal-close">取消</button>
      <button class="btn" data-action="modal-save" ${deviceCardId ? `data-dev="${esc(deviceCardId)}"` : ''} ${isEdit ? `data-edit="${esc(c.id)}"` : ''}>保存</button>
    </div>`);
}

/* ────────────────────────── 动作 ────────────────────────── */
document.addEventListener('click', async e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const { action, id } = btn.dataset;
  try {
    if (action === 'rescan') {
      const r = await api('/api/rescan', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'diagnose') {
      const info = await api('/api/diagnose');
      openModal(`<h3>运行环境自检</h3>
        <p class="hint">检测不到卡时，把以下内容完整复制（鼠标全选 / Ctrl+A → Ctrl+C）发给维护者，即可定位问题。</p>
        <pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;line-height:1.5;background:#0d1014;border:1px solid #262c36;border-radius:8px;padding:10px;max-height:52vh;overflow:auto">${esc(JSON.stringify(info, null, 2))}</pre>
        <div class="foot"><button class="btn ghost" data-action="modal-close">关闭</button></div>`);
    } else if (action === 'register-dev') {
      const dev = (SNAP.devices || []).find(d => d.card_id === id);
      openCardModal(null, id);
      if (dev) {
        $('#m-alias').value = dev.label || dev.display.split(' · ')[0] || '存储卡';
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
      if (!body.alias) { toast('请填写卡片别名', 'err'); return; }
      if (edit) {
        await api('/api/cards/' + encodeURIComponent(edit), { method: 'PATCH', body: JSON.stringify(body) });
        toast('已保存');
      } else {
        body.card_id = dev;
        const r = await api('/api/cards', { method: 'POST', body: JSON.stringify(body) });
        toast(r.message || '已注册');
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
        toast(c.enabled ? '已停用（插入时不再自动备份）' : '已启用');
      }
    } else if (action === 'reset-index') {
      if (!confirm('清除该卡的增量索引？\n\n下次插入时会重新逐文件比对（不会删除或覆盖已备份的文件），耗时较长。')) return;
      const r = await api('/api/cards/' + encodeURIComponent(id) + '/reset-index', { method: 'POST' });
      toast(r.message, 'info');
    } else if (action === 'del-card') {
      if (!confirm('移除这张卡的注册记录？\n\n已备份的文件会保留在磁盘上；再次插入该卡将不再自动备份。')) return;
      const r = await api('/api/cards/' + encodeURIComponent(id), { method: 'DELETE' });
      toast(r.message, 'info');
    } else if (action === 'task-errors') {
      const t = await api('/api/tasks/' + id);
      const rows = (t.errors || []).map(x =>
        `<tr><td class="mono">${esc(x.relpath)}</td><td>${esc(x.message)}</td></tr>`).join('');
      openModal(`<h3>任务 #${t.id} 错误详情</h3>
        ${rows ? `<table><thead><tr><th>文件</th><th>错误</th></tr></thead><tbody>${rows}</tbody></table>`
               : '<div class="empty">没有记录到错误明细。</div>'}
        <div class="foot"><button class="btn ghost" data-action="modal-close">关闭</button></div>`);
    } else if (action === 'eject-card') {
      const r = await api('/api/cards/' + encodeURIComponent(id) + '/unmount', { method: 'POST' });
      toast(r.message, 'info');
    }
  } catch (err) {
    toast(err.message || String(err), 'err');
  }
});

/* 设置表单 */
document.addEventListener('DOMContentLoaded', () => {
  const form = $('#settings-form');
  form.addEventListener('input', () => {
    settingsDirty = true;
    $('#settings-status').textContent = '有未保存的修改';
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
      };
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
      settingsDirty = false;
      $('#settings-status').textContent = '已保存 ' + fmtTime(Date.now() / 1000);
      toast('设置已保存');
    } catch (err) {
      toast(err.message || String(err), 'err');
    }
  });

  // 标签页
  document.querySelectorAll('.tab').forEach(b => {
    b.addEventListener('click', () => switchTab(b.dataset.tab));
  });
  switchTab(localStorage.getItem('sdbak.tab') || 'dash');

  connect();
});

function switchTab(t) {
  localStorage.setItem('sdbak.tab', t);
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t));
}
