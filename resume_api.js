/* =============================================================
   简历：上传 / 文本抽取 / 字段提取 / 回写画像
   -------------------------------------------------------------
   设计口径（和“只重排、不编造”一致）：
   1. 上传只存文件与抽取出来的纯文本，**不入库个人信息**（姓名/电话/邮箱
      一律不提取、不落盘到画像）。画像里只留对判断有用的属性：
      年龄、学历、专业、届别、城市、工作年限、技术栈、期望薪资。
   2. 提取是「建议」不是「结论」：每个字段都带原文证据，页面上由人确认后
      才写入画像（POST /api/resume/apply），并记下来源 resume。
   3. 抽不出文本（老版 .doc、扫描件 PDF、子集字体 PDF）不猜，返回
      quality=low 并让页面提示改用 docx / txt 或直接粘文本。

   为什么不用第三方库：项目 clone 下来要能直接跑，不想为一个上传功能引入
   需要编译的依赖。docx 就是个 zip，PDF 的文本流基本是 flate 压缩，
   node 自带 zlib 足够处理常见简历。
   ============================================================= */
'use strict';

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const MAX_BYTES = 8 * 1024 * 1024;      // 单份简历上限 8MB
const TEXT_EXT = ['.txt', '.md', '.markdown', '.csv', '.json', '.html', '.htm'];

/* ---------------- 通用小工具 ---------------- */
function safeExt(name){
  return String(path.extname(name || '') || '').toLowerCase().slice(0, 8);
}
function newId(){
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  const stamp = d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' +
    p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  return 'r' + stamp + '-' + Math.random().toString(36).slice(2, 6);
}
function decodeEntities(s){
  return String(s)
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&apos;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (m, d) => String.fromCharCode(Number(d)))
    .replace(/&amp;/g, '&');
}

/* ---------------- docx：zip → word/document.xml ---------------- */
function zipEntries(buf){
  let eocd = -1;
  const floor = Math.max(0, buf.length - 66000);
  for(let i = buf.length - 22; i >= floor; i--){
    if(buf.readUInt32LE(i) === 0x06054b50){ eocd = i; break; }
  }
  if(eocd < 0) throw new Error('不是有效的 zip/docx（找不到中央目录）');
  const count = buf.readUInt16LE(eocd + 10);
  let off = buf.readUInt32LE(eocd + 16);
  const out = [];
  for(let i = 0; i < count; i++){
    if(off + 46 > buf.length || buf.readUInt32LE(off) !== 0x02014b50) break;
    const method = buf.readUInt16LE(off + 10);
    const csize = buf.readUInt32LE(off + 20);
    const nameLen = buf.readUInt16LE(off + 28);
    const extraLen = buf.readUInt16LE(off + 30);
    const cmtLen = buf.readUInt16LE(off + 32);
    const lho = buf.readUInt32LE(off + 42);
    const name = buf.toString('utf8', off + 46, off + 46 + nameLen);
    out.push({ name, method, csize, lho });
    off += 46 + nameLen + extraLen + cmtLen;
  }
  return out;
}

function zipRead(buf, e){
  const nameLen = buf.readUInt16LE(e.lho + 26);
  const extraLen = buf.readUInt16LE(e.lho + 28);
  const start = e.lho + 30 + nameLen + extraLen;
  const data = buf.subarray(start, start + e.csize);
  if(e.method === 0) return data;
  if(e.method === 8) return zlib.inflateRawSync(data);
  throw new Error('不支持的压缩方式 ' + e.method);
}

function docxText(buf){
  const entries = zipEntries(buf);
  const want = entries.find(e => e.name === 'word/document.xml') ||
               entries.find(e => /^word\/document\d*\.xml$/.test(e.name));
  if(!want) throw new Error('docx 里没有 word/document.xml');
  let xml = zipRead(buf, want).toString('utf8');
  // 段落 / 换行 / 制表符先变成真正的空白，再去标签，否则整篇会粘成一行
  xml = xml.replace(/<w:tab\b[^>]*\/>/g, '\t')
           .replace(/<w:br\b[^>]*\/>/g, '\n')
           .replace(/<\/w:p>/g, '\n')
           .replace(/<\/w:tr>/g, '\n')
           .replace(/<\/w:tc>/g, '\t');
  xml = xml.replace(/<[^>]+>/g, '');
  return decodeEntities(xml).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

/* ---------------- PDF：解 flate 流，抓 Tj / TJ 文本 ---------------- */
function pdfStrings(content){
  const parts = [];
  // (literal) Tj / TJ：处理转义括号
  const re = /\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>/g;
  let m;
  while((m = re.exec(content))){
    const tok = m[0];
    // 只有紧跟 Tj / TJ / ' / " 的字符串才算正文，避免把注释当文本
    const tail = content.slice(m.index + tok.length, m.index + tok.length + 40);
    if(!/^\s*(Tj|TJ|'|")/.test(tail) && !/^\s*[-0-9.\s]+\]\s*TJ/.test(tail)) continue;
    if(tok[0] === '<'){
      const hex = tok.slice(1, -1).replace(/\s+/g, '');
      if(hex.length % 4 === 0){
        let s = '';
        for(let i = 0; i < hex.length; i += 4){
          const code = parseInt(hex.substr(i, 4), 16);
          if(code >= 32 && code !== 0xffff) s += String.fromCharCode(code);
        }
        parts.push(s);
      }
    } else {
      let s = tok.slice(1, -1)
        .replace(/\\([nrtbf()\\])/g, (x, c) => ({ n:'\n', r:'\r', t:'\t', b:'\b', f:'\f' }[c] || c))
        .replace(/\\([0-7]{1,3})/g, (x, o) => String.fromCharCode(parseInt(o, 8)));
      parts.push(s);
    }
    if(/\s(TJ|'|")/.test(tail) || /\]\s*TJ/.test(tail)) parts.push(' ');
  }
  return parts.join('');
}

function pdfText(buf){
  const raw = buf.toString('latin1');
  const chunks = [];
  let i = 0;
  while(true){
    const s = raw.indexOf('stream', i);
    if(s < 0) break;
    let start = s + 6;
    if(raw[start] === '\r') start++;
    if(raw[start] === '\n') start++;
    const e = raw.indexOf('endstream', start);
    if(e < 0) break;
    const slice = buf.subarray(start, e);
    let text = null;
    try { text = zlib.inflateSync(slice).toString('latin1'); }
    catch(err){ try { text = zlib.inflateRawSync(slice).toString('latin1'); } catch(e2){ text = null; } }
    if(text && /\b(Tj|TJ)\b/.test(text)) chunks.push(pdfStrings(text));
    i = e + 9;
  }
  return chunks.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/* 文本可信度：中文/字母数字占比低，说明字体是子集编码，抽出来是乱码 */
function textQuality(text){
  const s = String(text || '');
  if(s.length < 30) return { quality: 'empty', ratio: 0 };
  let good = 0;
  for(const ch of s){
    if(/[\u4e00-\u9fa5]/.test(ch)) good += 2;
    else if(/[A-Za-z0-9]/.test(ch)) good += 1.4;
    else if(/[\s，。、；：（）()\-—·|/.,:;+#%&]/.test(ch)) good += 1;
  }
  const ratio = good / s.length;
  if(ratio >= 0.78 && /[\u4e00-\u9fa5]{2,}|[A-Za-z]{3,}/.test(s)) return { quality: 'good', ratio };
  if(ratio >= 0.55) return { quality: 'fair', ratio };
  return { quality: 'low', ratio };
}

/* ---------------- 正文抽取分发 ---------------- */
function extractText(buf, ext){
  const e = String(ext || '').toLowerCase();
  if(TEXT_EXT.indexOf(e) >= 0){
    let s = buf.toString('utf8');
    if(e === '.html' || e === '.htm'){
      s = s.replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ')
           .replace(/<br\s*\/?>/gi, '\n').replace(/<\/(p|div|li|tr|h[1-6])>/gi, '\n')
           .replace(/<[^>]+>/g, ' ');
      s = decodeEntities(s);
    }
    return { text: s.replace(/\r\n/g, '\n').trim(), note: '' };
  }
  if(e === '.docx'){
    try { return { text: docxText(buf), note: '' }; }
    catch(err){ return { text: '', note: '无法解析 docx：' + err.message }; }
  }
  if(e === '.pdf'){
    const t = pdfText(buf);
    return { text: t, note: t ? '' : 'PDF 里没有可直接读取的文本层（可能是扫描件）' };
  }
  if(e === '.doc'){
    // 老式二进制 doc 没有可靠的纯文本段，硬猜只会写出乱码
    return { text: '', note: '老版 .doc 不支持解析，请另存为 .docx 或 .pdf 再传（也可以直接在下面粘贴文本）' };
  }
  return { text: '', note: '不支持的文件类型：' + (e || '未知') };
}

/* ---------------- 字段提取（只取属性，不取姓名/联系方式） ---------------- */
const CITIES = ['深圳','广州','北京','上海','杭州','成都','武汉','南京','西安','苏州','长沙',
  '重庆','厦门','合肥','郑州','天津','青岛','宁波','东莞','佛山','珠海','无锡','福州','济南',
  '大连','沈阳','昆明','南昌','贵阳','石家庄','太原','南宁','海口','兰州','乌鲁木齐'];

const TECH = ['JavaScript','TypeScript','React','React Native','Vue','Vue3','Nuxt','Next.js','Angular',
  'Node.js','Express','NestJS','Java','Spring Boot','Spring Cloud','MyBatis','Python','Django','Flask',
  'FastAPI','Go','Golang','C++','C#','PHP','Ruby','Rust','Kotlin','Swift','Flutter','uni-app','小程序',
  'HTML','HTML5','CSS','CSS3','Sass','Less','Tailwind','Webpack','Vite','Rollup','Babel','ESLint',
  'MySQL','PostgreSQL','SQLite','MongoDB','Redis','Elasticsearch','RabbitMQ','Kafka','GraphQL','REST',
  'Docker','Kubernetes','K8s','Nginx','Linux','Git','Jenkins','CI/CD','Jest','Vitest','Selenium','Pytest',
  'PyTorch','TensorFlow','LangChain','RAG','Prompt','OpenAI','Gemini','MCP','AIGC','SQL','Excel',
  'Tableau','Power BI','Figma','Sketch','Axure','Photoshop','剪映','Premiere'];

const EDU_ORDER = [
  ['博士', ['博士', 'phd', 'doctor']],
  ['硕士', ['硕士', '研究生', 'master', 'mba']],
  ['本科', ['本科', '学士', 'bachelor', '统招本科']],
  ['大专', ['大专', '专科', '高职', 'associate']],
  ['高中', ['高中', '中专', '中职']]
];

function pickEducation(text){
  const low = text.toLowerCase();
  for(const [label, keys] of EDU_ORDER){
    for(const k of keys){
      if(k.length <= 2 ? low.indexOf(k) >= 0 : low.indexOf(k) >= 0) return { value: label, ev: k };
    }
  }
  return null;
}

function pickCity(text){
  // 先看「期望城市 / 意向城市」这类显式说法，再退化成全文第一个出现的城市
  const want = /(期望城市|意向城市|期望工作地|目标城市|求职意向)[^\n]{0,20}/.exec(text);
  if(want){
    for(const c of CITIES) if(want[0].indexOf(c) >= 0) return { value: c, ev: want[0].trim() };
  }
  for(const c of CITIES){
    const i = text.indexOf(c);
    if(i >= 0) return { value: c, ev: text.slice(Math.max(0, i - 12), i + 8).replace(/\s+/g, ' ').trim() };
  }
  return null;
}

function pickGraduation(text){
  let m = /(20\d{2})\s*届/.exec(text);
  if(m) return { value: m[1], ev: m[0] };
  m = /(?:毕业(?:时间|年份|于)?|预计毕业)[:：\s]*(20\d{2})/.exec(text);
  if(m) return { value: m[1], ev: m[0] };
  m = /教育经历[\s\S]{0,120}?(20\d{2})\s*[.\-/年]\s*\d{1,2}?\s*[-—~至]/i.exec(text);
  if(m) return { value: m[1], ev: m[0].replace(/\s+/g, ' ').slice(0, 60) };
  // 兜底：全文最后出现的「20xx.xx - 20xx.xx」教育/工作区间取结束年
  const ranges = [...text.matchAll(/(20\d{2})\s*[.\-/年]\s*\d{0,2}\s*[-—~至]\s*(20\d{2}|至今|现在)/g)];
  if(ranges.length){
    const last = ranges[ranges.length - 1];
    if(last[2] && /^20\d{2}$/.test(last[2])) return { value: last[2], ev: last[0] };
  }
  return null;
}

function pickAge(text){
  let m = /(\d{2})\s*岁/.exec(text);
  if(m) return { value: Number(m[1]), ev: m[0] };
  m = /(19[6-9]\d|20[0-1]\d)\s*年\s*(\d{1,2})\s*月/.exec(text);
  if(m){
    const now = new Date();
    let age = now.getFullYear() - Number(m[1]);
    if(now.getMonth() + 1 < Number(m[2])) age -= 1;
    if(age > 14 && age < 70) return { value: age, ev: m[0] };
  }
  return null;
}

function pickMajor(text){
  let m = /专\s*业[:：]\s*([^\s，,。;；|/、\n]{2,14})/.exec(text);
  if(m) return { value: m[1].trim(), ev: m[0].trim() };
  m = /([\u4e00-\u9fa5]{2,12})\s*专\s*业/.exec(text);
  if(m && m[1].indexOf('所学') < 0) return { value: m[1], ev: m[0] };
  return null;
}

function pickWorkYears(text){
  const m = /(\d+(?:\.\d)?)\s*年(?:以上)?(?:的)?(?:工作|开发|从业|相关)?经[验历]/.exec(text);
  if(m) return { value: Number(m[1]), ev: m[0] };
  if(/应届|在校|无工作经验/.test(text)) return { value: 0, ev: '应届/在校' };
  return null;
}

function pickSalary(text){
  let m = /(\d{1,2}(?:\.\d)?)\s*[-~—至到]\s*(\d{1,2}(?:\.\d)?)\s*[kK千]/.exec(text);
  if(m) return { value: m[1] + '-' + m[2] + 'K', ev: m[0] };
  m = /期望薪资[:：]?\s*([^\s，,。;；\n]{1,16})/.exec(text);
  if(m) return { value: m[1].trim(), ev: m[0].trim() };
  m = /(\d{1,2})\s*[kK]\s*(?:以[上左右]|起)/.exec(text);
  if(m) return { value: m[1] + 'K 起', ev: m[0] };
  return null;
}

function pickTech(text){
  const hit = [];
  for(const t of TECH){
    if(/^[A-Za-z0-9+#./ -]+$/.test(t)){
      const esc = t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('(^|[^A-Za-z0-9+#.])' + esc + '([^A-Za-z0-9+#.]|$)', 'i');
      if(re.test(text)) hit.push(t);
    } else if(text.indexOf(t) >= 0) hit.push(t);
  }
  // 同义词折叠：Vue3/Vue、K8s/Kubernetes、Go/Golang 只留一个
  const canon = { 'Vue3': 'Vue', 'K8s': 'Kubernetes', 'Golang': 'Go', 'HTML5': 'HTML', 'CSS3': 'CSS' };
  const out = [];
  for(let t of hit){
    t = canon[t] || t;
    if(out.indexOf(t) < 0) out.push(t);
  }
  return out;
}

function extractFields(text){
  const t = String(text || '');
  if(!t.trim()) return { fields: {}, evidence: {} };
  const fields = {}, evidence = {};
  const put = (key, r) => { if(r){ fields[key] = r.value; evidence[key] = r.ev; } };

  put('education', pickEducation(t));
  put('city', pickCity(t));
  put('graduation', pickGraduation(t));
  put('age', pickAge(t));
  put('major', pickMajor(t));
  put('work_years', pickWorkYears(t));
  put('salary_expect', pickSalary(t));
  // 毕业年份还没到 → 在校/应届，工作年限就是 0（从日期推的，不是猜经历）
  if(fields.work_years === undefined && fields.graduation){
    const g = Number(fields.graduation);
    if(g && g >= new Date().getFullYear()){
      fields.work_years = 0;
      evidence.work_years = fields.graduation + ' 年毕业 → 应届';
    }
  }
  const tech = pickTech(t);
  if(tech.length) fields.tech_stack = tech;

  return { fields, evidence };
}

/* 把提取结果转成 profile 补丁（identity + conditions_profile + needs） */
function toProfilePatch(fields){
  const f = fields || {};
  const id = {}, cp = {}, needs = {};
  if(f.age != null) id.age = f.age;
  if(f.education) id.education = f.education;
  if(f.major) id.major = f.major;
  if(f.graduation) id.graduation = String(f.graduation);
  if(f.city) id.city = f.city;
  if(f.work_years != null) id.work_years = f.work_years;
  if(f.salary_expect) needs.salary_expect = f.salary_expect;

  if(f.education) cp.education = f.education;
  if(f.work_years != null) cp.exp_years = f.work_years;
  if(f.tech_stack && f.tech_stack.length) cp.tech_stack = f.tech_stack;
  if(f.graduation) cp.graduation = String(f.graduation);

  const patch = {};
  if(Object.keys(id).length) patch.identity = id;
  if(Object.keys(cp).length) patch.conditions_profile = cp;
  if(Object.keys(needs).length) patch.needs = needs;
  return patch;
}

/* ---------------- API ---------------- */
function createApi(opts){
  const root = opts.root;
  const dir = path.join(root, 'data', 'resume');
  const indexFile = path.join(dir, 'index.json');

  function ensure(){ fs.mkdirSync(dir, { recursive: true }); }

  function readIndex(){
    try {
      const j = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
      return Array.isArray(j.items) ? j : { schema: 'resume/index/v1', items: [] };
    } catch(e){ return { schema: 'resume/index/v1', items: [] }; }
  }
  function writeIndex(idx){
    ensure();
    const tmp = indexFile + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(idx, null, 1) + '\n', 'utf8');
    fs.renameSync(tmp, indexFile);
  }

  function publicItem(it){
    return Object.assign({}, it, {
      path: undefined,
      // 列表里不回传全文，节省带宽；要看正文走 /api/resume/text
      preview: it.preview || ''
    });
  }

  function saveText(id, text){
    ensure();
    fs.writeFileSync(path.join(dir, id + '.txt'), String(text || ''), 'utf8');
  }
  function loadText(id){
    try { return fs.readFileSync(path.join(dir, id + '.txt'), 'utf8'); } catch(e){ return ''; }
  }

  function handle(req, res, p, u, json, readBody){
    // ---- 上传 ----
    if(p === '/api/resume/upload' && req.method === 'POST'){
      readBody(req, body => {
        let payload;
        try { payload = JSON.parse(body || '{}'); }
        catch(e){ return json(res, { ok:false, code:'BAD_BODY', hint:'需要 JSON' }, 400); }

        const name = String(payload.name || 'resume');
        const ext = safeExt(name) || (payload.ext ? '.' + String(payload.ext).replace(/^\./, '') : '');
        let buf;
        try {
          const b64 = String(payload.data || '').replace(/^data:[^,]*,/, '');
          buf = Buffer.from(b64, 'base64');
        } catch(e){ return json(res, { ok:false, code:'BAD_DATA', hint:'base64 解不开' }, 400); }
        if(!buf.length) return json(res, { ok:false, code:'EMPTY', hint:'文件是空的' }, 400);
        if(buf.length > MAX_BYTES){
          return json(res, { ok:false, code:'TOO_LARGE',
            hint:'文件 ' + (buf.length / 1048576).toFixed(1) + 'MB，超过 8MB；请压缩或另存为 PDF 再传' }, 413);
        }

        let text = '', note = '';
        const pasted = String(payload.text || '');
        if(pasted.trim()){
          // 允许「只粘文本」：没法解析的格式（老 doc / 扫描件）走这条路
          text = pasted;
        } else {
          const r = extractText(buf, ext);
          text = r.text; note = r.note;
        }

        const q = textQuality(text);
        const ext2 = extractFields(text);
        const id = newId();
        ensure();
        fs.writeFileSync(path.join(dir, id + ext), buf);
        saveText(id, text);

        const item = {
          id,
          name,
          ext,
          size: buf.length,
          uploaded_at: new Date().toISOString(),
          chars: text.length,
          quality: q.quality,
          quality_ratio: Number(q.ratio.toFixed(3)),
          note,
          file: path.posix.join('data', 'resume', id + ext),
          fields: ext2.fields,
          evidence: ext2.evidence,
          preview: text.slice(0, 400)
        };
        const idx = readIndex();
        idx.items.unshift(item);
        writeIndex(idx);
        return json(res, {
          ok: true, item: publicItem(item),
          profile_patch: toProfilePatch(ext2.fields)
        }, 201);
      });
      return true;
    }

    // ---- 列表 ----
    if(p === '/api/resume/list' && req.method === 'GET'){
      const idx = readIndex();
      return json(res, { ok:true, dir: 'data/resume', items: idx.items.map(publicItem), count: idx.items.length });
    }

    // ---- 正文 ----
    if(p === '/api/resume/text' && req.method === 'GET'){
      const id = u.searchParams.get('id') || '';
      const idx = readIndex();
      const it = idx.items.find(x => x.id === id);
      if(!it) return json(res, { ok:false, code:'NOT_FOUND', hint:'没有这份简历' }, 404);
      return json(res, { ok:true, id, name: it.name, text: loadText(id) });
    }

    // ---- 删除 ----
    if(p.indexOf('/api/resume/') === 0 && p !== '/api/resume/list' && req.method === 'DELETE'){
      const id = p.split('/').pop();
      const idx = readIndex();
      const i = idx.items.findIndex(x => x.id === id);
      if(i < 0) return json(res, { ok:false, code:'NOT_FOUND' }, 404);
      const it = idx.items[i];
      idx.items.splice(i, 1);
      writeIndex(idx);
      for(const f of [id + (it.ext || ''), id + '.txt']){
        try { fs.unlinkSync(path.join(dir, f)); } catch(e){}
      }
      return json(res, { ok:true, id, removed:true });
    }

    // ---- 回写画像（确认后才写） ----
    if(p === '/api/resume/apply' && req.method === 'POST'){
      readBody(req, body => {
        let payload;
        try { payload = JSON.parse(body || '{}'); } catch(e){ return json(res, { ok:false, code:'BAD_BODY' }, 400); }
        const idx = readIndex();
        const it = idx.items.find(x => x.id === payload.id);
        if(!it) return json(res, { ok:false, code:'NOT_FOUND', hint:'没有这份简历' }, 404);

        const fields = payload.fields || it.fields || {};
        const patch = toProfilePatch(fields);
        if(!Object.keys(patch).length) return json(res, { ok:false, code:'NO_FIELDS', hint:'这份简历没提取到可回写的字段' }, 400);

        const r = opts.profile.apply(patch, { source: 'resume' });
        const prof = r.profile;
        prof.resume = Object.assign({ current: null, versions: [] }, prof.resume || {});
        prof.resume.current = { id: it.id, name: it.name, file: it.file, uploaded_at: it.uploaded_at };
        const set = new Set((prof.resume.versions || []).map(v => v && v.id));
        if(!set.has(it.id)){
          prof.resume.versions = [{ id: it.id, name: it.name, file: it.file, uploaded_at: it.uploaded_at }]
            .concat(prof.resume.versions || []).slice(0, 20);
        }
        const saved = opts.profile.apply({ resume: prof.resume }, { source: 'resume', backup: false });

        return json(res, { ok:true, profile: saved.profile, backup: r.backup, wrote: Object.keys(patch) });
      });
      return true;
    }

    return false;
  }

  return { handle, extractFields, toProfilePatch, textQuality, extractText, dir };
}

module.exports = { createApi, extractFields, toProfilePatch, textQuality, extractText, MAX_BYTES };
