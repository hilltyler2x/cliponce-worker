// Brands: one ClipOnce login can run several sets of social accounts ("brands"), each with its own
// sign-ins, daily caps, posting hours, creators to follow and duplicate ledger.
// Social tokens are stored encrypted (AES-256-GCM, key derived from SESSION_SECRET) and only ever
// leave the server as short-lived access tokens for the cloud worker.
const crypto = require('crypto');
const core = require('./core');

const PLATFORMS = ['tiktok', 'youtube', 'instagram', 'facebook', 'threads', 'x'];
const LABELS = { tiktok: 'TikTok', youtube: 'YouTube Shorts', instagram: 'Instagram Reels', facebook: 'Facebook Reels', threads: 'Threads', x: 'X' };
// Tyler's safe daily mix (recommended ranges). Going above shows a warning, the user decides.
const RECOMMENDED = { tiktok: [3, 4], youtube: [3, 5], instagram: [2, 3], facebook: [2, 3], threads: [5, 8], x: [5, 8] };
const DEFAULT_CAPS = { tiktok: 3, youtube: 3, instagram: 2, facebook: 2, threads: 5, x: 5 };
// The owner's first brand keeps the cloud keys it already posts with (GitHub secrets) and today's caps.
const LEGACY_CAPS = { tiktok: 0, youtube: 0, instagram: 3, facebook: 3, threads: 8, x: 0 };
const INCLUDED = { free: 1, creator: 1, pro: 2, agency: 5, owner: 50 };
const MAX_CAP = 20;

const SCHEMA = `
create table if not exists co_brands (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references co_users(id) on delete cascade,
  name text not null,
  legacy boolean not null default false,
  game text not null default '',
  creators text not null default '',
  caps jsonb not null default '{}'::jsonb,
  post_start integer not null default 10,
  post_end integer not null default 22,
  paused boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists co_brands_user on co_brands(user_id);
create table if not exists co_brand_links (
  brand_id uuid not null references co_brands(id) on delete cascade,
  platform text not null,
  handle text not null default '',
  token_enc text,
  refresh_enc text,
  expires_at timestamptz,
  meta jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (brand_id, platform)
);
create table if not exists co_addons (
  sub_id text primary key,
  user_id uuid references co_users(id) on delete cascade,
  customer text,
  qty integer not null default 1,
  active boolean not null default true,
  updated_at timestamptz not null default now()
);
alter table co_users add column if not exists active_brand uuid`;

let ready = false;
async function migrate(q) {
  if (ready) return;
  await core.migrate(q);
  for (const s of SCHEMA.split(';').map(x => x.trim()).filter(Boolean)) await q(s);
  ready = true;
}

function key() {
  return crypto.createHash('sha256').update('cliponce-social-tokens:' + (process.env.TOKEN_KEY || process.env.SESSION_SECRET || '')).digest();
}
function enc(text) {
  if (!text) return null;
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', key(), iv);
  const ct = Buffer.concat([c.update(String(text), 'utf8'), c.final()]);
  return 'v1:' + Buffer.concat([iv, c.getAuthTag(), ct]).toString('base64');
}
function dec(blob) {
  if (!blob || !String(blob).startsWith('v1:')) return null;
  const b = Buffer.from(String(blob).slice(3), 'base64');
  const d = crypto.createDecipheriv('aes-256-gcm', key(), b.subarray(0, 12));
  d.setAuthTag(b.subarray(12, 28));
  return Buffer.concat([d.update(b.subarray(28)), d.final()]).toString('utf8');
}

async function extraBrands(q, uid) {
  const r = await q('select coalesce(sum(qty), 0)::int n from co_addons where user_id = $1 and active', [uid]);
  return r[0].n;
}
async function limits(q, u) {
  const included = INCLUDED[u.plan] || 1;
  const extra = u.plan === 'free' ? 0 : await extraBrands(q, u.id);
  return { included, extra, max: included + extra, addonPrice: 9, addonLink: process.env.BRAND_ADDON_LINK || '', canBuy: u.plan !== 'free' && u.plan !== 'owner' };
}

function cleanCaps(caps, base) {
  const out = { ...base };
  for (const p of PLATFORMS) {
    const v = caps && caps[p];
    if (v !== undefined && v !== null && v !== '') out[p] = Math.max(0, Math.min(MAX_CAP, parseInt(v, 10) || 0));
  }
  return out;
}

// Every login has at least one brand. The owner's first brand is the existing DailyGTA6Info setup.
async function ensureFirst(q, u) {
  const rows = await q('select * from co_brands where user_id = $1 order by created_at', [u.id]);
  if (rows.length) return rows;
  const legacy = u.plan === 'owner';
  await q('insert into co_brands (user_id, name, legacy, game, caps) values ($1, $2, $3, $4, $5)',
    [u.id, legacy ? 'DailyGTA6Info' : 'My brand', legacy, legacy ? 'Grand Theft Auto VI' : '', JSON.stringify(legacy ? LEGACY_CAPS : DEFAULT_CAPS)]);
  return q('select * from co_brands where user_id = $1 order by created_at', [u.id]);
}

const META_APP_ID = process.env.META_APP_ID || '1146205954450083'; // ClipOnce Meta app
const META_CONFIG_ID = process.env.META_CONFIG_ID || '1439322461700423'; // Facebook Login for Business config "ClipOnce Connect" (user token, IG + Page publish)
const THREADS_APP_ID = process.env.THREADS_APP_ID || '1004231106087752'; // Threads app ID of the same Meta app
function oauthReady() {
  const e = process.env, meta = !!(META_APP_ID && e.META_APP_SECRET);
  return { youtube: !!(e.GOOGLE_CLIENT_ID && e.GOOGLE_CLIENT_SECRET),
           tiktok: !!(e.TIKTOK_CLIENT_KEY && e.TIKTOK_CLIENT_SECRET),
           instagram: meta, facebook: meta,
           threads: !!(THREADS_APP_ID && e.THREADS_APP_SECRET),
           x: !!(e.X_CLIENT_ID && e.X_CLIENT_SECRET) };
}
// What the owner still has to paste into Vercel (or do in the platform's dashboard) for each Connect button.
const SETUP = {
  youtube: 'Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Vercel (Google Cloud, OAuth web client).',
  tiktok: 'Needs TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in Vercel (TikTok developer app).',
  instagram: 'Needs META_APP_SECRET in Vercel (Meta app ClipOnce, Settings, Basic).',
  facebook: 'Needs META_APP_SECRET in Vercel (Meta app ClipOnce, Settings, Basic).',
  threads: 'Needs THREADS_APP_SECRET in Vercel (Meta app ClipOnce, Threads use case, Settings).',
  x: 'Needs X_CLIENT_ID and X_CLIENT_SECRET in Vercel (X developer app, OAuth 2.0).',
};
// Honest note on what still waits on a platform review after connecting.
const REVIEW = {
  youtube: 'Posts stay private until Google audits ClipOnce',
  tiktok: 'Posts stay private until TikTok audits ClipOnce',
};

function publicBrand(b, links) {
  const mine = links.filter(l => l.brand_id === b.id);
  const ready = oauthReady();
  const conn = {};
  for (const p of PLATFORMS) {
    const l = mine.find(x => x.platform === p);
    if (b.legacy && ['instagram', 'facebook', 'threads'].includes(p)) conn[p] = { state: 'cloud', handle: '@dailygta6info', note: 'Posting with your saved cloud keys' };
    else if (l && (l.token_enc || l.refresh_enc)) conn[p] = { state: 'connected', handle: l.handle, note: REVIEW[p] || (l.meta && l.meta.note) || '' };
    else conn[p] = { state: ready[p] ? 'connect' : 'setup', note: ready[p] ? (REVIEW[p] || '') : '', setup: SETUP[p] };
  }
  return { id: b.id, name: b.name, legacy: b.legacy, game: b.game, creators: b.creators, caps: cleanCaps(b.caps, DEFAULT_CAPS),
    postStart: b.post_start, postEnd: b.post_end, paused: b.paused, connections: conn, createdAt: b.created_at };
}

async function list(q, u) {
  await migrate(q);
  const brands = await ensureFirst(q, u);
  const ids = brands.map(b => b.id);
  const links = await q('select brand_id, platform, handle, token_enc is not null as has_t, refresh_enc is not null as has_r, meta from co_brand_links where brand_id = any($1::uuid[])', [ids]);
  const ls = links.map(l => ({ ...l, token_enc: l.has_t, refresh_enc: l.has_r }));
  const active = ids.includes(u.active_brand) ? u.active_brand : ids[0];
  return { brands: brands.map(b => publicBrand(b, ls)), active, limits: await limits(q, u), recommended: RECOMMENDED, labels: LABELS, platforms: PLATFORMS };
}

async function owned(q, u, id) {
  if (!/^[0-9a-f-]{36}$/i.test(String(id || ''))) return null;
  const r = await q('select * from co_brands where id = $1 and user_id = $2', [id, u.id]);
  return r[0] || null;
}

async function create(q, u, body) {
  await migrate(q);
  const brands = await ensureFirst(q, u);
  const lim = await limits(q, u);
  if (brands.length >= lim.max) {
    return { status: 402, error: u.plan === 'free'
      ? 'Free accounts have one brand. Pro includes 2 brands, and you can add more for $9 a month each.'
      : `Your plan includes ${lim.max} brand${lim.max === 1 ? '' : 's'}. Add another for $9 a month.`, needAddon: lim.canBuy };
  }
  const name = String(body.name || '').trim().slice(0, 40) || 'Brand ' + (brands.length + 1);
  const r = await q('insert into co_brands (user_id, name, game, creators, caps) values ($1, $2, $3, $4, $5) returning id',
    [u.id, name, String(body.game || '').trim().slice(0, 80), String(body.creators || '').slice(0, 400), JSON.stringify(DEFAULT_CAPS)]);
  await q('update co_users set active_brand = $2 where id = $1', [u.id, r[0].id]);
  return { status: 200, id: r[0].id };
}

async function update(q, u, body) {
  await migrate(q);
  const b = await owned(q, u, body.id);
  if (!b) return { status: 404, error: 'Brand not found.' };
  const name = body.name !== undefined ? String(body.name).trim().slice(0, 40) || b.name : b.name;
  const game = body.game !== undefined ? String(body.game).trim().slice(0, 80) : b.game;
  const creators = body.creators !== undefined ? String(body.creators).split(/[\s,]+/).map(s => s.replace(/^@/, '').replace(/[^A-Za-z0-9_]/g, '')).filter(Boolean).slice(0, 25).join(', ') : b.creators;
  const caps = body.caps ? cleanCaps(body.caps, cleanCaps(b.caps, DEFAULT_CAPS)) : b.caps;
  const hour = (v, d) => (v === undefined ? d : Math.max(0, Math.min(24, parseInt(v, 10) || 0)));
  let ps = hour(body.postStart, b.post_start), pe = hour(body.postEnd, b.post_end);
  if (pe <= ps) pe = Math.min(24, ps + 1);
  const paused = body.paused !== undefined ? !!body.paused : b.paused;
  await q('update co_brands set name = $2, game = $3, creators = $4, caps = $5, post_start = $6, post_end = $7, paused = $8 where id = $1',
    [b.id, name, game, creators, JSON.stringify(caps), ps, pe, paused]);
  return { status: 200 };
}

async function remove(q, u, body) {
  await migrate(q);
  const b = await owned(q, u, body.id);
  if (!b) return { status: 404, error: 'Brand not found.' };
  if (b.legacy) return { status: 400, error: 'This is your main brand with your saved cloud keys, so it cannot be deleted here.' };
  const n = await q('select count(*)::int n from co_brands where user_id = $1', [u.id]);
  if (n[0].n <= 1) return { status: 400, error: 'You need at least one brand.' };
  await q('delete from co_brands where id = $1', [b.id]);
  return { status: 200 };
}

async function setActive(q, u, body) {
  await migrate(q);
  const b = await owned(q, u, body.id);
  if (!b) return { status: 404, error: 'Brand not found.' };
  await q('update co_users set active_brand = $2 where id = $1', [u.id, b.id]);
  return { status: 200 };
}

async function disconnect(q, u, body) {
  await migrate(q);
  const b = await owned(q, u, body.id);
  if (!b || !PLATFORMS.includes(body.platform)) return { status: 404, error: 'Not found.' };
  await q('delete from co_brand_links where brand_id = $1 and platform = $2', [b.id, body.platform]);
  return { status: 200 };
}

async function saveLink(q, brandId, platform, { handle, token, refresh, expiresIn, meta }) {
  await q(`insert into co_brand_links (brand_id, platform, handle, token_enc, refresh_enc, expires_at, meta, updated_at)
    values ($1, $2, $3, $4, $5, $6, $7, now())
    on conflict (brand_id, platform) do update set handle = excluded.handle, token_enc = excluded.token_enc,
      refresh_enc = coalesce(excluded.refresh_enc, co_brand_links.refresh_enc), expires_at = excluded.expires_at, meta = excluded.meta, updated_at = now()`,
    [brandId, platform, String(handle || '').slice(0, 80), enc(token), enc(refresh), expiresIn ? new Date(Date.now() + expiresIn * 1000) : null, JSON.stringify(meta || {})]);
}

// Fresh access token for the worker. Refresh tokens and app secrets never leave this server.
async function accessToken(q, l) {
  if (l.platform === 'instagram' || l.platform === 'facebook') return { token: dec(l.token_enc) };
  const fresh = l.platform !== 'threads' && l.expires_at && new Date(l.expires_at) - Date.now() > 40 * 60 * 1000;
  if (fresh && l.token_enc) return { token: dec(l.token_enc) };
  const refresh = dec(l.refresh_enc);
  if (!refresh && l.platform !== 'threads') return l.token_enc ? { token: dec(l.token_enc) } : { error: 'no sign-in saved' };
  let r;
  if (l.platform === 'youtube') {
    r = await fetch('https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_id: process.env.GOOGLE_CLIENT_ID, client_secret: process.env.GOOGLE_CLIENT_SECRET, refresh_token: refresh, grant_type: 'refresh_token' }) }).then(x => x.json());
  } else if (l.platform === 'tiktok') {
    r = await fetch('https://open.tiktokapis.com/v2/oauth/token/', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_key: process.env.TIKTOK_CLIENT_KEY, client_secret: process.env.TIKTOK_CLIENT_SECRET, refresh_token: refresh, grant_type: 'refresh_token' }) }).then(x => x.json());
  } else if (l.platform === 'threads') {
    const cur = dec(l.token_enc);
    if (l.expires_at && new Date(l.expires_at) - Date.now() > 7 * 864e5) return { token: cur };
    r = await fetch('https://graph.threads.net/refresh_access_token?' + new URLSearchParams({ grant_type: 'th_refresh_token', access_token: cur })).then(x => x.json());
    if (!r.access_token) return cur ? { token: cur } : { error: 'Threads sign-in expired, connect again' };
    await q('update co_brand_links set token_enc = $3, expires_at = $4, updated_at = now() where brand_id = $1 and platform = $2',
      [l.brand_id, l.platform, enc(r.access_token), new Date(Date.now() + (r.expires_in || 5184000) * 1000)]);
    return { token: r.access_token };
  } else if (l.platform === 'x') {
    r = await fetch('https://api.x.com/2/oauth2/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded',
      Authorization: 'Basic ' + Buffer.from(process.env.X_CLIENT_ID + ':' + process.env.X_CLIENT_SECRET).toString('base64') },
      body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: refresh, client_id: process.env.X_CLIENT_ID }) }).then(x => x.json());
  } else return l.token_enc ? { token: dec(l.token_enc) } : { error: 'unsupported' };
  if (!r || !r.access_token) return { error: 'refresh failed: ' + String((r && (r.error_description || r.error)) || 'unknown').slice(0, 120) };
  await q('update co_brand_links set token_enc = $3, refresh_enc = coalesce($4, refresh_enc), expires_at = $5, updated_at = now() where brand_id = $1 and platform = $2',
    [l.brand_id, l.platform, enc(r.access_token), r.refresh_token ? enc(r.refresh_token) : null, new Date(Date.now() + (r.expires_in || 3600) * 1000)]);
  return { token: r.access_token };
}

// What the cloud worker needs to clip and post for every brand. Only the owner's brands post until the
// platforms approve ClipOnce for public use (set ALL_BRANDS_POST=1 in Vercel then).
async function forWorker(q) {
  await migrate(q);
  const brands = await q(`select b.*, u.plan from co_brands b join co_users u on u.id = b.user_id
    where ${process.env.ALL_BRANDS_POST === '1' ? "u.plan <> 'free'" : "u.plan = 'owner'"} order by b.created_at`);
  const out = [], seen = {}, maxBy = {};
  for (const b of brands) {
    // Brands past a lapsed add-on stay saved but stop posting until the slot is paid again.
    if (!(b.user_id in maxBy)) maxBy[b.user_id] = (await limits(q, { id: b.user_id, plan: b.plan })).max;
    seen[b.user_id] = (seen[b.user_id] || 0) + 1;
    if (seen[b.user_id] > maxBy[b.user_id]) continue;
    const links = await q('select * from co_brand_links where brand_id = $1', [b.id]);
    const conn = {};
    for (const l of links) {
      try { const t = await accessToken(q, l); conn[l.platform] = { handle: l.handle, meta: l.meta, ...t }; }
      catch (e) { conn[l.platform] = { handle: l.handle, error: 'token error' }; }
    }
    out.push({ id: b.id, name: b.name, legacy: b.legacy, game: b.game, creators: b.creators.split(/[\s,]+/).filter(Boolean),
      caps: cleanCaps(b.caps, b.legacy ? LEGACY_CAPS : DEFAULT_CAPS), post_start: b.post_start, post_end: b.post_end, paused: b.paused, links: conn });
  }
  return out;
}

module.exports = { META_APP_ID, META_CONFIG_ID, THREADS_APP_ID, oauthReady, PLATFORMS, LABELS, RECOMMENDED, INCLUDED, migrate, list, create, update, remove, setActive, disconnect, saveLink, forWorker, owned, enc, dec, limits };
