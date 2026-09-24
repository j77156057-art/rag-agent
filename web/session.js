/* Shared by the question page and workbench. Claim before loading history/sending.
 *
 * 会话按「页面域」隔离：问答首页（/）与代码工作台（/workbench）各自持有独立的
 * session id（独立存储键、独立 Web Lock）。同标签页在两个页面间跳转不再读到同一
 * 段对话——问答页的提问不会出现在工作台历史里，反之亦然。工作台沿用旧存储键，
 * 已有历史对话无缝保留。 */
(() => {
  // 路径判定不依赖构建产物名：/workbench（含 /workbench/...）= 工作台域，其余=问答域
  const scope = location.pathname.replace(/\/+$/, '').startsWith('/workbench') ? 'wb' : 'ask';
  // wb 保持历史键名（旧会话不丢）；ask 使用独立命名空间
  const key = scope === 'wb' ? 'docmind_session_id' : 'docmind_session_id:ask';
  const lockPrefix = scope === 'wb' ? 'docmind-session:' : 'docmind-session:ask:';
  const locks = navigator.locks;
  const hasLocks = !!locks?.request;
  let id = '', release = null, hidden = false;
  let serial = Promise.resolve();
  // id 前缀即作用域：ask- 问答页 / web- 工作台。会话历史列表据此互不展示
  // （后端按 id 落盘，slug 允许字母数字与 _-.）
  const fresh = () => (scope === 'wb' ? 'web-' : 'ask-') +
    (crypto.randomUUID?.().replaceAll('-', '') ||
    Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)).slice(0, 24);
  function save(value) {
    id = value;
    try { sessionStorage.setItem(key, value); } catch { /* memory only */ }
  }
  function claim(value) {
    return new Promise((resolve, reject) => {
      locks.request(lockPrefix + value, { ifAvailable: true }, lock => {
        if (!lock || hidden) { resolve(null); return; }
        return new Promise(done => { resolve(done); });
      }).catch(reject);
    });
  }
  async function acquire(value, rotate) {
    if (!hasLocks) {
      // Without an atomic browser lock, allocate a new document session. Never
      // silently adopt a copied id based on a throttled heartbeat or TTL.
      if (!rotate) return false;
      save(fresh());
      return true;
    }
    let candidate = value || fresh();
    let unlock = await claim(candidate);
    if (!unlock && rotate && !hidden) { candidate = fresh(); unlock = await claim(candidate); }
    if (!unlock) return false;
    const oldRelease = release;
    release = unlock;
    save(candidate);
    oldRelease?.();
    return true;
  }
  let saved = '';
  try { saved = sessionStorage.getItem(key) || ''; } catch { /* memory only */ }
  const ready = serial = acquire(saved, true).then(ok => {
    if (!ok) throw new Error('无法取得独立会话，请刷新页面重试。');
    return id;
  });
  window.DocMindSession = {
    scope,
    ready,
    get() { return id; },
    set(value) {
      const candidate = String(value || '').trim();
      const next = serial.then(async () => {
        if (!candidate || hidden) return false;
        if (candidate === id) return true;
        return acquire(candidate, false);
      });
      serial = next.catch(() => {});
      return next;
    },
    create() {
      const next = serial.then(async () => await acquire(fresh(), true) ? id : '');
      serial = next.catch(() => {});
      return next;
    },
  };
  window.addEventListener('pagehide', () => { hidden = true; release?.(); release = null; });
  // A BFCache document has released its claim; reload before accepting input.
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
})();
