(() => {
  // 智联搜索结果页左列卡片。只读原文，不做判定。
  // 形态：div.job-list-panel > div.job-card，当前选中带 .job-card--active。
  const txt = e => (e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '');
  const cards = [...document.querySelectorAll('.job-list-panel .job-card')];
  const act = document.querySelector('.job-card--active');

  const EDU = /^(本科|大专|硕士|博士|中专|高中|初中|学历不限)$/;
  const EXP = /^(\d+-\d+年|\d+年以内|\d+年以上|经验不限|无经验|应届|在校)/;

  // 标题三层兜底：vue-clamp 是懒渲染组件，刚进页面时 innerText 可能为空；
  // 退到 aria-label，再退到「标题行去掉薪资」—— 拿不到标题整批卡就白读了。
  const titleOf = c => {
    const el = c.querySelector('.job-card__title-clamp, .job-card__title, .job-card__title-main');
    let s = txt(el);
    if (s) return s;
    const v = c.querySelector('.vue-clamp__text');
    if (v) {
      s = v.getAttribute('aria-label') || txt(v);
      if (s) return s;
    }
    const row = txt(c.querySelector('.job-card__title-row'));
    return row.replace(/\s*\d+(?:\.\d+)?-\d+(?:\.\d+)?\s*[万千元].*$/, '').trim();
  };

  const rows = cards.map((c, i) => {
    const tags = [...c.querySelectorAll('.job-card__skill-tag, .job-card__tag')].map(txt).filter(Boolean);
    const href = (c.querySelector('a[href*="jobdetail"]') || {}).href || '';
    const m = href.match(/jobdetail\/([^.?#]+)\.htm/);
    const exp = tags.find(t => EXP.test(t)) || '';
    const edu = tags.find(t => EDU.test(t)) || '';
    return {
      idx: i,
      job_id: m ? m[1] : '',
      title: titleOf(c),
      salary: txt(c.querySelector('.job-card__salary')),
      company: txt(c.querySelector('.job-card__company-name')),
      tags: tags,
      experience_raw: exp,
      education_raw: edu,
      active: !!(act && cards[i] === act),
    };
  });

  return JSON.stringify({
    ok: true,
    url: location.href,
    active_idx: act ? cards.indexOf(act) : -1,
    count: cards.length,
    cards: rows,
  });
})()
