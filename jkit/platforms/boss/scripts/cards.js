(() => {
  // 读列表卡片。**索引以 .card-area 为准** —— 每张卡各自包在一个 .card-area 里
  // （它是 ul.rec-job-list 的子元素，li.job-card-box 只是 .card-area 的唯一孩子），
  // 所以 ul 的直接子元素就是卡片，第 N 张 = .card-area:nth-child(N)。
  // 之前按 li.job-card-box:nth-child(N) 取永远取不到，点击全部落空。
  //
  // 薪资数字用的是私用区字符（U+E031..U+E03A = 0..9），要解码，否则正则匹配不到数字。
  // 偏移量是 **0xE031** 不是 0xE030 —— 2026-11-13 用截图逐条核对出来的：页面显示
  // 「11-17K」的卡，码点是 E032 E032 - E032 E038；「20-30K·14薪」是 E033 E031 - E034 E031。
  // 旧代码按 0xE030 解，会把 11-17K 读成 91-221K，待遇维度全错（已修正）。
  const dec = t => (t || '').replace(/[\uE000-\uF8FF]/g, ch => {
    const c = ch.codePointAt(0) - 0xE031;
    return (c >= 0 && c <= 9) ? String(c) : '?';
  });
  const txt = e => e ? (e.innerText || '').trim() : '';

  const areas = [...document.querySelectorAll('.card-area')];
  const cards = areas.map((area, idx) => {
    const li = area.querySelector('li.job-card-box');
    if (!li) return null;
    const a = li.querySelector('a.job-name');
    const href = a ? (a.getAttribute('href') || '') : '';
    const m = href.match(/job_detail\/([^.?#/]+)/);
    const tags = [...li.querySelectorAll('ul.tag-list li')].map(x => txt(x));
    const salRaw = txt(li.querySelector('.job-salary'));
    return {
      idx: idx,
      job_id: m ? m[1] : null,
      title: txt(a),
      salary: dec(salRaw),
      salary_ok: /^[0-9]/.test(dec(salRaw)),
      tags: tags,
      exp: tags[0] || null,
      edu: tags[1] || null,
      company: txt(li.querySelector('.boss-name')),
      area: txt(li.querySelector('.company-location')),
      // 卡片上不显示活跃度，活跃度只能从右侧面板读；这里留 null 不算缺字段
      hr_active_raw: txt(li.querySelector('.boss-active-time')) || null,
      hr_online_raw: txt(li.querySelector('.boss-online-tag')) || null,
      active: area.querySelector('.job-card-wrap.active') !== null
    };
  }).filter(Boolean);

  return JSON.stringify({
    url: location.href,
    n: cards.length,
    active_idx: cards.findIndex(c => c.active),
    cards: cards
  });
})()
