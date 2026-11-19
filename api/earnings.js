// Owner-only earnings report: ClipOnce Stripe money (read-only) plus DailyGTA6Info audience numbers.
// Access: a logged-in owner account (OWNER_EMAILS), or ?token= matching REPORT_TOKEN (for the daily summary).
// Stripe: STRIPE_READ_KEY must be a RESTRICTED key with Read access only. This file never writes to Stripe.
const crypto = require('crypto');
const core = require('../lib/core');
const q = require('../lib/db');

const STATS = 'https://raw.githubusercontent.com/hilltyler2x/cliponce-worker/main/state/stats/';
const PLAN_BY_AMOUNT = { 1900: 'Creator', 3900: 'Pro', 9900: 'Agency' };

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

function tokenOk(given) {
  const want = process.env.REPORT_TOKEN || '';
  if (!want || !given || given.length !== want.length) return false;
  return crypto.timingSafeEqual(Buffer.from(given), Buffer.from(want));
}

async function isOwner(req) {
  const s = core.readSession(req);
  if (!s) return false;
  const u = await core.me(q, s.uid);
  return !!(u && (u.plan === 'owner' || core.isOwner(u.email)));
}

async function stripeList(path, params, max = 1000) {
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

async function stripeGet(path) {
  const r = await fetch('https://api.stripe.com/v1/' + path, { headers: { Authorization: 'Bearer ' + process.env.STRIPE_READ_KEY } });
  const j = await r.json();
  if (!r.ok) throw new Error((j.error && j.error.message) || ('Stripe ' + r.status));
  return j;
}

function monthly(sub) {
  let cents = 0;
  for (const it of (sub.items && sub.items.data) || []) {
    const p = it.price || {};
    const n = (p.unit_amount || 0) * (it.quantity || 1);
    const iv = (p.recurring && p.recurring.interval) || 'month';
    const c = (p.recurring && p.recurring.interval_count) || 1;
    cents += iv === 'year' ? n / (12 * c) : iv === 'week' ? n * 52 / (12 * c) : iv === 'day' ? n * 365 / (12 * c) : n / c;
  }
  return Math.round(cents);
}

function planName(sub) {
  const it = sub.items && sub.items.data && sub.items.data[0];
  const amt = it && it.price && it.price.unit_amount;
  return PLAN_BY_AMOUNT[amt] || (it && it.price && it.price.nickname) || 'Other';
}

async function money() {
  if (!process.env.STRIPE_READ_KEY) return { connected: false, note: 'Add a read-only Stripe key as STRIPE_READ_KEY in Vercel to see money here.' };
  const now = Date.now() / 1000;
  const [subs, charges, balance] = await Promise.all([
    stripeList('subscriptions', { status: 'all' }),
    stripeList('charges', { 'created[gte]': String(Math.floor(now - 400 * 86400)) }),
    stripeGet('balance').catch(e => ({ error: e.message })),
  ]);
  const plans = {};
  let mrr = 0, trialMrr = 0;
  const counts = { trialing: 0, active: 0, past_due: 0, canceled: 0, other: 0 };
  const upcoming = [];
  for (const s of subs) {
    const st = counts[s.status] !== undefined ? s.status : 'other';
    counts[st]++;
    const m = monthly(s), pn = planName(s);
    plans[pn] = plans[pn] || { trialing: 0, paying: 0 };
    if (s.status === 'trialing') {
      plans[pn].trialing++;
      if (!s.cancel_at_period_end) { trialMrr += m; upcoming.push({ plan: pn, amount: m, date: new Date(s.trial_end * 1000).toISOString().slice(0, 10) }); }
    }
    if (s.status === 'active' || s.status === 'past_due') { plans[pn].paying++; if (!s.cancel_at_period_end) mrr += m; }
  }
  upcoming.sort((a, b) => a.date.localeCompare(b.date));
  const paid = charges.filter(c => c.paid && c.status === 'succeeded');
  const net = c => c.amount - (c.amount_refunded || 0);
  const sum = (since) => paid.filter(c => c.created >= since).reduce((a, c) => a + net(c), 0);
  const startDay = new Date(); startDay.setUTCHours(5, 0, 0, 0); if (startDay.getTime() / 1000 > now) startDay.setUTCDate(startDay.getUTCDate() - 1); // midnight New York (EST)
  const byMonth = {};
  for (const c of paid) { const k = new Date(c.created * 1000).toISOString().slice(0, 7); byMonth[k] = (byMonth[k] || 0) + net(c); }
  const usd = (arr) => ((arr || []).find(b => b.currency === 'usd') || {}).amount || 0;
  return {
    connected: true,
    counts, plans,
    mrr_cents: mrr,
    mrr_after_trials_cents: mrr + trialMrr,
    collected: { today_cents: sum(startDay.getTime() / 1000), last7_cents: sum(now - 7 * 86400), last30_cents: sum(now - 30 * 86400), total_cents: sum(0) },
    by_month: byMonth,
    refunds_cents: charges.reduce((a, c) => a + (c.amount_refunded || 0), 0),
    balance: balance.error ? { note: balance.error } : { available_cents: usd(balance.available), pending_cents: usd(balance.pending) },
    upcoming_charges: upcoming.slice(0, 20),
  };
}

async function audience() {
  try {
    const [l, h] = await Promise.all(['latest.json', 'history.json'].map(f => fetch(STATS + f + '?t=' + Date.now()).then(r => r.ok ? r.json() : null)));
    return { latest: l, history: h };
  } catch (e) { return { latest: null, history: null, note: e.message }; }
}

module.exports = async (req, res) => {
  try {
    const token = (req.query && req.query.token) || req.headers['x-report-token'] || '';
    if (!tokenOk(String(token)) && !(await isOwner(req))) return send(res, 401, { error: 'Owner only. Log in with your owner account.' });
    const [m, a] = await Promise.all([money().catch(e => ({ connected: false, note: 'Stripe: ' + e.message })), audience()]);
    return send(res, 200, { generated_at: new Date().toISOString(), money: m, audience: a });
  } catch (e) {
    console.error('earnings error', e && e.message);
    return send(res, 500, { error: 'Could not build the report right now.' });
  }
};
