#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板导出自检：敏感串扫描 + 历史文件 hash 基线与复核。

零依赖、默认只读；仅 hash-out 会写基线文件（写到 gitignored 的本地目录）。

用法（项目根执行）：
  python tools/verify_template.example.py sensitive [--patterns FILE] [--root DIR]
  python tools/verify_template.example.py hash-out  FILE [--root DIR]
  python tools/verify_template.example.py hash-check FILE [--root DIR]

设计约定：
- 敏感词表默认读 config/local/sensitive_patterns.json（本地实例，gitignored，不进模板），
  脚本自身只带通用正则，因此本文件可以安全地进入通用模板白名单。
- hash 基线覆盖「历史不动清单」：除下面 EXCLUDE_* 外的全部文件。
  新增骨架、允许改动的源文件不进基线；hash-check 报告变更清单，由人工核对。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 新增骨架目录 / 运行时产物：不进历史基线
EXCLUDE_DIRS = {
    '.git', 'config', 'rules', 'templates', 'tools', 'workbuddy_out',
    '__pycache__', 'node_modules', '.idea', '.vscode',
}
# 允许改动的源文件 / 生成物：不进历史基线
EXCLUDE_FILES = {
    'server.js', 'prob.js', 'cleanup.py', '.gitignore', 'README.md',
    '启动招聘服务.bat', '招聘跟踪总览.html',
    '工作台.html', '工作台-投递台账.html', 'workbench_readonly.js',
    '工作台-岗位判断.html', '工作台-文档.html', '工作台-首页.html',
    '.workbuddy',
}
# 允许改动的子目录内源文件（按相对路径精确排除）
EXCLUDE_REL_PATHS = {
    'runs/20261114-app/app.template.html',
    'runs/20261114-app/build.js',
    'jkit/conditions.py',
    'jkit/conditions_loader.py',
    'docs/模板导出清单.md',
}
# 导出白名单（相对项目根）：这些文件必须零敏感串
TEMPLATE_GLOBS = [
    'README.md', 'LICENSE', '.gitignore', 'serve.example.js',
    'docs/*.md', 'workbuddy_out/README.md',
    'config/*.example.json', 'config/README.md',
    'rules/*.example.json', 'rules/README.md',
    'templates/**/*', 'tools/*.example.py', 'tools/README.md',
    '工作台.html', '工作台-投递台账.html', 'workbench_readonly.js',
    '工作台-岗位判断.html', '工作台-文档.html', '工作台-首页.html',
    # 服务端 / 概率引擎 / 清理：中性化后进模板轨（导出的路径映射见 tools/sync_template.py）
    'server.js', 'prob.js', 'cleanup.py',
    'app/*.js', 'app/*.html',
]
# 通用兜底正则（不含任何实例数据）
GENERIC_PATTERNS = [
    r'1[3-9]\d{9}',
    r'[\w.+-]+@[\w-]+\.[A-Za-z]{2,}',
    r'\b[0-9a-f]{32}\b',
    r'api[_-]?key\s*[:=]\s*["\x27]?[A-Za-z0-9_-]{16,}',
]
DEFAULT_PATTERNS_FILE = 'config/local/sensitive_patterns.json'
TEXT_EXT = {'.json', '.md', '.py', '.html', '.js', '.txt', '.yml', '.yaml', '.example', ''}


def rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def is_excluded(relpath: str) -> bool:
    parts = relpath.split('/')
    if parts[0] in EXCLUDE_DIRS:
        return True
    if relpath in EXCLUDE_REL_PATHS:
        return True
    if len(parts) == 1 and relpath in EXCLUDE_FILES:
        return True
    return False


def iter_history_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS and not x.startswith('.git')]
        for name in filenames:
            f = d / name
            if is_excluded(rel(f, root)):
                continue
            yield f


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build_snapshot(root: Path) -> dict:
    files = {}
    for f in iter_history_files(root):
        try:
            files[rel(f, root)] = sha256_of(f)
        except OSError:
            continue
    return {'generated_at': datetime.now().isoformat(timespec='seconds'), 'count': len(files), 'files': files}


def load_patterns(root: Path, path: str | None):
    p = root / (path or DEFAULT_PATTERNS_FILE)
    literals, regexes = [], []
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding='utf-8'))
            literals = list(cfg.get('literals') or [])
            regexes = list(cfg.get('regex') or [])
        except (OSError, ValueError) as e:
            print('WARN: 敏感词表读取失败：%s（%s）' % (p, e), file=sys.stderr)
    else:
        print('WARN: 未找到本地敏感词表 %s，仅使用通用正则' % p, file=sys.stderr)
    return literals, regexes + GENERIC_PATTERNS


def collect_template_files(root: Path):
    seen = set()
    for pattern in TEMPLATE_GLOBS:
        for hit in glob.glob(str(root / pattern), recursive=True):
            f = Path(hit)
            if f.is_file() and f.suffix in TEXT_EXT and str(f) not in seen:
                seen.add(str(f))
                yield f


def mask(s: str) -> str:
    s = s.strip()
    return (s[:24] + '…') if len(s) > 26 else s


def cmd_sensitive(root: Path, args) -> int:
    literals, regexes = load_patterns(root, args.patterns)
    compiled = []
    for r in regexes:
        try:
            compiled.append((r, re.compile(r, re.I)))
        except re.error as e:
            print('WARN: 忽略非法正则 %r（%s）' % (r, e), file=sys.stderr)
    hits = []
    files = list(collect_template_files(root))
    for f in files:
        try:
            lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for lit in literals:
                if lit and lit in line:
                    hits.append((rel(f, root), i, 'literal', mask(line)))
            for raw, rx in compiled:
                if rx.search(line):
                    hits.append((rel(f, root), i, 'regex:' + raw[:28], mask(line)))
    if hits:
        print('FAIL: 导出白名单发现 %d 处敏感串命中（扫描 %d 个文件）：' % (len(hits), len(files)))
        for f, i, kind, text in hits[:80]:
            print('  %s:%d  [%s]  %s' % (f, i, kind, text))
        if len(hits) > 80:
            print('  … 其余 %d 处省略' % (len(hits) - 80))
        return 1
    print('PASS: 导出白名单 %d 个文件，敏感串零命中。' % len(files))
    return 0


def cmd_hash_out(root: Path, args) -> int:
    snap = build_snapshot(root)
    out = Path(args.file)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
    print('基线已写入 %s（%d 个文件）' % (out, snap['count']))
    return 0


def cmd_hash_check(root: Path, args) -> int:
    base_path = Path(args.file)
    if not base_path.is_absolute():
        base_path = root / base_path
    if not base_path.exists():
        print('FAIL: 基线文件不存在：%s' % base_path)
        return 2
    base = {k: v for k, v in json.loads(base_path.read_text(encoding='utf-8'))['files'].items()
            if not is_excluded(k)}
    cur = build_snapshot(root)['files']
    added = sorted(set(cur) - set(base))
    removed = sorted(set(base) - set(cur))
    changed = sorted(k for k in set(base) & set(cur) if base[k] != cur[k])
    print('基线 %d 个文件 / 当前 %d 个文件' % (len(base), len(cur)))
    print('新增 %d、缺失 %d、变更 %d' % (len(added), len(removed), len(changed)))
    for k in removed[:30]:
        print('  MISSING  %s' % k)
    for k in changed[:30]:
        print('  CHANGED  %s' % k)
    for k in added[:10]:
        print('  ADDED    %s' % k)
    if removed or changed:
        print('FAIL: 历史文件发生变化，请核对是否属于允许改动清单。')
        return 1
    print('PASS: 全部历史文件 hash 与基线一致。')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='模板导出自检（敏感串 / hash 基线）')
    ap.add_argument('command', choices=['sensitive', 'hash-out', 'hash-check'])
    ap.add_argument('file', nargs='?', help='hash-out / hash-check 的基线文件路径')
    ap.add_argument('--root', default='.', help='项目根，默认当前目录')
    ap.add_argument('--patterns', default=None, help='本地敏感词表路径')
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if args.command == 'sensitive':
        return cmd_sensitive(root, args)
    if not args.file:
        print('FAIL: %s 需要基线文件路径' % args.command)
        return 2
    if args.command == 'hash-out':
        return cmd_hash_out(root, args)
    return cmd_hash_check(root, args)


if __name__ == '__main__':
    sys.exit(main())
