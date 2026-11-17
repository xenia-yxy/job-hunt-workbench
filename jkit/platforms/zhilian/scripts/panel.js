(() => {
  // 智联搜索结果页（/jobs/?jl=&kw=）读数。
  // 形态与 BOSS 一致：左 .job-list-panel 列表 + 右 .job-detail-modules__detail 详情，
  // 当前选中卡片带 .job-card--active。字段名刻意对齐 BOSS 的 panel.js，
  // 让 pipeline.detail_record / judge 原样复用。
  const txt = e => (e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '');

  const detail = document.querySelector('.job-detail-modules__detail');
  if (!detail) return JSON.stringify({ok: false, reason: 'no_panel'});

  const cards = [...document.querySelectorAll('.job-list-panel .job-card')];
  const act = document.querySelector('.job-card--active');
  const activeIdx = act ? cards.indexOf(act) : -1;

  const jdA = detail.querySelector('a[href*="/jobdetail/"]');
  const m = jdA ? (jdA.getAttribute('href') || '').match(/jobdetail\/([^.?#]+)\.htm/) : null;
  const jobId = m ? m[1] : '';

  // ---- 摘要区 ----
  const title = txt(detail.querySelector('.job-detail-summary__title-text'));
  const salary = txt(detail.querySelector('.job-detail-summary__salary'));

  const tags = {city: '', exp: '', edu: '', published: ''};
  for (const li of detail.querySelectorAll('.job-detail-summary__tag')) {
    const img = li.querySelector('img');
    const src = img ? (img.getAttribute('src') || '') : '';
    const v = txt(li);
    if (src.includes('address')) tags.city = v;
    else if (src.includes('workexp')) tags.exp = v;
    else if (src.includes('education')) tags.edu = v;
    else if (src.includes('time')) tags.published = v;
  }

  // ---- 薪资：8000-12000元 / 1.5-2.6万 / 2.5-5万·16薪 / 160-200元/天 / 面议 ----
  const sal = (() => {
    const s = salary.replace(/\s/g, '');
    const months = (s.match(/·\s*(\d+)\s*薪/) || [])[1];
    if (/元\/[天日]/.test(s)) {
      const a = s.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\/[天日]/);
      return a ? {min: +a[1], max: +a[2], unit: 'day', months: null}
               : {min: null, max: null, unit: 'day', months: null};
    }
    const w = s.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万/);
    if (w) return {min: Math.round(+w[1] * 10000), max: Math.round(+w[2] * 10000),
      unit: 'month', months: months ? +months : 12};
    const y = s.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元/);
    if (y) return {min: +y[1], max: +y[2], unit: 'month', months: months ? +months : 12};
    return {min: null, max: null, unit: null, months: null};
  })();

  // ---- 各区块按标题归类 ----
  const block = {};
  for (const sec of detail.querySelectorAll('section.job-detail-card')) {
    const head = txt(sec.querySelector('.job-detail-card__header'));
    const body = sec.querySelector('.job-detail-card__body');
    if (head) block[head] = body || sec;
  }

  // 职位描述：前面常挂一串技能标签，正文从「岗位职责/岗位描述…」起
  const descEl = block['职位描述'] || detail.querySelector('.job-detail-card__body');
  const descFull = txt(descEl);
  const START = /岗位职责|职位描述|工作内容|岗位内容|岗位描述|职位职责|工作职责|岗位要求/;
  const jdStart = descFull.search(START);
  const jd = jdStart > 0 ? descFull.slice(jdStart) : descFull;

  // 公司信息有两种写法：
  //   A 直招：公司名 未融资 · 500-999人 · 行业 已审核 68 个在招职位 公司介绍…
  //   B 外包：客户公司名称 华为技术有限公司 客户公司地址 … 客户公司人数 10000人以上
  const compTxt = txt(block['公司信息']);
  // 外包岗会额外多出一个「客户公司信息」区块：客户公司名称 / 地址 / 人数。
  // 这是判断外包最硬的证据 —— 比公司名字面可靠得多。
  const clientTxt = Object.keys(block)
    .filter(k => k.includes('客户公司'))
    .map(k => txt(block[k])).join(' ');
  const isOutsourceInfo = !!clientTxt || /客户公司名称|客户公司地址/.test(compTxt);
  const reg = t => (compTxt.match(t) || [])[1] || '';

  const company = {
    financing: reg(/(已上市|不需要融资|未融资|有融资|天使轮|A轮|B轮|C轮|D轮及以上)/),
    scale: reg(/(\d+-\d+人|\d+人以上|\d+人以下)/),
    open_jobs: (compTxt.match(/(\d+)\s*个在招职位/) || [])[1] || '',
    verified: compTxt.includes('已审核'),
    industry: (compTxt.match(/·\s*([^·]+?)\s*已审核/) || [])[1] || ''
  };
  const clientCompany = isOutsourceInfo
    ? ((clientTxt + ' ' + compTxt).match(/客户公司名称\s*([^\s]+)/) || [])[1] || '' : '';

  // 卡片标签图标：tag_JD_waipai = 外包
  let waipaiTag = false;
  if (act) {
    for (const img of act.querySelectorAll('img')) {
      if ((img.getAttribute('src') || '').includes('waipai')) { waipaiTag = true; break; }
    }
  }

  // 职位发布者：王佳雯 在线 3分钟前回复 今日回复50+次 高回复率 招聘主管 · 公司名
  const pubTxt = txt(block['职位发布者']);
  const hrReply = (pubTxt.match(/(\d+)\s*(分钟|小时|天)前回复/) || []);
  const hrDays = (() => {
    if (!hrReply.length) return /今日回复/.test(pubTxt) ? 0 : null;
    const n = +hrReply[1];
    if (hrReply[2] === '分钟') return 0;
    if (hrReply[2] === '小时') return n < 24 ? 0 : Math.floor(n / 24);
    return n;
  })();

  const cardTags = act ? [...act.querySelectorAll('.job-card__skill-tag')].map(txt) : [];
  const EDU = /本科|大专|硕士|博士|中专|高中|初中|学历不限/;
  const EXP = /经验|应届|在校|\d+\s*年/;

  const applyBtn = detail.querySelector('.job-detail-summary__apply');
  const chatBtn = detail.querySelector('.job-detail-summary__prechat');

  return JSON.stringify({
    ok: true,
    url: location.href,
    active_idx: activeIdx,
    active_id: jobId,
    card_count: cards.length,
    job_id: jobId,
    title, salary,
    salary_min: sal.min, salary_max: sal.max, salary_unit: sal.unit, salary_months: sal.months,
    city_raw: tags.city, experience_raw: tags.exp, education_raw: tags.edu,
    published: tags.published,
    company_name: txt(act ? act.querySelector('.job-card__company-name') : null),
    company_financing: company.financing,
    company_scale: company.scale,
    company_industry: company.industry,
    company_open_jobs: company.open_jobs ? +company.open_jobs : null,
    company_verified: company.verified,
    is_outsourcing: waipaiTag || isOutsourceInfo,
    client_company: clientCompany,
    outsourced_tag: waipaiTag,
    jd_duty: jd.slice(0, 2200),
    jd_len: jd.length,
    tech_tags: cardTags.filter(t => t && !EDU.test(t) && !EXP.test(t)).slice(0, 12),
    hr_name: (pubTxt.split(' ')[0] || '').slice(0, 10),
    hr_title: (pubTxt.match(/([^\s·]{2,12})\s*·\s*[^·]+$/) || [])[1] || '',
    hr_active_raw: (pubTxt.match(/(\d+\s*(?:分钟|小时|天)前回复|今日回复)/) || [])[0] || '',
    hr_active_days: hrDays,
    hr_reply_count: (pubTxt.match(/今日回复\s*(\d+\+?)\s*次/) || [])[1] || '',
    hr_high_reply: pubTxt.includes('高回复率'),
    chat_label: txt(chatBtn),
    apply_label: txt(applyBtn),
    chat_ready: !!chatBtn && chatBtn.offsetParent !== null,
    apply_ready: !!applyBtn && applyBtn.offsetParent !== null
  });
})()
