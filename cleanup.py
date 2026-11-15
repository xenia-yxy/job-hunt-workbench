# -*- coding: utf-8 -*-
"""
招聘台账 · 数据治理 + 瘦身（可复跑，唯一口径）

用法：
  python cleanup.py            # 预览，不写盘
  python cleanup.py --apply    # 执行：备份 → 补全 → 分类 → 清理 → 写盘 → 删除清单
  python cleanup.py --apply --no-http   # 由本地服务调用（不回环 PUT）

每次做三件事：
  1) 补全：岗位名 / 薪资 / 完整消息记录（从扫描产物回填，不编造）
  2) 分类：按 job_family_rules.json 打岗位族标签
  3) 清理：只留「有沟通 + 有后续机会」的记录，其余一律删除（有完整备份）

判定口径（参数集中在下方，改这里即可调整松紧）：
  - 明确结束（已终止 / 已拒绝 / 放弃 / 已完成 / 邀请已过期）→ 直接删
  - 机会分 = 录取概率 + HR 等我回复(+25) + 面试测评中(+18) + 有实质往来(+8)
              + 多轮对话(+8) + 岗位贴合(+10) - 低价值岗位族(-14)
  - 机会分 >= KEEP_THRESHOLD 才保留
"""
import json, re, sys, csv, shutil, urllib.request
from pathlib import Path
from datetime import datetime
from collections import Counter

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
# 路径 / 文件名 / 状态词一律来自配置（见下方 _cfg 与 _load_paths），代码里不留默认文件名。
# 这几个只是占位，真正的值在读完 config/app.json 之后赋值。
DATA = None
RULES = None
OUT = ROOT / 'runs' / 'cleanup'
SCAN_DIR = None
SCAN_FILES = []
DETAIL_FILES = []
KEYS = []

END_STATUS = set()
ACTIVE_STAGE = set()
NO_REPLY_NOTES = set()
SUBSTANTIVE_NOTES = set()
KEEP_THRESHOLD = 38

def load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:
        return default

# ---------- P2：配置 / 规则外置（逐值等价；写盘链路一字不动） ----------
def _cfg(name, kind='config'):
    base = ROOT / kind
    for f in (base / 'local' / (name + '.json'), base / (name + '.example.json')):
        v = load(f)
        if v:
            return v
    return None

_CH_CFG = _cfg('channels') or {}
_CH = [c for c in (_CH_CFG.get('channels') or []) if c.get('enabled', True)]
_CH.sort(key=lambda c: c.get('order', 0))
KEYS = [c['key'] for c in _CH]
if not KEYS:
    raise SystemExit('FAIL: 渠道配置为空或缺失（config/local/channels.json），不静默兜底。')
_CR = _cfg('cleanup', 'rules') or {}
_CR_W = _CR.get('weights') or {}
_CR_T = _CR.get('texts') or {}
END_STATUS = set(_CR.get('end_status') or [])
ACTIVE_STAGE = set(_CR.get('active_stage') or [])
NO_REPLY_NOTES = set(_CR.get('no_reply_notes') or [])
SUBSTANTIVE_NOTES = set(_CR.get('substantive_notes') or [])
KEEP_THRESHOLD = _CR.get('keep_threshold', 38)

# ---------- 路径与文件名：config/app.json（缺项直接退出，不做默认文件名兜底） ----------
_APP = _cfg('app') or {}
_F = _APP.get('files') or {}
if not _F.get('data'):
    raise SystemExit('FAIL: 缺少 app.files.data（config/local/app.json），不静默兜底。')
DATA = ROOT / _F['data']
RULES = (ROOT / _F['rules_fallback']) if _F.get('rules_fallback') else None
_CLEAN = _APP.get('cleanup') or {}
OUT = ROOT / (_CLEAN.get('out_dir') or 'runs/cleanup')
SCAN_DIR = (ROOT / _CLEAN['scan_dir']) if _CLEAN.get('scan_dir') else None
SCAN_FILES = [x for x in (_CLEAN.get('scan_files') or []) if x.get('file')]
DETAIL_FILES = [ROOT / p for p in (_CLEAN.get('detail_files') or [])]


def scan_role(role):
    """按 role 取扫描文件：jobs=岗位索引，jobs_alt=备用岗位索引，delta=会话列表快照。"""
    return [x for x in SCAN_FILES if (x.get('role') or 'jobs') == role]


def scan_path(item):
    return (SCAN_DIR / item['file']) if SCAN_DIR else None


# 备用来源的展示名：取配置里 role=jobs_alt 的第一条 tag，没有就用中性词
ALT_TAG = (((scan_role('jobs_alt') or [{}])[0]).get('tag')) or '备用来源'


def family_cfg():
    """岗位族规则三级加载：local 实例 > 配置指定 > example 骨架。"""
    cands = [ROOT / 'rules' / 'local' / 'job_family.json']
    if RULES is not None:
        cands.append(RULES)
    cands.append(ROOT / 'rules' / 'job_family.example.json')
    for f in cands:
        v = load(f)
        if v and v.get('rules'):
            return v
    return {'rules': [], 'preferred': [], 'low': [], 'fallback': '岗位未获取/待确认'}

def _text(key, **kw):
    s = str(_CR_T.get(key, ''))
    for k, v in kw.items():
        s = s.replace('{{' + k + '}}', str(v))
    return s

def key_of(s):
    s = str(s or '').strip().replace('...', '').replace('…', '')
    s = re.split(r'\s+', s)[0]
    return re.sub(r'[（）()·、,，\s]', '', s)

def family_of(role, cfg):
    text = str(role or '')
    for rule in cfg['rules']:
        if any(k in text for k in rule['kw']):
            return rule['family']
    return cfg['fallback']

TIME_RE = re.compile(r'^(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|\d{1,2}月\d{1,2}日|昨天|前天|今天|\d{1,2}:\d{2})\s*')

def split_time(txt):
    txt = str(txt or '').strip()
    m = TIME_RE.match(txt)
    t = m.group(1) if m else ''
    body = txt[m.end():].strip() if m else txt
    body = re.sub(r'^已读\s*', '', body).strip()
    return t, body

def msg(from_, text, time='', src=''):
    return {'from': from_, 'text': str(text or '').strip(), 'time': time, 'src': src}

# ---------- 1. 建索引：岗位名 / 薪资 / 完整对话 ----------
def build_job_index():
    idx = []
    for item in scan_role('jobs') + scan_role('jobs_alt'):
        f = scan_path(item)
        if not f:
            continue
        d = load(f, {}) or {}
        tag = item.get('tag') or item['file']
        for it in (d.get('items') or []):
            idx.append({
                'key': key_of(it.get('company')),
                'company': str(it.get('company') or '').split(' ')[0],
                'job': str(it.get('job') or '').strip(),
                'salary': str(it.get('salary') or '').strip(),
                'src': tag,
            })
    return idx

def match_job(company, idx):
    k = key_of(company)
    if not k:
        return None
    for it in idx:
        if it['key'] == k:
            return it
    for it in idx:
        if it['key'] and (it['key'].startswith(k) or k.startswith(it['key'])):
            return it
    return None

def build_detail_index():
    """逐条展开过的会话：最新抓取（kept_chats.json）优先，早期扫描兜底"""
    out = {}
    for f in DETAIL_FILES:
        d = load(f, {}) or {}
        for it in (d.get('items') or []):
            k = key_of(it.get('company'))
            built = _detail_item(dict(it))
            if built and (k not in out or len(built['messages']) > len(out[k]['messages'])):
                out[k] = built
    kc = load(OUT / 'kept_chats.json', {}) or {}
    for k, hit in (kc.get('results') or {}).items():
        if hit.get('messages'):
            out[k] = {'messages': hit['messages'], 'job': hit.get('job', ''), 'salary': hit.get('salary', '')}
    return out

def _detail_item(it):
    msgs = []
    for m in (it.get('messages') or []):
        cls = str(m.get('cls') or '')
        if cls == 'chat-message':       # 整段拼接的聚合节点，跳过
            continue
        who = 'HR' if 'item-friend' in cls else ('我' if 'item-myself' in cls else ('系统' if 'item-system' in cls else '记录'))
        t, body = split_time(m.get('txt'))
        if body:
            msgs.append(msg(who, body, t, '会话逐条扫描'))
    head = str(it.get('head') or '')
    hm = re.search(r'更多\s*(.+?)\s*(\d+\s*[-~]\s*\d+\s*K)', head)
    sm = re.search(r'(\d+\s*[-~]\s*\d+\s*K)', head)
    return {
        'messages': msgs,
        'job': hm.group(1).strip() if hm else '',
        'salary': sm.group(1).replace(' ', '') if sm else '',
    }

def build_chat_index():
    """会话列表快照（role=delta）：含时间 + HR + 最后消息全文"""
    out = {}
    for item in scan_role('delta'):
        f = scan_path(item)
        if not f:
            continue
        d = load(f, {}) or {}
        for it in (d.get('new') or []):
            k = key_of(it.get('company'))
            t, _ = split_time(it.get('txt'))
            out[k] = {'txt': str(it.get('txt') or ''), 'last': str(it.get('last') or ''), 'time': t or str(it.get('time') or '')}
    return out

def build_zl_index():
    """备用岗位索引（role=jobs_alt）：多份合并，后读到的覆盖前面的同公司记录"""
    out = {}
    for item in scan_role('jobs_alt'):
        f = scan_path(item)
        if not f:
            continue
        d = load(f, {}) or {}
        for it in (d.get('items') or []):
            out[key_of(it.get('company'))] = {
                'raw': str(it.get('raw') or ''), 'last': str(it.get('last') or ''),
                'job': str(it.get('job') or ''), 'salary': str(it.get('salary') or ''),
            }
    return out

# ---------- 2. 补全单条记录 ----------
def enrich(r, job_idx, det_idx, chat_idx, zl_idx, cfg):
    k = key_of(r.get('company'))
    role_before = str(r.get('role') or '').strip()
    sal_before = str(r.get('salary') or '').strip()
    det = det_idx.get(k)
    ji = match_job(r.get('company'), job_idx)

    # 岗位 / 薪资：只填空，不覆盖已有值
    if not role_before:
        for cand, src in [(det.get('job') if det else '', '会话详情'),
                          (ji.get('job') if ji else '', ji.get('src') if ji else ''),
                          (zl_idx.get(k, {}).get('job', ''), ALT_TAG)]:
            if cand:
                r['role'] = cand
                r['role_source'] = src
                break
    else:
        r['role_source'] = '原有'
    if not sal_before:
        for cand in [(det.get('salary') if det else ''), (ji.get('salary') if ji else ''), zl_idx.get(k, {}).get('salary', '')]:
            if cand:
                r['salary'] = cand
                break
    if not str(r.get('role') or '').strip():
        r['role_source'] = '未获取（HR 未注明）'

    # 完整消息记录
    existing = r.get('messages') or []
    complete = False
    if det and det.get('messages'):
        r['messages'] = det['messages']
        complete = True
    else:
        ms = []
        # 兼容旧格式 {cls,txt} / {text} / 纯字符串
        for m in existing:
            if isinstance(m, str):
                ms.append(msg('记录', m, src='旧台账'))
            elif isinstance(m, dict):
                t, body = split_time(m.get('txt') or m.get('text') or '')
                if body:
                    ms.append(msg(m.get('from') or '记录', body, m.get('time') or t, '旧台账'))
        if not ms:
            extra = (chat_idx.get(k) or {}).get('last') or (zl_idx.get(k) or {}).get('raw')
            body = str(extra or r.get('last_msg') or r.get('summary') or '').strip()
            if body:
                ms.append(msg('快照', body, (chat_idx.get(k) or {}).get('time', ''), '扫描快照·最后消息'))
        # 校招：截止说明单独一条，避免丢关键节点
        dn = str(r.get('deadline_note') or '').strip()
        if dn and dn not in [x['text'] for x in ms]:
            ms.append(msg('系统', dn, '', '邮件/截止说明'))
        r['messages'] = ms

    r['msg_complete'] = complete
    n = len(r['messages'])
    who = {m.get('from') for m in r['messages'] if isinstance(m, dict)}
    if n >= 4:
        r['talk_depth'] = '多轮对话'
    elif '我' in who and 'HR' in who:
        r['talk_depth'] = '双向沟通'
    elif 'HR' in who:
        r['talk_depth'] = 'HR单向'
    elif n:
        r['talk_depth'] = '我方单向/快照'
    else:
        r['talk_depth'] = '无记录'

    # 岗位族
    r['job_family'] = family_of(r.get('role'), cfg)
    return r

# ---------- 3. 机会分 / 保留判定 ----------
def decide(r, cfg):
    flag = str(r.get('prob_flag') or '')
    lvl = str(r.get('prob_level') or '')
    status = str(r.get('status') or '')
    note = str(r.get('scan_note') or '')
    stage = str(r.get('stage') or '')
    fam = str(r.get('job_family') or '')
    prob = int(r.get('prob') or 0)
    text = ' '.join([str(r.get('summary') or ''), str(r.get('last_msg') or ''), str(r.get('deadline_note') or '')])

    if flag == _CR.get('terminated_flag') or lvl == _CR.get('reject_level') or status in END_STATUS:
        return False, _text('end'), 0
    if _CR.get('expired_keyword', '') in text:
        return False, _text('expired'), 0

    hr_wait = any(x in note for x in (_CR.get('hr_wait_keywords') or []))
    in_process = lvl == 'interview' or stage in ACTIVE_STAGE
    substantive = _CR.get('substantive_keyword', '') in note or note in SUBSTANTIVE_NOTES

    # 硬规则：只发出招呼 / 系统自动回执、对方从没实质回应过的，一律不留
    if not hr_wait and not in_process:
        if note in NO_REPLY_NOTES or (lvl in set(_CR.get('no_reply_levels') or []) and not substantive):
            return False, _text('no_reply'), 0

    s = prob
    if hr_wait:
        s += _CR_W.get('hr_wait', 0)
    if in_process:
        s += _CR_W.get('in_process', 0)
    if substantive:
        s += _CR_W.get('substantive', 0)
    if len(r.get('messages') or []) >= _CR.get('multi_msg_min', 3):
        s += _CR_W.get('multi_msg', 0)
    pf = _CR.get('family_fields') or {}
    if fam in (cfg.get(pf.get('preferred', 'preferred')) or []):
        s += _CR_W.get('preferred', 0)
    if fam in (cfg.get(pf.get('low', 'low')) or []):
        s += _CR_W.get('low', 0)

    if s >= KEEP_THRESHOLD:
        return True, _text('keep', score=s), s
    return False, _text('drop', score=s), s

# ---------- 4. 主流程 ----------
def main():
    args = sys.argv[1:]
    apply = '--apply' in args
    no_http = '--no-http' in args
    cfg = family_cfg()
    data = load(DATA, {}) or {}
    job_idx, det_idx = build_job_index(), build_detail_index()
    chat_idx, zl_idx = build_chat_index(), build_zl_index()

    # 声明渠道并上数据文件实际顶层键（老数据不丢记录）
    keys = list(dict.fromkeys(list(KEYS) + [k for k, v in data.items() if isinstance(v, list)]))
    all_rows = [(k, r) for k in keys for r in (data.get(k) or [])]
    role_filled = msgs_before = 0
    for k, r in all_rows:
        if not str(r.get('role') or '').strip(): role_filled += 1
        if r.get('messages'): msgs_before += 1
        enrich(r, job_idx, det_idx, chat_idx, zl_idx, cfg)

    keep, drop = {k: [] for k in keys}, []
    for k, r in all_rows:
        ok, why, score = decide(r, cfg)
        r['keep_reason'] = why
        r['opportunity_score'] = score
        if ok:
            keep[k].append(r)
        else:
            drop.append((k, r, why))

    after = sum(len(v) for v in keep.values())
    summary = {
        'before': len(all_rows), 'after': after, 'removed': len(drop),
        'kept_by_source': {k: len(v) for k, v in keep.items()},
        'families_kept': dict(Counter(str(r.get('job_family')) for v in keep.values() for r in v)),
        'role_completed': sum(1 for v in keep.values() for r in v if str(r.get('role') or '').strip()),
        'role_unknown_kept': sum(1 for v in keep.values() for r in v if not str(r.get('role') or '').strip()),
        'messages_before': msgs_before,
        'messages_after': sum(1 for v in keep.values() for r in v if r.get('messages')),
        'msg_complete': sum(1 for v in keep.values() for r in v if r.get('msg_complete')),
        'drop_reasons': dict(Counter(re.sub(r'机会分 -?\d+', '机会分不足', why).split('（')[0] for _, _, why in drop)),
    }

    if not apply:
        print('DRY-RUN（未写盘）')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print('\n--- 保留清单 ---')
        for k in keys:
            for r in keep[k]:
                print(f"  {k[:6]:6s} | {str(r.get('company'))[:20]:20s} | {str(r.get('role') or '岗位未获取')[:26]:26s} | {str(r.get('job_family'))[:14]:14s} | {r.get('prob'):3d}% | {r.get('keep_reason')}")
        print('\n--- 保留但岗位未获取（需本人确认，绝不编造）---')
        for k in keys:
            for r in keep[k]:
                if not str(r.get('role') or '').strip():
                    print(f"  {k[:6]:6s} | {r.get('company')} | 消息数 {len(r.get('messages') or [])} | {str(r.get('last_msg') or '')[:50]}")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = OUT / f'unified_data.before-cleanup-{stamp}.json'
    shutil.copy2(DATA, backup)
    removed_csv = OUT / f'删除清单-{stamp}.csv'
    with removed_csv.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['渠道', '公司', '岗位', '岗位分类', '概率', '状态', '阶段', '消息数', '删除原因', '最后消息'])
        for k, r, why in drop:
            w.writerow([k, r.get('company', ''), r.get('role', ''), r.get('job_family', ''), r.get('prob', ''),
                        r.get('status', ''), r.get('stage', ''), len(r.get('messages') or []), why,
                        str(r.get('last_msg') or r.get('summary') or '')[:200]])

    newdata = {k: keep[k] for k in keys}
    tmp = DATA.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(newdata, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(DATA)

    pushed = False
    if not no_http:
        try:
            payload = json.dumps({'data': newdata, 'backup': False}, ensure_ascii=False).encode('utf-8')
            req = urllib.request.Request('http://127.0.0.1:8787/api/data', data=payload,
                                         headers={'Content-Type': 'application/json'}, method='PUT')
            with urllib.request.urlopen(req, timeout=8) as resp:
                pushed = resp.status == 200
        except Exception as e:
            print('WARN: 未能通知本地服务刷新 CSV：' + str(e), file=sys.stderr)

    summary.update({'backup': str(backup), 'removed_csv': str(removed_csv), 'csv_refreshed': pushed})
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
