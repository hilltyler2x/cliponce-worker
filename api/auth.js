const core = require('../lib/core');
const q = require('../lib/db');

function send(res, status, body, cookie) {
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  if (cookie) res.setHeader('Set-Cookie', cookie);
  res.statusCode = status;
  res.end(JSON.stringify(body));
}

module.exports = async (req, res) => {
  const action = (req.query && req.query.action) || '';
  try {
    if (req.method === 'GET' && action === 'me') {
      const s = core.readSession(req);
      if (!s) return send(res, 200, { user: null });
      const u = await core.me(q, s.uid);
      return send(res, 200, { user: u ? core.publicUser(u) : null });
    }
    if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed' });
    const origin = req.headers.origin || '';
    if (origin) { let h = ''; try { h = new URL(origin).host; } catch (e) {} if (h !== req.headers.host) return send(res, 403, { error: 'Bad origin' }); }
    const b = typeof req.body === 'object' && req.body ? req.body : {};
    if (action === 'signup') {
      const r = await core.signup(q, b.email, b.password);
      if (r.error) return send(res, r.status, { error: r.error });
      return send(res, 200, { user: core.publicUser(r.user) }, core.sessionCookie(r.user.id));
    }
    if (action === 'login') {
      const r = await core.login(q, b.email, b.password);
      if (r.error) return send(res, r.status, { error: r.error });
      return send(res, 200, { user: core.publicUser(r.user) }, core.sessionCookie(r.user.id));
    }
    if (action === 'logout') return send(res, 200, { ok: true }, core.clearCookie());
    return send(res, 404, { error: 'Unknown action' });
  } catch (e) {
    console.error('auth error', e && e.message);
    return send(res, 500, { error: 'Something went wrong. Try again in a moment.' });
  }
};
