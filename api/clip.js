// Cloud link clipping: starts a job on the ClipOnce GitHub worker and reads its results back.
// Needs GH_DISPATCH_TOKEN in Vercel: a fine-grained GitHub token for hilltyler2x/cliponce-worker
// with Actions (read and write) and Contents (read). Until it is set, link clipping says it is not switched on.
const crypto = require('crypto');
const q = require('../lib/db');
const core = require('../lib/core');

const REPO = process.env.WORKER_REPO || 'hilltyler2x/cliponce-worker';
const TWITCH = /^https:\/\/(www\.|m\.|clips\.)?twitch\.tv\/[A-Za-z0-9_\/?=&.-]{1,200}$/;
let ready = false;

function send(res, status, body) {
  res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}
function gh(path, opts = {}) {
  return fetch('https://api.github.com/repos/' + REPO + path, {
    ...opts,
    headers: { Authorization: 'Bearer ' + process.env.GH_DISPATCH_TOKEN, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'cliponce', ...(opts.headers || {}) },
  });
}
async function setup() {
  if (ready) return;
  await core.migrate(q);
  await q(`create table if not exists co_jobs (id uuid primary key, user_id uuid not null references co_users(id) on delete cascade,
    url text not null, clips integer not null, cost integer not null, refunded boolean not null default false, created_at timestamptz not null default now())`);
  ready = true;
}

module.exports = async (req, res) => {
  const action = (req.query && req.query.action) || '';
  try {
    const s = core.readSession(req);
    if (!s) return send(res, 401, { error: 'Log in to clip from links.', login: true });
    await setup();
    const u = await core.me(q, s.uid);
    if (!u) return send(res, 401, { error: 'Log in to clip from links.', login: true });

    if (req.method === 'POST' && action === 'start') {
      const origin = req.headers.origin || '';
      if (origin) { let h = ''; try { h = new URL(origin).host; } catch (e) {} if (h !== req.headers.host) return send(res, 403, { error: 'Bad origin' }); }
      if (!process.env.GH_DISPATCH_TOKEN) return send(res, 503, { error: 'Cloud link clipping is being switched on. Try again soon, or drop a file above.' });
      const b = typeof req.body === 'object' && req.body ? req.body : {};
      let url = String(b.url || '').trim().replace(/^http:\/\//i, 'https://');
      if (!/^https:\/\//i.test(url)) url = 'https://' + url;
      if (!TWITCH.test(url)) return send(res, 400, { error: 'Cloud clipping works with Twitch links right now: a channel, a past broadcast or a clip.' });
      const clips = Math.max(1, Math.min(parseInt(b.count, 10) || 3, 6));
      const cost = u.plan === 'owner' ? 0 : clips;
      if (cost && u.credits_min < cost) return send(res, 402, { error: `This needs ${cost} minutes and you have ${u.credits_min}. Upgrade or wait for your monthly reset.` });
      const recent = await q("select count(*)::int n from co_jobs where user_id = $1 and created_at > now() - interval '10 minutes'", [u.id]);
      if (recent[0].n >= 3 && u.plan !== 'owner') return send(res, 429, { error: 'You have a few jobs running already. Give them a few minutes.' });
      const id = crypto.randomUUID();
      const r = await gh('/actions/workflows/link.yml/dispatches', { method: 'POST',
        body: JSON.stringify({ ref: 'main', inputs: { job_id: id, url, count: String(clips) } }) });
      if (r.status !== 204) { console.error('dispatch failed', r.status, (await r.text()).slice(0, 200)); return send(res, 502, { error: 'The cloud clipper did not answer. Try again in a minute.' }); }
      await q('insert into co_jobs (id, user_id, url, clips, cost) values ($1, $2, $3, $4, $5)', [id, u.id, url, clips, cost]);
      if (cost) {
        await q('update co_users set credits_min = credits_min - $2 where id = $1', [u.id, cost]);
        await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [u.id, -cost, 'link clips ' + id]);
      }
      return send(res, 200, { id, clips, cost });
    }

    if (req.method === 'GET' && action === 'status') {
      const id = String((req.query && req.query.id) || '');
      if (!/^[0-9a-f-]{36}$/i.test(id)) return send(res, 400, { error: 'Bad job id' });
      const rows = await q('select * from co_jobs where id = $1 and user_id = $2', [id, u.id]);
      if (!rows.length) return send(res, 404, { error: 'Job not found' });
      const job = rows[0];
      const r = await gh('/contents/state/jobs/' + id + '.json?ref=main', { headers: { Accept: 'application/vnd.github.raw+json' } });
      if (r.status === 404) {
        const age = (Date.now() - new Date(job.created_at)) / 1000;
        return send(res, 200, { status: age > 900 ? 'failed' : 'queued', step: 'Starting a cloud machine', error: age > 900 ? 'The job never started.' : undefined });
      }
      if (!r.ok) return send(res, 200, { status: 'running', step: 'Working' });
      const d = await r.json();
      if (d.status === 'failed' && job.cost && !job.refunded) {
        const upd = await q('update co_jobs set refunded = true where id = $1 and refunded = false returning id', [id]);
        if (upd.length) {
          await q('update co_users set credits_min = credits_min + $2 where id = $1', [u.id, job.cost]);
          await q('insert into co_credit_log (user_id, delta_min, reason) values ($1, $2, $3)', [u.id, job.cost, 'refund ' + id]);
        }
      }
      const clean = (d.clips || []).map(c => ({ url: String(c.url || ''), kind: c.kind, start: c.start, end: c.end, caption: c.caption, why: c.why }))
        .filter(c => c.url.startsWith('https://github.com/' + REPO + '/releases/download/'));
      return send(res, 200, { status: d.status, step: d.step, title: d.title, creator: d.creator, source: d.source, error: d.error, clips: clean });
    }
    return send(res, 404, { error: 'Unknown action' });
  } catch (e) {
    console.error('clip error', e && e.message);
    return send(res, 500, { error: 'Something went wrong. Try again in a moment.' });
  }
};
