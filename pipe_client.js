// 管道客户端：向本机管道服务发一条指令并打印回执。
//   node pipe_client.js "状态"
//   node pipe_client.js "投递 delivery-fde.json max=2"
//   node pipe_client.js "CONFIRM <确认码>"
const fs = require('fs');
const path = require('path');
const net = require('net');
const crypto = require('crypto');

const ROOT = __dirname;
const CFG_FILE = 'config/local/pipe_bridge.json';

function cfg(){
  const p = path.join(ROOT, CFG_FILE);
  if(!fs.existsSync(p)) throw new Error('缺少 ' + CFG_FILE);
  const c = JSON.parse(fs.readFileSync(p, 'utf8'));
  if(!c.pipe_name || !c.token) throw new Error('pipe_bridge 配置缺少 pipe_name/token');
  return c;
}

const command = process.argv.slice(2).join(' ').trim();
if(!command){
  console.error('用法: node pipe_client.js "<指令>"');
  console.error('示例: node pipe_client.js "帮助" / "状态" / "分析 active"');
  process.exit(2);
}

let c;
try { c = cfg(); }
catch(e){ console.error(e.message); process.exit(1); }

const id = Date.now().toString(36) + '-' + crypto.randomBytes(3).toString('hex');
const payload = JSON.stringify({ id: id, token: c.token, command: command }) + '\n';
let buf = '';
let done = false;

const socket = net.connect(c.pipe_name, () => { socket.write(payload); });
socket.setEncoding('utf8');
socket.on('data', chunk => {
  buf += chunk;
  const i = buf.indexOf('\n');
  if(i < 0 || done) return;
  done = true;
  let res;
  try { res = JSON.parse(buf.slice(0, i)); }
  catch(e){ console.error('回执解析失败'); process.exit(1); }
  if(!res.ok){ console.error('管道拒绝：' + res.error); process.exit(1); }
  console.log(res.reply || ('[' + res.action + '] 无回执文本'));
  if(res.action === 'deliver_request') console.log('CONFIRM_CODE=' + res.code);
  if(res.job_id) console.log('JOB_ID=' + res.job_id);
  socket.end();
});
socket.on('error', e => {
  console.error('管道不可用：' + e.message + '（先启动 pipe_bridge.js）');
  process.exit(1);
});
socket.on('close', () => { if(!done) process.exit(1); });
