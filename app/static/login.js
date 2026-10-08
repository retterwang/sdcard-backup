'use strict';
/* 登录页逻辑：校验会话 → 提交账号密码 → 跳转控制台。
 * 文案全部走 i18n.js 的 T()，与主界面共用语言设置（localStorage: sdbak.lang）。
 */

const $ = (s, el) => (el || document).querySelector(s);

function updateLangButtons() {
  const cur = getLang();
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === cur);
  });
}

window.onLangChanged = () => {
  updateLangButtons();
  document.title = T('auth.title');
};

/* 已登录则直接进入控制台（避免重复登录；该接口未登录时返回 200 + user:null） */
async function skipIfSignedIn() {
  try {
    const r = await fetch('/api/auth/state', { headers: { 'Accept': 'application/json' } });
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.user) location.replace('/');
  } catch (e) { /* 后端未就绪时停留在登录页 */ }
}

document.addEventListener('DOMContentLoaded', () => {
  applyStaticI18n();
  document.title = T('auth.title');
  updateLangButtons();
  document.querySelectorAll('.lang-btn').forEach(b => {
    b.addEventListener('click', () => setLang(b.dataset.lang));
  });

  const form = $('#login-form');
  const btn = $('#login-submit');
  const errEl = $('#login-error');

  form.addEventListener('submit', async e => {
    e.preventDefault();
    errEl.textContent = '';
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = T('auth.signing_in');
    try {
      const r = await fetch('/api/auth/login?lang=' + getLang(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: $('#username').value.trim(),
          password: $('#password').value,
        }),
      });
      let data = {};
      try { data = await r.json(); } catch (e2) { /* 空响应 */ }
      if (!r.ok) {
        throw new Error((data && data.detail) || T('toast.request_failed', { status: r.status }));
      }
      location.replace('/');
      return;
    } catch (err) {
      errEl.textContent = err.message || String(err);
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  skipIfSignedIn();
});
