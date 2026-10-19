import os
import re
import sys
import json
import shutil
import difflib
import textwrap
import tempfile

USAGE = """
Block formats in 'patch.txt'   (every block must end with '### END'):

1) Full file replacement / creation:
   ### FILE: path/to/file.qml
   <full file contents>
   ### END

2) Inline Context-Anchored Edits (Works on .qml, .py, .rs, .toml, .ini, etc.):
   ### EDIT: ui/main.qml
   <<< FIND
   readonly property bool effectiveShowPreview: userShowPreview && (width >= 960 || height >= 760)
   ===
   readonly property bool effectiveShowPreview: userShowPreview && (width >= 800 || height >= 600)
   >>>
   <<< FIND
   function endRightDrag() {
       isRightDragging = false
   }
   ===
   function endRightDrag() {
       isRightDragging = false
       draggedFilePath = ""
   }
   >>>
   ### END

   * Supports multiple <<< FIND ... === ... >>> hunks inside a single ### EDIT block.
   * Indentation-insensitive matching: Even if your FIND or REPLACE block has 0 indent
     (or wrong base indent), the engine matches the exact relative structure and
     automatically re-indents your replacement to match the target file's exact level!
   * Uniqueness guarantee: Rejects the patch if FIND matches 0 times or >1 times.
   * Empty replacement (nothing between === and >>>) cleanly deletes the matched lines.

3) QML Smart Scoped Operations (.qml only - Zero-Indent Friendly):
   ### QML FIX: ui/main.qml          -> Replace function, id-block, component, or property
   ### QML ADD: ui/main.qml          -> Add inside root (or inside 'target_id' via @inside)
   ### QML DELETE: ui/main.qml       -> Delete by selector (e.g. 'function foo', 'id: bar')
   ### QML: ui/main.qml              -> Upsert (FIX if exists, else ADD)

   * Recognizes QML targets automatically at ANY nesting depth:
     - Functions:   function beginRightDrag(path, name, icon, globalPt) { ... }
     - ID Objects:  Rectangle { id: mainContainer ... }  (matched by 'id: mainContainer')
     - Components:  component ToolBtn: Rectangle { ... } (matched by 'component ToolBtn')
     - Properties:  property bool userShowSidebar: true  (or readonly/default/required)
     - Signals:     signal clicked()
   * For QML ADD, you can optionally specify where to insert using a header comment:
     // @inside: mainContainer   (inserts at the end of the component with id: mainContainer)
     // @before: previewPanel    (inserts right before the component with id: previewPanel)
     // @after: addressBar       (inserts right after the component with id: addressBar)
     (If omitted, QML ADD inserts cleanly inside the root QML object before its closing '}')

4) Top-level item operations (.rs and .py only):
   ### ITEM ADD: path/to/file.rs      -> add new item(s); ERROR if it already exists
   ### ITEM FIX: path/to/file.rs      -> replace existing item(s); ERROR if missing
   ### ITEM DELETE: path/to/file.rs   -> delete item(s); body = only the signature line(s)
   ### ITEM: path/to/file.rs          -> upsert (replace if exists, else add)

Safety & Revert:
   * Staged in memory first. ANY error -> NOTHING is written and patch.txt is kept.
   * QML & Rust & Python brace/bracket/string/comment balance is lexically verified
     after every operation. Malformed edits are rejected before touching disk.
   * CRLF/LF, BOM and final newline are preserved.
   * Project-wide snapshot of affected files is saved in '.project_backup/' before
     each update. Run 'python updateMAX.py --re' to revert the last update!
================================================================================
"""

SOURCE_CODE_EXTENSIONS = (
    '.rs', '.py', '.ld', '.conf', '.toml', '.s', '.asm', '.nsh', '.TAG', '.lock', '.ini', '.qml'
)

EXACT_SOURCE_FILES = {'CMakeLists.txt'}

IGNORED_DIRS = {
    "__pycache__", ".git", "dist", "build", ".venv", "venv",
    "node_modules", ".dart_tool", "ephemeral",
    ".gradle", ".idea", ".vscode", "Pods", ".fvm",
    "data", "downloads", "shared", "debug", "tools", "x86_64-unknown-none",
    ".project_backup"
}

DUMP_FILENAME = "full_project_dump.txt"
PATCH_FILENAME = "patch.txt"
BACKUP_DIR = ".project_backup"


# ==============================================================================
# Project dump
# ==============================================================================

def is_source_file(filename):
    if filename in {DUMP_FILENAME, PATCH_FILENAME, "index_cache.json", "cert.der", "key.der"}:
        return False
    if filename.endswith(".bak"):
        return False
    if filename in EXACT_SOURCE_FILES:
        return True
    return filename.endswith(SOURCE_CODE_EXTENSIONS)


def get_project_data(startpath='.'):
    source_files = []
    tree_lines = [f"PROJECT SOURCE STRUCTURE: {os.path.basename(os.path.abspath(startpath))}", "-" * 80]

    for root, dirs, files in os.walk(startpath):
        rel_root = os.path.relpath(root, startpath).replace("\\", "/")
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith('.')]
        valid_files = [f for f in sorted(files) if is_source_file(f) and f != os.path.basename(__file__)]

        level = 0 if rel_root == '.' else rel_root.count('/') + 1
        folder_name = os.path.basename(os.path.abspath(startpath)) if rel_root == '.' else os.path.basename(root)

        if valid_files or dirs:
            indent = ' ' * 4 * level
            tree_lines.append(f"{indent}[DIR]  {folder_name}/")
            subindent = ' ' * 4 * (level + 1)
            for f in valid_files:
                tree_lines.append(f"{subindent}[FILE] {f}")
                source_files.append(os.path.join(root, f))

    return "\n".join(tree_lines), source_files


def write_full_dump(tree_str, source_files, output_file=DUMP_FILENAME):
    with open(output_file, 'w', encoding='utf-8') as dump:
        dump.write(tree_str + "\n\n")
        dump.write("=" * 80 + "\nFULL SOURCE CODE DUMP\n" + "=" * 80 + "\n\n")

        for fpath in source_files:
            clean_fpath = fpath.replace("\\", "/")
            dump.write(f"\n{'=' * 80}\n--- FILE: {clean_fpath} ---\n{'=' * 80}\n\n")
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as src:
                    dump.write(src.read().strip() + "\n")
            except Exception as e:
                dump.write(f"[ERROR READING FILE]: {e}\n")

    print(f"\n[OK] Source code dump saved to: {output_file}")


def normalize_newlines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


# ==============================================================================
# Errors / data types
# ==============================================================================

class PatchError(Exception):
    pass


class Item:
    """A top-level item occupying lines [start, end). `header` is the line of the
    real definition; lines [start, header) are attached attributes/decorators/docs."""
    __slots__ = ("start", "header", "end", "kind", "name")

    def __init__(self, start, header, end, kind, name):
        self.start, self.header, self.end, self.kind, self.name = start, header, end, kind, name

    @property
    def key(self):
        return (self.kind, self.name)

    def label(self):
        s = self.name if self.kind in ('impl', 'use', 'import', 'extern', 'other') else f"{self.kind} {self.name}"
        s = s.strip()
        return s if len(s) <= 70 else s[:67] + "..."


class QmlNode:
    """A recognized QML construct at any nesting level:
    kind in ('function', 'id', 'component', 'property', 'signal')"""
    __slots__ = ("start", "header", "end", "kind", "name", "indent")

    def __init__(self, start, header, end, kind, name, indent):
        self.start = start
        self.header = header
        self.end = end
        self.kind = kind
        self.name = name
        self.indent = indent

    @property
    def key(self):
        return (self.kind, self.name)

    def label(self):
        return f"qml:{self.kind} '{self.name}'"


TYPE_KINDS = {'struct', 'enum', 'union', 'type', 'trait'}
VALUE_KINDS = {'const', 'static'}
SEMI_KINDS = {'const', 'static', 'type', 'use', 'extern_crate'}
GROUPED_KINDS = {'use', 'import'}


# ==============================================================================
# Lexers: replace comments / strings with spaces (same length, newlines kept)
# ==============================================================================

_RAW_STR = re.compile(r'b?r(#*)"')


def _blank(out, a, b):
    for k in range(a, b):
        if out[k] != '\n':
            out[k] = ' '


def mask_rust(t):
    n = len(t)
    out = list(t)
    i = 0
    while i < n:
        c = t[i]
        if c == '/' and t.startswith('//', i):
            j = t.find('\n', i)
            j = n if j == -1 else j
            _blank(out, i, j)
            i = j
        elif c == '/' and t.startswith('/*', i):
            depth, j = 1, i + 2
            while j < n and depth > 0:
                if t.startswith('/*', j):
                    depth += 1
                    j += 2
                elif t.startswith('*/', j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth > 0:
                return None
            _blank(out, i, j)
            i = j
        elif c in 'rb' and (i == 0 or not (t[i - 1].isalnum() or t[i - 1] == '_')) and _RAW_STR.match(t, i):
            m = _RAW_STR.match(t, i)
            term = '"' + m.group(1)
            j = t.find(term, m.end())
            if j == -1:
                return None
            _blank(out, i, j + len(term))
            i = j + len(term)
        elif c == '"':
            j = i + 1
            while j < n and t[j] != '"':
                j += 2 if t[j] == '\\' else 1
            if j >= n:
                return None
            _blank(out, i, j + 1)
            i = j + 1
        elif c == "'":
            if t.startswith('\\', i + 1):
                j = t.find("'", i + 3)
                if j != -1 and j - i <= 12:
                    _blank(out, i, j + 1)
                    i = j + 1
                else:
                    i += 1
            elif i + 2 < n and t[i + 2] == "'" and t[i + 1] != "'":
                _blank(out, i, i + 3)
                i += 3
            else:
                i += 1
        else:
            i += 1
    return ''.join(out)


def mask_python(t):
    n = len(t)
    out = list(t)
    i = 0
    while i < n:
        c = t[i]
        if c == '#':
            j = t.find('\n', i)
            j = n if j == -1 else j
            _blank(out, i, j)
            i = j
        elif c in '"\'':
            q = t[i:i + 3]
            if q in ('"""', "'''"):
                j = i + 3
                while j < n and not t.startswith(q, j):
                    j += 2 if t[j] == '\\' else 1
                if j >= n:
                    return None
                end = j + 3
            else:
                j = i + 1
                while j < n and t[j] != c and t[j] != '\n':
                    j += 2 if t[j] == '\\' else 1
                end = j + 1 if (j < n and t[j] == c) else j
            _blank(out, i, min(end, n))
            i = min(end, n)
        else:
            i += 1
    return ''.join(out)


def mask_qml(t):
    """Mask JS/QML comments (// and /* */), strings (", ', `), and regex literals."""
    n = len(t)
    out = list(t)
    i = 0
    last_non_ws = ''
    while i < n:
        c = t[i]
        if c == '/' and t.startswith('//', i):
            j = t.find('\n', i)
            j = n if j == -1 else j
            _blank(out, i, j)
            i = j
        elif c == '/' and t.startswith('/*', i):
            j = t.find('*/', i + 2)
            if j == -1:
                return None
            _blank(out, i, j + 2)
            i = j + 2
        elif c in ('"', "'", '`'):
            j = i + 1
            while j < n:
                if t[j] == '\\':
                    j += 2
                    continue
                if t[j] == c:
                    break
                if c != '`' and t[j] == '\n':
                    return None
                j += 1
            if j >= n:
                return None
            _blank(out, i, j + 1)
            last_non_ws = '"'
            i = j + 1
        elif c == '/' and last_non_ws in ('(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', ''):
            j = i + 1
            in_class = False
            valid_regex = False
            while j < n and t[j] != '\n':
                if t[j] == '\\':
                    j += 2
                    continue
                if t[j] == '[':
                    in_class = True
                elif t[j] == ']':
                    in_class = False
                elif t[j] == '/' and not in_class:
                    valid_regex = True
                    break
                j += 1
            if valid_regex and j > i + 1:
                k = j + 1
                while k < n and t[k].isalpha():
                    k += 1
                _blank(out, i, k)
                last_non_ws = '/'
                i = k
            else:
                last_non_ws = c
                i += 1
        else:
            if not c.isspace():
                last_non_ws = c
            i += 1
    return ''.join(out)


def verify_syntax_balance(ext, lines, rel):
    """Verify balanced braces/brackets/parens and valid strings/comments for .qml, .rs, .py."""
    text = '\n'.join(lines)
    if ext == '.qml':
        masked = mask_qml(text)
        if masked is None:
            raise PatchError(f"{rel}: unterminated string literal or block comment in QML")
        stack = []
        pairs = {'}': '{', ']': '[', ')': '('}
        for line_idx, mline in enumerate(masked.split('\n'), 1):
            for ch in mline:
                if ch in '{[(':
                    stack.append((ch, line_idx))
                elif ch in '}])':
                    if not stack or stack[-1][0] != pairs[ch]:
                        raise PatchError(f"{rel}:{line_idx}: unbalanced '{ch}' in QML after edit")
                    stack.pop()
        if stack:
            ch, ln = stack[-1]
            raise PatchError(f"{rel}:{ln}: unclosed '{ch}' in QML after edit")
    elif ext == '.rs':
        parse_rust(lines)
    elif ext == '.py':
        parse_python(lines)


# ==============================================================================
# QML Smart Parser (Functions, ID-Blocks, Inline Components, Properties, Signals)
# ==============================================================================

_QML_FUNC_RE = re.compile(r'^\s*function\s+([A-Za-z_]\w*)\s*\(')
_QML_COMP_RE = re.compile(r'^\s*component\s+([A-Za-z_]\w*)\s*:\s*[A-Za-z_][\w.]*')
_QML_PROP_RE = re.compile(
    r'^\s*(?:(?:default|readonly|required)\s+)*property\s+(?:[A-Za-z_]\w*(?:<[A-Za-z_]\w*>)?)\s+([A-Za-z_]\w*)\b'
)
_QML_ALIAS_RE = re.compile(
    r'^\s*(?:(?:default|readonly|required)\s+)*property\s+alias\s+([A-Za-z_]\w*)\b'
)
_QML_SIGNAL_RE = re.compile(r'^\s*signal\s+([A-Za-z_]\w*)\b')
_QML_ID_LINE_RE = re.compile(r'^\s*id\s*:\s*([A-Za-z_]\w*)\s*(?:;)?\s*$')


def _find_balanced_end(ml, start_line, open_ch='{', close_ch='}'):
    depth = 0
    started = False
    for j in range(start_line, len(ml)):
        for ch in ml[j]:
            if ch == open_ch:
                depth += 1
                started = True
            elif ch == close_ch:
                depth -= 1
                if started and depth == 0:
                    return j
                if depth < 0:
                    return None
    return None


def _attach_leading_comments(lines, ml, header_line):
    start = header_line
    while start > 0:
        prev_raw = lines[start - 1].strip()
        prev_m = ml[start - 1].strip()
        if prev_raw and not prev_m and (prev_raw.startswith('//') or prev_raw.startswith('/*') or prev_raw.startswith('*')):
            start -= 1
        else:
            break
    return start


def parse_qml_nodes(lines, attach_comments=True):
    """Returns a list of QmlNode items found across all nesting levels in a QML file."""
    text = '\n'.join(lines)
    masked = mask_qml(text)
    if masked is None:
        raise PatchError("unterminated string or comment in QML")
    ml = masked.split('\n')
    n = len(lines)
    nodes = []

    for i in range(n):
        m = ml[i]
        ms = m.strip()
        if not ms:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip(' \t'))

        # 1. Function definition
        fm = _QML_FUNC_RE.match(m)
        if fm:
            fname = fm.group(1)
            end_j = _find_balanced_end(ml, i, '{', '}')
            if end_j is None:
                raise PatchError(f"QML line {i + 1}: unbalanced braces in function '{fname}'")
            st = _attach_leading_comments(lines, ml, i) if attach_comments else i
            nodes.append(QmlNode(st, i, end_j + 1, 'function', fname, indent))
            continue

        # 2. Inline component definition: `component Name: BaseType {`
        cm = _QML_COMP_RE.match(m)
        if cm and '{' in m:
            cname = cm.group(1)
            end_j = _find_balanced_end(ml, i, '{', '}')
            if end_j is None:
                raise PatchError(f"QML line {i + 1}: unbalanced braces in component '{cname}'")
            st = _attach_leading_comments(lines, ml, i) if attach_comments else i
            nodes.append(QmlNode(st, i, end_j + 1, 'component', cname, indent))
            continue

        # 3. Signal declaration
        sm = _QML_SIGNAL_RE.match(m)
        if sm:
            sname = sm.group(1)
            end_j = i
            if '(' in m and ')' not in m:
                end_j = _find_balanced_end(ml, i, '(', ')') or i
            st = _attach_leading_comments(lines, ml, i) if attach_comments else i
            nodes.append(QmlNode(st, i, end_j + 1, 'signal', sname, indent))
            continue

        # 4. Property declaration (single-line or multi-line binding block/array)
        pm = _QML_PROP_RE.match(m) or _QML_ALIAS_RE.match(m)
        if pm:
            pname = pm.group(1)
            end_j = i
            delta_brace = m.count('{') - m.count('}')
            delta_brack = m.count('[') - m.count(']')
            delta_paren = m.count('(') - m.count(')')
            if delta_brace > 0:
                end_j = _find_balanced_end(ml, i, '{', '}')
            elif delta_brack > 0:
                end_j = _find_balanced_end(ml, i, '[', ']')
            elif delta_paren > 0:
                end_j = _find_balanced_end(ml, i, '(', ')')
            if end_j is None:
                raise PatchError(f"QML line {i + 1}: unbalanced expression in property '{pname}'")
            st = _attach_leading_comments(lines, ml, i) if attach_comments else i
            nodes.append(QmlNode(st, i, end_j + 1, 'property', pname, indent))
            continue

        # 5. Object with `id: <name>` inside its immediate top body lines
        if '{' in m:
            depth = 0
            found_id = None
            for k in range(i, min(n, i + 12)):
                for ch in ml[k]:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                if k > i and depth > 1:
                    break
                idm = _QML_ID_LINE_RE.match(ml[k])
                if idm and depth == 1:
                    found_id = idm.group(1)
                    break
                if depth <= 0:
                    break
            if found_id:
                end_j = _find_balanced_end(ml, i, '{', '}')
                if end_j is None:
                    raise PatchError(f"QML line {i + 1}: unbalanced braces for object id '{found_id}'")
                st = _attach_leading_comments(lines, ml, i) if attach_comments else i
                nodes.append(QmlNode(st, i, end_j + 1, 'id', found_id, indent))

    return nodes


def reindent_block(block_lines, target_indent_spaces):
    """Shift a dedented/arbitrary-indented list of lines so its base indent is `target_indent_spaces`."""
    if not block_lines:
        return []
    dedented = textwrap.dedent('\n'.join(block_lines)).split('\n')
    prefix = ' ' * target_indent_spaces
    return [(prefix + l if l.strip() else '') for l in dedented]


def parse_qml_directives(body_lines):
    """Extract optional // @inside: id, // @before: id, // @after: id from top of QML patch body."""
    directives = {}
    cleaned = []
    dir_re = re.compile(r'^\s*//\s*@(inside|before|after)\s*:\s*([A-Za-z_]\w*)\s*$', re.I)
    for l in body_lines:
        m = dir_re.match(l)
        if m and not cleaned:
            directives[m.group(1).lower()] = m.group(2)
        else:
            cleaned.append(l)
    return directives, cleaned


def parse_qml_delete_selectors(body_lines):
    """Parse selectors in a ### QML DELETE block."""
    sels = []
    for raw in body_lines:
        s = raw.strip().rstrip(';{').strip()
        if not s or s.startswith('//'):
            continue
        m = re.match(r'^function\s+([A-Za-z_]\w*)', s)
        if m:
            sels.append(('function', m.group(1)))
            continue
        m = re.match(r'^id\s*:\s*([A-Za-z_]\w*)$', s) or re.match(r'^#([A-Za-z_]\w*)$', s)
        if m:
            sels.append(('id', m.group(1)))
            continue
        m = re.match(r'^component\s+([A-Za-z_]\w*)', s)
        if m:
            sels.append(('component', m.group(1)))
            continue
        m = _QML_PROP_RE.match(s) or _QML_ALIAS_RE.match(s) or re.match(r'^property\s+([A-Za-z_]\w*)$', s)
        if m:
            sels.append(('property', m.group(1)))
            continue
        m = _QML_SIGNAL_RE.match(s)
        if m:
            sels.append(('signal', m.group(1)))
            continue
        raise PatchError(
            f"Unrecognized QML DELETE selector: '{s}'. "
            f"Use 'function <name>', 'id: <id>', 'component <Name>', 'property <name>', or 'signal <name>'."
        )
    return sels


def apply_qml_op(st, action, raw_body, rel, log):
    lines = st['lines']
    verify_syntax_balance('.qml', lines, rel)
    body = clean_body(raw_body, dedent=True)
    if not body:
        raise PatchError("empty QML block body")

    if action == 'DELETE':
        sels = parse_qml_delete_selectors(body)
        if not sels:
            raise PatchError("QML DELETE body has no selectors")
        for kind, name in sels:
            nodes = parse_qml_nodes(lines, attach_comments=True)
            matches = [nd for nd in nodes if nd.kind == kind and nd.name == name]
            if not matches:
                cands = [f"{nd.kind}:{nd.name}" for nd in nodes]
                close = difflib.get_close_matches(f"{kind}:{name}", cands, n=4, cutoff=0.45)
                raise PatchError(
                    f"QML {kind} '{name}' not found in {rel}." +
                    (f" Similar: {close}" if close else "")
                )
            if len(matches) > 1:
                raise PatchError(
                    f"QML {kind} '{name}' is ambiguous in {rel} (found at lines {[m.header + 1 for m in matches]}). "
                    f"Use '### EDIT: {rel}' with <<< FIND ... === ... >>> instead."
                )
            t = matches[0]
            del lines[t.start:t.end]
            collapse_after_delete(lines, t.start)
            verify_syntax_balance('.qml', lines, rel)
            log.append(f"[-] QML DELETE {t.label()}  ({rel}:{t.start + 1}-{t.end})")
        return

    directives, body = parse_qml_directives(body)
    body = clean_body(body, dedent=True)
    if not body:
        raise PatchError("empty QML body after directives")

    snippet_nodes = parse_qml_nodes(body, attach_comments=True)
    top_snippet_nodes = []
    last_end = -1
    for nd in sorted(snippet_nodes, key=lambda x: (x.start, -(x.end - x.start))):
        if nd.start >= last_end:
            top_snippet_nodes.append(nd)
            last_end = nd.end

    if not top_snippet_nodes and action in ('FIX', 'UPSERT'):
        raise PatchError(
            "No recognizable QML target in snippet (expected `function name()`, an object with `id: name`, "
            "`component Name: Base`, `property ...`, or `signal ...`). For arbitrary lines use `### EDIT:`."
        )

    if action == 'ADD' and not top_snippet_nodes:
        _insert_qml_raw_snippet(lines, body, directives, rel, log, label="qml:block")
        verify_syntax_balance('.qml', lines, rel)
        return

    for snd in top_snippet_nodes:
        file_nodes = parse_qml_nodes(lines, attach_comments=True)
        matches = [nd for nd in file_nodes if nd.kind == snd.kind and nd.name == snd.name]

        eff = action
        if eff == 'UPSERT':
            eff = 'FIX' if matches else 'ADD'

        if eff == 'FIX':
            if not matches:
                cands = [f"{nd.kind}:{nd.name}" for nd in file_nodes]
                close = difflib.get_close_matches(f"{snd.kind}:{snd.name}", cands, n=4, cutoff=0.45)
                raise PatchError(
                    f"QML {snd.kind} '{snd.name}' not found in {rel}." +
                    (f" Similar: {close}." if close else "") +
                    " (Use '### QML ADD:' or '### EDIT:' if creating new)"
                )
            if len(matches) > 1:
                raise PatchError(
                    f"QML {snd.kind} '{snd.name}' matched {len(matches)} times at lines "
                    f"{[m.header + 1 for m in matches]} in {rel}; use '### EDIT:' with surrounding context"
                )
            target = matches[0]
            has_comments = snd.header > snd.start
            rep_start = target.start if has_comments else target.header
            chunk = body[snd.start:snd.end]
            reindented = reindent_block(chunk, target.indent)
            if lines[rep_start:target.end] == reindented:
                log.append(f"[=] QML FIX {snd.label()}  (already identical)")
                continue
            lines[rep_start:target.end] = reindented
            verify_syntax_balance('.qml', lines, rel)
            log.append(f"[+] QML FIX {snd.label()}  ({rel}:{rep_start + 1}-{rep_start + len(reindented)})")
        elif eff == 'ADD':
            if matches:
                raise PatchError(
                    f"QML {snd.kind} '{snd.name}' already exists at line {matches[0].header + 1} in {rel}; use '### QML FIX:'"
                )
            chunk = body[snd.start:snd.end]
            _insert_qml_raw_snippet(lines, chunk, directives, rel, log, label=snd.label())
            verify_syntax_balance('.qml', lines, rel)


def _insert_qml_raw_snippet(lines, chunk, directives, rel, log, label="qml:item"):
    file_nodes = parse_qml_nodes(lines, attach_comments=True)
    masked = mask_qml('\n'.join(lines))
    ml = masked.split('\n')

    if 'before' in directives or 'after' in directives:
        key = 'before' if 'before' in directives else 'after'
        ref_name = directives[key]
        matches = [nd for nd in file_nodes if nd.name == ref_name]
        if not matches:
            raise PatchError(f"QML @{key} target '{ref_name}' not found in {rel}")
        ref = matches[0]
        reindented = reindent_block(chunk, ref.indent)
        pos = ref.start if key == 'before' else ref.end
        ins = (reindented + ['']) if key == 'before' else ([''] + reindented)
        lines[pos:pos] = ins
        log.append(f"[+] QML ADD {label} (@{key}:{ref_name} at {rel}:{pos + 1})")
        return

    if 'inside' in directives:
        parent_id = directives['inside']
        matches = [nd for nd in file_nodes if nd.kind == 'id' and nd.name == parent_id]
        if not matches:
            raise PatchError(f"QML @inside id '{parent_id}' not found in {rel}")
        parent = matches[0]
        close_line = parent.end - 1
        child_indent = parent.indent + 4
        reindented = reindent_block(chunk, child_indent)
        ins = [''] + reindented if lines[close_line - 1].strip() else reindented
        lines[close_line:close_line] = ins
        log.append(f"[+] QML ADD {label} (@inside:{parent_id} at {rel}:{close_line + 1})")
        return

    root_open = None
    for i, m in enumerate(ml):
        if '{' in m:
            root_open = i
            break
    if root_open is None:
        raise PatchError(f"{rel}: could not locate root QML object '{{'")
    root_end = _find_balanced_end(ml, root_open, '{', '}')
    if root_end is None:
        raise PatchError(f"{rel}: unbalanced root QML object")
    root_indent = len(lines[root_open]) - len(lines[root_open].lstrip(' \t'))
    child_indent = root_indent + 4
    reindented = reindent_block(chunk, child_indent)
    ins = [''] + reindented if root_end > 0 and lines[root_end - 1].strip() else reindented
    lines[root_end:root_end] = ins
    log.append(f"[+] QML ADD {label} ({rel}:{root_end + 1})")


# ==============================================================================
# Inline Context-Anchored Editor (### EDIT: path/to/file)
# ==============================================================================

def parse_edit_hunks(body_lines, start_line_num):
    """Parses one or more <<< FIND ... === ... >>> hunks inside an ### EDIT block."""
    hunks = []
    i = 0
    n = len(body_lines)
    while i < n:
        s = body_lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith('<<<'):
            hunk_line = start_line_num + i
            i += 1
            find_lines = []
            while i < n and not body_lines[i].strip().startswith('==='):
                find_lines.append(body_lines[i].rstrip('\r'))
                i += 1
            if i >= n:
                raise PatchError(f"line {hunk_line}: '<<< FIND' is missing '===' separator")
            i += 1  # skip ===
            rep_lines = []
            while i < n and not body_lines[i].strip().startswith('>>>'):
                rep_lines.append(body_lines[i].rstrip('\r'))
                i += 1
            if i >= n:
                raise PatchError(f"line {hunk_line}: '<<< FIND' block is missing closing '>>>'")
            i += 1  # skip >>>

            while find_lines and not find_lines[0].strip():
                find_lines.pop(0)
            while find_lines and not find_lines[-1].strip():
                find_lines.pop()
            while rep_lines and not rep_lines[0].strip():
                rep_lines.pop(0)
            while rep_lines and not rep_lines[-1].strip():
                rep_lines.pop()

            if not find_lines:
                raise PatchError(f"line {hunk_line}: empty FIND block inside '### EDIT'")
            hunks.append((hunk_line, find_lines, rep_lines))
        else:
            raise PatchError(
                f"line {start_line_num + i}: unexpected text outside '<<< FIND ... === ... >>>' in ### EDIT: "
                f"{s[:50]!r}"
            )
    if not hunks:
        raise PatchError("no '<<< FIND ... === ... >>>' hunks found in ### EDIT block")
    return hunks


def _min_indent(lines):
    indents = [len(l) - len(l.lstrip(' ')) for l in lines if l.strip()]
    return min(indents) if indents else 0


def _relative_profile(lines):
    """Returns (base_indent, [(rel_indent, stripped_text), ...]) for indentation-safe matching."""
    expanded = [l.expandtabs(4).rstrip() for l in lines]
    base = _min_indent(expanded)
    profile = []
    for l in expanded:
        s = l.strip()
        if not s:
            profile.append((0, ''))
        else:
            lead = len(l) - len(l.lstrip(' '))
            profile.append((lead - base, s))
    return base, profile


def find_hunk_in_lines(file_lines, find_lines, rel, hunk_line):
    """Finds a unique occurrence of `find_lines` inside `file_lines`."""
    m = len(find_lines)
    n = len(file_lines)
    if m > n:
        raise PatchError(f"line {hunk_line}: FIND block ({m} lines) is longer than {rel} ({n} lines)")

    # Pass 1: Exact match (with rstrip)
    norm_find = [l.rstrip() for l in find_lines]
    norm_file = [l.rstrip() for l in file_lines]
    exact_matches = [
        idx for idx in range(n - m + 1)
        if norm_file[idx:idx + m] == norm_find
    ]
    if len(exact_matches) == 1:
        idx = exact_matches[0]
        target_base = _min_indent([l.expandtabs(4) for l in file_lines[idx:idx + m]])
        return idx, idx + m, target_base
    if len(exact_matches) > 1:
        raise PatchError(
            f"line {hunk_line}: FIND block is ambiguous in {rel} (matched {len(exact_matches)} times "
            f"at lines {[x + 1 for x in exact_matches]}). Include 1-2 more surrounding context lines."
        )

    # Pass 2: Indentation-insensitive relative structure match
    _, find_prof = _relative_profile(find_lines)
    rel_matches = []
    for idx in range(n - m + 1):
        window = file_lines[idx:idx + m]
        if window[0].strip() != find_prof[0][1]:
            continue
        w_base, w_prof = _relative_profile(window)
        if w_prof == find_prof:
            rel_matches.append((idx, w_base))

    if len(rel_matches) == 1:
        idx, target_base = rel_matches[0]
        return idx, idx + m, target_base
    if len(rel_matches) > 1:
        raise PatchError(
            f"line {hunk_line}: FIND block is ambiguous in {rel} (matched {len(rel_matches)} times "
            f"at lines {[x[0] + 1 for x in rel_matches]}). Include more surrounding context lines."
        )

    first_non_empty = next((l.strip() for l in find_lines if l.strip()), "")
    near_lines = []
    if first_non_empty:
        for idx, fl in enumerate(file_lines):
            if first_non_empty in fl.strip() or difflib.SequenceMatcher(None, first_non_empty, fl.strip()).ratio() > 0.72:
                near_lines.append(f"L{idx + 1}: {fl.strip()[:65]}")
                if len(near_lines) >= 3:
                    break

    hint = f" Closest lines in {rel}: {near_lines}" if near_lines else ""
    raise PatchError(
        f"line {hunk_line}: FIND block not found in {rel} (starting with {first_non_empty[:55]!r}).{hint}"
    )


def apply_edit_block(st, ext, body_lines, op_line, rel, log):
    lines = st['lines']
    verify_syntax_balance(ext, lines, rel)
    hunks = parse_edit_hunks(body_lines, op_line)

    for h_idx, (hunk_line, find_lines, rep_lines) in enumerate(hunks, 1):
        start_i, end_i, target_base_indent = find_hunk_in_lines(lines, find_lines, rel, hunk_line)
        if not rep_lines:
            del lines[start_i:end_i]
            log.append(f"[-] EDIT   hunk #{h_idx} deleted lines {start_i + 1}-{end_i} in {rel}")
            continue

        find_base = _min_indent([l.expandtabs(4) for l in find_lines])
        rep_expanded = [l.expandtabs(4).rstrip() for l in rep_lines]
        rep_base = _min_indent(rep_expanded)

        base_to_strip = find_base if rep_base >= find_base else rep_base
        adapted_rep = []
        for l in rep_expanded:
            if not l.strip():
                adapted_rep.append('')
            else:
                cur_indent = len(l) - len(l.lstrip(' '))
                rel_offset = max(0, cur_indent - base_to_strip)
                new_indent = target_base_indent + rel_offset
                adapted_rep.append((' ' * new_indent) + l.lstrip(' '))

        if lines[start_i:end_i] == adapted_rep:
            log.append(f"[=] EDIT   hunk #{h_idx} at {rel}:{start_i + 1} (already identical)")
        else:
            lines[start_i:end_i] = adapted_rep
            log.append(
                f"[+] EDIT   hunk #{h_idx} applied ({rel}:{start_i + 1}-{start_i + len(adapted_rep)})"
            )

    verify_syntax_balance(ext, lines, rel)


# ==============================================================================
# Rust top-level item parser
# ==============================================================================

_TOK = re.compile(r'[A-Za-z_][A-Za-z0-9_]*!?|\S')
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def rust_header(m):
    toks = _TOK.findall(m.strip())
    n, k = len(toks), 0
    if k < n and toks[k] == 'pub':
        k += 1
        if k < n and toks[k] == '(':
            while k < n and toks[k] != ')':
                k += 1
            k += 1
    while k < n:
        t = toks[k]
        if t in ('default', 'async', 'unsafe', 'safe'):
            k += 1
        elif t == 'const' and k + 1 < n and toks[k + 1] in ('fn', 'unsafe', 'async', 'extern'):
            k += 1
        elif t == 'extern':
            if k + 1 < n and toks[k + 1] == 'crate':
                return 'extern_crate', (toks[k + 2] if k + 2 < n else '')
            k += 1
            if k < n and toks[k] == '{':
                return 'extern', ''
        else:
            break
    if k >= n:
        return None, None
    t = toks[k]
    nxt = toks[k + 1] if k + 1 < n else ''
    if t in ('fn', 'struct', 'enum', 'trait', 'mod', 'type', 'union', 'const', 'static'):
        if t == 'static' and nxt == 'mut':
            nxt = toks[k + 2] if k + 2 < n else ''
        if _IDENT.match(nxt):
            return t, nxt
        return None, None
    if t == 'impl':
        return 'impl', ''
    if t == 'use':
        return 'use', ''
    if t == 'macro_rules!':
        return 'macro_rules', nxt
    return 'other', ''


def rust_end(ml, i, kind):
    depth = 0
    for j in range(i, len(ml)):
        for ch in ml[j]:
            if ch in '([{':
                depth += 1
            elif ch in ')]':
                depth -= 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and kind not in SEMI_KINDS:
                    return j
            elif ch == ';' and depth == 0:
                return j
            if depth < 0:
                return None
    return None


def rust_name(kind, name, lines, ml, i, j):
    if kind == 'impl':
        txt = ' '.join(ml[i:j + 1]).split('{')[0]
        txt = re.split(r'\bwhere\b', txt)[0]
        txt = re.sub(r'^\s*unsafe\s+', '', txt)
        return re.sub(r'\s+', '', txt)
    if kind == 'use':
        txt = re.sub(r'\s+', '', ' '.join(ml[i:j + 1]))
        return re.sub(r',(?=[}\)])', '', txt)
    if kind == 'extern':
        return re.sub(r'\s+', '', lines[i].split('{')[0])
    if kind == 'other':
        return re.sub(r'\s+', '', lines[i])
    return name


def parse_rust(lines, attach_comments=False):
    n = len(lines)
    masked = mask_rust('\n'.join(lines))
    if masked is None:
        raise PatchError("unterminated string literal or block comment (cannot parse Rust safely)")
    ml = masked.split('\n')
    items, pending, i = [], None, 0
    while i < n:
        raw, m = lines[i], ml[i]
        s = raw.strip()
        if not s:
            pending = None
            i += 1
            continue
        if raw[0] in ' \t':
            i += 1
            continue
        if not m.strip():
            if s.startswith('///') or s.startswith('/**') or attach_comments:
                if pending is None:
                    pending = i
            else:
                pending = None
            i += 1
            continue
        if s.startswith('#'):
            if s.startswith('#[') or s.startswith('#!['):
                depth, j = 0, i
                while j < n:
                    depth += ml[j].count('[') - ml[j].count(']')
                    if depth <= 0:
                        break
                    j += 1
                if j >= n:
                    raise PatchError(f"line {i + 1}: unbalanced attribute")
                if s.startswith('#!['):
                    pending = None
                elif pending is None:
                    pending = i
                i = j + 1
                continue
            i += 1
            continue
        kind, name = rust_header(m)
        if kind is None:
            kind, name = 'other', ''
        j = rust_end(ml, i, kind)
        if j is None:
            raise PatchError(f"line {i + 1}: cannot find the end of item '{s[:60]}' (unbalanced braces?)")
        name = rust_name(kind, name, lines, ml, i, j)
        items.append(Item(pending if pending is not None else i, i, j + 1, kind, name))
        pending = None
        i = j + 1
    return items


# ==============================================================================
# Python top-level item parser
# ==============================================================================

PY_CONT = ('else', 'elif', 'except', 'finally')


def py_header_name(ml, lines, header, end):
    m = ml[header]
    mm = re.match(r'(?:async\s+)?def\s+(\w+)', m)
    if mm:
        return 'def', mm.group(1)
    mm = re.match(r'class\s+(\w+)', m)
    if mm:
        return 'class', mm.group(1)
    if re.match(r'(import|from)\s', m):
        t = ' '.join(' '.join(ml[header:end]).split())
        t = re.sub(r',\s*\)', ')', t)
        t = re.sub(r'\(\s+', '(', t)
        return 'import', t
    mm = re.match(r'([A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)', m)
    if mm:
        return 'var', mm.group(1)
    return 'other', ' '.join(lines[header].split())


def parse_python(lines, attach_comments=False):
    n = len(lines)
    masked = mask_python('\n'.join(lines))
    if masked is None:
        raise PatchError("unterminated triple-quoted string (cannot parse Python safely)")
    ml = masked.split('\n')
    starts, depth, cont = [], 0, False
    for i in range(n):
        m = ml[i]
        if m.strip() and m[0] not in ' \t' and depth == 0 and not cont:
            kw = re.match(r'[A-Za-z_]+', m)
            if not (kw and kw.group(0) in PY_CONT):
                starts.append(i)
        depth += sum(m.count(c) for c in '([{') - sum(m.count(c) for c in ')]}')
        depth = max(depth, 0)
        cont = m.rstrip().endswith('\\')
    items, k = [], 0
    while k < len(starts):
        first, h = starts[k], k
        while ml[starts[h]].lstrip().startswith('@') and h + 1 < len(starts):
            h += 1
        header = starts[h]
        end = starts[h + 1] if h + 1 < len(starts) else n
        while end > header + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith('#')):
            end -= 1
        start = first
        if attach_comments:
            prev_end = items[-1].end if items else 0
            while start - 1 >= prev_end and lines[start - 1].startswith('#'):
                start -= 1
        kind, name = py_header_name(ml, lines, header, end)
        items.append(Item(start, header, end, kind, name))
        k = h + 1
    return items


def parse_items(ext, lines, attach_comments=False):
    if ext == '.rs':
        return parse_rust(lines, attach_comments)
    if ext == '.py':
        return parse_python(lines, attach_comments)
    raise PatchError(
        f"ITEM operations support only .rs and .py files (got '{ext}'); "
        f"use '### QML FIX:' or '### EDIT:' for QML/other files"
    )


# ==============================================================================
# DELETE selectors (Rust / Python)
# ==============================================================================

def parse_selectors(ext, body):
    sels = []
    if ext == '.rs':
        masked = mask_rust('\n'.join(body))
        if masked is None:
            raise PatchError("unterminated string/comment in DELETE body")
        ml = masked.split('\n')
    else:
        masked = mask_python('\n'.join(body))
        if masked is None:
            raise PatchError("unterminated string in DELETE body")
        ml = masked.split('\n')
    n, i, attrs = len(body), 0, []
    while i < n:
        raw, s = body[i], body[i].strip()
        if not s:
            attrs = []
            i += 1
            continue
        if raw[0] in ' \t' or not ml[i].strip() or s[0] in '}])':
            i += 1
            continue
        if s.startswith('#[') or s.startswith('@'):
            attrs.append(s)
            i += 1
            continue
        if s.startswith('#'):
            i += 1
            continue
        end = i
        if ext == '.rs':
            kind, name = rust_header(ml[i])
            if kind in (None, 'other'):
                i += 1
                continue
            if kind in ('use', 'extern_crate'):
                while end < n - 1 and ';' not in ml[end]:
                    end += 1
            name = rust_name(kind, name, body, ml, i, end)
        else:
            depth = 0
            while end < n:
                depth += sum(ml[end].count(c) for c in '([{') - sum(ml[end].count(c) for c in ')]}')
                if depth <= 0:
                    break
                end += 1
            kind, name = py_header_name(ml, body, i, end + 1)
            if kind == 'other' and _IDENT.match(s):
                kind, name = 'var', s
            if kind == 'other':
                i += 1
                continue
        cfg = _cfg_norm(attrs) if attrs else None
        sels.append((kind, name, cfg))
        attrs = []
        i = end + 1
    return sels


def _cfg_norm(attr_lines):
    return tuple(re.sub(r'\s+', '', l) for l in attr_lines if 'cfg' in l or l.strip().startswith('@'))


def cfg_sig(lines, it):
    return _cfg_norm(lines[it.start:it.header])


def find_targets(ext, lines, items, kind, name, cfg=None):
    ex = [x for x in items if x.kind == kind and x.name == name]
    if not ex and ext == '.rs':
        for grp in (TYPE_KINDS, VALUE_KINDS):
            if kind in grp:
                ex = [x for x in items if x.kind in grp and x.name == name]
    if len(ex) > 1 and cfg is not None:
        f = [x for x in ex if cfg_sig(lines, x) == cfg]
        if f:
            ex = f
    return ex


def not_found_msg(items, kind, name, rel):
    cands = [x.label() for x in items]
    close = difflib.get_close_matches(f"{kind} {name}", cands, n=3, cutoff=0.5)
    same = [x.label() for x in items if x.name == name and x.kind != kind]
    msg = f"{kind} '{name}' not found in {rel}."
    if same:
        msg += f" Found with another kind: {same}."
    if close:
        msg += f" Similar items: {close}."
    return msg


# ==============================================================================
# Text state, body cleaning, indentation
# ==============================================================================

def load_state(path):
    with open(path, 'rb') as f:
        raw = f.read()
    bom = raw.startswith(b'\xef\xbb\xbf')
    if bom:
        raw = raw[3:]
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        raise PatchError(f"'{path}' is not valid UTF-8 ({e}); refusing to touch it")
    crlf = text.count('\r\n') * 2 > text.count('\n')
    text = normalize_newlines(text)
    final_nl = text.endswith('\n')
    lines = text.split('\n') if text else []
    if final_nl and lines:
        lines.pop()
    return {'lines': lines, 'final_nl': final_nl or not lines, 'crlf': crlf, 'bom': bom}


def serialize(st):
    s = '\n'.join(st['lines'])
    if st['lines']:
        s += '\n'
    if st['crlf']:
        s = s.replace('\n', '\r\n')
    data = s.encode('utf-8')
    return (b'\xef\xbb\xbf' + data) if st['bom'] else data


def clean_body(body, dedent=True):
    lines = [l.rstrip() for l in body]
    nz = [i for i, l in enumerate(lines) if l.strip()]
    if len(nz) >= 2 and lines[nz[0]].strip().startswith('```') and lines[nz[-1]].strip() == '```':
        lines = lines[nz[0] + 1:nz[-1]]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if dedent and lines:
        lines = textwrap.dedent('\n'.join(lines)).split('\n')
    return lines


def detect_indent(lines):
    tabs = sp = 0
    widths = []
    for l in lines:
        if l.startswith('\t'):
            tabs += 1
        elif l.startswith(' '):
            sp += 1
            widths.append(len(l) - len(l.lstrip(' ')))
    style = 'tab' if tabs > sp else 'space'
    w = 4
    if widths:
        for cand in (8, 4, 3, 2):
            if sum(1 for x in widths if x % cand == 0) >= 0.8 * len(widths):
                w = cand
                break
    return style, w


def adapt_indent(new_lines, file_lines, notes):
    if not file_lines or not any(l[:1] in (' ', '\t') for l in new_lines):
        return new_lines
    fs, fw = detect_indent(file_lines)
    ps, pw = detect_indent(new_lines)
    rescale = (fs == 'space' and ps == 'space' and fw != pw and fw in (2, 4) and pw in (2, 4))
    changed = False
    out = []
    for l in new_lines:
        if not l.strip():
            out.append('')
            continue
        lead = len(l) - len(l.lstrip(' \t'))
        ws, rest = l[:lead], l[lead:]
        new_ws = ws
        if fs == 'space':
            if '\t' in ws:
                new_ws = ws.replace('\t', ' ' * fw)
            if rescale and '\t' not in ws and len(ws) % pw == 0:
                new_ws = ' ' * (len(ws) // pw * fw)
        else:
            if ' ' in ws and '\t' not in ws:
                new_ws = '\t' * (len(ws) // fw) + ' ' * (len(ws) % fw)
        changed |= (new_ws != ws)
        out.append(new_ws + rest)
    if changed:
        notes.append(f"indentation adapted to file style ({fs}, width {fw})")
    return out


def is_comment_only(ext, l):
    s = l.lstrip()
    return s.startswith('#') if ext == '.py' else s.startswith(('//', '/*', '*'))


# ==============================================================================
# Item operations (with post-verification)
# ==============================================================================

def _sig(lines, items, skip):
    return [(x.key, tuple(lines[x.start:x.end])) for x in items if x.key not in skip]


def _reparse(ext, lines, rel):
    try:
        return parse_items(ext, lines)
    except PatchError as e:
        raise PatchError(f"result would be malformed ({e}); nothing written")


def collapse_after_delete(lines, pos):
    if pos < len(lines) and not lines[pos].strip() and (pos == 0 or not lines[pos - 1].strip()):
        del lines[pos]
    if pos == 0:
        while lines and not lines[0].strip():
            del lines[0]
    if pos >= len(lines):
        while lines and not lines[-1].strip():
            lines.pop()


def apply_upsert(st, ext, action, body, it, rel, log):
    lines = st['lines']
    items = parse_items(ext, lines)
    new_lines = body[it.start:it.end]
    has_prefix = it.header > it.start
    targets = find_targets(ext, lines, items, it.kind, it.name, cfg_sig(body, it))
    if len(targets) > 1:
        raise PatchError(f"'{it.label()}' is ambiguous ({len(targets)} matches at lines "
                         f"{[x.header + 1 for x in targets]}); include its #[cfg(..)] attribute to disambiguate")
    eff = action
    if eff == 'UPSERT':
        eff = 'FIX' if targets else 'ADD'
    if eff == 'ADD' and targets:
        raise PatchError(f"'{it.label()}' already exists at line {targets[0].header + 1}; use FIX")
    if eff == 'FIX' and not targets:
        raise PatchError(not_found_msg(items, it.kind, it.name, rel) + " (use ADD to create it)")

    if eff == 'FIX':
        t = targets[0]
        rs = t.start if has_prefix else t.header
        if lines[rs:t.end] == new_lines:
            log.append(f"[=] FIX    {it.label()}  (already identical)")
            return
        skip = {t.key, it.key}
        before = _sig(lines, items, skip)
        if has_prefix and ext == '.rs':
            old_docs = [l for l in lines[t.start:t.header] if l.lstrip().startswith('///')]
            if old_docs and not any(l.lstrip().startswith(('///', '/**')) for l in new_lines[:it.header - it.start]):
                new_lines = old_docs + new_lines
        lines[rs:t.end] = new_lines
        delta = 0
        where = f"{rs + 1}-{rs + len(new_lines)}"
    else:
        skip = {it.key}
        before = _sig(lines, items, skip)
        group = [x for x in items if x.kind == it.kind]
        if it.kind in GROUPED_KINDS and group:
            pos = group[-1].end
            lines[pos:pos] = new_lines
        elif it.kind in GROUPED_KINDS and items:
            pos = items[0].start
            lines[pos:pos] = new_lines + ['']
        else:
            sep = 2 if (ext == '.py' and it.kind in ('def', 'class')) else 1
            last = items[-1] if items else None
            if (ext == '.py' and last is not None and last.kind == 'other'
                    and last.name.replace(' ', '').startswith('if__name__')):
                p0 = last.start
                while p0 > 0 and not lines[p0 - 1].strip():
                    p0 -= 1
                lines[p0:last.start] = [''] * sep + new_lines + ['', '']
                pos = p0 + sep
            else:
                while lines and not lines[-1].strip():
                    lines.pop()
                if lines:
                    lines.extend([''] * sep)
                pos = len(lines)
                lines.extend(new_lines)
        delta = 1
        where = f"{pos + 1}-{pos + len(new_lines)}"

    after = _reparse(ext, lines, rel)
    if len(after) != len(items) + delta:
        raise PatchError(f"verification failed for '{it.label()}' (item count changed unexpectedly)")
    if not any(x.key == it.key for x in after):
        raise PatchError(f"verification failed for '{it.label()}' (item not found after patch)")
    if _sig(lines, after, skip) != before:
        raise PatchError(f"verification failed for '{it.label()}' (other items were affected)")
    log.append(f"[+] {eff:<6} {it.label()}  ({rel}:{where})")


def apply_delete(st, ext, kind, name, cfg, rel, log):
    lines = st['lines']
    items = parse_items(ext, lines)
    targets = find_targets(ext, lines, items, kind, name, cfg)
    if not targets:
        raise PatchError(not_found_msg(items, kind, name, rel))
    if len(targets) > 1:
        raise PatchError(f"'{kind} {name}' is ambiguous ({len(targets)} matches at lines "
                         f"{[x.header + 1 for x in targets]}); put its #[cfg(..)] line above the selector")
    t = targets[0]
    skip = {t.key}
    before = _sig(lines, items, skip)
    del lines[t.start:t.end]
    collapse_after_delete(lines, t.start)
    after = _reparse(ext, lines, rel)
    if len(after) != len(items) - 1 or _sig(lines, after, skip) != before:
        raise PatchError(f"verification failed deleting '{t.label()}'; nothing written")
    log.append(f"[-] DELETE {t.label()}  ({rel}:{t.start + 1}-{t.end})")


# ==============================================================================
# Patch parsing
# ==============================================================================

HDR = re.compile(
    r'^###\s*(FILE|ITEM|QML|EDIT)\b(?:\s+(ADD|DELETE|FIX|UPSERT|REPLACE))?\s*:\s*(.+?)\s*$',
    re.I
)
END = re.compile(r'^###\s*END\b', re.I)


def safe_path(rel):
    p = rel.strip().strip('`"\'').replace('\\', '/')
    while p.startswith('./'):
        p = p[2:]
    p = p.lstrip('/')
    if not p:
        raise PatchError("empty file path")
    norm = os.path.normpath(p)
    if os.path.isabs(norm) or norm.split(os.sep)[0] == '..':
        raise PatchError(f"path escapes the project directory: {rel}")
    return norm


def parse_patch(raw):
    lines = normalize_newlines(raw).split('\n')
    n, i = len(lines), 0
    ops, errors = {}, []
    while i < n:
        m = HDR.match(lines[i].strip())
        if not m:
            i += 1
            continue
        kind, action, rel = m.group(1).upper(), (m.group(2) or '').upper(), m.group(3)
        start = i
        i += 1
        body, closed = [], False
        while i < n:
            s = lines[i].strip()
            if END.match(s):
                closed = True
                i += 1
                break
            if HDR.match(s):
                break
            body.append(lines[i])
            i += 1
        if not closed:
            errors.append(f"line {start + 1}: block for '{rel}' is not closed with '### END'")
            continue
        if kind in ('FILE', 'EDIT') and action:
            errors.append(f"line {start + 1}: '### {kind}' does not take an action ({action})")
            continue
        if kind in ('ITEM', 'QML'):
            action = {'': 'UPSERT', 'REPLACE': 'FIX'}.get(action, action)
        try:
            path = safe_path(rel)
        except PatchError as e:
            errors.append(f"line {start + 1}: {e}")
            continue
        ops.setdefault(path, []).append({'kind': kind, 'action': action, 'body': body, 'line': start + 1})
    return ops, errors


# ==============================================================================
# Per-file processing
# ==============================================================================

def suggest_files(path):
    base = os.path.basename(path)
    found = []
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith('.')]
        if base in files:
            found.append(os.path.relpath(os.path.join(root, base), '.').replace('\\', '/'))
        if len(found) >= 5:
            break
    return found


def process_file(rel, ops):
    ext = os.path.splitext(rel)[1].lower()
    existed = os.path.exists(rel)
    st = load_state(rel) if existed else None
    log = []
    for op in ops:
        where = f"patch line {op['line']}"
        try:
            if op['kind'] == 'FILE':
                body = clean_body(op['body'], dedent=False)
                if not body:
                    raise PatchError("empty FILE body (refusing to wipe the file)")
                if st is None:
                    st = {'lines': [], 'final_nl': True, 'crlf': False, 'bom': False}
                st['lines'] = body
                verify_syntax_balance(ext, st['lines'], rel)
                log.append(f"[+] FILE   {'replaced' if existed else 'created'}  {rel}")
                continue

            if st is None:
                hint = suggest_files(rel)
                raise PatchError(f"target file '{rel}' does not exist" +
                                 (f" (did you mean {hint}?)" if hint else "") + "; use '### FILE:' to create files")

            if op['kind'] == 'EDIT':
                apply_edit_block(st, ext, op['body'], op['line'], rel, log)
                continue

            if op['kind'] == 'QML':
                if ext != '.qml':
                    raise PatchError(f"QML operations support only .qml files (got '{ext}')")
                apply_qml_op(st, op['action'], op['body'], rel, log)
                continue

            if ext not in ('.rs', '.py'):
                parse_items(ext, [])
            parse_items(ext, st['lines'])
            action = op['action']

            if action == 'DELETE':
                body = clean_body(op['body'], dedent=True)
                sels = parse_selectors(ext, body)
                if not sels:
                    raise PatchError("DELETE body has no recognizable signature (e.g. 'fn name', 'struct Name')")
                for kind, name, cfg in sels:
                    apply_delete(st, ext, kind, name, cfg, rel, log)
            else:
                body = clean_body(op['body'], dedent=True)
                if not body:
                    raise PatchError("empty ITEM body")
                notes = []
                body = adapt_indent(body, st['lines'], notes)
                new_items = parse_items(ext, body, attach_comments=True)
                if not new_items:
                    raise PatchError("no recognizable top-level item in the body")
                covered = set()
                for x in new_items:
                    covered.update(range(x.start, x.end))
                for idx, l in enumerate(body):
                    if idx not in covered and l.strip() and not is_comment_only(ext, l):
                        raise PatchError(f"body line {idx + 1} is outside any complete top-level item: "
                                         f"{l.strip()[:60]!r} (items must start at column 0 and be complete)")
                seen = set()
                for x in new_items:
                    if x.key in seen:
                        raise PatchError(f"'{x.label()}' appears twice in the same block")
                    seen.add(x.key)
                for x in new_items:
                    apply_upsert(st, ext, action, body, x, rel, log)
                log.extend(f"    note: {nt}" for nt in notes)
        except PatchError as e:
            raise PatchError(f"{where}: {e}")
    return st, log, existed


# ==============================================================================
# Project-wide Backup & Revert (--re) + Main
# ==============================================================================

def save_project_backup(staged, raw_patch=""):
    """Saves a clean single-level snapshot of all affected files into .project_backup/."""
    files_dir = os.path.join(BACKUP_DIR, "files")
    if os.path.exists(BACKUP_DIR):
        shutil.rmtree(BACKUP_DIR)
    os.makedirs(files_dir, exist_ok=True)

    manifest = {
        "modified": [],
        "created": []
    }

    for path, (_, existed) in staged.items():
        norm_p = path.replace("\\", "/")
        if existed and os.path.exists(path):
            dest = os.path.join(files_dir, norm_p)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)
            manifest["modified"].append(norm_p)
        else:
            manifest["created"].append(norm_p)

    if raw_patch:
        with open(os.path.join(BACKUP_DIR, PATCH_FILENAME), "w", encoding="utf-8") as pf:
            pf.write(raw_patch)

    with open(os.path.join(BACKUP_DIR, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)


def revert_last_update():
    """Reverts the project to the exact state right before the last update (--re)."""
    manifest_path = os.path.join(BACKUP_DIR, "manifest.json")
    files_dir = os.path.join(BACKUP_DIR, "files")

    if not os.path.exists(manifest_path):
        print("\n[ERR] No backup snapshot found in '.project_backup/' to revert!")
        return

    try:
        with open(manifest_path, "r", encoding="utf-8") as mf:
            manifest = json.load(mf)
    except Exception as e:
        print(f"\n[ERR] Failed to read backup manifest: {e}")
        return

    restored_count = 0
    removed_count = 0

    for rel_p in manifest.get("modified", []):
        src = os.path.join(files_dir, rel_p)
        if os.path.exists(src):
            d = os.path.dirname(rel_p)
            if d:
                os.makedirs(d, exist_ok=True)
            shutil.copy2(src, rel_p)
            print(f"  [↺] Restored: {rel_p}")
            restored_count += 1

    for rel_p in manifest.get("created", []):
        if os.path.exists(rel_p):
            try:
                os.remove(rel_p)
                print(f"  [-] Removed newly created file: {rel_p}")
                removed_count += 1
            except OSError:
                pass

    backup_patch = os.path.join(BACKUP_DIR, PATCH_FILENAME)
    if os.path.exists(backup_patch):
        shutil.copy2(backup_patch, PATCH_FILENAME)
        print(f"  [↺] Restored last '{PATCH_FILENAME}' content")

    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    print(f"\n[OK] Reverted last update ({restored_count} restored, {removed_count} removed).")

    tree_str, source_files = get_project_data('.')
    write_full_dump(tree_str, source_files)


def write_staged(staged, raw_patch=""):
    written = []
    try:
        save_project_backup(staged, raw_patch)
        for path, (st, existed) in staged.items():
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or '.', prefix='.patch_tmp_')
            try:
                with os.fdopen(fd, 'wb') as f:
                    f.write(serialize(st))
                if existed:
                    shutil.copymode(path, tmp)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            written.append((path, existed))
    except Exception as e:
        print(f"\n[ERR] Write failed ({e}); rolling back from '.project_backup/'...")
        revert_last_update()
        return False
    return True


def apply_patch(dry_run=False):
    print(USAGE)

    raw_patch = ""
    if os.path.exists(PATCH_FILENAME):
        with open(PATCH_FILENAME, 'r', encoding='utf-8-sig', errors='replace') as f:
            raw_patch = f.read()

    if not raw_patch.strip():
        print("[!] 'patch.txt' is empty. Extracting latest code dump...")
        tree_str, source_files = get_project_data('.')
        print("\n" + tree_str)
        write_full_dump(tree_str, source_files)
        return

    print("[*] Parsing patch and verifying changes...")
    ops_map, parse_errors = parse_patch(raw_patch)
    if parse_errors:
        print("\n[ERR] Patch parsing failed:")
        for err in parse_errors:
            print(f"  - {err}")
        print("\nNothing was written. 'patch.txt' was kept.")
        return
    if not ops_map:
        print("\n[ERR] No valid '### FILE:' / '### ITEM:' / '### QML:' / '### EDIT:' blocks found in patch.txt.")
        return

    staged, errors = {}, []
    for rel, ops in ops_map.items():
        try:
            st, log, existed = process_file(rel, ops)
            staged[rel] = (st, existed)
            for line in log:
                print("  " + line)
        except PatchError as e:
            errors.append((rel, str(e)))

    if errors:
        print("\n[ERR] Patch rejected:")
        for rel, msg in errors:
            print(f"  - {rel}: {msg}")
        print("\nNothing was written. 'patch.txt' was kept so you can fix it.")
        return

    if dry_run:
        print(f"\n[OK] --check: patch is valid for {len(staged)} file(s). Nothing was written.")
        return

    if not write_staged(staged, raw_patch):
        print("Nothing was changed. 'patch.txt' was kept.")
        return

    for rel in staged:
        print(f"  [+] Patched: {rel}")
    with open(PATCH_FILENAME, 'w', encoding='utf-8') as f:
        f.write("")
    print(f"\n[OK] Successfully applied patch to {len(staged)} file(s). (Use '--re' to undo)")

    tree_str, source_files = get_project_data('.')
    write_full_dump(tree_str, source_files)


if __name__ == "__main__":
    if '--re' in sys.argv or '--revert' in sys.argv:
        revert_last_update()
    else:
        apply_patch(dry_run=('--check' in sys.argv or '--dry' in sys.argv))