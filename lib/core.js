// ClipOnce account core: users, password hashing, signed session cookies, credits.
const crypto = require('crypto');

const PLANS = {
  free:    { name: 'Free',    minutes: 30 },
  creator: { name: 'Creator', minutes: 200 },
  pro:     { name: 'Pro',     minutes: 500 },
  agency:  { name: 'Agency',  minutes: 1500 },
  owner:   { name: 'Owner',   minutes: 100000 },
};

function isOwner(email) {
  return String(process.env.OWNER_EMAILS || '').toLowerCase().split(',').map(e => e.trim()).filter(Boolean).includes(String(email || '').toLowerCase());
}
async function ensureOwner(q, u) {
  if (!u || u.plan === 'owner' || !isOwner(u.email)) return u;
  const rows = await q("update co_users set plan = 'owner', credits_min = $2, credits_reset_at = now() + interval '30 days' where id = $1 returning *", [u.id, PLANS.owner.minutes]);
  await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [u.id, PLANS.owner.minutes, 'owner access']);
  return rows[0];
}

const SCHEMA = `
create table if not exists co_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  pass_hash text not null,
  plan text not null default 'free',
  credits_min integer not null default 30,
  credits_reset_at timestamptz not null default now() + interval '30 days',
  stripe_customer text,
  created_at timestamptz not null default now()
);
create table if not exists co_credit_log (
  id bigserial primary key,
  user_id uuid not null references co_users(id) on delete cascade,
  delta_min integer not null,
  reason text not null,
  created_at timestamptz not null default now()
);`;

let migrated = false;
async function migrate(q) {
  if (migrated) return;
  for (const stmt of SCHEMA.split(';').map(s => s.trim()).filter(Boolean)) await q(stmt);
  migrated = true;
}

function hashPassword(pw) {
  const salt = crypto.randomBytes(16);
  const key = crypto.scryptSync(pw, salt, 32, { N: 16384, r: 8, p: 1 });
  return 'scrypt$' + salt.toString('base64') + '$' + key.toString('base64');
}
function checkPassword(pw, stored) {
  const [alg, s, k] = String(stored).split('$');
  if (alg !== 'scrypt' || !s || !k) return false;
  const key = crypto.scryptSync(pw, Buffer.from(s, 'base64'), 32, { N: 16384, r: 8, p: 1 });
  const want = Buffer.from(k, 'base64');
  return want.length === key.length && crypto.timingSafeEqual(want, key);
}

function secret() {
  const s = process.env.SESSION_SECRET;
  if (!s || s.length < 32) throw new Error('SESSION_SECRET missing');
  return s;
}
function sign(payload) {
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const mac = crypto.createHmac('sha256', secret()).update(body).digest('base64url');
  return body + '.' + mac;
}
function verify(token) {
  if (!token || token.indexOf('.') < 0) return null;
  const [body, mac] = token.split('.');
  const want = crypto.createHmac('sha256', secret()).update(body).digest('base64url');
  if (mac.length !== want.length || !crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(want))) return null;
  try {
    const p = JSON.parse(Buffer.from(body, 'base64url').toString());
    if (!p.exp || p.exp < Date.now()) return null;
    return p;
  } catch (e) { return null; }
}

const COOKIE = 'co_session';
function sessionCookie(userId) {
  const exp = Date.now() + 30 * 864e5;
  return COOKIE + '=' + sign({ uid: userId, exp }) + '; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=' + 30 * 86400;
}
function clearCookie() { return COOKIE + '=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0'; }
function readSession(req) {
  const raw = req.headers.cookie || '';
  const m = raw.split(/;\s*/).find(c => c.startsWith(COOKIE + '='));
  return m ? verify(m.slice(COOKIE.length + 1)) : null;
}

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,24}$/;

function publicUser(u) {
  return {
    id: u.id, email: u.email, plan: u.plan,
    planName: (PLANS[u.plan] || PLANS.free).name,
    credits: u.credits_min,
    planMinutes: (PLANS[u.plan] || PLANS.free).minutes,
    resetsAt: u.credits_reset_at,
  };
}

async function signup(q, email, password) {
  email = String(email || '').trim().toLowerCase();
  password = String(password || '');
  if (!EMAIL_RE.test(email)) return { status: 400, error: 'Enter a valid email address.' };
  if (password.length < 8) return { status: 400, error: 'Use at least 8 characters for your password.' };
  if (password.length > 200) return { status: 400, error: 'That password is too long.' };
  await migrate(q);
  const exists = await q('select 1 from co_users where email = $1', [email]);
  if (exists.length) return { status: 409, error: 'An account with that email already exists. Log in instead.' };
  const rows = await q('insert into co_users (email, pass_hash) values ($1, $2) returning *', [email, hashPassword(password)]);
  await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [rows[0].id, PLANS.free.minutes, 'signup']);
  return { status: 200, user: await ensureOwner(q, rows[0]) };
}

async function login(q, email, password) {
  email = String(email || '').trim().toLowerCase();
  await migrate(q);
  const rows = await q('select * from co_users where email = $1', [email]);
  if (!rows.length || !checkPassword(String(password || ''), rows[0].pass_hash))
    return { status: 401, error: 'Wrong email or password.' };
  return { status: 200, user: await ensureOwner(q, rows[0]) };
}

async function me(q, uid) {
  await migrate(q);
  let rows = await q('select * from co_users where id = $1', [uid]);
  if (!rows.length) return null;
  const u = await ensureOwner(q, rows[0]);
  rows = [u];
  if (new Date(u.credits_reset_at) < new Date()) {
    const mins = (PLANS[u.plan] || PLANS.free).minutes;
    rows = await q("update co_users set credits_min = $2, credits_reset_at = now() + interval '30 days' where id = $1 returning *", [uid, mins]);
    await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [uid, mins, 'monthly reset']);
  }
  return rows[0];
}

module.exports = { PLANS, migrate, hashPassword, checkPassword, sign, verify, sessionCookie, clearCookie, readSession, publicUser, signup, login, me, isOwner, _reset: () => { migrated = false; } };
