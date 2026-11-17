(() => {
  const INPUT = "__INPUT__";

  const hash = (s) => {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  };

  const qs = (k) => {
    const m = location.search.match(new RegExp('[?&]' + k + '=([^&]*)'));
    return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
  };

  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return !!(r.width && r.height);
  };

  const cards = Array.from(document.querySelectorAll('.card-area'));
  const ids = cards.slice(0, 8).map((c) => {
    const a = c.querySelector('a.job-name') || c.querySelector('a[href*="/job_detail/"]');
    const h = a ? (a.getAttribute('href') || '') : '';
    const m = h.match(/job_detail\/([^.?#/]+)/);
    return m ? m[1] : '';
  }).filter(Boolean);

  const body = document.body ? (document.body.innerText || '') : '';
  const head = body.slice(0, 4000);

  // 筛选条上的「工作经验(N)」—— 真点了筛选才会出现括号
  const chipExp = (() => {
    const m = head.match(/工作经验\s*[（(]\s*(\d+)\s*[）)]/);
    return m ? parseInt(m[1], 10) : null;
  })();

  const loading = Array.from(document.querySelectorAll('[class*=loading],[class*=skeleton],[class*=spin]'))
    .some(vis);

  const emptyHint = /没有找到|暂无.{0,8}职位|换个词|没有相关|无搜索结果|抱歉/.test(head);

  let inputEl = null;
  try { inputEl = INPUT ? document.querySelector(INPUT) : null; } catch (e) { inputEl = null; }
  if (!inputEl) {
    inputEl = Array.from(document.querySelectorAll('input'))
      .find((el) => ((el.getAttribute('placeholder') || '').indexOf('搜索职位') >= 0)) || null;
  }

  const conf = window.__jkitList || null;

  return JSON.stringify({
    href: location.href,
    q: qs('query'),
    exp: qs('experience'),
    city: qs('city'),
    n: cards.length,
    ids_hash: hash(ids.join('|')),
    first_id: ids[0] || '',
    container_same: conf ? !!conf.isConnected : null,
    chip_exp: chipExp,
    input_value: inputEl ? (inputEl.value || '') : '',
    scroll_y: Math.round(window.scrollY || 0),
    doc_h: document.documentElement.scrollHeight || 0,
    loading: !!loading,
    empty_hint: !!emptyHint,
  });
})()
