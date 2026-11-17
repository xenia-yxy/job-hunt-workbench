/* =============================================================
   工作台统一交互层 / ui.js
   -------------------------------------------------------------
   和 ui.css 分工一致：样式在 css，行为在这里。页面只要在 </body> 前
   引入 <script src="/assets/ui.js"></script>，就自动得到四件事：

   1. 折叠卡片   <section class="card" data-fold="closed">  → 头部右侧出「展开」
                 data-fold="open" 默认展开；都不写则不加折叠按钮。
   2. 壳层跳转   WB.go('工作台-档案.html')  → 在 iframe 壳里换页，保留返回栈
   3. 变更广播   WB.notify('profile')       → 告诉壳层「数据变了」，壳层刷新统计并
                 把依赖它的页面标记为待刷新。所有 POST/PUT/DELETE /api/ 会自动广播，
                 页面自己不用记着调。
   4. 轻提示     WB.toast('已保存')

   为什么要有这一层：以前每个页面各写一套折叠、各自 location.href 跳转，
   结果点进内页就丢了导航、返回要靠浏览器后退、改完数据首页还是旧数字。
   行为收在一处之后，页面只声明「这块默认收起」「这里跳转」，不再自己实现。
   ============================================================= */
(function () {
  'use strict';

  var WB = window.WB = window.WB || {};

  /* 是否在壳层 iframe 里（直接双击打开页面时为 false） */
  WB.embedded = (function () {
    try { return !!(window.parent && window.parent !== window); } catch (e) { return false; }
  })();

  function post(msg) {
    if (!WB.embedded) return;
    try { window.parent.postMessage(msg, '*'); } catch (e) {}
  }

  /* ---------- 1. 壳层跳转 ---------- */
  WB.go = function (url) {
    if (!url) return;
    if (WB.embedded) post({ type: 'wb:go', url: url });
    else location.href = url;
  };

  /* ---------- 2. 变更广播 ---------- */
  WB.notify = function (scope, detail) {
    post({ type: 'wb:changed', scope: scope || 'data', detail: detail || null });
  };

  /* ---------- 3. 轻提示 ---------- */
  var toastEl = null, toastTimer = null;
  WB.toast = function (text, ms) {
    if (!text) return;
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'wb-toast';
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('on'); }, ms || 2000);
  };

  /* ---------- 4. 折叠卡片 ---------- */
  function paint(card, btn) {
    var closed = card.classList.contains('is-closed');
    btn.textContent = closed ? '展开' : '收起';
    btn.setAttribute('aria-expanded', closed ? 'false' : 'true');
    btn.title = closed ? '展开这块内容' : '收起，只留摘要';
  }

  function setupCard(card) {
    if (!card || card.dataset.foldReady) return;
    card.dataset.foldReady = '1';
    var head = card.querySelector('.card-h, .hd, header, h2');
    if (head && !head.classList.contains('card-h')) head = card.querySelector('.card-h') || head;
    if (!head) return;

    var btn = head.querySelector('.foldbtn');
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'foldbtn';
      head.appendChild(btn);
    }
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      card.classList.toggle('is-closed');
      paint(card, btn);
      if (!card.classList.contains('is-closed') && card.scrollIntoView) {
        try { card.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (err) {}
      }
    });
    if (String(card.dataset.fold) === 'closed') card.classList.add('is-closed');
    paint(card, btn);
  }

  WB.fold = function (root) {
    var box = root || document;
    var list = box.querySelectorAll ? box.querySelectorAll('.card[data-fold], section[data-fold]') : [];
    Array.prototype.forEach.call(list, setupCard);
  };

  /* 明确要求展开/收起某张卡（给页面脚本用） */
  WB.setFold = function (card, open) {
    if (!card) return;
    card.classList.toggle('is-closed', !open);
    var btn = card.querySelector('.foldbtn');
    if (btn) paint(card, btn);
  };

  /* URL 带 #id 时，自动展开它所在的卡片（壳层/内页互相跳转用） */
  function openByHash() {
    if (!location.hash) return;
    var el = null;
    try { el = document.querySelector(location.hash); } catch (e) { return; }
    if (!el) return;
    var card = el.closest ? el.closest('.card[data-fold]') : null;
    if (card) WB.setFold(card, true);
    setTimeout(function () {
      try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    }, 30);
  }

  /* ---------- 5. 写操作自动广播 ---------- */
  var origFetch = window.fetch;
  if (origFetch && !window.__wbFetchHooked) {
    window.__wbFetchHooked = 1;
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : ((input && input.url) || '');
      var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      var p = origFetch.apply(this, arguments);
      if (method !== 'GET' && method !== 'HEAD' && url.indexOf('/api/') >= 0) {
        p.then(function (r) {
          if (r && r.ok) WB.notify('data', { path: url.split('?')[0], method: method });
        }).catch(function () {});
      }
      return p;
    };
  }

  /* ---------- 6. 初始化 ---------- */
  function boot() {
    WB.fold();
    openByHash();
    // 页面里用 <a href="..."> 跳别的工作台页面时，交给壳层（自带返回栈）
    document.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a[href]') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (!href || href.charAt(0) === '#' || /^(https?:|mailto:|javascript:|\/\/)/i.test(href)) return;
      if (a.target === '_blank' || a.hasAttribute('download')) return;
      if (!WB.embedded) return;
      e.preventDefault();
      WB.go(href);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
