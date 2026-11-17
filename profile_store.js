/* =============================================================
   画像读写（profile.json）
   -------------------------------------------------------------
   为什么要单独一个模块：
   画像有两个写入方 —— 页面手填（POST /api/profile）和简历解析回写
   （POST /api/resume/apply）。两边都直接改文件就会互相覆盖，还会丢掉
   「这个字段是哪来的」这条信息。这里统一成唯一写入口：

   - 深合并补丁，而不是整份替换
   - 每个叶子字段记来源（manual / resume / agent / import）
   - 写前留档到 runs/backup/
   - 缺文件时允许建档（模板 clone 下来就能填第一份）

   注意：画像里**不存姓名**。台账、画像、导出都只按「届别/学历/城市」这类
   属性和岗位打交道，名字对判断没有用，留着只增加泄露面。
   ============================================================= */
'use strict';

const fs = require('fs');
const path = require('path');

const SCHEMA = 'profile/v2';

function isPlain(o){ return o && typeof o === 'object' && !Array.isArray(o); }

function mergeDeep(base, patch){
  const out = isPlain(base) ? Object.assign({}, base) : {};
  if(!isPlain(patch)) return out;
  for(const k of Object.keys(patch)){
    const v = patch[k];
    if(v === undefined) continue;
    if(isPlain(v)) out[k] = mergeDeep(out[k], v);
    else out[k] = v;
  }
  return out;
}

// 叶子路径：用来记来源，只记真正被写到的字段
function leafPaths(obj, prefix, out){
  const acc = out || [];
  if(!isPlain(obj)) return acc;
  for(const k of Object.keys(obj)){
    const p = prefix ? prefix + '.' + k : k;
    if(isPlain(obj[k])) leafPaths(obj[k], p, acc);
    else acc.push(p);
  }
  return acc;
}

function emptyProfile(){
  return {
    schema: SCHEMA,
    updated_at: '',
    identity: {},
    needs: {},
    intent: { raw: '', families: [] },
    conditions_profile: {},
    target_filters: {},
    sources: {},
    resume: { current: null, versions: [] }
  };
}

// 旧版把期望薪资 / 备注塞在 identity 里；读的时候顺手搬一份到 needs，
// 这样页面只认新结构，老文件也不会丢值。
function normalize(doc){
  const p = mergeDeep(emptyProfile(), doc || {});
  p.schema = p.schema || SCHEMA;
  const id = p.identity || {};
  p.needs = p.needs || {};
  if(!p.needs.salary_expect && id.salary_expect) p.needs.salary_expect = id.salary_expect;
  if(!p.needs.raw && id.notes) p.needs.raw = id.notes;
  if(!p.needs.available_from && id.available_from) p.needs.available_from = id.available_from;
  if(!p.intent) p.intent = { raw: '', families: [] };
  if(!Array.isArray(p.intent.families)) p.intent.families = [];
  // 姓名历史上存在过，升级时直接清掉，避免「删了字段还留在文件里」
  if('name' in p.identity) delete p.identity.name;
  if(!p.resume) p.resume = { current: null, versions: [] };
  return p;
}

function create(opts){
  const root = opts.root;
  const abs = path.join(root, 'config', 'local', 'profile.json');
  const backupDir = path.join(root, 'runs', 'backup');

  function exists(){ return fs.existsSync(abs); }

  function read(){
    if(!exists()) return null;
    try { return normalize(JSON.parse(fs.readFileSync(abs, 'utf8'))); }
    catch(e){ return null; }
  }

  function writeRaw(next){
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    const tmp = abs + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, abs);       // 原子替换：页面读不到写了一半的文件
  }

  /**
   * 深合并写入。
   * @param {object} patch  补丁（可以只带一个字段）
   * @param {object} o      { source, backup }
   */
  function apply(patch, o){
    const opt = o || {};
    const source = opt.source || 'manual';
    const created = !exists();
    const cur = read() || emptyProfile();
    const next = mergeDeep(cur, patch || {});
    next.schema = SCHEMA;
    next.updated_at = new Date().toISOString();

    const at = next.updated_at;
    next.sources = Object.assign({}, cur.sources || {});
    for(const p of leafPaths(patch || {}, '')){
      if(p === 'schema' || p === 'updated_at' || p === 'sources') continue;
      next.sources[p] = { by: source, at };
    }

    let backup = '';
    if(!created && opt.backup !== false){
      try {
        fs.mkdirSync(backupDir, { recursive: true });
        const stamp = at.replace(/[:.]/g, '-');
        const rel = path.join('runs', 'backup', 'profile-' + stamp + '.json');
        fs.copyFileSync(abs, path.join(root, rel));
        backup = rel.split(path.sep).join('/');
      } catch(e){ backup = ''; }
    }
    writeRaw(next);
    return { ok: true, created, profile: next, backup };
  }

  /** 只更新来源表（简历解析确认后打标，不改值） */
  function markSources(paths, source){
    const cur = read();
    if(!cur) return null;
    const at = new Date().toISOString();
    cur.sources = Object.assign({}, cur.sources || {});
    for(const p of paths || []) cur.sources[p] = { by: source, at };
    writeRaw(cur);
    return cur;
  }

  return { path: abs, exists, read, apply, markSources, empty: emptyProfile };
}

module.exports = { create, mergeDeep, normalize, emptyProfile, SCHEMA };
