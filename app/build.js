// 用统一数据源注入模板，生成主应用页面（零依赖 Node）。
//
// 用法： node app/build.js        （脚本放在哪一层都能跑，靠向上找 config/ 定位项目根）
//
// 全部路径来自 config/local/app.json：
//   files.data         数据文件（只读，绝不回写）
//   files.app_html     生成出的页面文件名
//   build.template_dir 模板目录（项目根相对）
//   build.template     模板文件名
// 渠道单一源 config/*channels.json；概率规则 rules/*prob.json；
// 客户端配置（channels / labels / namespace / 任务接口位 / prob）注入 window.__WB_CONFIG__。
const fs = require('fs');
const path = require('path');

// 向上找项目根：第一个含 config/ 的目录。脚本被挪到任意层级都不用改代码。
const ROOT = (function(){
  let d = __dirname;
  for(let i = 0; i < 6; i++){
    if(fs.existsSync(path.join(d, 'config'))) return d;
    const up = path.dirname(d);
    if(up === d) break;
    d = up;
  }
  throw new Error('找不到项目根：向上 6 层都没有 config/ 目录');
})();

const read = f => { try { return JSON.parse(fs.readFileSync(path.join(ROOT, f), 'utf8')); } catch(e){ return null; } };

function loadCfg(name, kind){
  const dir = kind === 'rule' ? 'rules' : 'config';
  for(const f of [dir + '/local/' + name + '.json', dir + '/' + name + '.example.json']){
    const v = read(f);
    if(v) return v;
  }
  throw new Error('缺少' + (kind === 'rule' ? '规则' : '配置') + '：' + name +
    '（需 ' + dir + '/local/' + name + '.json 或 ' + dir + '/' + name + '.example.json）');
}

const CH_CFG = loadCfg('channels');
const APP_CFG = loadCfg('app');
const WB_CFG = loadCfg('workbuddy');
const CHANNELS = (CH_CFG.channels || []).filter(c => c.enabled !== false)
  .sort((a, b) => (a.order || 0) - (b.order || 0));
const KEYS = CHANNELS.map(c => c.key);
if(!KEYS.length) throw new Error('channels 配置为空：至少需要一个 enabled 渠道');
const PROB_RULES = loadCfg('prob', 'rule');

const FILES = APP_CFG.files || {};
if(!FILES.data) throw new Error('缺少 app.files.data（config/local/app.json）；文件名不设默认值');
if(!FILES.app_html) throw new Error('缺少 app.files.app_html（config/local/app.json）；文件名不设默认值');
const BUILD = APP_CFG.build || {};
const TPL_DIR = BUILD.template_dir || path.relative(ROOT, __dirname).split(path.sep).join('/');
const TPL_FILE = BUILD.template || 'app.template.html';

// 数据文件只要求「存在且可读」，允许 0 条记录——全新克隆开箱就是空的。
// 注意：真实记录**不再内联进页面**。页面的一切数据都从服务端 /api/data 读，
// 内联一次就等于把私人台账烤进 HTML，既能被 grep 到、又会和磁盘数据打架。
const data = read(FILES.data);
if(data == null) {
  throw new Error('数据文件不可读：' + FILES.data + '（先跑一次 setup 生成空数据文件）');
}
const skeleton = {};
KEYS.forEach(k => { skeleton[k] = []; });

// 旧版 overrides：历史迁移已完成，这里只做提示，不自动迁移（历史数据不回改）
for(const f of [].concat(APP_CFG.legacy_overrides || [])){
  if(read(f)) console.log('WARN: 检测到旧版 overrides 文件 ' + f + '，本脚本不再自动迁移，请人工核对后再处理。');
}

const tplPath = path.join(ROOT, TPL_DIR, TPL_FILE);
const tpl = fs.readFileSync(tplPath, 'utf8');
const cfgMarker = '/*__CONFIG__*/null';
const marker = '/*__DATA__*/null';
const probMarker = '/*__PROB__*/';
for(const m of [cfgMarker, marker, probMarker]){
  if(tpl.indexOf(m) < 0) throw new Error('模板占位符未找到：' + m + '（' + tplPath + '）');
}

const clientCfg = {
  channels: CHANNELS.map(c => ({key:c.key, label:c.label, type:c.type,
    type_label:c.type_label, csv_label:c.csv_label})),
  labels: CH_CFG.labels || {},
  files: {
    app_html: FILES.app_html, data: FILES.data, backup: FILES.backup,
    csv_main: FILES.csv_main, csv_campus: FILES.csv_campus,
    rules_fallback: FILES.rules_fallback || ''
  },
  port: APP_CFG.port || 8787,
  workbench_title: APP_CFG.workbench_title || '',
  storage_namespace: APP_CFG.storage_namespace || '',
  api_base: APP_CFG.api_base || '',
  tasks_ref: WB_CFG.tasks_ref || '',
  output_root: WB_CFG.output_root || '',
  prob: PROB_RULES
};
const cfgScript = 'window.__WB_CONFIG__ = ' + JSON.stringify(clientCfg).replace(/<\//g, '<\\/') + ';\n';
// 页面内不内联任何真实数据：FALLBACK_D 只给「服务端未启动」时一个空骨架，
// 真实数据一律走 /api/data。这样 HTML 里永远不含私人记录，也不会与磁盘数据分叉。
const payload = JSON.stringify(skeleton);
// 概率引擎内联进页面，保证离线打开时也能按进度算概率（与服务端同一份代码 + 同一份规则）
const probSrc = fs.readFileSync(path.join(ROOT, 'prob.js'), 'utf8');
const out = tpl
  .replace(cfgMarker, () => cfgScript)
  .replace(marker, () => payload)
  .replace(probMarker, () => probSrc);
fs.writeFileSync(path.join(ROOT, FILES.app_html), out, 'utf8');

const total = KEYS.reduce((n, k) => n + ((data[k] || []).length), 0);
console.log('已生成 ' + FILES.app_html + '（页面内不内联记录，运行时从 /api/data 读）；'
  + '当前数据源 ' + FILES.data + ' 共 ' + total + ' 条（' +
  KEYS.map(k => k + ':' + ((data[k] || []).length)).join(' / ') + '），' + out.length + ' 字符');
