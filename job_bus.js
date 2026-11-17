// 任务总线（零依赖 Node）
// ---------------------------------------------------------------------------
// 解决的问题：页面上的按钮只能「复制一段指令」，人再去对话里粘、跑完再回来刷新。
// 这里把点击变成一条可追踪的任务：
//
//   页面 → POST /api/job → jobs/<id>.json (pending)
//                              ├─ 脚本类：本进程 spawn，日志写 jobs/<id>.log，跑完改 done/failed
//                              └─ 智能体类：留在 pending，由外部（WorkBuddy）取件执行后回写
//
// 两类任务的接口完全一样，页面不需要知道区别，只需要轮询 /api/job/:id。
//
// 安全：kind 必须命中白名单（内置 + config/local/jobs.json 覆盖），
// 条件文件只允许落在 config/local/conditions/ 之内，数字参数一律夹紧。
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const DEFAULT_TIMEOUT = 300;        // 秒
const MAX_LIST = 200;

function nowIso(){ return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'); }
function rid(){
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  const stamp = d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-'
    + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  return 'j' + stamp + '-' + Math.random().toString(36).slice(2, 6);
}

function createBus(ROOT, loadLocal){
  const DIR = path.join(ROOT, 'jobs');
  const PY = process.env.JKIT_PYTHON || process.env.PYTHON || 'python';
  const running = new Map();   // id -> child

  function ensureDir(){ fs.mkdirSync(DIR, { recursive: true }); }

  // ---- kind 白名单 ----------------------------------------------------------
  // 内置项全部基于 jkit 的通用子命令，不含任何实例文件名。
  // 实例专属动作（例如清理台账）通过 config/local/jobs.json 的 kinds 段追加。
  const BUILTIN = {
    'jkit-check': {
      runner: 'script', title: '校验投递条件',
      argv: p => ['-m', 'jkit', 'conditions', p.conditions, '--check'],
      timeout: 60
    },
    'jkit-inspect': {
      runner: 'script', title: '读取条件与规则',
      argv: p => ['-m', 'jkit', 'inspect', p.conditions],
      timeout: 60
    },
    'jkit-dry-run': {
      runner: 'script', title: '试跑判定（不投）',
      argv: p => ['-m', 'jkit', 'apply', '--conditions', p.conditions,
        '--platform', p.platform, '--dry-run']
        .concat(p.max !== undefined ? ['--max', String(p.max)] : [])
        .concat(p.budget_min !== undefined ? ['--budget-min', String(p.budget_min)] : [])
        .concat(p.recheck ? ['--recheck'] : [])
        .concat(p.result_path ? ['--json-out', p.result_path] : []),
      timeout: 600, result: 'apply'
    },
    'jkit-deliver': {
      runner: 'script', title: '真实投递', needsConfirm: true,
      argv: p => ['-m', 'jkit', 'apply', '--conditions', p.conditions,
        '--platform', p.platform]
        .concat(p.max !== undefined ? ['--max', String(p.max)] : [])
        .concat(p.budget_min !== undefined ? ['--budget-min', String(p.budget_min)] : [])
        .concat(p.recheck ? ['--recheck'] : [])
        .concat(p.result_path ? ['--json-out', p.result_path] : []),
      timeout: 1800, result: 'apply'
    },
    'position-analysis': {
      runner: 'script', title: '分析方向（画像 × 市场）',
      argv: p => ['-m', 'jkit', 'analyze', '--scope', p.scope || 'active']
        .concat(p.result_path ? ['--json-out', p.result_path] : []),
      timeout: 120, result: 'analyze'
    },
    // ---- 智能体类：只排队，不在此执行 ----
    // 复用「建任务 → WorkBuddy 会话按 SOP 执行 → POST /api/job/:id/result 回写」的既有流程。
    // 指令模板放在 config/local/tasks/（或模板轨 templates/workbuddy/tasks/），页面可复制。
    'market-research': { runner: 'agent', title: '市场调研' },
    'resume-rewrite': { runner: 'agent', title: '简历改写' },
    'reply-draft': { runner: 'agent', title: '回复草稿' },
    'jd-explain': { runner: 'agent', title: 'JD 解读' },
    'position-deep-analysis': { runner: 'agent', title: '方向深度分析（WorkBuddy · SOP）' },
    'track-sync': { runner: 'agent', title: '抓取今日跟踪动态（WorkBuddy · 只读）' }
  };

  // 投递引擎（jkit）是可选的：模板轨没有它，暴露一堆跑不起来的按钮只会误导。
  // 这里按目录存在性判断，任务列表带 available 标记，create 直接拒绝。
  const HAS_JKIT = fs.existsSync(path.join(ROOT, 'jkit', '__main__.py'));
  const NEEDS_JKIT = ['jkit-check', 'jkit-inspect', 'jkit-dry-run', 'jkit-deliver',
                      'position-analysis'];

  function kinds(){
    const out = {};
    for(const k of Object.keys(BUILTIN)){
      const available = NEEDS_JKIT.indexOf(k) >= 0 ? HAS_JKIT : true;
      out[k] = { ...BUILTIN[k], available };
    }
    const cfg = (loadLocal('jobs') || {});
    for(const k of Object.keys(cfg.kinds || {})){
      const v = cfg.kinds[k] || {};
      out[k] = {
        runner: v.runner === 'agent' ? 'agent' : 'script',
        title: v.title || k,
        needsConfirm: !!v.needsConfirm,
        available: true,
        // 配置里给的是 argv 模板（数组），占位符 {conditions}/{platform} 由参数替换
        argv_tpl: Array.isArray(v.argv) ? v.argv : null,
        timeout: Number(v.timeout) || DEFAULT_TIMEOUT
      };
    }
    return out;
  }

  // ---- 参数校验 ------------------------------------------------------------
  function safeConditions(v){
    const s = String(v || '');
    if(!/^[A-Za-z0-9._\-\/]+$/.test(s)) return '';
    const norm = s.replace(/\\/g, '/');
    if(norm.indexOf('..') >= 0) return '';
    const full = path.resolve(ROOT, norm);
    const base = path.resolve(ROOT, 'config', 'local', 'conditions');
    if(full !== base && !full.startsWith(base + path.sep)) return '';
    if(!fs.existsSync(full)) return '';
    return norm;
  }
  function clampInt(v, lo, hi, dflt){
    const n = Math.round(Number(v));
    if(!isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  }
  function clampNum(v, lo, hi, dflt){
    const n = Number(v);
    if(!isFinite(n)) return dflt;
    return Math.min(hi, Math.max(lo, n));
  }

  function normalize(kind, params){
    const p = {};
    const r = params || {};
    if(kind.indexOf('jkit-') === 0){
      const c = safeConditions(r.conditions);
      if(!c) return { err: 'conditions 必须是 config/local/conditions/ 下已存在的文件' };
      p.conditions = c;
      p.platform = ['boss', 'zhilian'].indexOf(r.platform) >= 0 ? r.platform : 'boss';
      // P-A：max / budget_min 缺省时**不写进 params**，让条件文件 deliver.max / budget_min
      // 成为唯一来源。以前默认 20/15 会经 CLI 参数覆盖条件文件（用户"改了不生效"的根因）。
      if(r.max !== undefined && r.max !== null && r.max !== '') p.max = clampInt(r.max, 1, 200, 20);
      if(r.budget_min !== undefined && r.budget_min !== null && r.budget_min !== '')
        p.budget_min = clampNum(r.budget_min, 1, 240, 15);
    }
    if(kind === 'position-analysis' || kind === 'position-deep-analysis'){
      p.scope = r.scope === 'all' ? 'all' : 'active';
    }
    // 深度分析任务只带一个可选的"用户补充想法"，长度夹紧；画像与市场数据由
    // WorkBuddy 会话走只读接口取，不把整份个人画像塞进任务文件落盘。
    if(kind === 'position-deep-analysis' && typeof r.note === 'string'){
      p.note = r.note.slice(0, 500);
    }
    // 跟踪抓取任务：渠道范围白名单 + 用户补充夹紧；平台数据由会话只读抓，不落进任务文件。
    if(kind === 'track-sync'){
      const chans = Array.isArray(r.channels) ? r.channels.filter(x => ['boss', 'zhilian'].indexOf(x) >= 0) : [];
      p.channels = chans.length ? chans.slice(0, 4) : ['boss'];
      if(typeof r.note === 'string') p.note = r.note.slice(0, 500);
    }
    for(const k of Object.keys(r)){
      if(k in p) continue;
      if(['confirm', 'account', 'note'].indexOf(k) >= 0) p[k] = r[k];
    }
    if(kind.indexOf('jkit-') === 0) p.recheck = r.recheck === true;
    if(r.confirm === true) p.confirm = true;
    return { params: p };
  }

  // ---- 交互持久化：指令原文 / 回执 / 结果 / AI 写入 -------------------------
  // 目标（用户 P3）：任意一次按钮/管道指令，刷新后能看到完整链路，原文逐字一致。
  // 索引写在 jobs/<id>.json 的字段里；不可变产物写在 workbuddy_out/interactions/<id>/。
  // output_root 从 workbuddy 配置读，不写死实例目录名（模板轨同样适用）。
  const OUT_ROOT = (function(){
    const w = loadLocal('workbuddy') || {};
    return String(w.output_root || 'workbuddy_out');
  })();
  function interactionDir(id){ return path.join(ROOT, OUT_ROOT, 'interactions', id); }
  function writeInteraction(id, name, text){
    try {
      const d = interactionDir(id);
      fs.mkdirSync(d, { recursive: true });
      const s = String(text == null ? '' : text);
      fs.writeFileSync(path.join(d, name), s + (/\n$/.test(s) ? '' : '\n'), 'utf8');
      return relOut(path.join(OUT_ROOT, 'interactions', id, name));
    } catch(e){ return ''; }
  }
  function relOut(sub){ return String(sub).replace(/\\/g, '/'); }
  function writeRequest(job){
    const src = job.command_source || 'page';
    const txt = job.command_text || (job.kind + '  ' + JSON.stringify(job.params || {}));
    return writeInteraction(job.id, 'request.md', [
      '# 指令原文', '',
      '- 任务：`' + job.id + '`',
      '- 类型：`' + job.kind + '`',
      '- 来源：`' + src + '`',
      '- 时间：' + job.created_at, '',
      '```text', txt, '```', ''
    ].join('\n'));
  }
  function writeResultArtifact(id, result, reply){
    const resp = writeInteraction(id, 'response.md', reply || '(无回执文本)');
    const res = result ? writeInteraction(id, 'result.json', JSON.stringify(result, null, 2)) : '';
    return [resp, res].filter(Boolean);
  }
  function writeWrites(id, writes){
    if(!writes || !writes.length) return '';
    return writeInteraction(id, 'writes.jsonl', writes.map(w => JSON.stringify(w)).join('\n'));
  }
  function readArtifact(id, name){
    try { return fs.readFileSync(path.join(interactionDir(id), name), 'utf8'); } catch(e){ return ''; }
  }

  // ---- 落盘 ----------------------------------------------------------------
  function jobFile(id){ return path.join(DIR, id + '.json'); }
  function logFile(id){ return path.join(DIR, id + '.log'); }

  function readJsonFile(p){
    try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return null; }
  }

  // 把 apply 的结构化结果压成一句话，页面顶上显示的就是它
  function applySummary(d){
    const s = d.stats || {};
    const n = (d.items || []).length;
    const head = d.mode === 'dry_run' ? '试跑' : '真实投递';
    const skip = (s.already || s.dup)
      ? ('跳过 ' + (s.skip || 0) + '（历史已判 ' + (s.already || 0) + '）')
      : ('跳过 ' + (s.skip || 0));
    return head + '：判定 ' + n + ' 条（读取 ' + (s.checked || 0)
      + ' ｜ 投出 ' + (s.delivered || 0) + ' ｜ 淘汰 ' + (s.drop || 0)
      + ' ｜ ' + skip + '）'
      + (d.run_id ? ' · run ' + d.run_id : '');
  }

  function read(id){
    if(!/^j[0-9a-z\-]+$/i.test(String(id || ''))) return null;
    try { return JSON.parse(fs.readFileSync(jobFile(id), 'utf8')); } catch(e){ return null; }
  }
  function write(job){
    ensureDir();
    const tmp = jobFile(job.id) + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(job, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, jobFile(job.id));
    return job;
  }
  function patch(id, fields){
    const cur = read(id);
    if(!cur) return null;
    return write({ ...cur, ...fields });
  }

  function list(opt){
    const o = opt || {};
    ensureDir();
    let names = [];
    try { names = fs.readdirSync(DIR).filter(n => /^j.*\.json$/.test(n)); } catch(e){ return []; }
    const jobs = [];
    for(const n of names){
      const j = read(n.replace(/\.json$/, ''));
      if(!j) continue;
      if(o.status && j.status !== o.status) continue;
      if(o.kind && j.kind !== o.kind) continue;
      jobs.push(j);
    }
    jobs.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
    return jobs.slice(0, Math.min(MAX_LIST, Number(o.limit) || 50));
  }

  function tail(id, n){
    try {
      const s = fs.readFileSync(logFile(id), 'utf8');
      const lines = s.split(/\r?\n/);
      const k = Math.min(2000, Math.max(1, Number(n) || 120));
      return lines.slice(Math.max(0, lines.length - k)).join('\n');
    } catch(e){ return ''; }
  }

  // ---- 执行 ----------------------------------------------------------------
  function argvOf(kindDef, params, cfgKind){
    if(cfgKind && cfgKind.argv_tpl){
      const rep = s => String(s)
        .replace(/\{conditions\}/g, params.conditions || '')
        .replace(/\{platform\}/g, params.platform || '')
        .replace(/\{max\}/g, String(params.max || ''))
        .replace(/\{budget_min\}/g, String(params.budget_min || ''));
      return cfgKind.argv_tpl.map(rep);
    }
    if(!kindDef || !kindDef.argv) return null;
    const args = kindDef.argv(params);
    // 内置 argv 只给 python 的参数部分，首元素固定是解释器
    return [PY].concat(args);
  }

  function analyzeSummary(d){
    const fams = d.families || [];
    const rec = d.recommended || [];
    const top = fams[0];
    return '分析 ' + fams.length + ' 个方向（共 ' + ((d.stats || {}).all_families || fams.length)
      + '）｜推荐 ' + rec.length + ' 个'
      + (top ? '｜首选 ' + top.label + ' ' + top.score : '');
  }

  function run(job, kindDef, cfgKind){
    const argv = argvOf(kindDef, job.params, cfgKind);
    if(!argv){
      return patch(job.id, {
        status: 'failed', ended_at: nowIso(),
        error: '这个任务没有可执行的 argv，且不是智能体类'
      });
    }
    const timeout = (cfgKind && cfgKind.timeout) || kindDef.timeout || DEFAULT_TIMEOUT;
    ensureDir();
    let fd;
    try { fd = fs.openSync(logFile(job.id), 'a'); } catch(e){ fd = null; }
    const child = spawn(argv[0], argv.slice(1), {
      cwd: ROOT,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      windowsHide: true
    });
    running.set(job.id, child);
    const chunk = b => { if(fd !== null){ try { fs.writeSync(fd, b); } catch(e){} } };

    patch(job.id, {
      status: 'running', started_at: nowIso(),
      argv: argv, pid: child.pid, timeout_sec: timeout
    });

    const timer = setTimeout(() => {
      try { child.kill(); } catch(e){}
      chunk(Buffer.from('\n[boss] 超时 ' + timeout + 's，已终止\n'));
    }, timeout * 1000);

    child.stdout.on('data', chunk);
    child.stderr.on('data', chunk);
    child.on('error', e => {
      clearTimeout(timer);
      running.delete(job.id);
      if(fd !== null){ try { fs.closeSync(fd); } catch(_){} fd = null; }
      patch(job.id, { status: 'failed', ended_at: nowIso(), error: 'spawn 失败: ' + e.message });
    });
    child.on('close', code => {
      clearTimeout(timer);
      running.delete(job.id);
      if(fd !== null){ try { fs.closeSync(fd); } catch(_){} fd = null; }
      const cur = read(job.id);
      if(cur && cur.status === 'cancelled') return;
      const timedOut = /超时 \d+s，已终止/.test(tail(job.id, 20));
      const ok = code === 0 && !timedOut;
      // 结构化结果：apply 类任务把 --json-out 写成的那份读回来，
      // 页面拿到的就不是一段日志文本，而是可以渲染、可以复用的逐条判定。
      let result = null;
      if(ok && kindDef && kindDef.result && job.params.result_path){
        const d = readJsonFile(path.join(ROOT, job.params.result_path));
        if(d){
          result = {
            ok: true,
            summary: kindDef.result === 'apply' ? applySummary(d) : analyzeSummary(d),
            artifacts: kindDef.result === 'apply'
              ? [job.params.result_path, 'runs/' + (d.run_id || '') + '/ledger.jsonl']
              : [job.params.result_path],
            data: d
          };
        }
      }
      patch(job.id, {
        status: ok ? 'done' : 'failed',
        ended_at: nowIso(), exit_code: code,
        result: result,
        error: ok ? '' : (timedOut ? '超时终止' : '退出码 ' + code)
      });
    });
    return read(job.id);
  }

  // ---- 对外动作 ------------------------------------------------------------
  function create(kind, params, meta){
    const K = kinds();
    const def = K[kind];
    if(!def) return { err: '未知任务类型：' + kind + '（可用：' + Object.keys(K).join(', ') + '）' };
    if(def.available === false){
      return { err: '这个任务需要投递引擎 jkit，当前工作区没有 jkit/ 目录' };
    }
    if(def.needsConfirm && !(params && params.confirm === true)){
      return { err: '这个任务会真实发出投递，必须显式 confirm:true' };
    }
    const n = normalize(kind, params);
    if(n.err) return { err: n.err };
    const m = meta || {};
    const commandText = typeof m.command_text === 'string' ? m.command_text.slice(0, 8000) : '';
    const commandSource = ['pipe', 'page', 'manual'].indexOf(m.command_source) >= 0
      ? m.command_source : 'page';
    const key = JSON.stringify({ kind, params: n.params });
    // 幂等：同 kind + 同参数在排队/执行中，直接返回那一条，避免连点重复投
    for(const j of list({ limit: MAX_LIST })){
      if(j.status === 'pending' || j.status === 'running'){
        if(JSON.stringify({ kind: j.kind, params: j.params }) === key) return { job: j, dedup: true };
      }
    }
    const job = write({
      id: rid(), kind, title: def.title || kind, params: n.params,
      runner: def.runner, status: 'pending',
      created_at: nowIso(), started_at: null, ended_at: null,
      exit_code: null, error: '', result: null,
      // 交互持久化字段（P3）：原文 / 来源 / 回执 / 产物 / AI 写入
      command_text: commandText, command_source: commandSource,
      reply_text: '', result_artifacts: [], ai_writes: [],
      log: ''
    });
    job.log = 'jobs/' + job.id + '.log';
    if(def.result) job.params.result_path = 'jobs/' + job.id + '.result.json';
    write(job);
    const reqArt = writeRequest(job);
    if(reqArt) patch(job.id, { result_artifacts: [reqArt] });
    if(def.runner === 'script'){
      const cfgKind = ((loadLocal('jobs') || {}).kinds || {})[kind];
      return { job: run(job, def, cfgKind) };
    }
    return { job: read(job.id) };
  }

  function submitResult(id, body){
    const cur = read(id);
    if(!cur) return { err: '任务不存在：' + id };
    const b = body || {};
    if(b.log) {
      try { fs.appendFileSync(logFile(id), String(b.log) + '\n', 'utf8'); } catch(e){}
    }
    const ok = b.ok !== false;
    const result = {
      ok: ok,
      summary: String(b.summary || ''),
      artifacts: Array.isArray(b.artifacts) ? b.artifacts.map(String) : [],
      data: (b.data && typeof b.data === 'object') ? b.data : null
    };
    // AI 写入留痕：只接受白名单字段，逐条夹紧，绝不落整份隐私数据
    const aiWrites = Array.isArray(b.ai_writes) ? b.ai_writes.slice(0, 100).map(w => {
      const o = w && typeof w === 'object' ? w : {};
      return {
        target: String(o.target || '').slice(0, 200),
        before: typeof o.before === 'string' ? o.before.slice(0, 3000) : (o.before == null ? null : String(o.before).slice(0, 3000)),
        after: typeof o.after === 'string' ? o.after.slice(0, 3000) : (o.after == null ? null : String(o.after).slice(0, 3000)),
        at: String(o.at || nowIso()).slice(0, 40)
      };
    }).filter(x => x.target) : [];
    const replyText = typeof b.reply_text === 'string' ? b.reply_text.slice(0, 8000)
      : (cur.reply_text || '');
    const patched = patch(id, {
      status: ok ? 'done' : 'failed',
      ended_at: nowIso(),
      runner: 'agent',
      reply_text: replyText,
      ai_writes: aiWrites,
      result: result,
      error: ok ? '' : String(b.error || '执行失败')
    });
    // 不可变产物：回执 / 结果 / 写入 diff。产物路径并入索引层 result_artifacts。
    const resp = writeInteraction(id, 'response.md', replyText);
    const res = writeInteraction(id, 'result.json', JSON.stringify(result, null, 2));
    const wr = writeWrites(id, aiWrites);
    const extra = [resp, res, wr].filter(Boolean);
    if(extra.length && patched){
      const merged = (patched.result_artifacts || []).concat(extra)
        .filter((v, i, a) => a.indexOf(v) === i);
      patch(id, { result_artifacts: merged });
    }
    return { job: read(id) };
  }

  function cancel(id){
    const cur = read(id);
    if(!cur) return { err: '任务不存在：' + id };
    const child = running.get(id);
    if(child){ try { child.kill(); } catch(e){} }
    return { job: patch(id, { status: 'cancelled', ended_at: nowIso(), error: '人工取消' }) };
  }

  // 把一条 job 压成时间线用的一格：指令 → 回执 → 结果 → AI 写入 diff
  function interactionBrief(j){
    return {
      id: j.id, kind: j.kind, title: j.title || j.kind, status: j.status,
      runner: j.runner || 'script', created_at: j.created_at, ended_at: j.ended_at,
      command_text: j.command_text || '', command_source: j.command_source || 'page',
      reply_text: j.reply_text || '',
      summary: (j.result && j.result.summary) || '',
      error: j.error || '',
      ai_writes: j.ai_writes || [],
      result_artifacts: j.result_artifacts || [],
      has_artifacts: fs.existsSync(interactionDir(j.id))
    };
  }

  // ---- HTTP 分发 -----------------------------------------------------------
  function handle(req, res, pathname, url, json, readBody){
    // 交互时间线（P3）：只读，聚合 jobs 的四个字段 + 不可变产物
    if(pathname === '/api/interactions' && req.method === 'GET'){
      const q = url.searchParams;
      let jobs = list({ limit: q.get('limit') || 60 });
      if(q.get('kind')) jobs = jobs.filter(j => j.kind === q.get('kind'));
      if(q.get('with_command') === '1') jobs = jobs.filter(j => !!j.command_text);
      json(res, { ok: true, readonly: true,
        interactions: jobs.map(interactionBrief), total: jobs.length });
      return true;
    }
    const im = pathname.match(/^\/api\/interactions\/([^\/]+)$/);
    if(im && req.method === 'GET'){
      const id = decodeURIComponent(im[1]);
      const j = read(id);
      if(!j) return (json(res, { ok: false, code: 'NOT_FOUND' }, 404), true);
      json(res, { ok: true, readonly: true, interaction: interactionBrief(j),
        artifacts: {
          request: readArtifact(id, 'request.md'),
          response: readArtifact(id, 'response.md'),
          result: readArtifact(id, 'result.json'),
          writes: readArtifact(id, 'writes.jsonl')
        } });
      return true;
    }

    if(pathname.indexOf('/api/job') !== 0) return false;

    if(pathname === '/api/jobs' && req.method === 'GET'){
      const q = url.searchParams;
      json(res, {
        ok: true,
        kinds: Object.keys(kinds()).map(k => ({
          kind: k, title: kinds()[k].title, runner: kinds()[k].runner,
          needsConfirm: !!kinds()[k].needsConfirm,
          available: kinds()[k].available !== false
        })),
        jobs: list({ status: q.get('status') || '', kind: q.get('kind') || '',
                     limit: q.get('limit') || 30 }),
        running: Array.from(running.keys())
      });
      return true;
    }

    if(pathname === '/api/job' && req.method === 'POST'){
      readBody(req, body => {
        let b = {};
        try { b = JSON.parse(body || '{}'); } catch(e){ return json(res, { ok:false, code:'BAD_JSON' }, 400); }
        const r = create(String(b.kind || ''), b.params || {}, {
          command_text: b.command_text, command_source: b.command_source
        });
        if(r.err) return json(res, { ok: false, code: 'JOB_REJECTED', err: r.err }, 400);
        json(res, { ok: true, dedup: !!r.dedup, job: r.job }, r.dedup ? 200 : 202);
      });
      return true;
    }

    const m = pathname.match(/^\/api\/job\/([^\/]+)(\/log|\/result|\/cancel)?$/);
    if(m){
      const id = decodeURIComponent(m[1]);
      const sub = m[2] || '';
      if(sub === '/log' && req.method === 'GET'){
        const j = read(id);
        if(!j) return (json(res, { ok: false, code: 'NOT_FOUND' }, 404), true);
        const n = url.searchParams.get('tail') || 200;
        return (json(res, { ok: true, id, status: j.status,
                            log: tail(id, n), path: 'jobs/' + id + '.log' }), true);
      }
      if(sub === '/result' && req.method === 'POST'){
        return readBody(req, body => {
          let b = {};
          try { b = JSON.parse(body || '{}'); } catch(e){ return json(res, { ok:false, code:'BAD_JSON' }, 400); }
          const r = submitResult(id, b);
          if(r.err) return json(res, { ok: false, code: 'NOT_FOUND', err: r.err }, 404);
          json(res, { ok: true, job: r.job });
        }), true;
      }
      if(sub === '/cancel' && req.method === 'POST'){
        const r = cancel(id);
        if(r.err) return (json(res, { ok: false, code: 'NOT_FOUND', err: r.err }, 404), true);
        return (json(res, { ok: true, job: r.job }), true);
      }
      if(!sub && req.method === 'GET'){
        const j = read(id);
        if(!j) return (json(res, { ok: false, code: 'NOT_FOUND' }, 404), true);
        return (json(res, { ok: true, job: j, log_tail: tail(id, 40) }), true);
      }
      return (json(res, { ok: false, code: 'METHOD' }, 405), true);
    }
    return false;
  }

  // 启动时把上次进程留下的 running 标成中断，避免页面永远转圈
  (function recover(){
    try {
      for(const j of list({ status: 'running' })){
        if(!running.has(j.id)){
          patch(j.id, { status: 'failed', ended_at: nowIso(), error: '服务重启，任务中断' });
        }
      }
    } catch(e){}
  })();

  return { handle, create, read, list, kinds, tail, cancel, submitResult, DIR };
}

module.exports = { createBus };
