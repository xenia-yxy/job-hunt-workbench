// 远程指令桥接器离线测试：真实投递二次确认不得被绕过。
// 运行：node --test tests/test_inbox_bridge.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const bridge = require('../inbox_bridge.js');

function msgFile(root, id, from, subject){
  const p = path.join(root, id + '.json');
  fs.writeFileSync(p, JSON.stringify({ id, from, subject, body: '', received_at: '2026-11-16T00:00:00Z' }));
  return p;
}

test('投递必须二次确认，确认码一次有效且限发件人', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inbox-bridge-'));
  fs.mkdirSync(path.join(root, 'config', 'local'), { recursive: true });
  const requests = [];

  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', d => { body += d; });
    req.on('end', () => {
      requests.push({ path: req.url, method: req.method, body: body ? JSON.parse(body) : null });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, jobs: [], job: { id: 'jtest', kind: 'jkit-deliver', status: 'running' } }));
    });
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  t.after(() => { server.close(); fs.rmSync(root, { recursive: true, force: true }); });

  fs.writeFileSync(path.join(root, 'config', 'local', 'inbox_bridge.json'), JSON.stringify({
    schema: 'inbox_bridge/v1', enabled: true, token: 'TESTTOKEN',
    allowed_senders: ['me@example.com'], api_base: 'http://127.0.0.1:' + port,
    state_file: 'jobs/state.json', confirm_ttl_minutes: 30,
    reply_prefix: '[T]', allow_delivery: true
  }));

  const bad = await bridge.run(msgFile(root, 'm1', 'evil@example.com', 'TESTTOKEN 投递 delivery-fde.json'), root);
  assert.equal(bad.action, 'reject');
  assert.equal(requests.length, 0);

  const req = await bridge.run(msgFile(root, 'm2', 'me@example.com', 'TESTTOKEN 投递 delivery-fde.json max=2'), root);
  assert.equal(req.action, 'deliver_request');
  assert.match(req.code, /^[A-F0-9]{8}$/);
  assert.equal(requests.length, 0, '仅发指令时不得创建投递任务');

  const wrongSender = await bridge.run(msgFile(root, 'm3', 'evil@example.com', 'TESTTOKEN CONFIRM ' + req.code), root);
  assert.equal(wrongSender.action, 'reject');
  assert.equal(requests.length, 0);

  const wrongCode = await bridge.run(msgFile(root, 'm4', 'me@example.com', 'TESTTOKEN CONFIRM DEADBEEF'), root);
  assert.equal(wrongCode.action, 'reject');
  assert.equal(requests.length, 0);

  const ok = await bridge.run(msgFile(root, 'm5', 'me@example.com', 'TESTTOKEN CONFIRM ' + req.code), root);
  assert.equal(ok.action, 'deliver_confirmed');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/job');
  assert.equal(requests[0].method, 'POST');
  assert.equal(requests[0].body.kind, 'jkit-deliver');
  assert.equal(requests[0].body.params.confirm, true);
  assert.equal(requests[0].body.params.max, 2);

  const replay = await bridge.run(msgFile(root, 'm6', 'me@example.com', 'TESTTOKEN CONFIRM ' + req.code), root);
  assert.equal(replay.action, 'reject');
  assert.equal(requests.length, 1, '确认码不得重复使用');

  const dup = await bridge.run(msgFile(root, 'm5', 'me@example.com', 'TESTTOKEN CONFIRM ' + req.code), root);
  assert.equal(dup.action, 'duplicate');
});

test('未开启投递时投递指令直接拒绝', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'inbox-bridge-'));
  fs.mkdirSync(path.join(root, 'config', 'local'), { recursive: true });
  t_after(root);
  fs.writeFileSync(path.join(root, 'config', 'local', 'inbox_bridge.json'), JSON.stringify({
    schema: 'inbox_bridge/v1', enabled: true, token: 'TESTTOKEN',
    allowed_senders: ['me@example.com'], api_base: 'http://127.0.0.1:1',
    state_file: 'jobs/state.json', allow_delivery: false
  }));
  const r = await bridge.run(msgFile(root, 'n1', 'me@example.com', 'TESTTOKEN 投递 delivery-fde.json'), root);
  assert.equal(r.action, 'reject');
});
function t_after(root){ process.once('exit', () => fs.rmSync(root, { recursive: true, force: true })); }
