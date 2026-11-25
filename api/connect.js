// Connect a brand's social accounts with each platform's official sign-in (OAuth). One redirect URL for all:
//   https://cliponce.vercel.app/api/connect
// Keys (Vercel env): GOOGLE_CLIENT_ID/SECRET, TIKTOK_CLIENT_KEY/SECRET, META_APP_SECRET (+ META_APP_ID),
// THREADS_APP_ID/SECRET, X_CLIENT_ID/SECRET. A platform without keys shows its setup note instead.
const crypto = require('crypto');
const q = require('../lib/db');
const core = require('../lib/core');
const brands = require('../lib/brands');

const BASE = process.env.SITE_URL || 'https://cliponce.vercel.app';
const REDIRECT = BASE + '/api/connect';
const G = 'https://graph.facebook.com/v21.0';
function go(res, url, cookie) { res.statusCode = 302; if (cookie) res.setHeader('Set-Cookie', cookie); res.setHeader('Location', url); res.setHeader('Cache-Control', 'no-store'); res.end(); }
const back = (res, msg, ok) => go(res, '/account.html?' + (ok ? 'connected=' : 'connect_error=') + encodeURIComponent(msg));
const form = (url, body, headers) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', ...(headers || {}) }, body: new URLSearchParams(body) }).then(r => r.json());
const getJ = (url, headers) => fetch(url, { headers: headers || {} }).then(r => r.json()).catch(() => ({}));
const b64url = b => b.toString('base64url');
function pkceCookie(v) { return 'co_pkce=' + v + '; Path=/api/connect; HttpOnly; Secure; SameSite=Lax; Max-Age=900'; }
function readPkce(req) { const m = String(req.headers.cookie || '').split(/;\s*/).find(c => c.startsWith('co_pkce=')); return m ? m.slice(8) : ''; }

async function start(res, p, state) {
  const e = process.env, ready = brands.oauthReady();
  if (!ready[p]) return back(res, 'That sign-in is not switched on yet.');
  if (p === 'youtube') return go(res, 'https://accounts.google.com/o/oauth2/v2/auth?' + new URLSearchParams({ client_id: e.GOOGLE_CLIENT_ID, redirect_uri: REDIRECT,
    response_type: 'code', access_type: 'offline', prompt: 'consent select_account', include_granted_scopes: 'true',
    scope: 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly', state }));
  if (p === 'tiktok') return go(res, 'https://www.tiktok.com/v2/auth/authorize/?' + new URLSearchParams({ client_key: e.TIKTOK_CLIENT_KEY, redirect_uri: REDIRECT,
    response_type: 'code', scope: 'user.info.basic,video.publish,video.upload', state }));
  if (p === 'instagram' || p === 'facebook') return go(res, 'https://www.facebook.com/v21.0/dialog/oauth?' + new URLSearchParams({ client_id: brands.META_APP_ID,
    redirect_uri: REDIRECT, response_type: 'code', state, config_id: brands.META_CONFIG_ID }));
  if (p === 'threads') return go(res, 'https://threads.net/oauth/authorize?' + new URLSearchParams({ client_id: brands.THREADS_APP_ID, redirect_uri: REDIRECT,
    response_type: 'code', scope: 'threads_basic,threads_content_publish', state }));
  if (p === 'x') {
    const verifier = b64url(crypto.randomBytes(32));
    const challenge = b64url(crypto.createHash('sha256').update(verifier).digest());
    return go(res, 'https://x.com/i/oauth2/authorize?' + new URLSearchParams({ response_type: 'code', client_id: e.X_CLIENT_ID, redirect_uri: REDIRECT,
      scope: 'tweet.read tweet.write users.read media.write offline.access', state, code_challenge: challenge, code_challenge_method: 'S256' }), pkceCookie(verifier));
  }
  return back(res, 'Unknown platform.');
}

async function finish(req, res, p, b, code) {
  const e = process.env;
  if (p === 'youtube') {
    const t = await form('https://oauth2.googleapis.com/token', { code, client_id: e.GOOGLE_CLIENT_ID, client_secret: e.GOOGLE_CLIENT_SECRET, redirect_uri: REDIRECT, grant_type: 'authorization_code' });
    if (!t.access_token) return back(res, 'YouTube did not accept the sign-in. Try again.');
    const ch = await getJ('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true', { Authorization: 'Bearer ' + t.access_token });
    const c = (ch.items || [])[0];
    if (!c) return back(res, 'That Google account has no YouTube channel.');
    await brands.saveLink(q, b.id, 'youtube', { handle: c.snippet.customUrl || c.snippet.title, token: t.access_token, refresh: t.refresh_token, expiresIn: t.expires_in, meta: { channel_id: c.id, title: c.snippet.title } });
    return back(res, 'YouTube', true);
  }
  if (p === 'tiktok') {
    const t = await form('https://open.tiktokapis.com/v2/oauth/token/', { client_key: e.TIKTOK_CLIENT_KEY, client_secret: e.TIKTOK_CLIENT_SECRET, code, grant_type: 'authorization_code', redirect_uri: REDIRECT });
    if (!t.access_token) return back(res, 'TikTok did not accept the sign-in. Try again.');
    const me = await getJ('https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,username', { Authorization: 'Bearer ' + t.access_token });
    const info = (me.data && me.data.user) || {};
    await brands.saveLink(q, b.id, 'tiktok', { handle: info.username ? '@' + info.username : (info.display_name || 'TikTok'), token: t.access_token, refresh: t.refresh_token, expiresIn: t.expires_in, meta: { open_id: t.open_id } });
    return back(res, 'TikTok', true);
  }
  if (p === 'instagram' || p === 'facebook') {
    // One Meta sign-in covers both: the Page posts Facebook Reels, its linked Instagram account posts Reels.
    const t = await getJ(G + '/oauth/access_token?' + new URLSearchParams({ client_id: brands.META_APP_ID, client_secret: e.META_APP_SECRET, redirect_uri: REDIRECT, code }));
    if (!t.access_token) return back(res, 'Meta did not accept the sign-in. Try again.');
    const ll = await getJ(G + '/oauth/access_token?' + new URLSearchParams({ grant_type: 'fb_exchange_token', client_id: brands.META_APP_ID, client_secret: e.META_APP_SECRET, fb_exchange_token: t.access_token }));
    const user = ll.access_token || t.access_token;
    const pages = (await getJ(G + '/me/accounts?' + new URLSearchParams({ fields: 'id,name,access_token,instagram_business_account{id,username}', access_token: user }))).data || [];
    if (!pages.length) return back(res, 'No Facebook Page was shared with ClipOnce. Connect again and pick your Page.');
    const page = pages.find(x => x.instagram_business_account) || pages[0];
    const done = [];
    await brands.saveLink(q, b.id, 'facebook', { handle: page.name, token: page.access_token, meta: { page_id: page.id } });
    done.push('Facebook');
    if (page.instagram_business_account) {
      const ig = page.instagram_business_account;
      await brands.saveLink(q, b.id, 'instagram', { handle: ig.username ? '@' + ig.username : 'Instagram', token: page.access_token, meta: { ig_user_id: ig.id, page_id: page.id } });
      done.push('Instagram');
    } else if (p === 'instagram') {
      return back(res, 'Facebook is connected, but the Page "' + page.name + '" has no Instagram professional account linked. Link one in Instagram settings, then connect again.');
    }
    return back(res, done.join(' and '), true);
  }
  if (p === 'threads') {
    const t = await form('https://graph.threads.net/oauth/access_token', { client_id: brands.THREADS_APP_ID, client_secret: e.THREADS_APP_SECRET, grant_type: 'authorization_code', redirect_uri: REDIRECT, code });
    if (!t.access_token) return back(res, 'Threads did not accept the sign-in. Try again.');
    const ll = await getJ('https://graph.threads.net/access_token?' + new URLSearchParams({ grant_type: 'th_exchange_token', client_secret: e.THREADS_APP_SECRET, access_token: t.access_token }));
    const tok = ll.access_token || t.access_token;
    const me = await getJ('https://graph.threads.net/v1.0/me?' + new URLSearchParams({ fields: 'id,username', access_token: tok }));
    await brands.saveLink(q, b.id, 'threads', { handle: me.username ? '@' + me.username : 'Threads', token: tok, expiresIn: ll.expires_in || 3600, meta: { user_id: me.id || t.user_id } });
    return back(res, 'Threads', true);
  }
  if (p === 'x') {
    const verifier = readPkce(req);
    if (!verifier) return back(res, 'That X sign-in expired. Try again.');
    const t = await form('https://api.x.com/2/oauth2/token', { code, grant_type: 'authorization_code', redirect_uri: REDIRECT, code_verifier: verifier, client_id: e.X_CLIENT_ID },
      { Authorization: 'Basic ' + Buffer.from(e.X_CLIENT_ID + ':' + e.X_CLIENT_SECRET).toString('base64') });
    if (!t.access_token) return back(res, 'X did not accept the sign-in. Try again.');
    const me = await getJ('https://api.x.com/2/users/me', { Authorization: 'Bearer ' + t.access_token });
    await brands.saveLink(q, b.id, 'x', { handle: me.data && me.data.username ? '@' + me.data.username : 'X', token: t.access_token, refresh: t.refresh_token, expiresIn: t.expires_in, meta: { user_id: me.data && me.data.id } });
    return back(res, 'X', true);
  }
  return back(res, 'Unknown platform.');
}

module.exports = async (req, res) => {
  try {
    const s = core.readSession(req);
    if (!s) return go(res, '/account.html');
    const u = await core.me(q, s.uid);
    if (!u) return go(res, '/account.html');
    await brands.migrate(q);
    const query = req.query || {};
    if (!query.code && !query.error && !query.error_description) {
      const p = String(query.p || '');
      if (!brands.PLATFORMS.includes(p)) return back(res, 'Unknown platform.');
      const b = await brands.owned(q, u, query.brand);
      if (!b) return back(res, 'Pick a brand first.');
      const state = core.sign({ uid: u.id, brand: b.id, p, n: crypto.randomBytes(8).toString('hex'), exp: Date.now() + 15 * 60e3 });
      return start(res, p, state);
    }
    const st = core.verify(String(query.state || '').replace(/#_$/, ''));
    if (!st || st.uid !== u.id) return back(res, 'That sign-in link expired. Try again.');
    if (query.error || query.error_description) return back(res, 'Sign-in was cancelled.');
    const b = await brands.owned(q, u, st.brand);
    if (!b) return back(res, 'Brand not found.');
    return finish(req, res, st.p, b, String(query.code).replace(/#_$/, ''));
  } catch (e) {
    console.error('connect error', e && e.message);
    return back(res, 'Something went wrong. Try again in a moment.');
  }
};
