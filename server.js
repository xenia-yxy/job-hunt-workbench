// 求职台账 · 轻量本地服务（零依赖 Node）
// 唯一数据源：config/local/app.json 的 files.data —— 页面任何增删改都会写回它，并自动重生成两份 CSV
// 启动：node server.js  →  浏览器打开 http://localhost:<port>/<pages.workbench>
// P2：端口 / CORS / 文件名 / 页面路由 / 渠道 / 概率规则 / 岗位族规则全部来自 config/ 与 rules/；
//     缺关键配置打印可读清单并退出非 0，绝不静默兜底。
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;

function readJSON(p){ try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return null; } }
// 配置 / 规则读取：local（工作区实例）> example（通用骨架）
function loadLocal(name){
  for(const f of ['config/local/' + name + '.json', 'config/' + name + '.example.json']){
    const v = readJSON(path.join(ROOT, f));
    if(v) return v;
  }
  return null;
}
function loadRule(name){
  for(const f of ['rules/local/' + name + '.json', 'rules/' + name + '.example.json']){
    const v = readJSON(path.join(ROOT, f));
    if(v) return v;
  }
  return null;
}

// ---- 关键配置门禁：缺任何一项都不静默启动 ----
(function requireLocal(){
  const REQUIRED = ['channels', 'app', 'routes', 'identity'];
  const missing = REQUIRED.filter(n => !fs.existsSync(path.join(ROOT, 'config/local/' + n + '.json')));
  if(missing.length){
    console.error('缺少本地实例配置：' + missing.join(', '));
    console.error('请把 config/<name>.example.json 复制到 config/local/<name>.json 并填写实例值。');
    console.error('缺配置不静默兜底，服务未启动。');
    process.exit(2);
  }
})();

const APP = loadLocal('app') || {};
const CH_CFG = loadLocal('channels') || {};
const CHANNELS = (CH_CFG.channels || []).filter(c => c.enabled !== false)
  .sort((a, b) => (a.order || 0) - (b.order || 0));
if(!CHANNELS.length){ console.error('channels 配置为空：至少需要一个 enabled 渠道'); process.exit(2); }
const KEYS = CHANNELS.map(c => c.key);
const CH_BY_KEY = {};
CHANNELS.forEach(c => { CH_BY_KEY[c.key] = c; });
const CSV_ORDER = (CH_CFG.csv_order && CH_CFG.csv_order.length ? CH_CFG.csv_order : KEYS)
  .filter(k => KEYS.indexOf(k) >= 0);
const CAMPUS_KEYS = KEYS.filter(k => CH_BY_KEY[k].type === 'campus');

const PROB_RULES = loadRule('prob');
if(!PROB_RULES){ console.error('缺少概率规则：rules/local/prob.json 或 rules/prob.example.json'); process.exit(2); }
const PE = require('./prob.js')(PROB_RULES, CHANNELS);
const WBRO = require('./workbench_readonly.js');

const F = APP.files || {};
const PAGES = APP.pages || {};
const PORT = Number(APP.port) || 8787;

// 文件名一律来自配置：缺项直接退出，不用默认值兜底——静默兜底会写错文件，比报错难查
function needFile(key){
  const v = F[key];
  if(!v){
    console.error('缺少 app.files.' + key + '（config/local/app.json）；文件名不设默认值，服务未启动。');
    process.exit(2);
  }
  return v;
}
const DATA_FILE  = path.join(ROOT, needFile('data'));
const BAK_FILE   = path.join(ROOT, needFile('backup'));
const CSV_MAIN   = path.join(ROOT, needFile('csv_main'));
const CSV_CAMPUS = path.join(ROOT, needFile('csv_campus'));
const APP_HTML   = needFile('app_html');
// 页面路由：工作台壳文件名 + 两组别名（旧书签继续可用）
const WB_PAGE    = PAGES.workbench || '工作台.html';
const WB_PATHS   = [].concat(PAGES.workbench_paths || ['/workbench']);
const HOME_PATHS = [].concat(PAGES.home_paths || ['/']);
const TITLE      = APP.workbench_title || '求职工作台';
const CORS = APP.cors || {origin:'*', methods:'GET,POST,PUT,DELETE,OPTIONS', headers:'Content-Type'};
const CACHE = (APP.static || {}).cache_control || 'no-store';

// 岗位族分类：local 实例 > 现有规则文件 > example 骨架；与 cleanup.py 共用同一口径
const FAMILY_RULES = (function(){
  const cands = ['rules/local/job_family.json', F.rules_fallback, 'rules/job_family.example.json'].filter(Boolean);
  for(const f of cands){
    const v = readJSON(path.join(ROOT, f));
    if(v && Array.isArray(v.rules) && v.rules.length) return v;
  }
  console.error('WARN: 岗位族规则不可用，回退为空规则集');
  return {rules:[], fallback:'岗位未获取/待确认', preferred:[], low:[]};
})();
function familyOf(role){
  const s = String(role || '');
  for(const rule of (FAMILY_RULES.rules || [])){
    if((rule.kw || []).some(k => s.indexOf(k) >= 0)) return rule.family;
  }
  return FAMILY_RULES.fallback;
}

const MIME = { '.html':'text/html;charset=utf-8', '.json':'application/json;charset=utf-8',
  '.js':'text/javascript;charset=utf-8', '.css':'text/css;charset=utf-8', '.csv':'text/csv;charset=utf-8',
  '.md':'text/plain;charset=utf-8', '.txt':'text/plain;charset=utf-8' };

function emptyData(){ const o = {}; KEYS.forEach(k => { o[k] = []; }); return o; }
function keysOf(d){
  const keys = KEYS.slice();
  Object.keys(d || {}).forEach(k => {
    if(Array.isArray(d[k]) && keys.indexOf(k) < 0) keys.push(k);   // 实际顶层键兜底，防丢记录
  });
  return keys;
}
function readData(){
  const d = readJSON(DATA_FILE);
  const out = emptyData();
  if(d && typeof d === 'object') keysOf(d).forEach(k => { if(Array.isArray(d[k])) out[k] = d[k]; });
  return out;
}
function countOf(d){ return Object.keys(d).reduce((n,k) => n + (d[k] || []).length, 0); }

// 写入前统一规整，保证渠道/来源一致、字段齐全；未知字段（含 source_refs）原样透传
function normalize(d){
  const out = emptyData();
  keysOf(d).forEach(k => {
    out[k] = (Array.isArray(d[k]) ? d[k] : []).map((r, i) => {
      const o = Object.assign({}, r || {});
      o.source = k;
      if(!o.id) o.id = k.slice(0,2) + '_' + Date.now().toString(36) + '_' + i;
      if(!Array.isArray(o.links)) o.links = [];
      if(!Array.isArray(o.messages)) o.messages = [];
      if(!Array.isArray(o.prob_reason)) o.prob_reason = [];
      if(o.prob != null && o.prob !== '') o.prob = Number(o.prob) || 0;
      if(o.status == null) o.status = '待办';
      if(o.note == null) o.note = '';
      o.job_family = familyOf(o.role);
      // 概率 = 岗位基础分 + 当前进度修正；每次写盘都按最新状态重算（幂等）
      return PE.apply(o);
    });
  });
  return out;
}

function esc(v){
  let s = (v == null ? '' : String(v));
  if(/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}
function writeCSV(file, header, rows){
  const lines = [header.map(esc).join(',')];
  rows.forEach(r => lines.push(r.map(esc).join(',')));
  fs.writeFileSync(file, '\ufeff' + lines.join('\n'), 'utf8');
}
function linksOf(r){ return (r.links || []).map(l => (l.label || '') + ':' + (l.url || '')).join(' | '); }

// 两份 CSV 完全由同一份数据生成，永不错位；渠道名与顺序由 channels.json 单一提供
function regenCSV(d){
  const label = {};
  CHANNELS.forEach(c => { label[c.key] = c.csv_label || c.label || c.key; });
  const rows = [];
  CSV_ORDER.forEach(k => {
    (d[k] || []).forEach(r => rows.push([
      label[k] || k, r.company||'', r.hr_name||'', r.role||'', r.job_family||'', r.salary||'', r.stage||'',
      r.status||'', r.type||'', r.is_read ? '是' : '未读', r.deadline||'',
      r.summary||r.last_msg||'', (r.messages||[]).length, r.msg_complete ? '逐条完整' : '仅快照',
      (r.messages||[]).map(m => '[' + (m.from||'记录') + '] ' + (m.text||'')).join(' ｜ '),
      linksOf(r), r.prob != null ? r.prob+'%' : '',
      r.prob_stage||'', (r.prob_reason||[]).join('；'), r.prob_next||'',
      r.prob_flag||'', r.opportunity_score != null ? r.opportunity_score : '', r.captured_at||''
    ]));
  });
  writeCSV(CSV_MAIN, [(CH_CFG.labels && CH_CFG.labels.csv_main_header) || '来源','公司','HR','岗位','岗位分类','薪资','阶段','我的状态','类型','已读','截止',
    '摘要/最后消息','沟通记录数','消息完整度','完整沟通记录','链接','录取概率','当前进度','概率依据',
    '下一步建议','风险标记','机会分','快照'], rows);

  const crows = [];
  CAMPUS_KEYS.forEach(k => (d[k] || []).forEach(r => crows.push([
    r.company||'', r.role||'', r.job_family||'', r.batch||'校招', r.stage||'', r.status||'', r.type||'',
    r.is_read ? '是' : '否(未读)', r.deadline||'', r.deadline_note||'', r.received_at||r.captured_at||'',
    r.from||'', r.summary||'', (r.messages||[]).map(m => '[' + (m.from||'记录') + '] ' + (m.text||'')).join(' ｜ '),
    linksOf(r), r.prob != null ? r.prob+'%' : '',
    r.prob_stage||'', (r.prob_reason||[]).join('；'), r.prob_next||'', r.prob_flag||''
  ])));
  writeCSV(CSV_CAMPUS, ['公司','岗位','岗位分类','届别','阶段','我的状态','类型','已读','截止','截止说明',
    '收到时间','发件人','摘要','完整沟通记录','链接','录取概率','当前进度','概率依据','下一步建议','风险标记'], crows);
}

// 原子写 + 自动备份（防误删）
function saveData(d, withBackup){
  const norm = normalize(d);
  if(withBackup !== false && fs.existsSync(DATA_FILE)){
    try { fs.copyFileSync(DATA_FILE, BAK_FILE); } catch(e){}
  }
  const tmp = DATA_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(norm, null, 1), 'utf8');
  fs.renameSync(tmp, DATA_FILE);
  regenCSV(norm);
  return norm;
}

function json(res, obj, code){
  res.writeHead(code || 200, {'Content-Type':'application/json;charset=utf-8', 'Cache-Control':'no-store'});
  res.end(JSON.stringify(obj));
}
function readBody(req, cb){
  let b = '';
  req.on('data', c => { b += c; if(b.length > 8e6) req.destroy(); });
  req.on('end', () => cb(b));
}
function serveFile(res, name){
  const fp = path.join(ROOT, name);
  fs.readFile(fp, (err, buf) => {
    if(err){ res.writeHead(404); return res.end('404'); }
    res.writeHead(200, {'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream', 'Cache-Control':CACHE});
    res.end(buf);
  });
}

const server = http.createServer((req, res) => {
  // 允许 file:// 预览面板 / 其他端口跨域连到本服务
  res.setHeader('Access-Control-Allow-Origin', CORS.origin || '*');
  res.setHeader('Access-Control-Allow-Methods', CORS.methods || 'GET,POST,PUT,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', CORS.headers || 'Content-Type');
  if(req.method === 'OPTIONS'){ res.writeHead(204); return res.end(); }

  const u = new URL(req.url, 'http://localhost');
  // 中文文件名在浏览器里会被 percent-encode，必须解码后再与路由表 / 磁盘路径比对
  let p;
  try { p = decodeURIComponent(u.pathname); } catch(e){ p = u.pathname; }

  // ---- 工作台：只读数据链（独立模块，内存聚合、零落盘、幂等）----
  if(WBRO.handle(ROOT, p, u, res, json)) return;

  // ---- 工作台：只读配置 ----
  if(p === '/api/routes'){
    return json(res, {ok:true, routes: loadLocal('routes') || {groups:[], items:[]}});
  }
  if(p === '/api/config'){
    const ap = loadLocal('app') || {};
    const wb = loadLocal('workbuddy') || {};
    return json(res, {ok:true, config:{
      app_title:(CH_CFG.labels || {}).app_title || '',
      workbench_title: APP.workbench_title || (CH_CFG.labels || {}).workbench_title || '',
      channels:CHANNELS.map(c => ({key:c.key, label:c.label, type:c.type})),
      storage_namespace:(ap.storage_namespace || ''),
      ai_disabled_hint:(wb.ai_disabled_hint || ''),
      adapter:(wb.adapter || ''),
      output_root:(wb.output_root || ''),
      tasks_ref:(wb.tasks_ref || ''),
      result_dirs:(wb.result_dirs || {})
    }});
  }

  // ---- 主 API：整份数据读 / 写 ----
  if(p === '/api/data' && req.method === 'GET'){
    const d = readData();
    return json(res, {ok:true, data:d, count:countOf(d), hasBackup:fs.existsSync(BAK_FILE)});
  }
  if(p === '/api/data' && (req.method === 'PUT' || req.method === 'POST')){
    return readBody(req, body => {
      try {
        const payload = JSON.parse(body || '{}');
        const d = payload.data || payload;
        const norm = saveData(d, payload.backup !== false);
        return json(res, {ok:true, count:countOf(norm), updatedAt:new Date().toISOString()});
      } catch(e){ return json(res, {ok:false, err:String(e)}, 500); }
    });
  }
  if(p === '/api/restore' && (req.method === 'POST' || req.method === 'PUT')){
    try {
      if(!fs.existsSync(BAK_FILE)) return json(res, {ok:false, err:'暂无备份文件'}, 404);
      const d = readJSON(BAK_FILE);
      if(!d) return json(res, {ok:false, err:'备份文件不可读'}, 500);
      const norm = saveData(d, false);
      return json(res, {ok:true, count:countOf(norm), restored:true});
    } catch(e){ return json(res, {ok:false, err:String(e)}, 500); }
  }

  // ---- 一键清理：按 cleanup.py 的固定口径只留「有沟通 + 有后续机会」的记录 ----
  if(p === '/api/cleanup'){
    if(req.method !== 'POST') return json(res, {ok:false, err:'需要 POST'}, 405);
    try {
      const cp = require('child_process');
      const raw = cp.execFileSync(process.platform === 'win32' ? 'python' : 'python3',
        ['cleanup.py', '--apply', '--no-http'],
        {cwd: ROOT, encoding: 'utf8', timeout: 60000, windowsHide: true});
      let stat = {};
      try { stat = JSON.parse(String(raw).slice(String(raw).indexOf('{'))); } catch(e){}
      const d = readData();
      regenCSV(d);
      return json(res, {ok:true, count:countOf(d), stat});
    } catch(e){ return json(res, {ok:false, err:String(e && e.message || e)}, 500); }
  }

  // ---- 页面内 AI 已停用：分析与话术全部由外部会话承担 ----
  if(p === '/api/ai'){
    const hint = (loadLocal('workbuddy') || {}).ai_disabled_hint ||
      '页面内 AI 已停用，请在外部 AI 会话中执行分析';
    return json(res, {ok:false, code:'AI_REMOVED', hint}, 410);
  }

  // ---- 兼容旧页面的 /api/load、/api/save（映射到同一数据源，不会分叉）----
  if(p === '/api/load'){
    const file = u.searchParams.get('file') || 'unified';
    const d = readData();
    const out = {};
    keysOf(d).forEach(k => {
      if(file === 'campus' && CH_BY_KEY[k] && CH_BY_KEY[k].type !== 'campus') return;
      (d[k] || []).forEach(r => {
        if(r.status || r.note){
          const id = (file === 'campus') ? String(r.id).replace(/^cx_/, '') : r.id;
          out[id] = {status:r.status || '', note:r.note || ''};
        }
      });
    });
    return json(res, out);
  }
  if(p === '/api/save' && req.method === 'POST'){
    return readBody(req, body => {
      try {
        const {file, store} = JSON.parse(body || '{}');
        const d = readData();
        const patch = r => {
          const key = (file === 'campus') ? String(r.id).replace(/^cx_/, '') : r.id;
          const v = (store || {})[key];
          if(!v) return;
          if('status' in v) r.status = v.status || '';
          if('note' in v) r.note = v.note || '';
        };
        if(file === 'campus') CAMPUS_KEYS.forEach(k => (d[k] || []).forEach(patch));
        else keysOf(d).forEach(k => (d[k] || []).forEach(patch));
        saveData(d, false);
        return json(res, {ok:true});
      } catch(e){ return json(res, {ok:false, err:String(e)}, 500); }
    });
  }

  // ---- 页面路由：工作台 + 主应用（别名来自 config/local/app.json 的 pages）----
  if(WB_PATHS.indexOf(p) >= 0 || p === '/' + WB_PAGE){ return serveFile(res, WB_PAGE); }
  if(HOME_PATHS.indexOf(p) >= 0 || p === '' || p === '/' + APP_HTML){
    return serveFile(res, APP_HTML);
  }
  if(p === '/health') return json(res, {ok:true});

  const fp = path.join(ROOT, path.normalize(p).replace(/^(\.\.[/\\])+/, ''));
  fs.readFile(fp, (err, buf) => {
    if(err){ res.writeHead(404); return res.end('404'); }
    res.writeHead(200, {'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream', 'Cache-Control': CACHE});
    res.end(buf);
  });
});

server.on('error', e => {
  if(e.code === 'EADDRINUSE'){ console.log(PORT + ' 端口已有服务在运行，本实例自动退出（不影响现有服务）。'); process.exit(0); }
  console.error(e); process.exit(1);
});
server.listen(PORT, () => {
  console.log(TITLE + ' 已启动： http://localhost:' + PORT + '/');
  console.log('  工作台： http://localhost:' + PORT + (WB_PATHS[0] || '/' + WB_PAGE));
  console.log('  数据源： ' + F.data + '（' + countOf(readData()) + ' 条）');
  console.log('  渠道： ' + KEYS.join(' / '));
  console.log('  改动实时写回 JSON + ' + F.csv_main + ' + ' + F.csv_campus);
});
