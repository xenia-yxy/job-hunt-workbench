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
// 任务总线：页面按钮 → jobs/<id>.json → 脚本类就地执行 / 智能体类等外部取件
const JOBS = require('./job_bus.js').createBus(ROOT, loadLocal);
// 投递条件读写：规则从 jkit inspect 取，不在这里复制阈值
const COND = require('./conditions_api.js').createApi(ROOT, loadLocal);
// 图谱内容只读索引：定位页「分析结果」点开就地展开，不跳 atlas.html
const ATLAS = require('./atlas_api.js').createApi(ROOT, APP);
// 台账数据层：唯一的写入口，版本化 + 记录级 CRUD + 串行写
const STORE = require('./data_store.js');
// 投递事件流 → 跟踪台账 的单向回流
const LEDGER = require('./ledger_import.js');
// 画像：唯一写入口（手填 / 简历回写 / 智能体回写都走它）
const PROFILE = require('./profile_store.js').create({ root: ROOT });
const RESUME = require('./resume_api.js').createApi({ root: ROOT, profile: PROFILE });

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
  return DB.read().data;
}
function countOf(d){ return DB.countOf(d); }

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

// 数据层：JSON 是唯一真相，CSV 是派生品；写盘失败必须能看见，写成功不能报错
const DB = STORE.create({
  root: ROOT,
  dataFile: DATA_FILE,
  backupFile: BAK_FILE,
  keys: KEYS,
  normalize,
  regenCSV
});

function json(res, obj, code){
  res.writeHead(code || 200, {'Content-Type':'application/json;charset=utf-8', 'Cache-Control':'no-store'});
  res.end(JSON.stringify(obj));
}
function readBody(req, cb, res){
  let b = '';
  let tooLarge = false;
  // 16MB：简历上传走 base64（8MB 文件 ≈ 10.7MB 字符串），要留出余量。
  // 超限不能直接 destroy —— 那样浏览器只会看到「连接被重置」，
  // 正确的做法是收完流之后回一个能读懂的 413。
  req.on('data', c => {
    if(tooLarge) return;
    if(b.length + c.length > 16e6){ tooLarge = true; b = ''; return; }
    b += c;
  });
  req.on('end', () => {
    if(tooLarge){
      if(res) return json(res, {ok:false, code:'TOO_LARGE', hint:'请求体超过 16MB'}, 413);
      return cb('');
    }
    cb(b);
  });
  // 返回 true：调用点普遍写成 `if(...) return readBody(req, cb);`
  // ——readBody 是异步的，返回值只有「接住了 / 没接住」这一个含义。
  // 这里返回 undefined 会让请求继续往下走静态兜底，造成二次写头
  // （ERR_HTTP_HEADERS_SENT，浏览器侧表现为连接被重置）。
  return true;
}
// 画像写入用：对象递归合并，数组整体替换（tech_stack 不能按下标缝补）
function mergeDeep(base, patch){
  const out = (base && typeof base === 'object' && !Array.isArray(base)) ? Object.assign({}, base) : {};
  const src = patch || {};
  for(const k of Object.keys(src)){
    const v = src[k];
    if(v === undefined) continue;
    if(v && typeof v === 'object' && !Array.isArray(v)
       && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])){
      out[k] = mergeDeep(out[k], v);
    } else {
      out[k] = v;
    }
  }
  return out;
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

  // ---- 画像写入：改一次，所有投递方向跟着变 ----
  // 这是「我是谁」的唯一真相。conditions/*.json 通过 profile_ref 引用它，
  // 所以写盘前必须留档 —— 一次误改会同时改变所有方向的筛选口径。
  // 必须排在 WBRO.handle 之前：只读模块对 /api/profile 只应答 GET，POST 到这里才算写入。
  if(p === '/api/profile' && (req.method === 'POST' || req.method === 'PUT')){
    return readBody(req, body => {
      try{
        const patch = JSON.parse(body || '{}');
        if(!patch || typeof patch !== 'object' || Array.isArray(patch)){
          return json(res, {ok:false, code:'BAD_BODY', hint:'画像补丁必须是 JSON 对象'}, 400);
        }
        const r = PROFILE.apply(patch, {
          source: patch.__source || 'manual',
          backup: true
        });
        return json(res, {ok:true, created:r.created, profile:r.profile, backup:r.backup});
      }catch(e){ return json(res, {ok:false, code:'WRITE_FAIL', err:String(e)}, 500); }
    });
  }

  // ---- 简历：上传 / 列表 / 正文 / 删除 / 回写画像 ----
  // 上传的是文件与抽取出来的纯文本；姓名、电话、邮箱一律不提取、不入库。
  if(RESUME.handle(req, res, p, u, json, (rq, cb) => readBody(rq, cb, res))) return;

  // ---- 工作台：只读数据链（独立模块，内存聚合、零落盘、幂等）----
  if(WBRO.handle(ROOT, p, u, res, json)) return;

  // ---- 任务总线：点一下就执行，脚本类秒级、智能体类排队等取件 ----
  if(JOBS.handle(req, res, p, u, json, readBody)) return;

  // ---- 图谱内容只读索引：给定位页分析结果就地展开用 ----
  if(ATLAS.handle(req, res, p, u, json)) return;

  // ---- 投递条件：六份条件的人话视图与白名单写入 ----
  if(COND.handle(req, res, p, u, json, readBody)) return;

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

  // ---- 主 API：整份读 / 写（写带版本号，过期即冲突，绝不静默覆盖）----
  if(p === '/api/data' && req.method === 'GET'){
    const cur = DB.read();
    return json(res, {ok:true, data:cur.data, count:DB.countOf(cur.data),
      version:cur.version, hasBackup:fs.existsSync(BAK_FILE)});
  }
  if(p === '/api/data' && (req.method === 'PUT' || req.method === 'POST')){
    return readBody(req, async body => {
      try {
        const payload = JSON.parse(body || '{}');
        const d = payload.data || payload;
        const r = await DB.replaceAll(d, { backup: payload.backup !== false, baseVersion: payload.baseVersion });
        return json(res, {ok:true, count:r.count, version:r.version, updatedAt:new Date().toISOString(),
          csv_warning:r.csvError || undefined});
      } catch(e){
        if(e.code === 'VERSION_CONFLICT'){
          // 页面拿着旧快照来覆盖：拒绝，并把最新版本回给页面，由页面决定合并还是重载
          return json(res, {ok:false, code:'VERSION_CONFLICT', err:String(e.message),
            version:e.current && e.current.version, data:e.current && e.current.data}, 409);
        }
        return json(res, {ok:false, code:e.code || 'WRITE_FAIL', err:String(e && e.message || e)}, 500);
      }
    });
  }

  // ---- 记录级增删改查：页面每改一条只动这一条，不做整份覆盖 ----
  //   POST   /api/data/:channel        {record}
  //   PATCH  /api/data/:channel/:id    {patch}
  //   DELETE /api/data/:channel/:id
  //   GET    /api/data/:channel/:id
  const recMatch = p.match(/^\/api\/data\/([^/]+)(?:\/([^/]+))?$/);
  if(recMatch){
    let ch = recMatch[1], id = recMatch[2];
    try { ch = decodeURIComponent(ch); if(id) id = decodeURIComponent(id); } catch(e){}
    const fail = (e) => {
      const codes = {BAD_CHANNEL:400, BAD_RECORD:400, NOT_FOUND:404, VERSION_CONFLICT:409};
      return json(res, {ok:false, code:e.code || 'WRITE_FAIL', err:String(e && e.message || e)}, codes[e.code] || 500);
    };
    if(req.method === 'GET'){
      if(!id) return json(res, {ok:false, code:'NOT_FOUND', err:'需要记录 id'}, 404);
      const f = DB.find(ch, id);
      return f.record ? json(res, {ok:true, record:f.record, version:f.version})
                      : json(res, {ok:false, code:'NOT_FOUND', err:'记录不存在'}, 404);
    }
    if(req.method === 'POST' && !id){
      return readBody(req, async body => {
        try {
          const payload = JSON.parse(body || '{}');
          const rec = payload.record || payload;
          const r = await DB.create(ch, rec, { backup: payload.backup !== false });
          return json(res, {ok:true, record:r.record, version:r.version, csv_warning:r.csvError || undefined}, 201);
        } catch(e){ return fail(e); }
      });
    }
    if((req.method === 'PATCH' || req.method === 'PUT') && id){
      return readBody(req, async body => {
        try {
          const payload = JSON.parse(body || '{}');
          const patch = payload.patch || payload;
          const r = await DB.update(ch, id, patch, { backup: payload.backup !== false, baseVersion: payload.baseVersion });
          return json(res, {ok:true, record:r.record, version:r.version, csv_warning:r.csvError || undefined});
        } catch(e){ return fail(e); }
      });
    }
    if(req.method === 'DELETE' && id){
      return DB.remove(ch, id, { backup:true })
        .then(r => json(res, {ok:true, removed:r.removed, version:r.version, csv_warning:r.csvError || undefined}))
        .catch(fail);
    }
  }

  // ---- 投递事件流 → 跟踪台账 单向回流（幂等，可反复点）----
  if(p === '/api/ledger/import'){
    if(req.method !== 'POST') return json(res, {ok:false, err:'需要 POST'}, 405);
    return readBody(req, body => {
      try {
        let payload = {};
        try { payload = JSON.parse(body || '{}'); } catch(e){}
        const wb = loadLocal('workbuddy') || {};
        const run = LEDGER.importLedger(DB, {
          root: ROOT,
          runsDir: (APP.source_paths && APP.source_paths.runs) || wb.runs_dir || 'runs',
          channels: KEYS,
          dryRun: !!payload.dry_run,
          runIds: Array.isArray(payload.run_ids) ? payload.run_ids : null
        });
        Promise.resolve(run)
          .then(r => json(res, r))
          .catch(e => json(res, {ok:false, err:String(e && e.message || e)}, 500));
      } catch(e){ return json(res, {ok:false, err:String(e && e.message || e)}, 500); }
    });
  }

  if(p === '/api/restore' && (req.method === 'POST' || req.method === 'PUT')){
    return (async () => {
      try {
        if(!fs.existsSync(BAK_FILE)) return json(res, {ok:false, err:'暂无备份文件'}, 404);
        const d = readJSON(BAK_FILE);
        if(!d) return json(res, {ok:false, err:'备份文件不可读'}, 500);
        const r = await DB.replaceAll(d, { backup:false });
        return json(res, {ok:true, count:r.count, version:r.version, restored:true});
      } catch(e){ return json(res, {ok:false, err:String(e && e.message || e)}, 500); }
    })();
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
    return readBody(req, async body => {
      try {
        const payload = JSON.parse(body || '{}');
        const patch = payload.patch || payload.store || {};
        if(!patch || typeof patch !== 'object' || Array.isArray(patch)){
          return json(res, {ok:false, code:'BAD_BODY', err:'patch 必须是对象'}, 400);
        }
        // 旧接口只改 status / note：逐条走记录级写入，不整份覆盖
        const file = payload.file;
        const channels = (file === 'campus') ? CAMPUS_KEYS : keysOf(readData());
        const cur = DB.read();
        const ops = [];
        channels.forEach(ch => (cur.data[ch] || []).forEach(r => {
          const key = (file === 'campus') ? String(r.id).replace(/^cx_/, '') : r.id;
          const v = patch[key];
          if(!v) return;
          const p = {};
          if('status' in v) p.status = v.status || '';
          if('note' in v) p.note = v.note || '';
          if(Object.keys(p).length) ops.push(DB.update(ch, r.id, p, { backup:false }));
        }));
        await Promise.all(ops);
        const after = DB.read();
        return json(res, {ok:true, version:after.version, updated:ops.length});
      } catch(e){ return json(res, {ok:false, code:e.code || 'WRITE_FAIL', err:String(e && e.message || e)}, 500); }
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
    if(res.headersSent) return;          // 前面已经应答过，别再写一次头
    if(err){ res.writeHead(404); return res.end('404'); }
    res.writeHead(200, {'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream', 'Cache-Control': CACHE});
    res.end(buf);
  });
});

server.on('error', e => {
  if(e.code === 'EADDRINUSE'){ console.log(PORT + ' 端口已有服务在运行，本实例自动退出（不影响现有服务）。'); process.exit(0); }
  console.error(e); process.exit(1);
});

// 本地单人工具：单个请求抛错不应该带走整个服务，但必须喊出来，不静默
process.on('uncaughtException', e => { console.error('[uncaught] ' + (e && e.stack || e)); });
process.on('unhandledRejection', e => { console.error('[unhandled] ' + (e && e.stack || e)); });

server.listen(PORT, () => {
  const cur = DB.read();
  console.log(TITLE + ' 已启动： http://localhost:' + PORT + '/');
  console.log('  工作台： http://localhost:' + PORT + (WB_PATHS[0] || '/' + WB_PAGE));
  console.log('  数据源： ' + F.data + '（' + DB.countOf(cur.data) + ' 条 · 版本 ' + cur.version + '）');
  console.log('  渠道： ' + KEYS.join(' / '));
  console.log('  写入：记录级增删改查 + 版本冲突保护；JSON 是唯一真相，CSV 为派生文件');
});
