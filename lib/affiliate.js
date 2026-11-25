// ClipOnce Creator Partner program: referral codes, signup bonuses, recurring commissions, payouts.
// Money is read from Stripe with STRIPE_READ_KEY (read-only). Payouts are sent by the owner by hand and recorded here.
const core = require('./core');

const RULES = {
  rate: 0.30,            // 30% of what each referred fan pays
  months: 12,            // for their first 12 months
  holdDays: 30,          // commission waits 30 days (refund window) before it can be paid out
  minPayoutCents: 2000,  // $20 minimum payout
  fanSignupBonus: 60,    // fan who signs up with a code: +60 minutes
  creatorWelcome: 60,    // new creator partner: +60 minutes to clip their own streams
  creatorPerPaid: 100,   // creator: +100 minutes each time a fan becomes a paying subscriber
};
const KINDS = { streamer: 'Streamer', youtuber: 'YouTuber', tiktoker: 'TikToker', other: 'Creator' };
const PAID = ['creator', 'pro', 'agency'];

const SCHEMA = `
alter table co_users add column if not exists referred_by uuid;
alter table co_users add column if not exists ref_code text;
alter table co_users add column if not exists creator_kind text;
alter table co_users add column if not exists fan_paid_bonus_at timestamptz;
create table if not exists co_affiliates (
  user_id uuid primary key references co_users(id) on delete cascade,
  code text not null unique,
  kind text not null default 'other',
  channel text not null default '',
  payout_email text not null default '',
  clicks integer not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists co_commissions (
  invoice_id text primary key,
  affiliate_id uuid not null references co_affiliates(user_id) on delete cascade,
  fan_id uuid not null,
  amount_cents integer not null,
  commission_cents integer not null,
  paid_at timestamptz not null,
  status text not null default 'earned',
  created_at timestamptz not null default now()
);
create table if not exists co_payouts (
  id bigserial primary key,
  affiliate_id uuid not null references co_affiliates(user_id) on delete cascade,
  amount_cents integer not null,
  method text not null default 'paypal',
  note text not null default '',
  created_at timestamptz not null default now()
);
create table if not exists co_meta (k text primary key, v text not null, updated_at timestamptz not null default now())`;

let migrated = false;
async function migrate(q) {
  if (migrated) return;
  await core.migrate(q);
  for (const stmt of SCHEMA.split(';').map(s => s.trim()).filter(Boolean)) await q(stmt);
  migrated = true;
}

function cleanCode(c) { return String(c || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20); }
function handleFrom(channel) {
  let s = String(channel || '').trim();
  if (!/^https?:\/\//i.test(s) && /^[\w.-]+\.[a-z]{2,}\//i.test(s)) s = 'https://' + s;
  try { if (/^https?:\/\//i.test(s)) { const u = new URL(s); s = u.pathname.split('/').filter(Boolean).pop() || u.hostname.split('.')[0]; } } catch (e) {}
  return s.replace(/^@/, '');
}
function cleanChannel(c) { return String(c || '').trim().slice(0, 200); }
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,24}$/;

async function codeFree(q, code) { return !(await q('select 1 from co_affiliates where code = $1', [code])).length; }
async function pickCode(q, wanted, channel, email) {
  const tries = [cleanCode(wanted), cleanCode(handleFrom(channel)), cleanCode(String(email).split('@')[0])].filter(c => c.length >= 3);
  for (const c of tries) if (await codeFree(q, c)) return c;
  const base = (tries[0] || 'CLIP').slice(0, 14);
  for (let i = 0; i < 20; i++) { const c = base + Math.floor(100 + Math.random() * 9900); if (await codeFree(q, c)) return c; }
  return base + Date.now().toString(36).toUpperCase();
}

async function credit(q, uid, mins, reason) {
  await q('update co_users set credits_min = credits_min + $2 where id = $1', [uid, mins]);
  await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [uid, mins, reason]);
}

// Make (or update) a creator partner. Returns { error } or { affiliate }.
async function join(q, u, b) {
  await migrate(q);
  const kind = KINDS[b.kind] ? b.kind : 'other';
  const channel = cleanChannel(b.channel);
  if (channel.length < 2) return { status: 400, error: 'Add your channel link or handle so fans know it is you.' };
  const existing = await q('select * from co_affiliates where user_id = $1', [u.id]);
  if (existing.length) {
    await q('update co_affiliates set kind = $2, channel = $3, active = true where user_id = $1', [u.id, kind, channel]);
    return { affiliate: (await q('select * from co_affiliates where user_id = $1', [u.id]))[0] };
  }
  const wanted = cleanCode(b.code);
  if (b.code && wanted.length < 3) return { status: 400, error: 'Codes need at least 3 letters or numbers.' };
  if (wanted && !(await codeFree(q, wanted))) return { status: 409, error: 'That code is taken. Try another one.' };
  const code = await pickCode(q, wanted, channel, u.email);
  const rows = await q('insert into co_affiliates (user_id, code, kind, channel) values ($1, $2, $3, $4) on conflict (user_id) do nothing returning *', [u.id, code, kind, channel]);
  await q('update co_users set creator_kind = $2 where id = $1', [u.id, kind]);
  if (rows.length) await credit(q, u.id, RULES.creatorWelcome, 'creator partner welcome bonus');
  return { affiliate: rows[0] || (await q('select * from co_affiliates where user_id = $1', [u.id]))[0] };
}

// Runs right after a new account is created. Never throws into signup.
async function onSignup(q, u, b) {
  await migrate(q);
  const out = {};
  const code = cleanCode(b.ref);
  if (code) {
    const a = await q('select a.user_id, u.email from co_affiliates a join co_users u on u.id = a.user_id where a.code = $1 and a.active', [code]);
    if (a.length && a[0].user_id !== u.id && a[0].email !== u.email) {
      await q('update co_users set referred_by = $2, ref_code = $3 where id = $1 and referred_by is null', [u.id, a[0].user_id, code]);
      await credit(q, u.id, RULES.fanSignupBonus, 'signed up with code ' + code);
      out.referred = code;
    }
  }
  if (b.creator && b.creator.kind && b.creator.kind !== 'no') {
    let r = await join(q, u, b.creator);
    if (r.error && b.creator.code) r = await join(q, u, { ...b.creator, code: '' }); // wanted code taken: pick one for them
    if (r.affiliate) out.creator = r.affiliate.code;
  }
  return out;
}

async function lookup(q, code) {
  await migrate(q);
  code = cleanCode(code);
  if (!code) return null;
  const r = await q('select code, kind, channel from co_affiliates where code = $1 and active', [code]);
  return r.length ? { code: r[0].code, kind: KINDS[r[0].kind] || 'Creator', name: handleFrom(r[0].channel) } : null;
}

async function click(q, code) {
  await migrate(q);
  await q('update co_affiliates set clicks = clicks + 1 where code = $1', [cleanCode(code)]);
}

// ---------- Stripe sync (read-only) ----------
async function stripeList(path, params, max = 2000) {
  const key = process.env.STRIPE_READ_KEY;
  const out = [];
  let after = null;
  while (out.length < max) {
    const qs = new URLSearchParams({ limit: '100', ...params, ...(after ? { starting_after: after } : {}) });
    const r = await fetch('https://api.stripe.com/v1/' + path + '?' + qs, { headers: { Authorization: 'Bearer ' + key } });
    const j = await r.json();
    if (!r.ok) throw new Error((j.error && j.error.message) || ('Stripe ' + r.status));
    out.push(...(j.data || []));
    if (!j.has_more || !j.data.length) break;
    after = j.data[j.data.length - 1].id;
  }
  return out;
}

function invoiceBase(inv) {
  const paid = inv.amount_paid || 0;
  const ex = inv.total_excluding_tax != null ? inv.total_excluding_tax : paid;
  return Math.max(0, Math.min(paid, ex));
}

// Turn paid invoices of referred fans into commissions. Safe to run often; throttled to once per 10 minutes.
async function sync(q, force) {
  await migrate(q);
  if (!process.env.STRIPE_READ_KEY) return { ok: false, note: 'STRIPE_READ_KEY missing' };
  const last = await q("select extract(epoch from updated_at) as t from co_meta where k = 'aff_sync'");
  if (!force && last.length && Date.now() / 1000 - Number(last[0].t) < 600) return { ok: true, skipped: true };
  await q("insert into co_meta (k, v) values ('aff_sync', 'running') on conflict (k) do update set v = excluded.v, updated_at = now()");
  const fans = await q('select id, email, stripe_customer, referred_by, plan, fan_paid_bonus_at from co_users where referred_by is not null');
  if (!fans.length) return { ok: true, fans: 0 };
  const byCust = {}, byEmail = {};
  for (const f of fans) { if (f.stripe_customer) byCust[f.stripe_customer] = f; byEmail[String(f.email).toLowerCase()] = f; }
  const since = Math.floor(Date.now() / 1000 - 400 * 86400);
  const invoices = (await stripeList('invoices', { status: 'paid', 'created[gte]': String(since) })).sort((a, b) => a.created - b.created);
  const first = {};
  let added = 0;
  for (const inv of invoices) {
    const f = byCust[inv.customer] || byEmail[String(inv.customer_email || '').toLowerCase()];
    if (!f) continue;
    const base = invoiceBase(inv);
    if (base <= 0) continue;
    const paidAt = (inv.status_transitions && inv.status_transitions.paid_at) || inv.created;
    first[f.id] = first[f.id] || paidAt;
    if (paidAt > first[f.id] + RULES.months * 30.44 * 86400) continue;
    if (!f.stripe_customer && inv.customer) await q('update co_users set stripe_customer = coalesce(stripe_customer, $2) where id = $1', [f.id, inv.customer]);
    const r = await q(`insert into co_commissions (invoice_id, affiliate_id, fan_id, amount_cents, commission_cents, paid_at)
      select $1, $2, $3, $4, $5, to_timestamp($6) where exists (select 1 from co_affiliates where user_id = $2)
      on conflict (invoice_id) do nothing returning invoice_id`, [inv.id, f.referred_by, f.id, base, Math.round(base * RULES.rate), paidAt]);
    if (r.length) {
      added++;
      // First real payment from this fan: fan gets a second month's worth of minutes, creator gets +100.
      const upd = await q('update co_users set fan_paid_bonus_at = now() where id = $1 and fan_paid_bonus_at is null returning plan', [f.id]);
      if (upd.length) {
        const mins = (core.PLANS[upd[0].plan] || core.PLANS.creator).minutes;
        await credit(q, f.id, PAID.includes(upd[0].plan) ? mins : core.PLANS.creator.minutes, 'creator code bonus: double minutes first paid month');
        await credit(q, f.referred_by, RULES.creatorPerPaid, 'a fan subscribed with your code');
      }
    }
  }
  // Refunds: void the commission for a refunded payment (matched by customer, amount and day).
  const refunded = (await stripeList('charges', { 'created[gte]': String(since) })).filter(c => c.amount_refunded > 0);
  for (const c of refunded) {
    const f = byCust[c.customer];
    if (!f) continue;
    await q(`update co_commissions set status = 'void' where fan_id = $1 and status = 'earned' and abs(extract(epoch from paid_at) - $2) < 172800
      and amount_cents <= $3`, [f.id, c.created, c.amount]);
  }
  await q("update co_meta set v = 'ok', updated_at = now() where k = 'aff_sync'");
  return { ok: true, fans: fans.length, added };
}

function money(rows, payouts) {
  const now = Date.now();
  let earned = 0, pending = 0, payable = 0;
  for (const c of rows) {
    if (c.status !== 'earned') continue;
    earned += c.commission_cents;
    if (now - new Date(c.paid_at).getTime() < RULES.holdDays * 864e5) pending += c.commission_cents;
  }
  const paid = payouts.reduce((a, p) => a + p.amount_cents, 0);
  payable = Math.max(0, earned - pending - paid);
  return { earned_cents: earned, pending_cents: pending, paid_cents: paid, payable_cents: payable };
}

function publicRules() {
  return { ratePct: RULES.rate * 100, months: RULES.months, holdDays: RULES.holdDays, minPayoutCents: RULES.minPayoutCents,
    fanSignupBonus: RULES.fanSignupBonus, creatorWelcome: RULES.creatorWelcome, creatorPerPaid: RULES.creatorPerPaid };
}

// Creator's own dashboard.
async function mine(q, u) {
  await migrate(q);
  const a = (await q('select * from co_affiliates where user_id = $1', [u.id]))[0];
  if (!a) return { partner: false, rules: publicRules() };
  try { await sync(q); } catch (e) { console.error('aff sync', e.message); }
  const fans = await q('select id, plan, created_at, fan_paid_bonus_at from co_users where referred_by = $1 order by created_at desc', [u.id]);
  const comm = await q('select * from co_commissions where affiliate_id = $1 order by paid_at desc', [u.id]);
  const pays = await q('select amount_cents, method, note, created_at from co_payouts where affiliate_id = $1 order by created_at desc', [u.id]);
  const paying = new Set(comm.filter(c => c.status === 'earned' && Date.now() - new Date(c.paid_at).getTime() < 40 * 864e5).map(c => c.fan_id));
  return {
    partner: true, rules: publicRules(),
    code: a.code, kind: KINDS[a.kind] || 'Creator', channel: a.channel, payoutEmail: a.payout_email, since: a.created_at,
    stats: {
      clicks: a.clicks, signups: fans.length,
      trials: fans.filter(f => PAID.includes(f.plan) && !f.fan_paid_bonus_at).length,
      paying: paying.size,
      ...money(comm, pays),
    },
    recent: comm.slice(0, 12).map(c => ({ date: c.paid_at, amount_cents: c.amount_cents, commission_cents: c.commission_cents, status: c.status })),
    payouts: pays.slice(0, 12),
  };
}

async function setPayout(q, u, email) {
  await migrate(q);
  email = String(email || '').trim().toLowerCase();
  if (email && !EMAIL_RE.test(email)) return { status: 400, error: 'Enter the email of your PayPal account.' };
  const r = await q('update co_affiliates set payout_email = $2 where user_id = $1 returning user_id', [u.id, email]);
  return r.length ? { ok: true } : { status: 404, error: 'Join the creator program first.' };
}

async function setCode(q, u, code) {
  await migrate(q);
  code = cleanCode(code);
  if (code.length < 3) return { status: 400, error: 'Codes need at least 3 letters or numbers.' };
  if (!(await codeFree(q, code))) return { status: 409, error: 'That code is taken. Try another one.' };
  const old = await q('select code from co_affiliates where user_id = $1', [u.id]);
  if (!old.length) return { status: 404, error: 'Join the creator program first.' };
  await q('update co_affiliates set code = $2 where user_id = $1', [u.id, code]);
  return { ok: true };
}

// Owner view for earnings.html.
async function owner(q) {
  await migrate(q);
  let syncNote = null;
  try { const s = await sync(q); if (!s.ok) syncNote = s.note; } catch (e) { syncNote = e.message; }
  const affs = await q(`select a.*, u.email,
      (select count(*) from co_users f where f.referred_by = a.user_id)::int as signups
    from co_affiliates a join co_users u on u.id = a.user_id order by a.created_at desc`);
  const comm = await q('select * from co_commissions');
  const pays = await q('select * from co_payouts order by created_at desc');
  const list = affs.map(a => {
    const c = comm.filter(x => x.affiliate_id === a.user_id), p = pays.filter(x => x.affiliate_id === a.user_id);
    const revenue = c.filter(x => x.status === 'earned').reduce((s, x) => s + x.amount_cents, 0);
    return { id: a.user_id, code: a.code, kind: KINDS[a.kind] || 'Creator', channel: a.channel, email: a.email, payoutEmail: a.payout_email,
      clicks: a.clicks, signups: a.signups, payingFans: new Set(c.filter(x => x.status === 'earned').map(x => x.fan_id)).size,
      revenue_cents: revenue, ...money(c, p), active: a.active };
  });
  const tot = k => list.reduce((s, a) => s + (a[k] || 0), 0);
  return {
    rules: publicRules(), syncNote,
    totals: { partners: list.length, clicks: tot('clicks'), signups: tot('signups'), payingFans: tot('payingFans'), revenue_cents: tot('revenue_cents'),
      earned_cents: tot('earned_cents'), pending_cents: tot('pending_cents'), payable_cents: tot('payable_cents'), paid_cents: tot('paid_cents') },
    partners: list,
    payouts: pays.slice(0, 30).map(p => ({ code: (affs.find(a => a.user_id === p.affiliate_id) || {}).code, amount_cents: p.amount_cents, method: p.method, note: p.note, date: p.created_at })),
  };
}

async function recordPayout(q, affiliateId, amountCents, note) {
  await migrate(q);
  const a = await q('select user_id from co_affiliates where user_id = $1', [affiliateId]);
  if (!a.length) return { status: 404, error: 'No such creator.' };
  const comm = await q('select * from co_commissions where affiliate_id = $1', [affiliateId]);
  const pays = await q('select amount_cents from co_payouts where affiliate_id = $1', [affiliateId]);
  const m = money(comm, pays);
  const amt = Math.round(Number(amountCents) || m.payable_cents);
  if (amt <= 0 || amt > m.payable_cents) return { status: 400, error: 'Nothing payable right now for this creator.' };
  await q('insert into co_payouts (affiliate_id, amount_cents, note) values ($1, $2, $3)', [affiliateId, amt, String(note || '').slice(0, 200)]);
  return { ok: true, amount_cents: amt };
}

module.exports = { RULES, KINDS, migrate, join, onSignup, lookup, click, sync, mine, setPayout, setCode, owner, recordPayout, cleanCode, _money: money };
