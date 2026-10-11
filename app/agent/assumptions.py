import re

import sqlglot
from sqlglot import exp

MAX_ITEMS = 4
MAX_VALUE_CHARS = 30

_DATE_RE = re.compile(r"^(\d{4})-\d{2}")
_CMP = {exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_WRAPPERS = (exp.Lower, exp.Upper, exp.Trim)


def _clean(value):
    return " ".join(str(value).split())[:MAX_VALUE_CHARS]


def _literal(node):
    """The fixed value of a literal (or negative number) node, else None."""
    if isinstance(node, exp.Literal):
        return _clean(node.name)
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        return _clean("-" + node.this.name)
    return None


def _column(node):
    """The column name, looking through LOWER/UPPER/TRIM, else None."""
    while isinstance(node, _WRAPPERS):
        node = node.this
    return _clean(node.name) if isinstance(node, exp.Column) else None


def _sides(node):
    """(column, literal, flipped) for 'column OP literal' or 'literal OP column', else None."""
    left, right = node.this, node.expression
    col, lit = _column(left), _literal(right)
    if col is not None and lit is not None:
        return col, lit, False
    col, lit = _column(right), _literal(left)
    if col is not None and lit is not None:
        return col, lit, True
    return None


def _conjuncts(node):
    """The parts of a condition joined by AND. OR conditions come out whole and are skipped later."""
    if isinstance(node, exp.Paren):
        yield from _conjuncts(node.this)
    elif isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    else:
        yield node


def _in_list(node, negate):
    col = _column(node.this)
    if col is None or node.args.get("query") is not None or not node.expressions:
        return None
    lits = [_literal(e) for e in node.expressions]
    if any(v is None for v in lits):
        return None
    word = "is not one of" if negate else "is one of"
    return f"{col} {word} {', '.join(lits)}", lits


def _describe(node):
    """(text, values) for a filter we can explain, else None."""
    if isinstance(node, exp.Not) and isinstance(node.this, exp.In):
        return _in_list(node.this, True)
    if isinstance(node, exp.In):
        return _in_list(node, False)
    if isinstance(node, (exp.Like, exp.ILike)):
        col, lit = _column(node.this), _literal(node.expression)
        if col is None or lit is None:
            return None
        return f"{col} matches {lit}", [lit.strip("%_")]
    if isinstance(node, (exp.EQ, exp.NEQ)):
        s = _sides(node)
        if s is None:
            return None
        col, lit, _ = s
        return (f"{col} is {lit}" if isinstance(node, exp.EQ) else f"{col} is not {lit}"), [lit]
    op = _CMP.get(type(node))
    if op is not None:
        s = _sides(node)
        if s is None:
            return None
        col, lit, flipped = s
        return f"{col} {_FLIP[op] if flipped else op} {lit}", [lit]
    return None


def _mentioned(value, question):
    """True if the value (or, for a date, its year) appears in the question text."""
    v = str(value).strip().lower()
    if not v:
        return True
    pattern = r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])"
    if re.search(pattern, question):
        return True
    m = _DATE_RE.match(v)
    return bool(m and re.search(r"(?<![0-9])" + m.group(1) + r"(?![0-9])", question))


def find_assumptions(sql, question, max_items=MAX_ITEMS):
    """Filters in the SQL that the question did not mention. Never raises; returns [] on any problem."""
    try:
        if not sql:
            return []
        tree = sqlglot.parse_one(sql, read="sqlite")
        q = " ".join(str(question or "").lower().split())
        found, seen = [], set()
        for where in tree.find_all(exp.Where):
            for cond in _conjuncts(where.this):
                described = _describe(cond)
                if described is None:
                    continue
                text, values = described
                if text in seen or all(_mentioned(v, q) for v in values):
                    continue
                seen.add(text)
                found.append(text)
                if len(found) >= max_items:
                    return found
        return found
    except Exception:
        return []


def assumption_note(items):
    if not items:
        return None
    return "Filters applied that your question did not mention: " + "; ".join(items) + "."