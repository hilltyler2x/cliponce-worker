// Connect a brand's YouTube or TikTok with the platform's official sign-in (OAuth).
// Needs GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET and TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET in Vercel.
// Redirect URLs to register: https://cliponce.vercel.app/api/connect?p=youtube and ?p=tiktok
const crypto = require('crypto');
const q = require('../lib/db');
const core = require('../lib/core');
const brands = require('../lib/brands');

const BASE = process.env.SITE_URL || 'https://cliponce.vercel.app';
const redirect = p => BASE + '/api/connect?p=' + p;
function go(res, url) { res.statusCode = 302; res.setHeader('Location', url); res.setHeader('Cache-Control', 'no-store'); res.end(); }
const back = (res, msg, ok) => go(res, '/account.html?' + (ok ? 'connected=' : 'connect_error=') + encodeURIComponent(msg));

module.exports = async (req, res) => {
  const p = String((req.query && req.query.p) || '');
  try {
    const s = core.readSession(req);
    if (!s) return go(res, '/account.html');
    const u = await core.me(q, s.uid);
    if (!u) return go(res, '/account.html');
    await brands.migrate(q);
    const query = req.query || {};

    if (!query.code && !query.error) {
      // Step 1: send the user to the platform's sign-in page.
      const b = await brands.owned(q, u, query.brand);
      if (!b) return back(res, 'Pick a brand first.');
      const state = core.sign({ uid: u.id, brand: b.id, p, n: crypto.randomBytes(8).toString('hex'), exp: Date.now() + 15 * 60e3 });
      if (p === 'youtube' && process.env.GOOGLE_CLIENT_ID) {
        return go(res, 'https://accounts.google.com/o/oauth2/v2/auth?' + new URLSearchParams({ client_id: process.env.GOOGLE_CLIENT_ID, redirect_uri: redirect(p),
          response_type: 'code', access_type: 'offline', prompt: 'consent select_account', include_granted_scopes: 'true',
          scope: 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly', state }));
      }
      if (p === 'tiktok' && process.env.TIKTOK_CLIENT_KEY) {
        return go(res, 'https://www.tiktok.com/v2/auth/authorize/?' + new URLSearchParams({ client_key: process.env.TIKTOK_CLIENT_KEY, redirect_uri: redirect(p),
          response_type: 'code', scope: 'user.info.basic,video.publish,video.upload', state }));
      }
      return back(res, 'That sign-in is not switched on yet.');
    }

    // Step 2: the platform sent the user back.
    const st = core.verify(String(query.state || ''));
    if (!st || st.uid !== u.id || st.p !== p) return back(res, 'That sign-in link expired. Try again.');
    if (query.error) return back(res, 'Sign-in was cancelled.');
    const b = await brands.owned(q, u, st.brand);
    if (!b) return back(res, 'Brand not found.');

    if (p === 'youtube') {
      const t = await fetch('https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ code: String(query.code), client_id: process.env.GOOGLE_CLIENT_ID, client_secret: process.env.GOOGLE_CLIENT_SECRET,
          redirect_uri: redirect(p), grant_type: 'authorization_code' }) }).then(r => r.json());
      if (!t.access_token) return back(res, 'YouTube did not accept the sign-in. Try again.');
      const ch = await fetch('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true', { headers: { Authorization: 'Bearer ' + t.access_token } }).then(r => r.json()).catch(() => ({}));
      const c = (ch.items || [])[0];
      if (!c) return back(res, 'That Google account has no YouTube channel.');
      await brands.saveLink(q, b.id, 'youtube', { handle: c.snippet.customUrl || c.snippet.title, token: t.access_token, refresh: t.refresh_token, expiresIn: t.expires_in, meta: { channel_id: c.id, title: c.snippet.title } });
      return back(res, 'YouTube', true);
    }
    if (p === 'tiktok') {
      const t = await fetch('https://open.tiktokapis.com/v2/oauth/token/', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ client_key: process.env.TIKTOK_CLIENT_KEY, client_secret: process.env.TIKTOK_CLIENT_SECRET, code: String(query.code),
          grant_type: 'authorization_code', redirect_uri: redirect(p) }) }).then(r => r.json());
      if (!t.access_token) return back(res, 'TikTok did not accept the sign-in. Try again.');
      const me = await fetch('https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,username', { headers: { Authorization: 'Bearer ' + t.access_token } }).then(r => r.json()).catch(() => ({}));
      const info = (me.data && me.data.user) || {};
      await brands.saveLink(q, b.id, 'tiktok', { handle: info.username ? '@' + info.username : (info.display_name || 'TikTok'), token: t.access_token, refresh: t.refresh_token, expiresIn: t.expires_in, meta: { open_id: t.open_id } });
      return back(res, 'TikTok', true);
    }
    return back(res, 'Unknown platform.');
  } catch (e) {
    console.error('connect error', e && e.message);
    return back(res, 'Something went wrong. Try again in a moment.');
  }
};
