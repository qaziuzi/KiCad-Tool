"""
kicadlib.py - safe reader/writer for KiCad .kicad_sym symbol libraries.

Design rule: the LLM never writes S-expressions. This module owns every byte
that lands in a library file. Every mutation is validated, backed up, and
written atomically.

No third-party dependencies. Python 3.9+.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from typing import Iterable, Iterator, List, Optional, Sequence, Union

# KiCad on Windows writes CRLF, UTF-8, no BOM. Match it byte-for-byte so that
# regenerating a library does not produce a whole-file diff.
NEWLINE = "\r\n"
INDENT = "\t"

# The file-format version stamped into a newly created library. KiCad refuses
# to open a file claiming a newer format than it understands, so this must
# match the installed KiCad, not whatever was current when this was written.
# config.load() overwrites it with the version detected from the stock
# libraries; this value is only the fallback.
DEFAULT_SYM_VERSION = "20251024"
GENERATOR_VERSION = "10.0"
# KiCad wraps the xy pairs inside a (pts ...) list at this many per line.
PTS_PER_LINE = 6

Node = Union["Atom", List["Node"]]


class Atom:
    """A leaf token. `quoted` preserves whether KiCad wrote it in quotes."""

    __slots__ = ("value", "quoted")

    def __init__(self, value: str, quoted: bool = False) -> None:
        self.value = value
        self.quoted = quoted

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Atom({self.value!r}, quoted={self.quoted})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Atom)
            and other.value == self.value
            and other.quoted == self.quoted
        )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

# Escape table. Must be symmetric with _encode_string or round-tripping
# corrupts backslashes.
_UNESCAPE = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}
_ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

_WHITESPACE = " \t\r\n"


class ParseError(ValueError):
    pass


def _tokenize(text: str) -> Iterator[tuple]:
    """Yield ('(', None) | (')', None) | ('atom', Atom)."""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == "(":
            yield ("(", None)
            i += 1
            continue
        if ch == ")":
            yield (")", None)
            i += 1
            continue
        if ch == '"':
            i += 1
            buf = []
            while True:
                if i >= n:
                    raise ParseError("unterminated string literal")
                c = text[i]
                if c == "\\":
                    if i + 1 >= n:
                        raise ParseError("dangling escape in string literal")
                    nxt = text[i + 1]
                    buf.append(_UNESCAPE.get(nxt, nxt))
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                buf.append(c)
                i += 1
            yield ("atom", Atom("".join(buf), quoted=True))
            continue
        # bare atom
        start = i
        while i < n and text[i] not in _WHITESPACE and text[i] not in "()":
            i += 1
        yield ("atom", Atom(text[start:i], quoted=False))


def parse(text: str) -> List[Node]:
    """Parse a .kicad_sym document into a nested list tree."""
    stack: List[List[Node]] = []
    root: Optional[List[Node]] = None
    for kind, payload in _tokenize(text):
        if kind == "(":
            new: List[Node] = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
        elif kind == ")":
            if not stack:
                raise ParseError("unbalanced ')'")
            done = stack.pop()
            if not stack:
                if root is not None:
                    raise ParseError("multiple top-level expressions")
                root = done
        else:
            if not stack:
                raise ParseError("atom outside of any expression")
            stack[-1].append(payload)
    if stack:
        raise ParseError("unbalanced '(' - %d unclosed" % len(stack))
    if root is None:
        raise ParseError("empty document")
    return root


def parse_file(path: str) -> List[Node]:
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read())


# --------------------------------------------------------------------------
# Serialising
# --------------------------------------------------------------------------


def _encode_string(value: str) -> str:
    out = [_ESCAPE.get(c, c) for c in value]
    return '"' + "".join(out) + '"'


def _atom_text(atom: Atom) -> str:
    if atom.quoted:
        return _encode_string(atom.value)
    return atom.value


def _head(node: Node) -> Optional[str]:
    """Name of a list node, i.e. its first bare atom."""
    if isinstance(node, list) and node and isinstance(node[0], Atom):
        return node[0].value
    return None


def _is_flat(node: List[Node]) -> bool:
    return all(isinstance(child, Atom) for child in node)


def _dump_node(node: Node, depth: int, out: List[str]) -> None:
    pad = INDENT * depth
    if isinstance(node, Atom):
        out.append(pad + _atom_text(node))
        return

    if _is_flat(node):
        out.append(pad + "(" + " ".join(_atom_text(a) for a in node) + ")")
        return

    head = _head(node)

    # KiCad inlines the xy pairs inside (pts ...), wrapping at PTS_PER_LINE
    # pairs. Mirror that exactly so polylines round-trip byte-for-byte.
    if head == "pts" and all(
        isinstance(c, Atom) or (isinstance(c, list) and _head(c) == "xy")
        for c in node[1:]
    ):
        parts = [
            _atom_text(c)
            if isinstance(c, Atom)
            else "(" + " ".join(_atom_text(a) for a in c) + ")"
            for c in node[1:]
        ]
        out.append(pad + "(pts")
        inner_pad = INDENT * (depth + 1)
        for i in range(0, len(parts), PTS_PER_LINE):
            out.append(inner_pad + " ".join(parts[i : i + PTS_PER_LINE]))
        out.append(pad + ")")
        return

    # Leading atoms sit on the opening line: (symbol "NAME" ...)
    lead: List[str] = []
    idx = 1
    lead.append(_atom_text(node[0]) if isinstance(node[0], Atom) else "")
    while idx < len(node) and isinstance(node[idx], Atom):
        lead.append(_atom_text(node[idx]))
        idx += 1

    out.append(pad + "(" + " ".join(lead))
    for child in node[idx:]:
        _dump_node(child, depth + 1, out)
    out.append(pad + ")")


def dump(root: Node) -> str:
    out: List[str] = []
    _dump_node(root, 0, out)
    return NEWLINE.join(out) + NEWLINE


# --------------------------------------------------------------------------
# Tree helpers
# --------------------------------------------------------------------------


def children(node: Node, name: str) -> List[List[Node]]:
    if not isinstance(node, list):
        return []
    return [c for c in node[1:] if isinstance(c, list) and _head(c) == name]


def child(node: Node, name: str) -> Optional[List[Node]]:
    found = children(node, name)
    return found[0] if found else None


def atom_values(node: List[Node]) -> List[str]:
    return [a.value for a in node if isinstance(a, Atom)]


def clone(node: Node) -> Node:
    if isinstance(node, Atom):
        return Atom(node.value, node.quoted)
    return [clone(c) for c in node]


def tree_equal(a: Node, b: Node) -> bool:
    """Structural equality, ignoring formatting.

    Two files that parse to the same tree are the same file as far as KiCad is
    concerned, whatever their indentation or line breaks. Comparing raw bytes
    would call a reformatted-but-identical footprint a conflict.
    """
    if isinstance(a, Atom) and isinstance(b, Atom):
        return a.value == b.value and a.quoted == b.quoted
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(tree_equal(x, y) for x, y in zip(a, b))
    return False


def files_equal(path_a: str, path_b: str) -> bool:
    """True if two S-expression files parse to the same tree."""
    try:
        return tree_equal(parse_file(path_a), parse_file(path_b))
    except (ParseError, OSError):
        return False


# --------------------------------------------------------------------------
# Symbol-library level operations
# --------------------------------------------------------------------------


def symbols(lib: List[Node]) -> List[List[Node]]:
    return children(lib, "symbol")


def symbol_name(sym: List[Node]) -> str:
    vals = atom_values(sym)
    if len(vals) < 2:
        raise ParseError("symbol node has no name")
    return vals[1]


def symbol_names(lib: List[Node]) -> List[str]:
    return [symbol_name(s) for s in symbols(lib)]


def get_symbol(lib: List[Node], name: str) -> Optional[List[Node]]:
    for sym in symbols(lib):
        if symbol_name(sym) == name:
            return sym
    return None


def new_library(version: Optional[str] = None) -> List[Node]:
    """An empty .kicad_sym, stamped for the installed KiCad."""
    return [
        Atom("kicad_symbol_lib"),
        [Atom("version"), Atom(version or DEFAULT_SYM_VERSION)],
        [Atom("generator"), Atom("kicad_symbol_editor", quoted=True)],
        [Atom("generator_version"), Atom(GENERATOR_VERSION, quoted=True)],
    ]


def load_library(path: str) -> List[Node]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return new_library()
    lib = parse_file(path)
    if _head(lib) != "kicad_symbol_lib":
        raise ParseError(f"{path}: not a kicad_symbol_lib (got {_head(lib)!r})")
    return lib


def remove_symbol(lib: List[Node], name: str) -> bool:
    for i, node in enumerate(lib):
        if (
            isinstance(node, list)
            and _head(node) == "symbol"
            and symbol_name(node) == name
        ):
            del lib[i]
            return True
    return False


def add_symbol(lib: List[Node], sym: List[Node], replace: bool = False) -> None:
    """Insert a symbol, keeping the library sorted by name like KiCad does."""
    name = symbol_name(sym)
    if get_symbol(lib, name) is not None:
        if not replace:
            raise ValueError(f"symbol {name!r} already exists (use replace=True)")
        remove_symbol(lib, name)

    existing = [n for n in lib if isinstance(n, list) and _head(n) == "symbol"]
    header = [n for n in lib if not (isinstance(n, list) and _head(n) == "symbol")]
    existing.append(sym)
    existing.sort(key=lambda s: symbol_name(s).lower())
    lib[:] = header + existing


def write_library(path: str, lib: List[Node], backup: bool = True) -> None:
    """Atomically write the library, keeping a .bak of the previous content."""
    text = dump(lib)
    # Re-parse what we are about to write. If this fails we never touch the
    # real file.
    reparsed = parse(text)
    if symbol_names(reparsed) != symbol_names(lib):
        raise AssertionError("round-trip check failed: symbol list changed")

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    if backup and os.path.exists(path):
        shutil.copy2(path, path + ".bak")

    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# Symbol-level operations
# --------------------------------------------------------------------------

_PROP_ORDER = ["at", "show_name", "do_not_autoplace", "hide", "effects"]


def _make_property(key: str, value: str, hide: bool = True) -> List[Node]:
    node: List[Node] = [
        Atom("property"),
        Atom(key, quoted=True),
        Atom(value, quoted=True),
        [Atom("at"), Atom("0"), Atom("0"), Atom("0")],
        [Atom("show_name"), Atom("no")],
        [Atom("do_not_autoplace"), Atom("no")],
    ]
    if hide:
        node.append([Atom("hide"), Atom("yes")])
    node.append(
        [
            Atom("effects"),
            [Atom("font"), [Atom("size"), Atom("1.27"), Atom("1.27")]],
        ]
    )
    return node


def properties(sym: List[Node]) -> List[List[Node]]:
    return children(sym, "property")


def get_property(sym: List[Node], key: str) -> Optional[str]:
    for prop in properties(sym):
        vals = atom_values(prop)
        if len(vals) >= 3 and vals[1] == key:
            return vals[2]
    return None


def property_keys(sym: List[Node]) -> List[str]:
    out = []
    for prop in properties(sym):
        vals = atom_values(prop)
        if len(vals) >= 2:
            out.append(vals[1])
    return out


def set_property(
    sym: List[Node], key: str, value: str, hide: bool = True
) -> None:
    """Update an existing property in place, or append a new hidden one."""
    for prop in properties(sym):
        atoms = [a for a in prop if isinstance(a, Atom)]
        if len(atoms) >= 3 and atoms[1].value == key:
            atoms[2].value = value
            atoms[2].quoted = True
            return

    new_prop = _make_property(key, value, hide=hide)
    # Insert directly after the last existing property so the file keeps
    # KiCad's ordering (properties, then graphic sub-symbols).
    last = -1
    for i, node in enumerate(sym):
        if isinstance(node, list) and _head(node) == "property":
            last = i
    if last >= 0:
        sym.insert(last + 1, new_prop)
    else:
        insert_at = len(sym)
        for i, node in enumerate(sym):
            if isinstance(node, list) and _head(node) == "symbol":
                insert_at = i
                break
        sym.insert(insert_at, new_prop)


def remove_property(sym: List[Node], key: str) -> bool:
    for i, node in enumerate(sym):
        if isinstance(node, list) and _head(node) == "property":
            vals = atom_values(node)
            if len(vals) >= 2 and vals[1] == key:
                del sym[i]
                return True
    return False


def rename_symbol(sym: List[Node], new_name: str) -> None:
    """Rename a symbol and its `NAME_unit_style` graphic sub-symbols."""
    old = symbol_name(sym)
    atoms = [a for a in sym if isinstance(a, Atom)]
    atoms[1].value = new_name
    atoms[1].quoted = True
    for sub in children(sym, "symbol"):
        sub_atoms = [a for a in sub if isinstance(a, Atom)]
        if len(sub_atoms) >= 2 and sub_atoms[1].value.startswith(old):
            suffix = sub_atoms[1].value[len(old) :]
            sub_atoms[1].value = new_name + suffix
            sub_atoms[1].quoted = True


def pins(sym: List[Node]) -> List[List[Node]]:
    """Every pin across all graphic sub-symbols."""
    out: List[List[Node]] = []
    for sub in children(sym, "symbol"):
        out.extend(children(sub, "pin"))
    out.extend(children(sym, "pin"))
    return out


def pin_numbers(sym: List[Node]) -> List[str]:
    out = []
    for pin in pins(sym):
        num = child(pin, "number")
        if num is not None:
            vals = atom_values(num)
            if len(vals) >= 2:
                out.append(vals[1])
    return out


def pin_count(sym: List[Node]) -> int:
    return len(pin_numbers(sym))


def extends_target(sym: List[Node]) -> Optional[str]:
    ext = child(sym, "extends")
    if ext is None:
        return None
    vals = atom_values(ext)
    return vals[1] if len(vals) >= 2 else None


def resolve_extends(lib: List[Node], sym: List[Node]) -> List[Node]:
    """
    Flatten a derived symbol into a standalone one.

    KiCad stock libraries use `(extends "Parent")` heavily (e.g. C_Small
    extends C). Copying such a symbol without its parent yields a part with no
    graphics and no pins, so resolve it before copying anything out.
    """
    parent_name = extends_target(sym)
    if parent_name is None:
        return sym

    parent = get_symbol(lib, parent_name)
    if parent is None:
        raise ValueError(
            f"symbol {symbol_name(sym)!r} extends {parent_name!r}, "
            "which is not in the same library"
        )
    parent = resolve_extends(lib, parent)

    merged = clone(parent)
    rename_symbol(merged, symbol_name(sym))

    # The child's own properties win over the inherited ones.
    for prop in properties(sym):
        vals = atom_values(prop)
        if len(vals) >= 3:
            hidden = child(prop, "hide") is not None
            set_property(merged, vals[1], vals[2], hide=hidden)

    # Carry across child-level display settings if it declared any.
    for key in ("pin_numbers", "pin_names", "exclude_from_sim", "in_bom", "on_board"):
        node = child(sym, key)
        if node is not None:
            for i, existing in enumerate(merged):
                if isinstance(existing, list) and _head(existing) == key:
                    merged[i] = clone(node)
                    break
            else:
                merged.insert(1, clone(node))

    remove_property(merged, "ki_locked")
    return merged


def copy_symbol(
    src_lib: List[Node], src_name: str, new_name: str
) -> List[Node]:
    """Deep-copy a symbol out of a library, flattening `extends` and renaming."""
    sym = get_symbol(src_lib, src_name)
    if sym is None:
        raise ValueError(f"symbol {src_name!r} not found in source library")
    resolved = resolve_extends(src_lib, sym)
    out = clone(resolved)
    rename_symbol(out, new_name)
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z_]*$")


def validate_symbol(sym: List[Node]) -> List[str]:
    """Return a list of human-readable problems. Empty list means clean."""
    issues: List[str] = []
    name = symbol_name(sym)

    if not name.strip():
        issues.append("symbol name is empty")
    for bad in "/\\:":
        if bad in name:
            issues.append(f"symbol name contains illegal character {bad!r}")

    numbers = pin_numbers(sym)
    if not numbers:
        issues.append("symbol has no pins")

    seen = {}
    for num in numbers:
        seen[num] = seen.get(num, 0) + 1
    dupes = sorted(n for n, c in seen.items() if c > 1 and n != "")
    if dupes:
        issues.append(
            "duplicate pin numbers: " + ", ".join(dupes)
        )

    ref = get_property(sym, "Reference")
    if ref is None:
        issues.append("missing Reference property")
    elif not _REF_RE.match(ref):
        issues.append(
            f"Reference is {ref!r}; expected letters only (e.g. 'C', 'U', 'R')"
        )

    if get_property(sym, "Value") is None:
        issues.append("missing Value property")

    fp = get_property(sym, "Footprint")
    if not fp:
        issues.append("Footprint is empty")
    elif ":" not in fp:
        issues.append(
            f"Footprint {fp!r} is not in LIBRARY:NAME form"
        )

    return issues
