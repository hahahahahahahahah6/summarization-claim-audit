"""Phrase matching and provider-resolved AST evidence; source is never executed."""
import ast
import fnmatch
import os
import re
from pathlib import Path
from .phrases import SUMMARIZATION, NOSUMMARIZATION

SUMY = {'LexRankSummarizer', 'LsaSummarizer', 'TextRankSummarizer',
        'LuhnSummarizer', 'EdmundsonSummarizer', 'RandomSummarizer'}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        if fence:
            if re.match(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}\s*$', line):
                fence = None
            lines.append('')
        else:
            match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
            if match:
                fence = (match[1][0], len(match[1]))
                lines.append('')
            else:
                lines.append(line)
    return '\n'.join(lines)


def remove_matches(text, phrases):
    found = []
    for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
        pattern = r'\b' + re.escape(phrase) + r'\b'
        if re.search(pattern, text):
            found.append(phrase)
            text = re.sub(pattern, lambda m: ' ' * len(m[0]), text)
    return text, found


def claims(text):
    positive, negative = set(), set()
    for paragraph in re.split(r'\n\s*\n', prose(text)):
        remaining, no = remove_matches(normalize(paragraph), NOSUMMARIZATION)
        _, yes = remove_matches(remaining, SUMMARIZATION)
        positive.update(yes)
        negative.update(no)
    return sorted(positive), sorted(negative)


def evidence(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    imports = {}
    shadows = set()
    assignments = []
    # Single-module resolution: local definitions always override imports.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module + '.' + alias.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadows.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                shadows.update(a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs if a.arg != 'self')
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assignments.extend((target, node.value) for target in targets)
    def key(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = key(node.value)
            return base + '.' + node.attr if base else None
        return None
    assigned = {key(t) for t, v in assignments if key(t)}
    def resolve(node):
        name = key(node)
        if not name:
            return ''
        head, *tail = name.split('.')
        if head in shadows or head in assigned:
            return ''
        return '.'.join([imports.get(head, ''), *tail]) if head in imports else ''
    def constructor(node):
        if not isinstance(node, ast.Call):
            return False
        path = resolve(node.func)
        return path.startswith('sumy.') and path.rsplit('.', 1)[-1] in SUMY
    bound = set()
    # Resolve aliases to already constructed objects, including self attributes.
    for _ in range(len(assignments) + 1):
        before = len(bound)
        for target, value in assignments:
            if constructor(value) or (key(value) in bound if value is not None else False):
                if key(target):
                    bound.add(key(target))
        if len(bound) == before:
            break
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path = resolve(node.func)
        leaf = path.rsplit('.', 1)[-1]
        if path.startswith('transformers.'):
            if leaf == 'SummarizationPipeline':
                return True
            if leaf == 'pipeline':
                tasks = list(node.args[:1]) + [kw.value for kw in node.keywords if kw.arg == 'task']
                if any(isinstance(t, ast.Constant) and isinstance(t.value, str) and
                       re.search(r'(?<![a-z])summarization(?![a-z])', normalize(t.value)) for t in tasks):
                    return True
        if path.startswith('gensim.summarization.') and leaf == 'summarize':
            return True
        if key(node.func) in bound or constructor(node.func):
            return True
    return False


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('root must be an existing directory')
    docs, production, tests = [], [], []
    py_count = 0
    def ignored(rel):
        return any(fnmatch.fnmatch(rel, p) or any(fnmatch.fnmatch(c, p) for c in rel.split('/')) for p in ignore)
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink() and not ignored((Path(directory)/d).relative_to(root).as_posix()))
        for filename in sorted(files):
            path = Path(directory)/filename
            rel = path.relative_to(root)
            if path.is_symlink() or ignored(rel.as_posix()):
                continue
            parts = rel.parts
            if rel.as_posix() == 'README.md' or (path.suffix == '.md' and 'docs' in parts[:-1]):
                yes, no = claims(path.read_text(encoding='utf-8'))
                if yes or no:
                    docs.append({'path': rel.as_posix(), 'SUMMARIZATION': yes, 'NOSUMMARIZATION': no})
            if path.suffix != '.py':
                continue
            py_count += 1
            if any(p in {'fixtures', 'docs', 'examples'} for p in parts[:-1]):
                continue
            if evidence(path.read_text(encoding='utf-8')):
                is_test = 'tests' in parts[:-1] or filename.startswith('test_') or filename.endswith('_test.py')
                (tests if is_test else production).append(rel.as_posix())
    findings = []
    yes = [d['path'] for d in docs if d['SUMMARIZATION']]
    no = [d['path'] for d in docs if d['NOSUMMARIZATION']]
    if yes and not production:
        findings.append({'id': 'documented-summarization-claim-false', 'severity': 'MEDIUM' if tests else 'HIGH', 'test_only': bool(tests), 'documents': yes})
    if no and production:
        findings.append({'id': 'documented-no-summarization-claim-false', 'severity': 'HIGH', 'documents': no})
    return {'status': 'FAIL' if findings else 'PASS', 'findings': findings, 'claims': docs,
            'production_evidence': sorted(production), 'test_evidence': sorted(tests), 'python_files': py_count}
