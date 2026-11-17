(() => {
  // 智联搜索结果页的列表状态。字段与 BOSS 版对齐，searchflow 可原样复用。
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

  const cards = Array.from(document.querySelectorAll('.job-list-panel .job-card'));
  const ids = cards.slice(0, 8).map((c) => {
    const a = c.querySelector('a[href*="jobdetail"]');
    const h = a ? (a.getAttribute('href') || '') : '';
    const m = h.match(/jobdetail\/([^.?#/]+)\.htm/);
    return m ? m[1] : '';
  }).filter(Boolean);

  const body = document.body ? (document.body.innerText || '') : '';
  const head = body.slice(0, 4000);

  const loading = Array.from(document.querySelectorAll('[class*=loading],[class*=skeleton],[class*=spin]'))
    .some(vis);

  const emptyHint = /没有找到|暂无.{0,8}职位|换个词|没有相关|无搜索结果|抱歉/.test(head);

  let inputEl = document.querySelector('input.query-sug__input');
  if (!inputEl) {
    inputEl = Array.from(document.querySelectorAll('input'))
      .find((el) => ((el.getAttribute('placeholder') || '').indexOf('搜索') >= 0)) || null;
  }

  const act = document.querySelector('.job-card--active');

  return JSON.stringify({
    href: location.href,
    q: qs('kw'),
    exp: '',
    city: qs('jl'),
    n: cards.length,
    ids_hash: hash(ids.join('|')),
    first_id: ids[0] || '',
    active_idx: act ? cards.indexOf(act) : -1,
    input_value: inputEl ? (inputEl.value || '') : '',
    scroll_y: Math.round(window.scrollY || 0),
    doc_h: document.documentElement.scrollHeight || 0,
    loading: !!loading,
    empty_hint: !!emptyHint,
  });
})()
