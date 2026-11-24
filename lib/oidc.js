// Verifies a GitHub Actions OIDC token, so only the ClipOnce worker's own runs on main can read brand sign-ins.
// No shared secret to copy around: GitHub signs a short-lived token for each run.
const crypto = require('crypto');

const ISS = 'https://token.actions.githubusercontent.com';
const REPO = process.env.WORKER_REPO || 'hilltyler2x/cliponce-worker';
let jwks = null, jwksAt = 0;

async function keys(force) {
  if (!jwks || force || Date.now() - jwksAt > 3600e3) {
    jwks = (await fetch(ISS + '/.well-known/jwks').then(r => r.json())).keys || [];
    jwksAt = Date.now();
  }
  return jwks;
}

async function verifyWorker(req) {
  const h = String(req.headers.authorization || '');
  const tok = h.startsWith('Bearer ') ? h.slice(7) : '';
  const parts = tok.split('.');
  if (parts.length !== 3) return null;
  let head, body;
  try { head = JSON.parse(Buffer.from(parts[0], 'base64url')); body = JSON.parse(Buffer.from(parts[1], 'base64url')); } catch (e) { return null; }
  if (head.alg !== 'RS256') return null;
  let jwk = (await keys()).find(k => k.kid === head.kid);
  if (!jwk) jwk = (await keys(true)).find(k => k.kid === head.kid);
  if (!jwk) return null;
  const ok = crypto.verify('RSA-SHA256', Buffer.from(parts[0] + '.' + parts[1]), crypto.createPublicKey({ key: jwk, format: 'jwk' }), Buffer.from(parts[2], 'base64url'));
  const now = Date.now() / 1000;
  if (!ok || body.iss !== ISS || body.aud !== 'cliponce' || !(body.exp > now) || (body.nbf && body.nbf > now + 60)) return null;
  if (body.repository !== REPO || body.ref !== 'refs/heads/main' || body.event_name === 'pull_request' || body.event_name === 'pull_request_target') return null;
  return body;
}

module.exports = { verifyWorker };
