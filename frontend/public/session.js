/* Shared by the question page and workbench. Claim before loading history/sending. */
(() => {
  const key = 'docmind_session_id';
  const locks = navigator.locks;
  const hasLocks = !!locks?.request;
  let id = '', release = null, hidden = false;
  let serial = Promise.resolve();
  const fresh = () => 'web-' + (crypto.randomUUID?.().replaceAll('-', '') ||
    Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)).slice(0, 24);
  function save(value) {
    id = value;
    try { sessionStorage.setItem(key, value); } catch { /* memory only */ }
  }
  function claim(value) {
    return new Promise((resolve, reject) => {
      locks.request('docmind-session:' + value, { ifAvailable: true }, lock => {
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
