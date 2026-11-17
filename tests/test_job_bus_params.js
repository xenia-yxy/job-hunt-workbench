// P-A 回归：页面/管道不填数量时，job_bus 不得塞默认 max/budget_min 覆盖条件文件。
// 运行：node --test tests/test_job_bus_params.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createBus } = require('../job_bus.js');

function sandbox(){
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jobbus-'));
  fs.mkdirSync(path.join(root, 'config', 'local', 'conditions'), { recursive: true });
  fs.mkdirSync(path.join(root, 'jobs'), { recursive: true });
  fs.writeFileSync(path.join(root, 'config', 'local', 'conditions', 'x.json'),
    JSON.stringify({ id: 'x' }));
  // 没有 jkit -> script 任务标 unavailable，但我们要测的是 normalize + argv，
  // 所以直接给一个假 jkit 入口，让 create 走 run()（spawn python -m jkit 会失败但不影响 params 断言）。
  fs.mkdirSync(path.join(root, 'jkit'), { recursive: true });
  fs.writeFileSync(path.join(root, 'jkit', '__main__.py'), '');
  return root;
}
function loadLocal(root){
  return name => {
    const p = path.join(root, 'config', 'local', name + '.json');
    return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : null;
  };
}

test('jkit 任务缺省不写 max/budget_min，让条件文件值成为唯一来源', () => {
  const root = sandbox();
  const bus = createBus(root, loadLocal(root));
  // 只传 conditions，不传 max/budget_min
  const r = bus.create('jkit-deliver', {
    conditions: 'config/local/conditions/x.json', confirm: true
  });
  assert.ok(r.job && r.job.id, '任务应被创建');
  const job = bus.read(r.job.id);
  assert.equal(job.params.max, undefined, '缺省 max 不应写进 params（否则覆盖条件文件）');
  assert.equal(job.params.budget_min, undefined, '缺省 budget_min 不应写进 params');
  // argv 里也不应出现 --max / --budget-min
  const argv = job.argv || [];
  assert.ok(!argv.includes('--max'), 'argv 不应含 --max');
  assert.ok(!argv.includes('--budget-min'), 'argv 不应含 --budget-min');
  try { fs.rmSync(root, { recursive: true, force: true }); } catch(e){ /* 子进程句柄未释放，忽略 */ }
});

test('显式传 max/budget_min 时才写入并进入 argv', () => {
  const root = sandbox();
  const bus = createBus(root, loadLocal(root));
  const r = bus.create('jkit-deliver', {
    conditions: 'config/local/conditions/x.json', confirm: true, max: 7, budget_min: 12
  });
  const job = bus.read(r.job.id);
  assert.equal(job.params.max, 7);
  assert.equal(job.params.budget_min, 12);
  const argv = job.argv || [];
  assert.ok(argv.includes('--max'));
  assert.ok(argv.includes('--budget-min'));
  try { fs.rmSync(root, { recursive: true, force: true }); } catch(e){ /* 子进程句柄未释放，忽略 */ }
});
