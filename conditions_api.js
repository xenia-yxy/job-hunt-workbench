// 投递条件读写（零依赖 Node）
// ---------------------------------------------------------------------------
// 工作台「投递条件」页要用两样东西：
//   1) 六份条件的**人话视图**（L0 平台筛选 / L1 搜索词 / L2 硬否决 / L3 方向词 / L4 投放控制）
//   2) 能改（只改该族真正不同的那几个数，画像共性字段去 profile.json 改）
//
// 规则的唯一真相在 jkit/score.py；这里不复制阈值，一律 `python -m jkit inspect` 取。
// 结果按文件 mtime 缓存，改完条件会自动失效。
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

function createApi(ROOT, loadLocal){
  const DIR = path.join(ROOT, 'config', 'local', 'conditions');
  const PY = process.env.JKIT_PYTHON || process.env.PYTHON || 'python';
  const cache = new Map();   // file -> {mtimeMs, data}

  function rel(p){ return path.relative(ROOT, p).replace(/\\/g, '/'); }
  function readJson(p){ try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return null; } }

  function families(){
    const f = readJson(path.join(ROOT, 'config', 'local', 'families.json')) || {};
    return f;
  }
  function files(){
    try {
      return fs.readdirSync(DIR).filter(n => /\.json$/i.test(n)).sort()
        .map(n => path.join(DIR, n));
    } catch(e){ return []; }
  }
  function safeFile(id){
    const s = String(id || '');
    if(!/^[A-Za-z0-9._\-]+$/.test(s)) return '';
    const fp = path.join(DIR, s + '.json');
    if(!fp.startsWith(DIR + path.sep)) return '';
    return fs.existsSync(fp) ? fp : '';
  }

  function inspect(fp){
    let st;
    try { st = fs.statSync(fp); } catch(e){ return null; }
    const hit = cache.get(fp);
    if(hit && hit.mtimeMs === st.mtimeMs) return hit.data;
    let data = null;
    try {
      const raw = execFileSync(PY, ['-m', 'jkit', 'inspect', rel(fp)], {
        cwd: ROOT, encoding: 'utf8', timeout: 30000,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
        maxBuffer: 8 * 1024 * 1024
      });
      data = JSON.parse(raw);
    } catch(e){
      data = { error: String(e.message || e).slice(0, 500), source: rel(fp) };
    }
    cache.set(fp, { mtimeMs: st.mtimeMs, data });
    return data;
  }

  // ---- 列表：轻量，不跑 python ----
  function list(){
    const fam = families();
    const bySlug = {};
    for(const k of Object.keys(fam.families || {})) bySlug[k] = fam.families[k];
    const out = [];
    for(const fp of files()){
      const d = readJson(fp);
      if(!d) continue;
      const slug = String(path.basename(fp, '.json'));
      const f = bySlug[d.family_id] || {};
      out.push({
        slug, file: rel(fp), id: d.id || '',
        family_id: d.family_id || '',
        label: f.label || d.family_id || slug,
        score_family: f.score_family || '',
        active: f.active !== false,
        order: typeof f.order === 'number' ? f.order : 999,
        queries: Array.isArray(d.queries) ? d.queries : [],
        salary_floor: (d.profile_overrides || {}).salary_floor != null
          ? d.profile_overrides.salary_floor
          : ((d.profile || {}).salary_floor != null ? d.profile.salary_floor : null),
        min_grade: (d.deliver || {}).min_grade || '',
        daily_limit: (d.deliver || {}).daily_limit,
        budget_min: (d.deliver || {}).budget_min,
        pages_per_query: (d.deliver || {}).pages_per_query,
        max: (d.deliver || {}).max,
        platforms: Object.keys(d.filters || {}),
        mtime: fs.statSync(fp).mtime.toISOString()
      });
    }
    out.sort((a, b) => (a.order - b.order) || (a.label < b.label ? -1 : 1));
    return out;
  }

  // ---- 写入：白名单 + 写前留档 ----
  const ALLOWED_DELIVER = ['min_grade', 'max', 'daily_limit', 'budget_min', 'pages_per_query', 'pace'];

  function writePatch(slug, patch){
    const fp = safeFile(slug);
    if(!fp) return { err: '条件文件不存在：' + slug };
    const cur = readJson(fp);
    if(!cur) return { err: '条件文件不是合法 JSON：' + slug };
    const next = JSON.parse(JSON.stringify(cur));
    const changed = [];

    const po = patch.profile_overrides || {};
    if('salary_floor' in po){
      const v = po.salary_floor;
      const n = (v === null || v === '') ? null : Math.max(0, Math.min(100000, Math.round(Number(v))));
      if(v !== null && v !== '' && !isFinite(n)) return { err: 'salary_floor 必须是数字或 null' };
      next.profile_overrides = next.profile_overrides || {};
      next.profile_overrides.salary_floor = n;
      changed.push('profile_overrides.salary_floor');
    }

    const de = patch.deliver || {};
    for(const k of Object.keys(de)){
      if(ALLOWED_DELIVER.indexOf(k) < 0) continue;
      next.deliver = next.deliver || {};
      next.deliver[k] = de[k];
      changed.push('deliver.' + k);
    }

    if(Array.isArray(patch.queries)){
      const qs = patch.queries.map(s => String(s).trim()).filter(Boolean);
      if(!qs.length) return { err: 'queries 不能为空' };
      if(qs.length > 40) return { err: 'queries 最多 40 个' };
      next.queries = qs;
      changed.push('queries');
    }

    if(patch.filters && typeof patch.filters === 'object'){
      // 只允许「整平台覆盖」，不做深合并——筛选口径必须一眼看全，合并容易留下幽灵项
      for(const p of Object.keys(patch.filters)){
        if(['boss', 'zhilian'].indexOf(p) < 0) continue;
        const v = patch.filters[p];
        if(!v || typeof v !== 'object' || Array.isArray(v)) continue;
        const norm = {};
        for(const k of Object.keys(v)){
          const raw = v[k];
          const val = Array.isArray(raw)
            ? raw.map(x => String(x).trim()).filter(Boolean).slice(0, 12)
            : String(raw == null ? '' : raw).trim();
          if(Array.isArray(raw) ? val.length : val) norm[k] = val;
        }
        if(!Object.keys(norm).length) continue;
        next.filters = next.filters || {};
        next.filters[p] = norm;
        changed.push('filters.' + p);
      }
    }

    // 城市码也是 L0 平台原生筛选的一部分：只允许 boss/zhilian、纯数字串
    if(patch.city_code && typeof patch.city_code === 'object'){
      for(const p of Object.keys(patch.city_code)){
        if(['boss', 'zhilian'].indexOf(p) < 0) continue;
        const v = String(patch.city_code[p] == null ? '' : patch.city_code[p]).trim();
        if(v && !/^\d{3,12}$/.test(v)) return { err: p + ' 城市码必须是 3–12 位数字' };
        if(!v) continue;
        next.city_code = next.city_code || {};
        next.city_code[p] = v;
        changed.push('city_code.' + p);
      }
    }

    if(!changed.length) return { err: '没有可改的字段（支持：salary_floor / deliver.* / queries / filters.* / city_code.*）' };

    const bdir = path.join(ROOT, 'runs', 'backup');
    fs.mkdirSync(bdir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const bname = 'conditions-' + slug + '-' + stamp + '.json';
    fs.copyFileSync(fp, path.join(bdir, bname));
    const tmp = fp + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, fp);
    cache.delete(fp);
    return { changed, backup: 'runs/backup/' + bname, file: rel(fp) };
  }

  function handle(req, res, pathname, url, json, readBody){
    if(pathname.indexOf('/api/conditions') !== 0) return false;

    if(pathname === '/api/conditions' && req.method === 'GET'){
      const id = url.searchParams.get('id');
      if(id){
        const fp = safeFile(id);
        if(!fp) return (json(res, { ok: false, code: 'NOT_FOUND' }, 404), true);
        const d = inspect(fp);
        if(!d) return (json(res, { ok: false, code: 'READ_FAIL' }, 500), true);
        const fam = families();
        const meta = (fam.families || {})[d.family_id] || {};
        return (json(res, { ok: true, detail: { ...d, family_label: meta.label || '' } }), true);
      }
      return (json(res, {
        ok: true, dir: rel(DIR), conditions: list(),
        families: (families().families) || {}
      }), true);
    }

    if(pathname === '/api/conditions' && (req.method === 'PUT' || req.method === 'POST')){
      return readBody(req, body => {
        let b = {};
        try { b = JSON.parse(body || '{}'); } catch(e){ return json(res, { ok: false, code: 'BAD_JSON' }, 400); }
        const slug = String(b.slug || b.id || '');
        const r = writePatch(slug, b.patch || b);
        if(r.err) return json(res, { ok: false, code: 'WRITE_REJECTED', err: r.err }, 400);
        json(res, { ok: true, ...r });
      }), true;
    }

    return (json(res, { ok: false, code: 'METHOD' }, 405), true);
  }

  return { handle, list, inspect, writePatch, DIR };
}

module.exports = { createApi };
