// Stripe webhook: turns a paid subscription into a plan + minutes on the matching ClipOnce account.
// Needs STRIPE_WEBHOOK_SECRET (whsec_...) in Vercel. Until it is set, every call is refused.
const crypto = require('crypto');
const q = require('../lib/db');
const core = require('../lib/core');
const brands = require('../lib/brands');

let pendingReady = false;
const BY_AMOUNT = { 1900: 'creator', 3900: 'pro', 9900: 'agency' };
const ADDON_AMOUNT = 900; // extra brand, $9 a month each

// Extra-brand add-on subscriptions never change the plan; they only add brand slots.
function addonQty(obj) {
  const items = (obj.items && obj.items.data) || (obj.lines && obj.lines.data) || [];
  const it = items.find(i => i.price && i.price.unit_amount === ADDON_AMOUNT);
  return it ? (it.quantity || 1) : 0;
}
async function setAddon(subId, customer, qty, active, userId) {
  if (!subId) return;
  let uid = userId || null;
  if (!uid && customer) { const r = await q('select id from co_users where stripe_customer = $1 limit 1', [customer]); uid = r.length ? r[0].id : null; }
  await q(`insert into co_addons (sub_id, user_id, customer, qty, active, updated_at) values ($1, $2, $3, $4, $5, now())
    on conflict (sub_id) do update set user_id = coalesce(excluded.user_id, co_addons.user_id), customer = coalesce(excluded.customer, co_addons.customer),
      qty = excluded.qty, active = excluded.active, updated_at = now()`, [subId, uid, customer || null, Math.max(1, qty || 1), !!active]);
}

function rawBody(req) {
  return new Promise((ok, no) => { const c = []; req.on('data', d => c.push(d)); req.on('end', () => ok(Buffer.concat(c))); req.on('error', no); });
}

function verify(buf, header, secret) {
  const parts = Object.fromEntries(String(header || '').split(',').map(p => p.split('=')).filter(p => p.length === 2 && p[0] !== 'v1'));
  const sigs = String(header || '').split(',').filter(p => p.startsWith('v1=')).map(p => p.slice(3));
  const t = parts.t;
  if (!t || !sigs.length || Math.abs(Date.now() / 1000 - Number(t)) > 600) return false;
  const want = crypto.createHmac('sha256', secret).update(t + '.' + buf.toString('utf8')).digest('hex');
  return sigs.some(s => s.length === want.length && crypto.timingSafeEqual(Buffer.from(s), Buffer.from(want)));
}

function planOf(obj) {
  const item = (obj.items && obj.items.data && obj.items.data[0]) || (obj.lines && obj.lines.data && obj.lines.data[0]);
  const amt = (item && item.price && item.price.unit_amount) || obj.amount_total || obj.amount_subtotal;
  return BY_AMOUNT[amt] || null;
}

async function setPlan(userWhere, params, plan, customer, reason) {
  const mins = (core.PLANS[plan] || core.PLANS.free).minutes;
  const rows = await q(`update co_users set plan = $${params.length + 1}, credits_min = ${plan === 'free' ? 'least' : 'greatest'}(credits_min, $${params.length + 2}),
    stripe_customer = coalesce($${params.length + 3}, stripe_customer) where ${userWhere} and plan <> 'owner' returning id`,
    [...params, plan, mins, customer || null]);
  for (const r of rows) await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [r.id, plan === 'free' ? 0 : mins, reason]);
  return rows.length;
}

module.exports = async (req, res) => {
  const send = (s, b) => { res.statusCode = s; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(b)); };
  if (req.method !== 'POST') return send(405, { error: 'POST only' });
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) return send(503, { error: 'Webhook not configured' });
  const buf = await rawBody(req);
  if (!verify(buf, req.headers['stripe-signature'], secret)) return send(400, { error: 'Bad signature' });
  let ev; try { ev = JSON.parse(buf.toString('utf8')); } catch (e) { return send(400, { error: 'Bad JSON' }); }
  try {
    await core.migrate(q);
    if (!pendingReady) { await q('create table if not exists co_pending_plan (customer text primary key, plan text not null, created_at timestamptz not null default now())'); pendingReady = true; }
    const o = ev.data && ev.data.object || {};
    await brands.migrate(q);
    if (ev.type === 'checkout.session.completed' && o.mode === 'subscription' && (o.amount_total === ADDON_AMOUNT || o.amount_subtotal === ADDON_AMOUNT || (o.metadata && o.metadata.addon === 'brand'))) {
      let uid = o.client_reference_id && /^[0-9a-f-]{36}$/i.test(o.client_reference_id) ? o.client_reference_id : null;
      const email = (o.customer_details && o.customer_details.email || o.customer_email || '').toLowerCase();
      if (!uid && email) { const r = await q('select id from co_users where email = $1', [email]); uid = r.length ? r[0].id : null; }
      if (uid && o.customer) await q('update co_users set stripe_customer = coalesce(stripe_customer, $2) where id = $1', [uid, o.customer]);
      await setAddon(o.subscription, o.customer, Math.max(1, Math.round((o.amount_subtotal || ADDON_AMOUNT) / ADDON_AMOUNT)), true, uid);
      return send(200, { ok: true, addon: true, matched: !!uid });
    }
    if (ev.type.startsWith('customer.subscription.') && addonQty(o)) {
      const active = ev.type !== 'customer.subscription.deleted' && ['active', 'trialing', 'past_due'].includes(o.status);
      await setAddon(o.id, o.customer, addonQty(o), active, null);
      return send(200, { ok: true, addon: true });
    }
    if (ev.type === 'invoice.paid' && addonQty(o)) return send(200, { ok: true, addon: true });
    if (ev.type === 'checkout.session.completed' && o.mode === 'subscription') {
      // amount_total is 0 during a free trial, so fall back to the price on the line items via the subscription event below.
      let plan = planOf(o) || null;
      if (!plan && o.customer) { const p = await q('select plan from co_pending_plan where customer = $1', [o.customer]); plan = p.length ? p[0].plan : null; }
      const email = (o.customer_details && o.customer_details.email || o.customer_email || '').toLowerCase();
      let n = 0;
      if (o.client_reference_id && /^[0-9a-f-]{36}$/i.test(o.client_reference_id))
        n = await setPlan('id = $1', [o.client_reference_id], plan || 'creator', o.customer, 'subscribed');
      if (!n && email) n = await setPlan('email = $1', [email], plan || 'creator', o.customer, 'subscribed');
      if (n && o.customer) await q('delete from co_pending_plan where customer = $1', [o.customer]);
      return send(200, { ok: true, matched: n });
    }
    if (ev.type === 'customer.subscription.created' || ev.type === 'customer.subscription.updated' || ev.type === 'invoice.paid') {
      const active = ev.type === 'invoice.paid' || ['active', 'trialing', 'past_due'].includes(o.status);
      const plan = active ? planOf(o) : 'free';
      if (plan && o.customer) {
        const n = await setPlan('stripe_customer = $1', [o.customer], plan, o.customer, 'plan ' + plan);
        if (!n && plan !== 'free') await q('insert into co_pending_plan (customer, plan) values ($1, $2) on conflict (customer) do update set plan = excluded.plan', [o.customer, plan]);
      }
      return send(200, { ok: true });
    }
    if (ev.type === 'customer.subscription.deleted') {
      await setPlan('stripe_customer = $1', [o.customer], 'free', o.customer, 'cancelled');
      return send(200, { ok: true });
    }
    return send(200, { ok: true, ignored: ev.type });
  } catch (e) {
    console.error('stripe webhook error', e && e.message);
    return send(500, { error: 'server' });
  }
};
