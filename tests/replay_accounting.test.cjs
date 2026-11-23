// Offline accounting-label regression against the actual replay UI and public fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const read = file => JSON.parse(fs.readFileSync(path.join(root, 'ui/v2', file)));
const html = fs.readFileSync(path.join(root, 'ui/v2/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/start\(\);\s*$/, '');
const context = vm.createContext({document: {querySelector: () => ({getContext: () => ({})})}});
vm.runInContext(script, context);
const examples = read('examples.json');
let jev;
let generated;
for (const entry of examples) {
  const bytes = fs.readFileSync(path.join(root, 'ui/v2', entry.file));
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), entry.sha256);
  const raw = JSON.parse(bytes);
  const replay = context.normalise(raw, entry);
  if (entry.kind !== 'model') continue;
  const last = replay.steps.at(-1);
  assert.equal(last.accounting.output_tokens, raw.output_tokens, 'reported usage is preserved');
  assert.ok(Math.abs(last.accounting.paid_usd - raw.cost_usd) < 1e-8);
  assert.equal(last.state.completed_orders, raw.completed);
  assert.equal(last.state.invalid_actions, raw.invalid);
  assert.ok(replay.steps.every(step => step.state.orders.every(order => order.status !== 'PENDING')));
  const text = context.tokenUsageText(replay, last.accounting);
  if (entry.model_accounting.provider === 'TypeSafe') {
    jev = {raw, entry, replay};
    assert.match(text, /Generation-token comparison: not applicable/);
    assert.doesNotMatch(text, /generated tokens|\bfree\b|\b0\b/);
  } else {
    generated = {raw, entry, replay};
    assert.match(text, new RegExp(raw.output_tokens.toLocaleString() + ' generated tokens used'));
  }
}
assert.ok(jev && generated);
const clone = value => JSON.parse(JSON.stringify(value));
const misleadingLabel = {...jev.entry, label: 'A generative model'};
assert.equal(context.normalise(jev.raw, misleadingLabel).model_accounting.generation_token_eligible, false);
const missing = {...jev.entry};
delete missing.model_accounting;
assert.match(context.tokenUsageText(context.normalise(jev.raw, missing), {output_tokens: 0}), /unavailable/);
const mislabeled = clone(jev.entry);
mislabeled.model_accounting.generation_token_eligible = true;
assert.throws(() => context.normalise(jev.raw, mislabeled), /accounting metadata differs/);
assert.throws(() => context.normalise({...jev.raw, token_cap: 1000}, jev.entry), /not eligible/);
assert.throws(() => context.normalise({...jev.raw, token_cap: 1000}, missing), /verified generation accounting/);
const wrongManifest = clone(jev.entry);
wrongManifest.audit.manifest_sha256 = '0'.repeat(64);
assert.throws(() => context.normalise(jev.raw, wrongManifest), /manifest accounting source differs/);
assert.match(context.tokenUsageText({...generated.replay, token_cap: 1000}, {output_tokens: 100}), /100 generated tokens used · 900 left/);
console.log('Replay accounting passed: six model fixtures, hash/usage/quality preservation, provider eligibility and fail-closed metadata.');
