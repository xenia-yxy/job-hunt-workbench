// 图谱只读 API：把 atlas_index.json 的内容交给定位页就地展开，不再跳单独页面。
// 路径来自 config/local/app.json 的 sources.atlas_index；缺配置/文件返回 SOURCE_MISSING，
// 不静默兜底，也不把 507KB 的索引整份塞给页面。
const fs = require('fs');
const path = require('path');

function createApi(ROOT, APP){
  const src = ((APP.sources || {}).atlas_index || '');
  let cache = null;
  let cachedMtime = 0;

  function read(){
    if(!src) return { err: 'SOURCES_MISSING', hint: 'config/local/app.json 缺少 sources.atlas_index' };
    const p = path.join(ROOT, src);
    let st;
    try { st = fs.statSync(p); }
    catch(e){ return { err: 'SOURCE_MISSING', hint: '找不到 ' + src + '，先运行 python tools/build_atlas_index.py' }; }
    try {
      if(cache && st.mtimeMs === cachedMtime) return { data: cache };
      const d = JSON.parse(fs.readFileSync(p, 'utf8'));
      cache = d;
      cachedMtime = st.mtimeMs;
      return { data: d };
    } catch(e){ return { err: 'SOURCE_BAD', hint: src + ' 读取失败：' + e.message }; }
  }

  function brief(f){
    return {
      id: f.id, label: f.label, active: f.active, rank: f.rank,
      band: f.band, match: f.match, tagline: f.tagline,
      jd_n: (f.req && f.req.n_jd) || 0,
      skill_n: ((f.req && f.req.tiers) ? Object.values(f.req.tiers)
        .reduce((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0) : 0),
      sample_n: (f.kpis && f.kpis.n) || 0
    };
  }

  function handle(req, res, p, u, json){
    if(p !== '/api/atlas' || req.method !== 'GET') return false;
    const r = read();
    if(r.err) return (json(res, { ok: false, code: r.err, hint: r.hint }, 404), true);
    const d = r.data;
    const want = u.searchParams.get('family');
    if(want){
      const key = String(want).trim().toLowerCase();
      const f = (d.families || []).find(x =>
        String(x.id).toLowerCase() === key || String(x.label).toLowerCase() === key);
      if(!f) return (json(res, { ok: false, code: 'NOT_FOUND', hint: '没有这个族：' + want }, 404), true);
      return (json(res, {
        ok: true, schema: d.schema, snapshot: d.snapshot,
        mine_skills: d.mine_skills || [], total_jd: d.total_jd, family: f
      }), true);
    }
    return (json(res, {
      ok: true, schema: d.schema, snapshot: d.snapshot,
      total_jd: d.total_jd, mine_skills: d.mine_skills || [],
      families: (d.families || []).map(brief)
    }), true);
  }

  return { handle, read };
}

module.exports = { createApi };
