// 最小可跑服务（零依赖 Node）——只做两件事：静态文件 + 只读接口
//
//   node serve.example.js            → http://127.0.0.1:8787/workbench
//   node serve.example.js --port 9000
//
// 与生产用的 server.js 的区别：这里**没有**主应用（写库、CSV 重生成、采集、清理），
// 只挂 workbench_readonly.js 的 7 个只读接口，用来验证「壳 + 页面 + 配置」这一层。
// 端口优先级：--port > 环境变量 PORT > config/local/app.json 的 port > 8787。
const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const ROOT = __dirname;
const WBRO = require('./workbench_readonly.js');

function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return null; } }

const argv = process.argv.slice(2);
const iPort = argv.indexOf('--port');
const APP = readJSON(path.join(ROOT, 'config/local/app.json')) || readJSON(path.join(ROOT, 'config/app.example.json')) || {};
const PORT = Number(iPort >= 0 ? argv[iPort + 1] : process.env.PORT || APP.port || 8787);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.csv': 'text/csv; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8'
};

function send(res, code, body, type) {
  res.writeHead(code, { 'Content-Type': type || 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(body);
}

function json(res, obj, code) {
  send(res, code || 200, JSON.stringify(obj), 'application/json; charset=utf-8');
}

// 静态文件：解码中文名 → 规范化 → 必须仍在 ROOT 内（防 ../ 越界）
function serveStatic(p, res) {
  let rel;
  try { rel = decodeURIComponent(p); } catch (e) { return send(res, 400, '400 bad path'); }
  const abs = path.resolve(ROOT, '.' + path.posix.normalize(rel));
  if (abs !== ROOT && !abs.startsWith(ROOT + path.sep)) return send(res, 403, '403 forbidden');
  fs.stat(abs, (err, st) => {
    if (err || !st.isFile()) {
      return send(res, 404, '404 ' + rel + '\n\n文件不存在。若是简历/调研/台账等个人产物，说明还没生成或路径与 config 不符；'
        + '工作台内容区会显示兜底面板，可以点「回首页」继续。');
    }
    const type = MIME[path.extname(abs).toLowerCase()] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store', 'Content-Length': st.size });
    fs.createReadStream(abs).pipe(res);
  });
}

const server = http.createServer((req, res) => {
  const u = url.parse(req.url, true);
  const p = u.pathname;
  if (req.method !== 'GET' && req.method !== 'HEAD') return json(res, { ok: false, code: 'READ_ONLY', hint: '本服务只提供 GET' }, 405);

  // 别名：/ 与 /workbench 都进工作台
  // 注意：Location 头只能是 ASCII，中文文件名必须 percent-encode，否则 node 直接抛 ERR_INVALID_CHAR
  if (p === '/' || p === '/workbench') {
    res.writeHead(302, { Location: '/' + encodeURIComponent('工作台.html') });
    return res.end();
  }
  if (p.startsWith('/api/')) {
    // /api/config 由外壳直接消费（标题 / 渠道 / 产物目录），readonly 模块不管这块
    if (p === '/api/config') {
      const wb = readJSON(path.join(ROOT, 'config/local/workbuddy.json')) || readJSON(path.join(ROOT, 'config/workbuddy.example.json')) || {};
      const ch = readJSON(path.join(ROOT, 'config/local/channels.json')) || readJSON(path.join(ROOT, 'config/channels.example.json')) || {};
      const labels = ch.labels || {};
      // 占位符（<XXX>）不往下发，回退到中性默认值，避免顶栏显示「<WORKBENCH_TITLE>」
      const pick = (v, d) => (typeof v === 'string' && v && v.charAt(0) !== '<' ? v : d);
      return json(res, { ok: true, config: {
        workbench_title: pick(labels.workbench_title, '求职工作台'),
        app_title: pick(labels.app_title, ''),
        channels: (ch.channels || []).map((c) => ({ key: c.key, label: c.label, type: c.type })),
        adapter: wb.adapter || '', output_root: wb.output_root || '',
        tasks_ref: wb.tasks_ref || '', result_dirs: wb.result_dirs || {},
        storage_namespace: APP.storage_namespace || ''
      } });
    }
    // handle 内部已做 try/catch 与错误码，返回 true 表示这个路径归它管
    if (WBRO.handle(ROOT, p, u, res, json)) return;
    return json(res, { ok: false, code: 'NO_ROUTE', path: p }, 404);
  }
  serveStatic(p, res);
});

server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    console.error('端口 %d 已被占用。换一个：node serve.example.js --port 8788', PORT);
    process.exit(1);
  }
  throw e;
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('工作台   http://127.0.0.1:%d/workbench', PORT);
  console.log('接口自检 http://127.0.0.1:%d/api/routes', PORT);
  const miss = ['app', 'routes'].filter((n) => !fs.existsSync(path.join(ROOT, 'config/local', n + '.json')));
  if (miss.length) {
    console.log('\n提示：config/local/%s.json 还不存在，当前读的是 example 骨架（含 <占位符>）。', miss.join('.json、'));
    console.log('      cp config/%s.example.json config/local/%s.json 之后再填值。', miss[0], miss[0]);
  }
});
