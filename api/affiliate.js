// Creator Partner API.
// Public: GET ?action=check&code=  POST ?action=click {code}
// Logged in: GET ?action=me  POST ?action=join|payout|code
// Owner: GET ?action=owner  POST ?action=paid {id, amount_cents, note}
const q = require('../lib/db');
const core = require('../lib/core');
const aff = require('../lib/affiliate');

function send(res, status, body) {
  res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

module.exports = async (req, res) => {
  const action = (req.query && req.query.action) || 'me';
  try {
    if (req.method === 'GET' && action === 'check') {
      const c = await aff.lookup(q, req.query.code);
      return send(res, 200, { valid: !!c, creator: c, rules: { fanSignupBonus: aff.RULES.fanSignupBonus } });
    }
    const origin = req.headers.origin || '';
    if (req.method === 'POST' && origin) { let h = ''; try { h = new URL(origin).host; } catch (e) {} if (h !== req.headers.host) return send(res, 403, { error: 'Bad origin' }); }
    const b = typeof req.body === 'object' && req.body ? req.body : {};
    if (req.method === 'POST' && action === 'click') { await aff.click(q, b.code); return send(res, 200, { ok: true }); }

    const s = core.readSession(req);
    if (!s) return send(res, 401, { error: 'Log in first.', login: true });
    const u = await core.me(q, s.uid);
    if (!u) return send(res, 401, { error: 'Log in first.', login: true });
    const isOwner = u.plan === 'owner' || core.isOwner(u.email);

    if (req.method === 'GET' && action === 'me') return send(res, 200, await aff.mine(q, u));
    if (req.method === 'GET' && action === 'owner') {
      if (!isOwner) return send(res, 403, { error: 'Owner only.' });
      return send(res, 200, await aff.owner(q));
    }
    if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed' });
    let r;
    if (action === 'join') r = await aff.join(q, u, b);
    else if (action === 'payout') r = await aff.setPayout(q, u, b.email);
    else if (action === 'code') r = await aff.setCode(q, u, b.code);
    else if (action === 'paid') {
      if (!isOwner) return send(res, 403, { error: 'Owner only.' });
      r = await aff.recordPayout(q, String(b.id || ''), b.amount_cents, b.note);
      if (!r.error) return send(res, 200, { ok: true, ...(await aff.owner(q)) });
    } else return send(res, 404, { error: 'Unknown action' });
    if (r.error) return send(res, r.status || 400, { error: r.error });
    return send(res, 200, await aff.mine(q, await core.me(q, u.id)));
  } catch (e) {
    console.error('affiliate error', e && e.message);
    return send(res, 500, { error: 'Something went wrong. Try again in a moment.' });
  }
};
