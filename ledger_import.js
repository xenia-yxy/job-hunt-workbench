// 投递事件 → 跟踪台账 的回流。
//
// 系统里有两套数据，职责不同，不能混：
//   runs/<run_id>/ledger.jsonl   append-only 投递流水（谁、什么时候、投了什么、判了几分）
//   <files.data>                 跟踪台账（真正要跟的沟通记录，页面在这里增删改查）
//
// 以前两者互不相通：投完 50 条，台账里一条都没有，用户要手工再录一遍。
// 这里做的是单向、幂等的回流：只把 outcome=delivered 的行写进台账，
// 用 job_id 去重（老格式没有 job_id 就退回 公司+岗位），重复执行不会产生重复记录。
'use strict';
const fs = require('fs');
const path = require('path');

function readJSONL(file){
  const out = [];
  let txt = '';
  try { txt = fs.readFileSync(file, 'utf8'); } catch(e){ return out; }
  txt.split(/\r?\n/).forEach(line => {
    const s = line.trim();
    if(!s) return;
    try { out.push(JSON.parse(s)); } catch(e){}
  });
  return out;
}

function listRuns(root, runsDir){
  const dir = path.join(root, runsDir || 'runs');
  let names = [];
  try { names = fs.readdirSync(dir); } catch(e){ return []; }
  return names
    .filter(n => fs.existsSync(path.join(dir, n, 'ledger.jsonl')))
    .sort();
}

// 老格式（20261110-*）与新格式（apply.py 的 _ledger_row）字段不同，这里统一成一种
function normalizeRow(r){
  return {
    at: r.at || r.sent_at || '',
    run_id: r.run_id || '',
    platform: r.platform || 'boss',
    job_id: r.job_id || '',
    title: r.title || r.job_title || r.job_snapshot && r.job_snapshot.title || '',
    company: r.company || r.company_name || r.job_snapshot && r.job_snapshot.company || '',
    salary: r.salary || r.job_snapshot && r.job_snapshot.salary || '',
    city: r.city || '',
    exp: r.exp || '',
    edu: r.edu || '',
    grade: r.grade || '',
    score: r.score != null ? r.score : null,
    job_family: r.job_family || '',
    hr_level: r.hr_level || '',
    sent_text: r.sent_text || '',
    sent_text_type: r.sent_text_type || '',
    jd_brief: r.jd_brief || '',
    evidence: r.evidence || ''
  };
}

// 投递事件 → 台账记录（字段与主应用同构，normalize 会再补概率/族）
function toRecord(row){
  const stamp = row.at || new Date().toISOString();
  const msgs = [];
  if(row.sent_text){
    msgs.push({
      from: '我',
      text: row.sent_text,
      time: stamp,
      src: row.sent_text_type === 'platform_default_greeting' ? '投递台账·平台默认招呼语' : '投递台账'
    });
  }
  return {
    company: row.company,
    role: row.title,
    salary: row.salary,
    hr_name: '',
    stage: '已投递',
    status: '等对方',
    type: 'platform',
    is_read: true,
    summary: row.sent_text ? row.sent_text.slice(0, 120) : ('已投递：' + row.title),
    links: row.job_id ? [{ label: 'BOSS 岗位', url: 'https://www.zhipin.com/job_detail/' + row.job_id + '.html' }] : [],
    messages: msgs,
    last_msg: row.sent_text || '',
    msg_complete: false,
    city: row.city,
    exp: row.exp,
    edu: row.edu,
    delivered_at: stamp,
    source_refs: [{
      kind: 'ledger',
      run_id: row.run_id,
      job_id: row.job_id,
      at: stamp,
      grade: row.grade,
      score: row.score
    }],
    note: row.grade ? ('自动投递 · 判定 ' + row.grade + (row.score != null ? ' / ' + row.score + ' 分' : '')) : ''
  };
}

function sameRecord(rec, row){
  const refs = Array.isArray(rec.source_refs) ? rec.source_refs : [];
  if(row.job_id && refs.some(x => x && (x.job_id === row.job_id || (x.kind === 'ledger' && x.job_id === row.job_id)))) return true;
  if(row.job_id && rec.job_id === row.job_id) return true;
  // 无 job_id 时退回「公司 + 岗位」精确匹配，宁可漏合并也不误合并
  return !!(row.company && rec.company === row.company && (rec.role || '') === (row.title || ''));
}

// 执行回流：返回发生了什么，便于页面显示「导入几条、跳过几条」
function importLedger(store, opts){
  const { root, runsDir, channelMap = {}, dryRun = false, runIds = null, channels = [] } = opts;

  // 平台 → 台账渠道的落点：先看平台名本身就是渠道 key，再看 channelMap，再看默认渠道。
  // 不能写死 'boss'——不同人的渠道清单不一样（演示配置里就是 platform_a / platform_b）。
  const known = Array.isArray(channels) ? channels : [];
  function channelOf(platform){
    const p = String(platform || '');
    if(p && known.indexOf(p) >= 0) return p;
    if(p && channelMap[p] && known.indexOf(channelMap[p]) >= 0) return channelMap[p];
    if(known.indexOf('boss') >= 0) return 'boss';
    return known[0] || 'boss';
  }

  const runs = listRuns(root, runsDir).filter(n => !runIds || runIds.indexOf(n) >= 0);
  const seen = new Map();       // 同一次导入内先去重（同一岗位多轮投递只留一条）
  const rows = [];
  runs.forEach(name => {
    readJSONL(path.join(root, runsDir || 'runs', name, 'ledger.jsonl'))
      .filter(r => r && r.outcome === 'delivered')
      .forEach(r => {
        const row = normalizeRow(r);
        if(!row.company) return;               // 没有公司名的行无法进台账
        const key = row.job_id || (row.company + '|' + row.title);
        if(seen.has(key)) return;
        seen.set(key, true);
        rows.push(row);
      });
  });

  const { data } = store.read();
  const planned = [];
  let duplicated = 0;
  rows.forEach(row => {
    const ch = channelOf(row.platform);
    const arr = data[ch] || [];
    if(arr.some(rec => sameRecord(rec, row))){ duplicated++; return; }
    planned.push({ channel: ch, record: toRecord(row) });
  });

  if(dryRun) return { ok: true, dryRun: true, scanned: rows.length, duplicated, planned: planned.length, runs: runs.length };

  let imported = 0;
  const errors = [];
  // 串行写入：每条都走 data_store 的同一把锁，避免并发覆盖
  const chain = planned.reduce((p, item) => p.then(() => store.create(item.channel, item.record, { backup: false })
    .then(() => { imported++; })
    .catch(e => { errors.push(item.channel + ':' + (item.record.company || '') + ' → ' + (e.message || e)); })
  ), Promise.resolve());

  return chain.then(() => {
    const after = store.read();
    return {
      ok: true,
      scanned: rows.length,
      duplicated,
      imported,
      failed: errors.length,
      errors: errors.slice(0, 10),
      runs: runs.length,
      version: after.version,
      count: store.countOf(after.data)
    };
  });
}

module.exports = { importLedger, normalizeRow, toRecord, listRuns, readJSONL };
