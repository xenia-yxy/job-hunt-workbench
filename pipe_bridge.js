// 本机管道指令服务（Windows Named Pipe，零依赖 Node）
// ---------------------------------------------------------------------------
// 形态与 bsk daemon 一样：常驻在本机，客户端把一行 JSON 写进管道，服务解析后
// 调用本机任务总线，回执从同一条连接返回。不依赖平台定时任务、不依赖邮箱登录。
//
//   node pipe_bridge.js                      启动服务（保持窗口/任务存活）
//   node pipe_client.js "状态"              发一条指令并拿回执
//
// 指令解析、去重、确认码和 API 调用全部复用 inbox_bridge.js，避免两套口径。
// 真实投递仍然要两步：先拿确认码，再 CONFIRM <码>；码一次有效、限时。
const fs = require('fs');
const path = require('path');
const net = require('net');
const crypto = require('crypto');
const ib = require('./inbox_bridge.js');

const CFG_FILE = 'config/local/pipe_bridge.json';
const ROOT = __dirname;

function cfg(){
  const p = path.join(ROOT, CFG_FILE);
  if(!fs.existsSync(p)) throw new Error('缺少 ' + CFG_FILE + '，不设默认配置');
  const c = JSON.parse(fs.readFileSync(p, 'utf8'));
  if(c.schema !== 'pipe/v1') throw new Error('pipe_bridge 配置 schema 不匹配');
  for(const k of ['token', 'sender', 'pipe_name', 'api_base', 'state_file']){
    if(!c[k]) throw new Error('pipe_bridge 配置缺少 ' + k);
  }
  return c;
}

function loadState(c){
  try { return JSON.parse(fs.readFileSync(path.join(ROOT, c.state_file), 'utf8')); }
  catch(e){ return { seen: [], confirms: {} }; }
}

function saveState(c, st){
  const p = path.join(ROOT, c.state_file);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const tmp = p + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(st, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, p);
}

function append(c, key, text){
  if(!c[key]) return;
  const p = path.join(ROOT, c[key]);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.appendFileSync(p, text + '\n', 'utf8');
}

async function handle(line, c, st){
  let req;
  try { req = JSON.parse(line); }
  catch(e){ return { ok: false, error: 'BAD_JSON' }; }

  if(c.enabled === false) return { ok: false, error: 'PIPE_DISABLED' };
  if(req.token !== c.token) return { ok: false, error: 'BAD_TOKEN' };

  const id = String(req.id || crypto.randomBytes(6).toString('hex'));
  if((st.seen || []).indexOf(id) >= 0) return { ok: true, id, action: 'duplicate', reply: '' };

  const command = String(req.command || '');
  const msg = { id: id, from: c.sender, subject: c.token + ' ' + command, body: '' };
  let out;
  try { out = await ib.execute(msg, c, st); }
  catch(e){ out = { action: 'error', reply: (c.reply_prefix || '[管道]') + ' 执行失败：' + e.message }; }

  st.seen = (st.seen || []).concat([id]).slice(-1000);
  saveState(c, st);

  const res = {
    ok: true, id: id, action: out.action,
    reply: out.reply || '', code: out.code || '',
    job_id: (out.job && out.job.id) || ''
  };
  const record = JSON.stringify(Object.assign({ at: new Date().toISOString() }, res));
  append(c, 'log_file', record);
  append(c, 'reply_log', record);
  return res;
}

function main(){
  const c = cfg();
  const st = loadState(c);
  let queue = Promise.resolve();

  const server = net.createServer(socket => {
    socket.setEncoding('utf8');
    let buf = '';
    socket.on('data', chunk => {
      buf += chunk;
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if(!line) continue;
        queue = queue.then(() => handle(line, c, st)).then(res => {
          if(!socket.destroyed) socket.write(JSON.stringify(res) + '\n');
        }).catch(e => {
          if(!socket.destroyed) socket.write(JSON.stringify({ ok: false, error: e.message }) + '\n');
        });
      }
    });
    socket.on('error', () => {});
  });

  server.on('error', e => {
    console.error('管道启动失败：' + e.message);
    process.exit(1);
  });
  server.listen(c.pipe_name, () => {
    console.log('管道已就绪：' + c.pipe_name);
    console.log('本机 8787 服务需保持运行；真实投递需二次确认。');
  });
}

if(require.main === module) main();
module.exports = { cfg, handle, loadState, saveState };
