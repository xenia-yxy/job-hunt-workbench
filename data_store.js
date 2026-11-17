// 台账数据层：唯一写入口。
//
// 目标（这四点不满足就等于没做）：
//   1. 记录级增删改查 —— 页面上改一条，只影响这一条，不做整份覆盖
//   2. 版本号 —— 每次写盘产生新 version；带 baseVersion 的写入若已过期返回冲突，不静默覆盖
//   3. 写成功才算成功 —— CSV 是派生品，CSV 写失败不能让「数据已落盘」变成「接口失败」
//   4. 串行化 —— 同一个进程内所有写入排队，避免并发读改写互相踩掉
//
// 依赖由外部注入（normalize / regenCSV / keys），保证与 server.js 的规整与 CSV 口径完全一致。
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function readJSON(p){ try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch(e){ return null; } }

function createStore(opts){
  const {
    root,
    dataFile,
    backupFile,
    keys,
    normalize,
    regenCSV,
    warn = (msg) => console.warn('WARN: ' + msg)
  } = opts;

  // 写入队列：单进程内串行，任何一次 mutate 都不会读到别人写了一半的状态
  let chain = Promise.resolve();
  function serialize(fn){
    const run = chain.then(fn, fn);
    // 队列不因单次失败而中断
    chain = run.then(() => undefined, () => undefined);
    return run;
  }

  function emptyData(){
    const o = {};
    (keys || []).forEach(k => { o[k] = []; });
    return o;
  }

  function keysOf(d){
    const out = (keys || []).slice();
    Object.keys(d || {}).forEach(k => {
      if(Array.isArray(d[k]) && out.indexOf(k) < 0) out.push(k);   // 实际顶层键兜底，防丢记录
    });
    return out;
  }

  function shape(d){
    const out = emptyData();
    if(d && typeof d === 'object'){
      keysOf(d).forEach(k => { if(Array.isArray(d[k])) out[k] = d[k]; });
    }
    return out;
  }

  function hashOf(obj){
    return crypto.createHash('sha1').update(JSON.stringify(obj)).digest('hex').slice(0, 12);
  }

  // 读取：永远返回「结构完整 + 带版本号」的数据
  function read(){
    const raw = readJSON(dataFile);
    const data = shape(raw);
    return { data, version: hashOf(data) };
  }

  function countOf(d){ return Object.keys(d || {}).reduce((n, k) => n + (d[k] || []).length, 0); }

  // 落盘：JSON 是唯一真相；CSV 是派生品，失败只告警不报错
  function write(norm, withBackup){
    const dir = path.dirname(dataFile);
    try { fs.mkdirSync(dir, { recursive: true }); } catch(e){}   // 首次运行时数据目录可能还不存在
    if(withBackup !== false && fs.existsSync(dataFile)){
      try { fs.copyFileSync(dataFile, backupFile); } catch(e){ warn('备份失败：' + e.message); }
    }
    const tmp = dataFile + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(norm, null, 1), 'utf8');
    fs.renameSync(tmp, dataFile);      // 原子替换：不会留下写了一半的文件
    let csvError = '';
    try { regenCSV(norm); } catch(e){ csvError = String(e && e.message || e); warn('CSV 派生失败（数据已落盘）：' + csvError); }
    return { data: norm, version: hashOf(norm), count: countOf(norm), csvError };
  }

  // 唯一的写入口：读 → 改 → 规整 → 原子写
  function mutate(fn, { baseVersion, backup = true } = {}){
    return serialize(() => {
      const cur = read();
      if(baseVersion && baseVersion !== cur.version){
        const err = new Error('数据已被更新（服务端 ' + cur.version + '，页面 ' + baseVersion + '）');
        err.code = 'VERSION_CONFLICT';
        err.current = cur;
        throw err;
      }
      const draft = JSON.parse(JSON.stringify(cur.data));
      const maybe = fn(draft, cur);
      const next = maybe && typeof maybe === 'object' && maybe.data ? maybe.data : draft;
      const norm = normalize(next);
      const saved = write(norm, backup);
      return Object.assign({ ok: true }, saved, { previousVersion: cur.version });
    });
  }

  function channelOf(data, ch){
    if(!Array.isArray(data[ch])){
      if((keys || []).indexOf(ch) < 0) throw Object.assign(new Error('未知渠道：' + ch), { code: 'BAD_CHANNEL' });
      data[ch] = [];
    }
    return data[ch];
  }

  function newId(ch){
    return ch.slice(0, 2) + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 6);
  }

  // 深合并：对象递归、数组整体替换（tech_stack / messages 这类不能按下标缝补）
  function mergePatch(base, patch){
    const out = Object.assign({}, base || {});
    Object.keys(patch || {}).forEach(k => {
      const v = patch[k];
      if(v === undefined) return;
      if(v && typeof v === 'object' && !Array.isArray(v)
         && out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])){
        out[k] = mergePatch(out[k], v);
      } else {
        out[k] = v;
      }
    });
    return out;
  }

  // ---- 记录级 CRUD ----

  function create(ch, record, meta){
    if(!record || typeof record !== 'object' || Array.isArray(record)) throw Object.assign(new Error('记录必须是对象'), { code: 'BAD_RECORD' });
    const id = record.id || newId(ch);
    return mutate((d) => {
      const arr = channelOf(d, ch);
      const rec = Object.assign({}, record);
      rec.id = id;
      rec.source = ch;
      if(!rec.created_at) rec.created_at = new Date().toISOString();
      rec.updated_at = rec.created_at;
      const i = arr.findIndex(r => r && r.id === rec.id);
      if(i >= 0) arr[i] = mergePatch(arr[i], rec); else arr.push(rec);
      return d;
    }, meta).then(res => ({ ok: true, record: (res.data[ch] || []).find(r => r.id === id) || null, version: res.version, csvError: res.csvError }));
  }

  function update(ch, id, patch, meta){
    if(!patch || typeof patch !== 'object' || Array.isArray(patch)) throw Object.assign(new Error('patch 必须是对象'), { code: 'BAD_RECORD' });
    return mutate((d) => {
      const arr = channelOf(d, ch);
      const i = arr.findIndex(r => r && r.id === id);
      if(i < 0) throw Object.assign(new Error('记录不存在：' + ch + '/' + id), { code: 'NOT_FOUND' });
      const next = mergePatch(arr[i], patch);
      next.id = id;                       // id 不允许被 patch 改掉
      next.source = ch;
      next.updated_at = new Date().toISOString();
      arr[i] = next;
      return d;
    }, meta).then(res => ({ ok: true, record: (res.data[ch] || []).find(r => r.id === id) || null, version: res.version, csvError: res.csvError }));
  }

  function remove(ch, id, meta){
    return mutate((d) => {
      const arr = channelOf(d, ch);
      const i = arr.findIndex(r => r && r.id === id);
      if(i < 0) throw Object.assign(new Error('记录不存在：' + ch + '/' + id), { code: 'NOT_FOUND' });
      const [gone] = arr.splice(i, 1);
      return { data: d, removed: gone };
    }, meta).then(res => ({ ok: true, removed: res.removed, version: res.version, csvError: res.csvError }));
  }

  // 整份替换：只给批量导入 / 恢复用；带版本号时不覆盖别人的新数据
  function replaceAll(data, meta){
    if(!data || typeof data !== 'object') throw Object.assign(new Error('data 必须是对象'), { code: 'BAD_RECORD' });
    return mutate((d) => data, meta);
  }

  function find(ch, id){
    const { data, version } = read();
    const arr = data[ch] || [];
    return { record: arr.find(r => r && r.id === id) || null, version };
  }

  // 按「业务键」幂等写入：有则更新、无则新增。投递事件回流台账用的就是它。
  function upsertByKey(ch, matchFn, record, meta){
    return mutate((d) => {
      const arr = channelOf(d, ch);
      const i = arr.findIndex(r => r && matchFn(r, record));
      const now = new Date().toISOString();
      if(i >= 0){
        const merged = mergePatch(arr[i], record);
        merged.id = arr[i].id;
        merged.source = ch;
        merged.created_at = arr[i].created_at || now;
        merged.updated_at = now;
        arr[i] = merged;
        return d;
      }
      const rec = Object.assign({}, record);
      rec.id = rec.id || newId(ch);
      rec.source = ch;
      rec.created_at = now;
      rec.updated_at = now;
      arr.push(rec);
      return d;
    }, meta).then(res => ({ ok: true, data: res.data, version: res.version, csvError: res.csvError }));
  }

  return {
    read, find, countOf, create, update, remove, replaceAll, upsertByKey, mutate,
    files: { data: dataFile, backup: backupFile }
  };
}

module.exports = { create: createStore };
