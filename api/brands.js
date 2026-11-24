// Brands API: list, add, edit, switch, delete brands and their social sign-ins, plus the worker's read.
const q = require('../lib/db');
const core = require('../lib/core');
const brands = require('../lib/brands');
const { verifyWorker } = require('../lib/oidc');

function send(res, status, body) {
  res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

module.exports = async (req, res) => {
  const action = (req.query && req.query.action) || 'list';
  try {
    if (action === 'worker') {
      const w = await verifyWorker(req);
      if (!w) return send(res, 401, { error: 'worker only' });
      return send(res, 200, { brands: await brands.forWorker(q) });
    }
    const s = core.readSession(req);
    if (!s) return send(res, 401, { error: 'Log in first.', login: true });
    const u = await core.me(q, s.uid);
    if (!u) return send(res, 401, { error: 'Log in first.', login: true });
    if (req.method === 'GET' && action === 'list') return send(res, 200, await brands.list(q, u));
    if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed' });
    const origin = req.headers.origin || '';
    if (origin) { let h = ''; try { h = new URL(origin).host; } catch (e) {} if (h !== req.headers.host) return send(res, 403, { error: 'Bad origin' }); }
    const b = typeof req.body === 'object' && req.body ? req.body : {};
    const fn = { create: brands.create, update: brands.update, delete: brands.remove, switch: brands.setActive, disconnect: brands.disconnect }[action];
    if (!fn) return send(res, 404, { error: 'Unknown action' });
    const r = await fn(q, u, b);
    if (r.error) return send(res, r.status, { error: r.error, needAddon: r.needAddon });
    return send(res, 200, { ok: true, id: r.id, ...(await brands.list(q, await core.me(q, u.id))) });
  } catch (e) {
    console.error('brands error', e && e.message);
    return send(res, 500, { error: 'Something went wrong. Try again in a moment.' });
  }
};
