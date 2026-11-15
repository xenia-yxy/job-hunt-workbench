// 招聘跟踪 · 录取概率引擎（Node / 浏览器共用，零依赖）
// 口径：概率 = 岗位基础分 + 当前进度修正。进度变了，概率立刻重算。
// 这是基于简历事实 / 岗位方向 / 沟通进度的启发式估算，不是真实录取率。
//
// P2：常量、词表、分级、文案全部外置到 rules/*prob.json；渠道加分与文案由
// config/*channels.json 的 prob 段单一提供。本文件只保留引擎逻辑，不含实例值。
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;   // Node 端返回工厂
  else root.ProbEngine = factory(
    (root.__WB_CONFIG__ || {}).prob || null,
    (root.__WB_CONFIG__ || {}).channels || []
  );
})(typeof globalThis !== 'undefined' ? globalThis : this, function (rules, channels) {
  'use strict';
  if (!rules) throw new Error('prob 规则未注入：Node 端需传 rules/local/prob.json，浏览器端需注入 window.__WB_CONFIG__.prob');
  var R = rules;
  var CH = {};
  (channels || []).forEach(function (c) { CH[c.key] = c; });

  var rxCache = {};
  function rx(k) {
    if (!rxCache[k]) rxCache[k] = new RegExp(R.regex[k], R.regex_flags || 'i');
    return rxCache[k];
  }
  function has(s, re) { return re.test(String(s == null ? '' : s)); }
  function tpl(key, vars) {
    return String(R.texts[key] == null ? '' : R.texts[key]).replace(/\{\{(\w+)\}\}/g,
      function (_, k) { return vars[k] == null ? '' : String(vars[k]); });
  }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  // ---------- 文本取材：只用可举证字段，不用 messages（避免把我方招呼误判成 HR 回复） ----------
  function textOf(r) {
    return [r.role, r.company, r.summary, r.last_msg, r.salary, r.stage, r.deadline_note]
      .filter(Boolean).join(' ');
  }

  // ---------- 1. 岗位基础分：方向贴合度 + 门槛 + 渠道 ----------
  function baseOf(r, t) {
    var W = R.weights, F = R.flags, T = R.texts, TH = R.thresholds;
    var out = { score: R.base_score, why: [], flag: '' };
    var role = String(r.role || ''), company = String(r.company || '');
    var off = has(t, rx('off')), ai = has(t, rx('ai')), tech = has(t, rx('tech')), soft = has(t, rx('soft'));

    if (off && !tech) {
      out.score += W.off_penalty;
      out.why.push(tpl('off', { role_or_company: (role || company).slice(0, TH.role_slice) }));
      out.flag = out.flag || F.off;
    } else if (ai && tech) {
      out.score += W.ai_tech_bonus; out.why.push(T.ai_tech);
    } else if (tech) {
      out.score += W.tech_bonus; out.why.push(T.tech);
    } else if (ai) {
      out.score += W.ai_bonus; out.why.push(T.ai); out.flag = out.flag || F.ai;
    } else if (soft) {
      out.score += W.soft_bonus; out.why.push(T.soft); out.flag = out.flag || F.soft;
    } else {
      out.score += W.none_bonus; out.why.push(T.none); out.flag = out.flag || F.none;
    }

    if (has(company + t, rx('big'))) {
      out.score += W.company_penalty; out.why.push(T.company_big); out.flag = out.flag || F.big;
    } else if (has(company, rx('company_tech'))) {
      out.score += W.company_tech_bonus; out.why.push(T.company_tech);
    }

    if (has(t, rx('senior'))) {
      out.score += W.senior_penalty; out.why.push(T.senior); out.flag = out.flag || F.senior;
    }

    var sal = String(r.salary || '');
    var nums = sal.replace(/,/g, '').match(/\d+/g);
    if (nums && nums.length) {
      var lo = Number(nums[0]);
      if (lo >= TH.salary_high) { out.score += W.salary_high_penalty; out.why.push(tpl('salary_high', { salary: sal })); }
      else if (lo > 0 && lo <= TH.salary_low && tech) { out.score += W.salary_low_bonus; out.why.push(tpl('salary_low', { salary: sal })); }
    }

    var c = CH[r.source || ''];
    if (c && c.prob) {
      out.score += Number(c.prob.add) || 0;
      if (c.prob.why) out.why.push(c.prob.why);
    }

    if (has(t, rx('cohort'))) {
      out.why.push(T.cohort); out.flag = out.flag || F.cohort;
    }

    out.score = clamp(Math.round(out.score), R.clamp.base[0], R.clamp.base[1]);
    return out;
  }

  // ---------- 2. 当前进度：决定修正幅度 ----------
  function progressOf(r, t) {
    var S = R.status_sets;
    var s = String(r.status || ''), st = String(r.stage || '');
    if (S.reject.indexOf(s) >= 0 || has(st, rx('stage_reject'))) return R.levels.reject;
    if (S.interview.indexOf(s) >= 0 || has(st, rx('stage_interview')) || has(t, rx('text_interview'))) return R.levels.interview;
    if (S.reply.indexOf(s) >= 0 || (!has(t, rx('mine')) && has(t, rx('question')))) return R.levels.reply;
    if (S.talk.indexOf(s) >= 0) return R.levels.talk;
    if (S.ack.indexOf(s) >= 0) return R.levels.ack;
    if (S.sent.indexOf(s) >= 0 || has(t, rx('mine'))) return R.levels.sent;
    return R.levels.none;
  }

  // ---------- 3. 合成（可重复调用，结果稳定） ----------
  // 基础分来源三选一：用户手动设定(base_manual) / 校招原人工评估(base_locked) / 引擎按岗位实算
  // 所有中间量都缓存在记录里，因此重复写盘不会让理由丢失或分数漂移。
  function apply(r) {
    var t = textOf(r);
    var prior = Array.isArray(r.prob_prior) ? r.prob_prior.slice(0, R.thresholds.prior_slice) :
      (Array.isArray(r.prob_reason) ? r.prob_reason : [])
        .filter(function (v) { return typeof v === 'string' && !rx('prior_prefix').test(v); })
        .slice(0, R.thresholds.prior_slice);

    var fixed = null, manual = false, locked = false, b;
    if (r.base_manual === true && isFinite(Number(r.base_prob)) && Number(r.base_prob) > 0) {
      fixed = clamp(Math.round(Number(r.base_prob)), R.clamp.fixed[0], R.clamp.fixed[1]); manual = true;
    } else if (r.base_locked === true && isFinite(Number(r.base_prob)) && Number(r.base_prob) > 0) {
      fixed = clamp(Math.round(Number(r.base_prob)), R.clamp.fixed[0], R.clamp.fixed[1]); locked = true;
    }
    if (fixed != null) {
      b = { score: fixed, flag: manual ? R.flags.manual : '' };
      b.why = [manual ? tpl('manual', { fixed: fixed }) : tpl('locked', { fixed: fixed })];
    } else {
      b = baseOf(r, t);
    }

    var lv = progressOf(r, t);
    var add = lv.add;
    // 校招原评估已包含阶段判断，进度修正按 25% 计，避免把低分机械抬高
    var discount = (locked && lv.key !== 'reject');
    if (discount) add = Math.round(add * R.discount_factor);

    var prob = lv.fixed != null ? lv.fixed : clamp(Math.round(b.score + add), R.clamp.total[0], R.clamp.total[1]);

    var vars = {
      label: lv.label, icon: lv.icon, base: b.score, prob: prob, next: lv.next,
      add_signed: (add >= 0 ? '+' : '') + add,
      discount: discount ? R.texts.discount_note : ''
    };
    var reason = [tpl(lv.fixed != null ? 'progress_fixed' : 'progress_normal', vars)];
    if (!locked) b.why.forEach(function (v) { reason.push(v); });
    prior.forEach(function (v) { reason.push(R.texts.prior_prefix + v); });

    r.base_prob = b.score;
    r.prob = prob;
    r.prob_stage = lv.label;
    r.prob_level = lv.key;
    r.prob_next = lv.next;
    r.prob_reason = reason;
    r.prob_prior = prior;
    r.prob_base_why = b.why;
    r.prob_flag = lv.key === 'reject' ? R.flags.terminated : (b.flag || '');
    return r;
  }

  return {
    apply: apply,
    baseOf: baseOf,
    progressOf: progressOf,
    levels: R.levels,
    version: 2,
    rules_schema: R.schema
  };
});
