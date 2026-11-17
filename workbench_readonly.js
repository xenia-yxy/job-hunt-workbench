// 工作台只读数据接口（零依赖 · 零写盘 · 无外呼）
//
//   GET /api/ledger-index        投递台账 append-only 事件的运行时内存聚合
//   GET /api/workbuddy/tasks     任务指令模板的元数据清单（不返回正文）
//   GET /api/workbuddy/results   WorkBuddy 落盘结果的文件名清单（不返回正文）
//   GET /api/job-families        岗位族匹配数据的只读聚合（metrics + 简历版本映射）
//   GET /api/doc?src=...         白名单内 Markdown 原文（纯文本返回，不解析落盘）
//
// 幂等约定：返回值不含任何「当前时间」，只由文件 mtime / size / 内容派生，
// 因此连续两次 GET 的 JSON 完全一致；任何接口都不创建、不修改、不删除文件。
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function readJSON(p){ try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return null; } }
function loadLocal(root, name){
  for(const f of ['config/local/' + name + '.json', 'config/' + name + '.example.json']){
    const v = readJSON(path.join(root, f));
    if(v) return v;
  }
  return null;
}
function rel(p){ return String(p).split(path.sep).join('/'); }
function iso(ms){ try { return new Date(ms).toISOString(); } catch(e){ return ''; } }
function unquote(s){ return String(s == null ? '' : s).replace(/^['\"]|['\"]$/g, ''); }

// 与 jkit/store.py::dedupe_key 逐值一致：job_id 优先，缺失时退回「公司|标题」
function dedupeKey(rec){
  if(rec && rec.job_id) return String(rec.job_id);
  const js = (rec && rec.job_snapshot) || rec || {};
  return 'noid:' + String(js.company || js.company_name || '') + '|' + String(js.title || '');
}
// 与 jkit/store.py::delivered_index 一致：兼容顶层 outcome 与 delivery.outcome
function outcomeOf(rec){
  return String((rec && (rec.outcome || ((rec.delivery || {}).outcome))) || 'unknown');
}

function listLedgerFiles(root){
  const runsDir = path.join(root, 'runs');
  let names = [];
  try {
    names = fs.readdirSync(runsDir, {withFileTypes:true})
      .filter(e => e.isDirectory()).map(e => e.name).sort();
  } catch(e){ return []; }
  const out = [];
  for(const name of names){
    const fp = path.join(runsDir, name, 'ledger.jsonl');
    try {
      const st = fs.statSync(fp);
      if(st.isFile()) out.push({run_id:name, fp, st});
    } catch(e){ /* 该 run 没有台账，跳过 */ }
  }
  return out;
}

function buildLedgerIndex(root, params){
  params = params || {};
  const files = listLedgerFiles(root);
  const runs = [], events = [], outcomes = {};
  const digest = crypto.createHash('sha256');
  let lines = 0, parsed = 0, bad = 0, delivered = 0, maxMtime = 0;

  for(const f of files){
    const text = fs.readFileSync(f.fp, 'utf8');
    const arr = text.split(/\r?\n/);
    const ro = {};
    let rLines = 0, rParsed = 0, rBad = 0, rDelivered = 0;
    digest.update(rel(f.fp) + '|' + f.st.size + '|' + f.st.mtimeMs + '\n');
    maxMtime = Math.max(maxMtime, f.st.mtimeMs);

    for(let i = 0; i < arr.length; i++){
      const raw = arr[i].trim();
      if(!raw) continue;                 // 空行不计入（对齐 read_jsonl）
      lines++; rLines++;
      let rec;
      try { rec = JSON.parse(raw); } catch(e){ bad++; rBad++; continue; }  // 坏行跳过
      parsed++; rParsed++;
      const oc = outcomeOf(rec);
      outcomes[oc] = (outcomes[oc] || 0) + 1;
      ro[oc] = (ro[oc] || 0) + 1;
      const isDelivered = oc === 'delivered';
      if(isDelivered){ delivered++; rDelivered++; }
      const snap = (rec && rec.job_snapshot) || {};
      events.push({
        id: f.run_id + ':' + (i + 1) + ':' + dedupeKey(rec),  // 稳定 ID
        run_id: f.run_id, line_no: i + 1,
        dedupe_key: dedupeKey(rec), job_id: String((rec && rec.job_id) || ''),
        outcome: oc, delivered: isDelivered,
        verdict: String((rec && rec.verdict) || ''),
        reason: String((rec && rec.reason) || rec.evidence || ''),
        // P-D：详情级判定留痕——让"为何跳过/为何投"在台账可见（薪资/地点/经验不符等）
        veto: String((rec && rec.veto) || ''),
        reasons: Array.isArray(rec && rec.reasons) ? rec.reasons.map(String) : [],
        detail: (rec && rec.detail) || null,
        hr_level: String((rec && rec.hr_level) || ''),
        jd_brief: String((rec && rec.jd_brief) || ''),
        jd_len: Number((rec && rec.jd_len) || 0),
        grade: String((rec && rec.grade) || ''),
        score: (rec && rec.score != null) ? rec.score : null,
        at: String((rec && (rec.at || rec.updated_at || rec.observed_at)) || ''),
        company: String(snap.company || snap.company_name || ''),
        title: String(snap.title || snap.role || ''),
        salary: String(snap.salary || (rec && rec.salary) || ''),
        platform: String((rec && rec.platform) || ''),
        condition_id: String((rec && rec.condition_id) || ''),
        source_query: String((rec && rec.source_query) || ''),
        replied: !!(((rec && rec.tracking) || {}).replied)
      });
    }
    // P-B：本次实际生效条件——优先读 summary.json（apply 每次落盘的结构化条件），
    // 让"这一批到底用了什么条件、投够没有"可见，而不再只有一个路径字符串。
    const condRel = 'runs/' + f.run_id + '/condition.json';
    const condExists = fs.existsSync(path.join(root, condRel));
    const sum = readJSON(path.join(root, 'runs', f.run_id, 'summary.json'));
    const used = sum ? {
      condition_id: String(sum.condition_id || ''),
      platform: String(sum.platform || ''),
      filters: sum.filters || {},
      applied_filters: sum.applied_filters || {},
      queries: Array.isArray(sum.queries) ? sum.queries : [],
      target: sum.target != null ? sum.target : null,
      completed: sum.completed != null ? sum.completed : null,
      shortfall: sum.shortfall != null ? sum.shortfall : null,
      stopped: String(sum.stopped || '')
    } : null;
    runs.push({
      run_id: f.run_id, file: rel(path.relative(root, f.fp)),
      lines: rLines, parsed: rParsed, bad_lines: rBad, delivered: rDelivered,
      outcomes: ro,
      condition_ref: condRel, condition_exists: condExists,
      condition_note: used ? '本次实际条件见 used_conditions' :
        (condExists ? '旧格式快照，仅展示原文路径（不解析、不回填）' : '无条件快照'),
      used_conditions: used,
      mtime: iso(f.st.mtimeMs)
    });
  }

  const totals = {files:files.length, runs:runs.length, lines, parsed, bad_lines:bad, delivered, outcomes};

  // 查询参数只过滤返回的 events；totals 始终基于全量，保证 lines == 所有台账行之和
  let shown = events;
  if(params.run) shown = shown.filter(e => e.run_id === params.run);
  if(params.outcome) shown = shown.filter(e => e.outcome === params.outcome);
  if(params.delivered === '1') shown = shown.filter(e => e.delivered);
  if(params.q){
    const q = String(params.q).toLowerCase();
    shown = shown.filter(e =>
      (e.company + ' ' + e.title + ' ' + e.job_id + ' ' + e.reason + ' ' + e.dedupe_key)
        .toLowerCase().indexOf(q) >= 0);
  }
  const limit = Math.max(0, parseInt(params.limit || '0', 10) || 0);
  const totalShown = shown.length;
  if(limit) shown = shown.slice(0, limit);

  return {
    ok: true, readonly: true,
    source_root: 'runs',
    source_hash: digest.digest('hex'),
    source_mtime_max: iso(maxMtime),
    runs, events: shown,
    totals: Object.assign({}, totals, {
      shown: totalShown, returned: shown.length,
      truncated: limit > 0 && totalShown > shown.length
    })
  };
}

// 只解析 front matter 元数据块；返回体中绝不包含模板正文
function parseFrontMatter(text){
  const lines = String(text).split(/\r?\n/);
  if(!lines.length || lines[0].trim() !== '---') return {};
  const out = {}; let cur = null;
  for(let i = 1; i < lines.length; i++){
    const line = lines[i];
    if(line.trim() === '---') break;
    const m = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line);
    if(m){
      const k = m[1], v = m[2].trim();
      if(v === ''){ out[k] = []; cur = k; }
      else { out[k] = unquote(v); cur = null; }
      continue;
    }
    const li = /^\s*-\s+(.*)$/.exec(line);
    if(li && cur) out[cur].push(unquote(li[1].trim()));
  }
  return out;
}

function listTasks(root, cfg){
  const ref = (cfg && cfg.tasks_ref) || 'templates/workbuddy/tasks';
  const dir = path.join(root, ref);
  let names = [];
  try { names = fs.readdirSync(dir).filter(n => /\.md$/i.test(n)).sort(); } catch(e){}
  const tasks = names.map(n => {
    const fp = path.join(dir, n), st = fs.statSync(fp);
    const m = parseFrontMatter(fs.readFileSync(fp, 'utf8'));
    return {
      id: m.id || n.replace(/\.example\.md$|\.md$/i, ''),
      title: m.title || m.id || n,
      trigger: m.trigger || '',
      inputs: m.inputs || [],
      output_path: m.output_path || '',
      output_schema: m.output_schema || [],
      prompt_ref: m.prompt_ref || '',
      enabled: m.enabled !== 'false',
      file: n, path: rel(path.relative(root, fp)),
      size: st.size, mtime: iso(st.mtimeMs)
    };
  });
  return {
    ok: true, readonly: true, tasks_ref: ref, tasks,
    totals: {files:tasks.length, enabled:tasks.filter(t => t.enabled).length}
  };
}

function safeSub(s){
  const v = String(s || '');
  if(!v || path.isAbsolute(v) || v.indexOf('..') >= 0) return '';
  return v;
}
function listResults(root, cfg){
  const outRoot = safeSub((cfg && cfg.output_root) || 'workbuddy_out') || 'workbuddy_out';
  const dirsCfg = (cfg && cfg.result_dirs) || {};
  const dirs = []; let n = 0;
  for(const key of Object.keys(dirsCfg).sort()){
    const sub = safeSub(dirsCfg[key]);
    if(!sub) continue;
    const dp = path.join(root, outRoot, sub);
    let files = [];
    try {
      files = fs.readdirSync(dp, {withFileTypes:true})
        .filter(e => e.isFile())
        .map(e => {
          const fp = path.join(dp, e.name), st = fs.statSync(fp);
          return {name:e.name, path:rel(path.relative(root, fp)), size:st.size, mtime:iso(st.mtimeMs)};
        })
        .sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    } catch(e){ /* 目录尚未生成，返回空 */ }
    n += files.length;
    dirs.push({key, path:rel(path.relative(root, dp)), files});
  }
  return {ok:true, readonly:true, output_root:outRoot, dirs,
    totals:{dirs:dirs.length, files:n}};
}

// ---- 岗位族匹配：只读聚合 -------------------------------------------------
// 数据源路径一律来自 config 的 sources 段；代码里不写死工作区实例值。
function sourcesOf(root){
  const ap = loadLocal(root, 'app') || {};
  return ap.sources || {};
}
function insideRoot(root, abs){
  const base = path.resolve(root), p = path.resolve(abs);
  return p === base || p.indexOf(base + path.sep) === 0;
}
function num(v){ return (v === null || v === undefined || v === '') ? null : v; }

function buildJobFamilies(root){
  const src = sourcesOf(root);
  const relMetrics = safeSub(src.job_families || '');
  if(!relMetrics){
    return {ok:false, readonly:true, code:'SOURCE_MISSING',
      hint:'config 的 sources.job_families 未配置，无法聚合岗位族数据'};
  }
  const fpMetrics = path.resolve(root, relMetrics);
  if(!insideRoot(root, fpMetrics)){
    return {ok:false, readonly:true, code:'SOURCE_OUT_OF_ROOT',
      hint:'sources.job_families 越出项目根，已拒绝'};
  }
  let metrics = null, st = null;
  try { st = fs.statSync(fpMetrics); metrics = readJSON(fpMetrics); } catch(e){}
  if(!metrics || !metrics.families){
    return {ok:false, readonly:true, code:'SOURCE_UNREADABLE',
      hint:'读不到 ' + relMetrics + '（文件缺失或不是合法 JSON）'};
  }

  // atlas 的页内锚点是 p-fN，N 与 metrics.order 的下标一一对应
  const order = Array.isArray(metrics.order) ? metrics.order : [];
  const idx = {};
  order.forEach((name, i) => { idx[String(name)] = i; });

  // 简历版本只按 label 精确匹配；对不上就 null，绝不猜
  const rvRel = safeSub(src.resume_versions || '');
  const rv = rvRel ? readJSON(path.resolve(root, rvRel)) : null;
  const byLabel = {};
  if(rv && Array.isArray(rv.groups)){
    for(const g of rv.groups){
      const dir = String(g.dir || '.').split(path.sep).join('/');
      for(const v of (g.versions || [])){
        if(!v || !v.label || !v.file) continue;
        byLabel[String(v.label).trim()] = {
          id: v.id || '',
          label: v.label,
          url: (dir === '.' ? '' : dir.replace(/\/$/, '') + '/') + v.file
        };
      }
    }
  }
  const deep = String(src.atlas_deep_link || '');

  const families = Object.keys(metrics.families).map(name => {
    const f = metrics.families[name] || {};
    const pos = idx[String(name)];
    return {
      name,
      order: (typeof pos === 'number') ? pos : null,
      rank: num(f.rank),
      band: String(f.band || ''),
      match: num(f.match),
      dims: f.dims || {},
      n: f.n || 0,
      open: f.open || 0,
      open_rate: num(f.open_rate),
      block: f.block || 0,
      check: f.check || 0,
      suspicious: f.suspicious || 0,
      intern: f.intern || 0,
      excluded: f.excluded || 0,
      salary_open_median: num(f.salary_open_median),
      salary_open_min: num(f.salary_open_min),
      salary_open_max: num(f.salary_open_max),
      salary_all_median: num(f.salary_all_median),
      edu: f.edu || {}, exp: f.exp || {}, form: f.form || {},
      resume: byLabel[String(name).trim()] || null,
      atlas_url: (deep && typeof pos === 'number') ? deep + 'f' + pos : ''
    };
  }).sort((a, b) => (a.rank == null ? 999 : a.rank) - (b.rank == null ? 999 : b.rank));

  const bands = {};
  for(const f of families){ const b = f.band || '—'; bands[b] = (bands[b] || 0) + 1; }

  return {
    ok: true, readonly: true,
    source: relMetrics, source_mtime: st ? iso(st.mtimeMs) : '',
    total: metrics.total || 0, total_open: metrics.total_open || 0,
    atlas_deep_link: deep, bands, families,
    directions: buildDirections(root, src),
    totals: {families: families.length, with_resume: families.filter(f => f.resume).length}
  };
}

// ---- 族契约：把「族匹配数据(中文族名) / 条件(slug) / 简历(fN)」三边按 id 串起来 ----
// 为什么要这张表：判断页按中文族名排序、投递按 conditions 文件名筛、简历按 fN 编号，
// 三套命名空间没有公共语言，用户看到的「A 档」和实际投出去的口径接不上。
function buildFamilies(root){
  const contract = loadLocal(root, 'families');
  if(!contract || !contract.families){
    return {ok:false, readonly:true, code:'NO_CONTRACT',
      hint:'缺 config/local/families.json —— 它是族 id 契约，没有它判断/投递/简历三边对不上'};
  }
  const jf = buildJobFamilies(root);
  const byName = {};
  if(jf && jf.ok){ for(const f of (jf.families || [])) byName[String(f.name)] = f; }

  const dir = String(contract.resume_dir || '').replace(/\/$/, '');
  const list = Object.keys(contract.families).map(id => {
    const c = contract.families[id] || {};
    const m = c.metrics_key ? (byName[String(c.metrics_key)] || null) : null;
    return {
      id,
      label: String(c.label || id),
      active: !!c.active,
      order: (typeof c.order === 'number') ? c.order : 999,
      metrics_key: c.metrics_key || null,
      conditions: c.conditions || null,
      note: String(c.note || ''),
      resume_url: c.resume ? (dir + '/' + String(c.resume) + '.html') : '',
      // 有 metrics 才有市场分；没有就明确 null，页面写「无样本」，不补 0 也不补平均
      metrics: m ? {
        match: m.match, band: m.band, rank: m.rank, dims: m.dims,
        n: m.n, open: m.open, open_rate: m.open_rate,
        block: m.block, check: m.check, suspicious: m.suspicious,
        salary_open_median: m.salary_open_median,
        edu: m.edu, exp: m.exp, form: m.form,
        atlas_url: m.atlas_url || ''
      } : null
    };
  }).sort((a, b) => (a.order - b.order) || String(a.label).localeCompare(String(b.label), 'zh'));

  return {
    ok: true, readonly: true,
    source: contract.metrics_source || '',
    source_mtime: (jf && jf.source_mtime) || '',
    sample_total: (jf && jf.total) || 0,
    sample_open: (jf && jf.total_open) || 0,
    contract_schema: contract.schema || '',
    families: list,
    active: list.filter(f => f.active).map(f => f.id),
    totals: {
      all: list.length,
      active: list.filter(f => f.active).length,
      with_metrics: list.filter(f => f.metrics).length
    }
  };
}

// ---- 用户画像：只认 config/local/profile.json，绝不回退 example ----
// 这是「我是谁」；回退到示例文件等于拿别人的画像投自己的岗。
// 还没建档时返回空骨架 + exists:false：页面能直接渲染空表单，第一次保存由
// POST /api/profile 建档（读取侧仍不猜任何值，两者职责分开）。
function readProfile(root){
  const abs = path.join(root, 'config/local/profile.json');
  const v = readJSON(abs);
  if(!v){
    const empty = require('./profile_store.js').emptyProfile();
    return {ok:true, readonly:true, exists:false, code:'NO_PROFILE',
      hint:'还没填过画像：保存一次就会建档', profile:empty};
  }
  return {ok:true, readonly:true, exists:true,
    profile:require('./profile_store.js').normalize(v)};
}

// ---- 扩展方向：与主族同构展示，但**不并入**匹配分排序 ----
// 单列成区的原因：这批数据来自网络公开 JD 抽样，不是平台批量实测，
// 混进主族的 rank 里会让「实测」和「调研」分不开，匹配分也不再可比。
function buildDirections(root, src){
  const rel = safeSub((src || {}).directions || '');
  if(!rel) return {ok:false, code:'NOT_CONFIGURED',
    hint:'config 的 sources.directions 未配置'};
  const fp = path.resolve(root, rel);
  if(!insideRoot(root, fp)) return {ok:false, code:'SOURCE_OUT_OF_ROOT',
    hint:'sources.directions 越出项目根，已拒绝'};

  let raw = null, st = null;
  try { st = fs.statSync(fp); raw = readJSON(fp); } catch(e){}
  if(!raw || !Array.isArray(raw.directions)){
    return {ok:false, code:'SOURCE_UNREADABLE', source:rel,
      hint:'读不到 ' + rel + '（文件缺失或不是合法 JSON）'};
  }

  const STATUS = {fit:'能投', patch:'补一点', try:'可试', no:'够不着'};
  const list = raw.directions.map(d => {
    const jobs = (Array.isArray(d.jobs) ? d.jobs : []).map(j => ({
      title: String(j.title || ''),
      company: String(j.company || ''),
      meta: String(j.meta || ''),
      salary: String(j.salary || ''),
      salary_month: Array.isArray(j.salary_month) ? j.salary_month : null,
      status: STATUS[j.status] ? j.status : 'patch',
      status_text: String(j.status_text || STATUS[j.status] || ''),
      short: String(j.short || ''),
      blocker: String(j.blocker || ''),
      must: Array.isArray(j.must) ? j.must.map(String) : [],
      note: String(j.note || '')
    }));
    const counts = {fit:0, patch:0, try:0, no:0};
    for(const j of jobs) counts[j.status] += 1;
    return {
      id: String(d.id || ''),
      label: String(d.label || ''),
      summary: String(d.summary || ''),
      n_jobs: jobs.length,
      salary_min: num(d.salary_min), salary_max: num(d.salary_max),
      resume_hint: String(d.resume_hint || ''),
      counts, jobs
    };
  });

  const roles = (Array.isArray(raw.roles) ? raw.roles : []).map(r => ({
    group: String(r.group || ''),
    name: String(r.name || ''),
    star: !!r.star,
    makers: String(r.makers || ''),
    do: String(r.do || ''),
    type: String(r.type || ''),
    prospect: String(r.prospect || ''),
    verdict: String(r.verdict || ''),
    detail: String(r.detail || '')
  }));

  return {
    ok: true, readonly: true,
    source: rel, source_mtime: st ? iso(st.mtimeMs) : '',
    method: String(raw.method || 'web_research'),
    as_of: String(raw.as_of || ''),
    note: String(raw._note || ''),
    roles_note: String(raw._roles_note || ''),
    source_label: String(raw.source_label || ''),
    report_url: String(raw.report_url || ''),
    roles_report_url: String(raw.roles_report_url || ''),
    atlas_url: String(raw.atlas_url || ''),
    headlines: Array.isArray(raw.headlines) ? raw.headlines : [],
    paths: Array.isArray(raw.paths) ? raw.paths : [],
    roles,
    list,
    gaps: Array.isArray(raw.gaps) ? raw.gaps : [],
    plan: Array.isArray(raw.plan) ? raw.plan : [],
    caveats: Array.isArray(raw.caveats) ? raw.caveats : [],
    evidence: Array.isArray(raw.evidence) ? raw.evidence : [],
    totals: {
      directions: list.length,
      roles: roles.length,
      starred: roles.filter(r => r.star).length,
      jobs: list.reduce((n, d) => n + d.jobs.length, 0),
      fit: list.reduce((n, d) => n + d.counts.fit, 0),
      patch: list.reduce((n, d) => n + d.counts.patch, 0),
      try: list.reduce((n, d) => n + d.counts.try, 0),
      no: list.reduce((n, d) => n + d.counts.no, 0)
    }
  };
}

// ---- Markdown 文档：白名单内只读 ------------------------------------------
function resolveDoc(root, src, sources){
  const s = String(src == null ? '' : src).trim();
  if(!s) return {err:'EMPTY'};
  if(s.indexOf('\0') >= 0) return {err:'BAD'};
  if(!/\.(md|markdown)$/i.test(s)) return {err:'TYPE'};
  const norm = s.split('\\').join('/').replace(/^\.\//, '');
  if(path.isAbsolute(norm) || norm.split('/').indexOf('..') >= 0) return {err:'TRAVERSE'};
  const abs = path.resolve(root, norm);
  if(!insideRoot(root, abs)) return {err:'TRAVERSE'};

  const roots = (sources.doc_roots || []).map(r =>
    String(r).split('\\').join('/').replace(/^\.\//, '').replace(/\/+$/, ''));
  const allow = (sources.doc_allow || []).map(r => String(r).split('\\').join('/').replace(/^\.\//, ''));
  const inRoot = roots.some(r => r && (norm === r || norm.indexOf(r + '/') === 0));
  if(!inRoot && allow.indexOf(norm) < 0) return {err:'FORBIDDEN'};

  let real;
  try { real = fs.realpathSync(abs); } catch(e){ return {err:'NOT_FOUND'}; }
  if(!insideRoot(root, real)) return {err:'TRAVERSE'};   // 防符号链接逃逸
  let st;
  try { st = fs.statSync(real); } catch(e){ return {err:'NOT_FOUND'}; }
  if(!st.isFile()) return {err:'NOT_FILE'};
  return {abs: real, st, norm};
}

// ---- 导航合成：配置 + 目录扫描 --------------------------------------------
// 段（group）代表流程环节，侧栏只显示每段的**主视图**；
// 具体产物（报告、简历版本、图谱快照）是主视图下的变体，不进侧栏。
// 变体 = 配置里写死的（文本报告）+ 目录扫描出的（新增简历版本 / 新图谱快照自动出现）。
function globRe(seg){
  return new RegExp('^' + seg.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*') + '$');
}
// 仅支持同层通配：`简历_*.html`、`survey/*/atlas.html`。零依赖，不递归、不跟随符号链接。
function expandPattern(root, pattern){
  const pat = String(pattern || '').split('\\').join('/').replace(/^\.\//, '');
  if(!pat || pat.indexOf('..') >= 0 || path.isAbsolute(pat)) return [];
  const parts = pat.split('/').filter(Boolean);
  let cur = [''];
  for(let i = 0; i < parts.length; i++){
    const seg = parts[i], last = i === parts.length - 1, next = [];
    for(const base of cur){
      let ents = [];
      try { ents = fs.readdirSync(path.join(root, base), {withFileTypes:true}); } catch(e){ continue; }
      for(const e of ents){
        if(last ? !e.isFile() : !e.isDirectory()) continue;
        const hit = (seg === '*') ? true
          : (seg.indexOf('*') >= 0) ? globRe(seg).test(e.name)
          : (e.name === seg);
        if(hit) next.push(base ? base + '/' + e.name : e.name);
      }
    }
    cur = next;
  }
  return cur;
}
function labelOf(relPath, rule){
  const segs = relPath.split('/');
  let s = (rule.label_from === 'dirname')
    ? (segs.length > 1 ? segs[segs.length - 2] : segs[0])
    : segs[segs.length - 1].replace(/\.[^.]+$/, '');
  // 快照目录的命名惯例 YYYYMMDD-HHMM-xxx：只保留 MM-DD HH:MM，其余进 tooltip
  if(rule.label_date){
    const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(s);
    if(m) s = m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5];
  }
  if(rule.strip_prefix) s = s.split(String(rule.strip_prefix)).join('');
  return s;
}

function buildRoutes(root){
  const raw = loadLocal(root, 'routes') || {groups:[], items:[]};
  const app = loadLocal(root, 'app') || {};
  const scans = Array.isArray(app.artifact_scans) ? app.artifact_scans : [];
  const groups = (raw.groups || []).slice().sort((a,b) => (a.order||0) - (b.order||0));
  const flat = (raw.items || []).slice().sort((a,b) => (a.order||0) - (b.order||0));

  function variantOf(it){
    return {
      id: it.id, label: it.label || it.id, url: it.url, mode: it.mode || 'embed',
      note: it.note || '', badge: it.badge || '', badge_kind: it.badge_kind || '',
      source: it.source || 'config', mtime: ''
    };
  }

  const out = [];
  for(const g of groups){
    const list = flat.filter(i => (i.group || '') === g.id);
    // 直达组（首页）：一个链接，不折叠、无子项
    if(g.kind === 'link'){
      const url = g.url || (list[0] && list[0].url) || '';
      if(!url) continue;
      out.push({id:g.id, label:g.label||g.id, group:g.id, role:'link', url,
        mode:g.mode||'embed', order:g.order||0, note:g.note||'',
        badge:g.badge||'', badge_kind:g.badge_kind||'', variants:[]});
      continue;
    }
    if(!list.length) continue;
    // 主视图：显式 role=primary，否则取段内第一项（兼容旧平铺配置）
    const primary = list.find(i => i.role === 'primary') || list[0];
    let variants;
    if(Array.isArray(primary.variants) && primary.variants.length){
      variants = primary.variants.filter(v => v && v.url).map(variantOf);
      for(const it of list){
        if(it === primary || !it.url) continue;
        if(variants.some(v => v.url === it.url)) continue;
        variants.push(variantOf(it));
      }
    } else {
      variants = list.filter(i => i !== primary && i.url).map(variantOf);
    }
    out.push({
      id: primary.id, label: primary.label || primary.id, group: g.id,
      role: 'primary', url: primary.url, mode: primary.mode || 'embed',
      order: primary.order || 0, note: primary.note || '',
      badge: primary.badge || '', badge_kind: primary.badge_kind || '',
      writable: primary.writable === true,
      variants
    });
  }

  for(const rule of scans){
    const item = out.find(o => o.group === rule.group && o.role === 'primary');
    if(!item) continue;
    const ex = (rule.exclude || []).map(String);
    const stats = expandPattern(root, rule.pattern)
      .filter(f => ex.indexOf(f) < 0 && ex.indexOf(f.split('/').pop()) < 0)
      .map(f => {
        let m = 0;
        try { m = fs.statSync(path.join(root, f)).mtimeMs; } catch(e){}
        return {f, m};
      });
    if(rule.sort === 'mtime_desc') stats.sort((a,b) => b.m - a.m);
    else if(rule.sort === 'mtime_asc') stats.sort((a,b) => a.m - b.m);
    else stats.sort((a,b) => a.f < b.f ? -1 : a.f > b.f ? 1 : 0);
    const take = (rule.limit === undefined || rule.limit === null)
      ? stats : stats.slice(0, Math.max(0, parseInt(rule.limit, 10) || 0));

    for(const s of take){
      if(item.url === s.f) continue;
      if(item.variants.some(v => v.url === s.f)) continue;
      item.variants.push({
        id: 'scan:' + s.f, label: labelOf(s.f, rule), url: s.f, mode: 'embed',
        note: rule.note ? (rule.note + ' · ' + s.f) : s.f,
        badge: rule.badge || '', badge_kind: rule.badge_kind || '',
        source: 'scan', mtime: iso(s.m)
      });
    }
  }

  return {
    ok: true, readonly: true, synthesized: true,
    schema: raw.schema || 'routes/v1',
    groups, items: out,
    totals: {
      groups: groups.length, pages: out.length,
      variants: out.reduce((n, o) => n + (o.variants || []).length, 0)
    }
  };
}

function handle(root, p, u, res, json){
  if(p === '/api/routes'){
    try {
      const r = buildRoutes(root);
      // 保持 /api/routes 的既有契约：外层 {ok, routes}，内部合成 groups/items
      json(res, {ok:true, routes:r, totals:r.totals});
    } catch(e){ json(res, {ok:false, code:'INTERNAL', err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/ledger-index'){
    try {
      json(res, buildLedgerIndex(root, {
        run: u.searchParams.get('run'), outcome: u.searchParams.get('outcome'),
        q: u.searchParams.get('q'), delivered: u.searchParams.get('delivered'),
        limit: u.searchParams.get('limit')
      }));
    } catch(e){ json(res, {ok:false, err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/workbuddy/tasks'){
    try { json(res, listTasks(root, loadLocal(root, 'workbuddy'))); }
    catch(e){ json(res, {ok:false, err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/workbuddy/results'){
    try { json(res, listResults(root, loadLocal(root, 'workbuddy'))); }
    catch(e){ json(res, {ok:false, err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/job-families'){
    try {
      const j = buildJobFamilies(root);
      json(res, j);
    } catch(e){ json(res, {ok:false, code:'INTERNAL', err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/families'){
    try { json(res, buildFamilies(root)); }
    catch(e){ json(res, {ok:false, code:'INTERNAL', err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/profile'){
    try { json(res, readProfile(root)); }
    catch(e){ json(res, {ok:false, code:'INTERNAL', err:String(e)}, 500); }
    return true;
  }
  if(p === '/api/doc'){
    const r = resolveDoc(root, u.searchParams.get('src'), sourcesOf(root));
    if(!r.abs){
      const code = (r.err === 'NOT_FOUND') ? 404 : (r.err === 'FORBIDDEN') ? 403 : 400;
      json(res, {ok:false, code:r.err, hint:'仅允许读取配置白名单内的 Markdown 文档'}, code);
      return true;
    }
    try {
      const body = fs.readFileSync(r.abs, 'utf8');
      res.writeHead(200, {
        'Content-Type': 'text/markdown; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Doc-Mtime': iso(r.st.mtimeMs)
      });
      res.end(body);
    } catch(e){ json(res, {ok:false, code:'READ_FAIL', err:String(e)}, 500); }
    return true;
  }
  return false;
}

module.exports = {handle, buildLedgerIndex, listTasks, listResults, buildJobFamilies, buildFamilies, readProfile,
  buildDirections, buildRoutes, expandPattern, resolveDoc, dedupeKey, outcomeOf};
