// 客户端写盘回归测试（零依赖）。
//
// 为什么要有这个文件：用户侧「增删改查没落盘」的根因不在服务端，而在页面——
//   · 整份 PUT + fire-and-forget，写完不等回执
//   · 任何一次失败就把 serverMode 永久置 false，之后所有改动只进 localStorage
//   · 失败没有重试入口
// 这个测试把页面脚本放进一个假的 DOM 里跑，直接断言：
//   1) 记录级写入走的是 POST/PATCH/DELETE + 正确的 URL
//   2) 成功后连接状态变「已落盘」，并记住服务端版本号
//   3) 失败后状态是「可重试」，serverMode 仍然为 true（下一次写还会真正落盘）
//   4) 整份写入带 baseVersion；409 时重新载入服务端数据，不拿旧快照覆盖
//   5) CSV 派生失败只是告警，不当作写盘失败
//
// 跑：node tests/test_client_write.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const cfgFile = path.join(ROOT, 'config', 'local', 'app.json');
let htmlFile = 'app.html';
try {
  const app = JSON.parse(fs.readFileSync(cfgFile, 'utf8'));
  if (app.files && app.files.app_html) htmlFile = app.files.app_html;
} catch (e) {
  console.log('SKIP: 没有 config/local/app.json，跳过客户端测试');
  process.exit(0);
}
const htmlPath = path.join(ROOT, htmlFile);
if (!fs.existsSync(htmlPath)) {
  console.log('SKIP: 页面还没构建（' + htmlFile + '），先跑 node runs/20261114-app/build.js');
  process.exit(0);
}

const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [];
html.replace(/<script>([\s\S]*?)<\/script>/g, (m, src) => { if (src.trim()) scripts.push(src); });
const code = scripts.join('\n;\n');

function el(tag){
  const node = {
    tagName: tag || 'div', className: '', textContent: '', innerHTML: '', hidden: false,
    dataset: {}, style: {}, value: '', checked: false, children: [],
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    addEventListener(){}, removeEventListener(){}, appendChild(c){ this.children.push(c); return c; },
    removeChild(){}, remove(){}, setAttribute(){}, getAttribute(){ return ''; },
    querySelector(){ return el('div'); }, querySelectorAll(){ return []; },
    focus(){}, click(){}, insertAdjacentHTML(){}, append(){},
    getBoundingClientRect(){ return {top:0,left:0,width:100,height:20}; }
  };
  return node;
}

const calls = [];
let responder = () => ({ok:true, version:'v1', data:emptyData(), count:0});
function fakeFetch(url, opt){
  const method = (opt && opt.method) || 'GET';
  const body = opt && opt.body ? JSON.parse(opt.body) : null;
  calls.push({url, method, body});
  const out = responder({url, method, body});
  if(out instanceof Error) return Promise.reject(out);
  return Promise.resolve({
    ok: out.status ? out.status < 400 : true,
    status: out.status || 200,
    json: () => Promise.resolve(out.body || out)
  });
}

const store = {};
const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: fakeFetch,
  Promise, JSON, Date, Math, Number, String, Array, Object, RegExp, Error, isNaN,
  encodeURIComponent, decodeURIComponent,
  Blob: function(){}, URL: {createObjectURL(){return '';}, revokeObjectURL(){}},
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; }
  },
  document: {
    documentElement: el('html'),
    body: el('body'),
    title: '',
    getElementById: () => el('div'),
    querySelector: () => el('div'),
    querySelectorAll: () => [],
    createElement: t => el(t),
    addEventListener(){}
  },
  location: {protocol: 'http:', href: 'http://127.0.0.1/'},
  navigator: {userAgent: 'node-test'},
  open(){}
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
try { vm.runInContext(code, sandbox, {filename: htmlFile}); }
catch(e){ console.log('FAIL: 页面脚本无法执行：' + e.message); process.exit(1); }

// 页面里的 serverMode / D / serverVersion 是 let 声明，不会挂到 vm 全局对象上，
// 只能回到上下文里读，不能用 sandbox.xxx
const get = expr => vm.runInContext(expr, sandbox);
// 渠道 key 不能写死：不同人的渠道清单不同（演示配置里就是 campus/platform_a/platform_b）
const CH = JSON.parse(fs.readFileSync(path.join(ROOT, 'config/local/channels.json'), 'utf8'))
  .channels.filter(c => c.enabled !== false)[0].key;
const ALL_KEYS = () => JSON.parse(fs.readFileSync(path.join(ROOT, 'config/local/channels.json'), 'utf8'))
  .channels.filter(c => c.enabled !== false).map(c => c.key);
const emptyData = () => { const o = {}; ALL_KEYS().forEach(k => { o[k] = []; }); return o; };
const results = [];
function check(name, fn){
  return Promise.resolve().then(fn).then(
    () => results.push({name, ok:true}),
    e => results.push({name, ok:false, msg:e.message})
  );
}
function assert(cond, msg){ if(!cond) throw new Error(msg); }
const lastCall = () => calls[calls.length - 1];
const wait = ms => new Promise(r => setTimeout(r, ms));

async function main(){
  // 让页面进入「已连服务端」状态
  await sandbox.init();
  assert(get('serverMode') === true, 'init 之后应连上服务端');

  await check('新增 = POST /api/data/{channel}', async () => {
    calls.length = 0;
    responder = ({method, body}) => ({ok:true, version:'v2', record:Object.assign({}, body && body.record, {id:'x1'})});
    await sandbox.writeRecord('POST', '/api/data/' + CH, {record:{company:'测试'}}, null, '已新增');
    const c = lastCall();
    assert(c.method === 'POST' && c.url === '/api/data/' + CH, '请求不对：' + c.method + ' ' + c.url);
    assert(get('serverVersion') === 'v2', '成功后应记住服务端版本号');
  });

  await check('修改 = PATCH 单条', async () => {
    calls.length = 0;
    responder = ({body}) => ({ok:true, version:'v3', record:Object.assign({id:'x1'}, body && body.patch)});
    await sandbox.writeRecord('PATCH', '/api/data/' + CH + '/x1', {patch:{status:'沟通中'}}, null, '已保存');
    const c = lastCall();
    assert(c.method === 'PATCH' && c.url === '/api/data/' + CH + '/x1', '请求不对：' + c.method + ' ' + c.url);
    assert(c.body.patch.status === '沟通中', 'patch 内容不对');
  });

  await check('删除 = DELETE 单条', async () => {
    calls.length = 0;
    responder = () => ({ok:true, version:'v4', removed:{id:'x1'}});
    await sandbox.writeRecord('DELETE', '/api/data/' + CH + '/x1', null, null, '已删除', true);
    const c = lastCall();
    assert(c.method === 'DELETE' && c.url === '/api/data/' + CH + '/x1', '请求不对：' + c.method + ' ' + c.url);
  });

  await check('写失败后不关服务端模式，并保留重试', async () => {
    const before = get('serverMode');
    responder = () => ({status:500, body:{ok:false, code:'WRITE_FAIL', err:'磁盘只读'}});
    const ok = await sandbox.writeRecord('PATCH', '/api/data/' + CH + '/x1', {patch:{note:'x'}}, null, '备注');
    assert(ok === false, '失败应返回 false');
    assert(get('serverMode') === before, '失败后 serverMode 不能被关掉（这是丢数据的根源）');
    // 恢复后重试必须真的再打一次请求
    calls.length = 0;
    responder = () => ({ok:true, version:'v5', record:{id:'x1'}});
    sandbox.flushPending();
    await wait(30);
    assert(calls.length >= 1, '重试应重新发起请求');
  });

  await check('整份写入带 baseVersion，409 时重载服务端数据', async () => {
    calls.length = 0;
    vm.runInContext('serverVersion = "old"', sandbox);
    responder = ({method, url}) => {
      if(method === 'PUT') return {status:409, body:{ok:false, code:'VERSION_CONFLICT', err:'已更新', version:'new'}};
      if(url.indexOf('/api/data') === 0){ const d = emptyData(); d[CH] = [{id:'srv1', company:'服务端记录'}]; return {ok:true, version:'new', data:d}; }
      return {ok:true};
    };
    await sandbox.persist('导入完成');
    const put = calls.find(c => c.method === 'PUT');
    assert(put, '应发出整份写入请求');
    assert(put.body.baseVersion === 'old', '整份写入必须带 baseVersion');
    assert(calls.some(c => c.method === 'GET'), '409 之后应重新拉取服务端数据');
    assert(get('serverVersion') === 'new', '应更新到服务端最新版本');
    assert(get('(D["' + CH + '"] || []).some(function(r){return r.id === "srv1"})'), '应以服务端数据为准重新载入');
  });

  await check('CSV 派生失败只告警，不算写盘失败', async () => {
    responder = () => ({ok:true, version:'v6', csv_warning:'EPERM csv'});
    const ok = await sandbox.writeRecord('PATCH', '/api/data/' + CH + '/x1', {patch:{note:'y'}}, null, '备注');
    assert(ok === true, 'CSV 失败不应判定为写盘失败');
  });

  const bad = results.filter(r => !r.ok);
  results.forEach(r => console.log((r.ok ? '  ok   ' : '  FAIL ') + r.name + (r.msg ? ' → ' + r.msg : '')));
  console.log('\n客户端写盘测试：' + (results.length - bad.length) + '/' + results.length + ' 通过');
  process.exit(bad.length ? 1 : 0);
}

main().catch(e => { console.log('FAIL: ' + (e && e.stack || e)); process.exit(1); });
