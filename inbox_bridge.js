// 远程指令桥接器（零依赖 Node）
// ---------------------------------------------------------------------------
// 只做确定性工作：校验发件人与令牌、去重、解析指令、生成/消费确认码、调用本机任务总线。
// 邮件收发不在这里做，由定时任务调用通讯工具完成，避免把凭据写进脚本。
//
//   node inbox_bridge.js run --input jobs/inbox_msg.json
//
// 输入文件：{"id":"msg_x","from":"user@example.com","subject":"...","body":"...","received_at":"..."}
// 输出：JSON { action, reply, job?, code?, to }
//
// 安全：真实投递必须先拿确认码，再回 CONFIRM <码>；确认码一次有效、限发件人、限时。
const fs = require('fs');
const path = require('path');
const http = require('http');
const crypto = require('crypto');

const CFG_FILE = 'config/local/inbox_bridge.json';

function cfg(root){
  const p = path.join(root, CFG_FILE);
  if(!fs.existsSync(p)) throw new Error('缺少 ' + CFG_FILE + '，不设默认配置');
  const c = JSON.parse(fs.readFileSync(p, 'utf8'));
  if(c.schema !== 'inbox_bridge/v1') throw new Error('inbox_bridge 配置 schema 不匹配');
  if(!c.token) throw new Error('inbox_bridge 配置缺少 token');
  if(!c.state_file) throw new Error('inbox_bridge 配置缺少 state_file');
  return c;
}

function statePath(root, c){ return path.join(root, c.state_file); }

function loadState(root, c){
  try { return JSON.parse(fs.readFileSync(statePath(root, c), 'utf8')); }
  catch(e){ return { seen: [], confirms: {} }; }
}

function saveState(root, c, st){
  const p = statePath(root, c);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(st, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, p);
}

function api(c, pathname, method, body){
  return new Promise((resolve, reject) => {
    const u = new URL(c.api_base);
    const data = body ? JSON.stringify(body) : null;
    const req = http.request({
      hostname: u.hostname,
      port: u.port || 80,
      path: pathname,
      method: method || 'GET',
      headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}
    }, res => {
      let s = '';
      res.setEncoding('utf8');
      res.on('data', d => { s += d; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(s); } catch(e){}
        if(res.statusCode >= 400){
          const err = (j && (j.err || j.code)) || s.slice(0, 200);
          return reject(new Error('HTTP ' + res.statusCode + ' ' + err));
        }
        resolve(j);
      });
    });
    req.on('error', reject);
    if(data) req.write(data);
    req.end();
  });
}

function normEmail(s){
  const m = String(s || '').match(/<([^>]+)>/);
  return (m ? m[1] : String(s || '')).trim().toLowerCase();
}

function authorize(msg, c){
  if(c.enabled === false) return { err: '指令通道已停用' };
  const from = normEmail(msg.from);
  const allow = (c.allowed_senders || []).map(normEmail);
  if(!allow.length || allow.indexOf(from) < 0) return { err: '发件人不在白名单' };
  const text = String(msg.subject || '') + '\n' + String(msg.body || '');
  if(text.indexOf(c.token) < 0) return { err: '缺少有效令牌' };
  return { from: from };
}

function textOf(msg){
  const b = msg.body;
  if(typeof b === 'string') return b;
  if(b && typeof b === 'object') return b.text || b.content || b.plain || '';
  return '';
}

function stripToken(s, c){
  return String(s || '').split(c.token).join(' ').trim();
}

function clamp(n, lo, hi, dflt){
  return Math.min(hi, Math.max(lo, isFinite(n) ? n : dflt));
}

function parseOpts(s){
  const p = {};
  let err = '';
  String(s || '').split(/\s+/).forEach(tok => {
    let m = tok.match(/max\s*=\s*(\d+)/i);
    if(m){ p.max = clamp(Number(m[1]), 1, 50, 20); return; }
    m = tok.match(/budget\s*=\s*(\d+)/i);
    if(m){ p.budget_min = clamp(Number(m[1]), 1, 240, 15); return; }
    if(tok && !/^\s*$/.test(tok)) err = '无法识别的参数：' + tok;
  });
  return err ? { err: err } : { params: p };
}

function parseCommand(msg, c){
  const raw = stripToken(String(msg.subject || '') + '\n' + textOf(msg), c);
  const line = raw.split(/\r?\n/).map(s => s.trim()).find(Boolean) || '';
  const t = line.replace(/[。！？!?,，]/g, '').trim();
  let m;
  if(/^(状态|status)$/i.test(t)) return { kind: 'status' };
  if(/^(帮助|help)$/i.test(t)) return { kind: 'help' };
  if((m = t.match(/^(任务|job)\s+([a-z0-9-]+)$/i))) return { kind: 'job', id: m[2] };
  if((m = t.match(/^(分析|analyze)\s*(active|all)?$/i)))
    return { kind: 'create', job: 'position-analysis', params: { scope: (m[2] || 'active').toLowerCase() } };
  if((m = t.match(/^(检查|check)\s+([a-z0-9._-]+)$/i)))
    return { kind: 'create', job: 'jkit-check', params: { conditions: m[2] } };
  if((m = t.match(/^(试跑|dry|dryrun)\s+([a-z0-9._-]+)(.*)$/i))){
    const o = parseOpts(m[3]);
    if(o.err) return o;
    return { kind: 'create', job: 'jkit-dry-run', params: Object.assign({ conditions: m[2], platform: 'boss' }, o.params) };
  }
  if((m = t.match(/^(投递|deliver)\s+([a-z0-9._-]+)(.*)$/i))){
    if(c.allow_delivery !== true) return { err: '该通道未开启真实投递' };
    const o = parseOpts(m[3]);
    if(o.err) return o;
    return { kind: 'deliver_request', params: Object.assign({ conditions: m[2], platform: 'boss' }, o.params) };
  }
  if((m = t.match(/^CONFIRM\s+([a-z0-9]+)$/i))) return { kind: 'confirm', code: m[1].toUpperCase() };
  return { kind: 'unknown', text: line.slice(0, 120) };
}

function helpText(c){
  const credential = c.token_hint
    ? c.token_hint
    : '主题或正文首行写，须带令牌 ' + c.token;
  return [
    '可用指令（' + credential + '）：',
    '状态 / 任务 <id> / 分析 [active|all] / 检查 <条件名>',
    '试跑 <条件名> [max=N] [budget=N]',
    '投递 <条件名> [max=N]   ← 只生成确认码，不会立即投递',
    'CONFIRM <码>            ← 二次确认后才真实投递',
    '帮助',
    '条件名必须是 config/local/conditions/ 内已存在的 json 文件'
  ].join('\n');
}

function prefix(c, s){ return (c.reply_prefix || '[工作台]') + ' ' + s; }

async function execute(msg, c, st){
  const a = parseCommand(msg, c);
  if(a.err) return { action: 'reject', reply: prefix(c, a.err) };

  if(a.kind === 'help') return { action: 'help', reply: prefix(c, helpText(c)) };

  if(a.kind === 'status'){
    const d = await api(c, '/api/jobs?limit=10', 'GET');
    const lines = (d.jobs || []).map(j =>
      '- ' + j.id + '｜' + j.kind + '｜' + j.status + '｜' + (j.title || '') + (j.error ? '｜' + j.error : ''));
    return { action: 'status', reply: prefix(c,
      '任务状态（最近 ' + lines.length + ' 条）\n' + (lines.join('\n') || '暂无任务')) };
  }

  if(a.kind === 'job'){
    const d = await api(c, '/api/job/' + encodeURIComponent(a.id), 'GET');
    const j = d.job || {};
    const sum = (j.result && j.result.summary) || '';
    return { action: 'job', reply: prefix(c,
      '任务 ' + (j.id || a.id) + '｜' + (j.kind || '') + '｜' + (j.status || '') + '\n'
      + (j.error || '') + '\n' + sum
      + (d.log_tail ? '\n日志尾部：\n' + String(d.log_tail).slice(-600) : '')) };
  }

  if(a.kind === 'create'){
    const d = await api(c, '/api/job', 'POST', { kind: a.job, params: a.params });
    const j = d.job || {};
    return { action: 'create', job: j, reply: prefix(c,
      '已建任务 ' + j.id + '｜' + j.kind + '｜' + j.status + '\n'
      + (j.runner === 'agent' ? '智能体类，等待取件执行' : '本地脚本执行中')
      + '\n完成后回「任务 ' + j.id + '」查结果。') };
  }

  if(a.kind === 'deliver_request'){
    const code = crypto.randomBytes(4).toString('hex').toUpperCase();
    st.confirms[code] = {
      params: a.params,
      from: normEmail(msg.from),
      expires_at: Date.now() + (Number(c.confirm_ttl_minutes) || 30) * 60000
    };
    return { action: 'deliver_request', code: code, reply: prefix(c,
      '真实投递需二次确认。\n确认码：' + code
      + '\n条件：' + a.params.conditions + '｜平台：' + a.params.platform
      + '｜上限：' + (a.params.max || 20) + '｜预算：' + (a.params.budget_min || 15) + '分钟'
      + '\n请在 ' + (c.confirm_ttl_minutes || 30) + ' 分钟内回复「CONFIRM ' + code + '」。未确认前不会投递。') };
  }

  if(a.kind === 'confirm'){
    const e = st.confirms[a.code];
    const from = normEmail(msg.from);
    if(!e) return { action: 'reject', reply: prefix(c, '确认码不存在或已使用') };
    if(e.from !== from) return { action: 'reject', reply: prefix(c, '确认码发件人不匹配') };
    if(Date.now() > e.expires_at){
      delete st.confirms[a.code];
      return { action: 'reject', reply: prefix(c, '确认码已过期') };
    }
    delete st.confirms[a.code];
    const d = await api(c, '/api/job', 'POST', {
      kind: 'jkit-deliver',
      params: Object.assign({}, e.params, { confirm: true })
    });
    const j = d.job || {};
    return { action: 'deliver_confirmed', job: j, reply: prefix(c,
      '已确认，真实投递任务 ' + j.id + '｜' + j.status
      + '\n条件：' + e.params.conditions + '，上限 ' + (e.params.max || 20) + '。结果回来后我会回执。') };
  }

  return { action: 'unknown', reply: prefix(c,
    '不支持的指令：「' + a.text + '」。回「帮助」看可用指令。') };
}

async function run(inputFile, root){
  const msg = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
  const c = cfg(root);
  const st = loadState(root, c);
  const au = authorize(msg, c);
  if(au.err) return { action: 'reject', to: msg.from, reply: prefix(c, au.err) };
  if((st.seen || []).indexOf(msg.id) >= 0) return { action: 'duplicate', to: msg.from, reply: '' };

  let out;
  try { out = await execute(msg, c, st); }
  catch(e){ out = { action: 'error', reply: prefix(c, '执行失败：' + e.message) }; }

  st.seen = (st.seen || []).concat([msg.id]).slice(-1000);
  saveState(root, c, st);
  return Object.assign({ to: msg.from }, out);
}

function main(){
  const args = process.argv.slice(2);
  if(args[0] !== 'run'){
    console.error('用法: node inbox_bridge.js run --input <message.json>');
    process.exit(2);
  }
  const i = args.indexOf('--input');
  if(i < 0 || !args[i + 1]){
    console.error('缺少 --input <message.json>');
    process.exit(2);
  }
  run(path.resolve(args[i + 1]), path.resolve(__dirname))
    .then(r => { console.log(JSON.stringify(r, null, 2)); })
    .catch(e => { console.error(e.message); process.exit(1); });
}

if(require.main === module) main();

module.exports = { cfg, authorize, parseCommand, execute, run, api, helpText };
