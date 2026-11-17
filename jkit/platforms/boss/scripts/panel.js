(() => {
  // 从右侧详情面板读一次，返回判定所需的全部字段。
  //
  // 为什么要「一次读齐」：以前是 detail.js 读完再 liststate.js 反复轮询，
  // 每张卡要读七八次。面板切换后读一次就够，读得越少越不像脚本在扫页面
  // （用户口径 2026-11-13：「动作要真点，不要一直读数据」）。
  //
  // 作用域锁死在 .job-detail-container 内：左侧卡片上也有 .job-name / .job-salary，
  // 不限定容器会读到卡片而不是面板。
  // 薪资同样是私用区字符，偏移 0xE031（0..9）（2026-11-13 截图核对，见 cards.js 注释）。
  const dec = t => (t || '').replace(/[\uE000-\uF8FF]/g, ch => {
    const c = ch.codePointAt(0) - 0xE031;
    return (c >= 0 && c <= 9) ? String(c) : '?';
  });
  const txt = e => e ? (e.innerText || '').trim() : '';
  const panel = document.querySelector('.job-detail-container');
  if (!panel) return JSON.stringify({ ok: false, reason: 'no_panel' });

  // 当前选中的卡片：job_id 以它为准。面板本身不带 id，靠 .active 反查最可靠，
  // 比对面板标题文本稳（同名岗位很常见）。
  const activeCard = document.querySelector('.job-card-wrap.active a.job-name');
  const href = activeCard ? (activeCard.getAttribute('href') || '') : '';
  const m = href.match(/job_detail\/([^.?#/]+)/);
  const activeId = m ? m[1] : '';
  const activeIdx = (() => {
    const area = document.querySelector('.job-card-wrap.active');
    const wrap = area ? area.closest('.card-area') : null;
    return wrap ? [...wrap.parentElement.children].indexOf(wrap) : -1;
  })();

  const jdEl = panel.querySelector('.job-detail-body p.desc') || panel.querySelector('p.desc');
  let jd = txt(jdEl);
  jd = jd.replace(/^微信扫码分享\s*举报\s*/, '');

  // JD 太长时只留首段 + 任职要求段：判定只看这两处，没必要把整篇搬过来
  let brief = jd;
  const KEYS = /(任职要求|岗位要求|职位要求|任职资格|能力要求|任职条件|加分项)/;
  if (jd.length > 1300) {
    const k = jd.match(KEYS);
    brief = k ? (jd.slice(0, 520) + '\n…\n' + jd.slice(k.index, k.index + 820)) : jd.slice(0, 1050);
  }

  const bossInfo = panel.querySelector('.job-boss-info');
  const chatBtn = panel.querySelector('.op-btn-chat');

  return JSON.stringify({
    ok: true,
    url: location.href,
    active_id: activeId,
    active_idx: activeIdx,
    card_count: document.querySelectorAll('.card-area').length,
    title: txt(panel.querySelector('.job-detail-info .job-name')) || txt(panel.querySelector('.job-header-info')),
    salary: dec(txt(panel.querySelector('.job-detail-info .job-salary'))),
    city_line: txt(panel.querySelector('.job-detail-info')),
    status: txt(panel.querySelector('.job-detail-op .job-status')),
    jd_len: jd.length,
    jd_brief: brief,
    hr_name: txt(panel.querySelector('.job-boss-info .name')).split('\n')[0].trim(),
    hr_active_raw: txt(panel.querySelector('.job-boss-info .boss-active-time')) || null,
    hr_online_raw: txt(panel.querySelector('.job-boss-info .boss-online-tag')) || null,
    hr_info: bossInfo ? txt(bossInfo).replace(/\n+/g, ' | ').slice(0, 120) : null,
    // 面板里没有独立的「城市/经验/学历」节点，只有一行混排文本
    // （实测 job-header-info = "AI产品经理 15-25K·14薪 深圳 3-5年 本科"）。
    // 原样带回，解析放 Python，页面结构变了只改一处解析。
    header_line: txt(panel.querySelector('.job-header-info')),
    // "中软国际 · HRM"：前半段是公司名。公司名只在 HR 卡片里出现一次，
    // 面板正文不重复，做公司维度评分与去重都要它。
    company_raw: txt(panel.querySelector('.job-boss-info .boss-info-attr')),
    chat_label: chatBtn ? txt(chatBtn) : null,
    chat_ready: !!chatBtn && chatBtn.offsetParent !== null
  });
})()
