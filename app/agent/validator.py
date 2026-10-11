import sqlglot
from sqlglot import exp

MAX_LIMIT = 1000


class ValidationError(Exception):
    pass


def validate_sql(sql, allowed_tables, max_limit=MAX_LIMIT):
    """Return (safe_sql, notes). Raise ValidationError if the query is not allowed."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValidationError("Empty query.")

    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError as e:
        raise ValidationError(f"Could not parse SQL: {str(e)[:200]}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise ValidationError("Only one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.With)):
        raise ValidationError("Only SELECT queries are allowed.")

    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
        exp.Alter, exp.Command, exp.Pragma, exp.Attach,
    )
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise ValidationError("Query contains a forbidden operation.")

    # Tables defined by WITH (CTEs) are allowed names too
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in allowed:
            raise ValidationError(f"Table '{table.name}' is not available.")

    notes = []
    top = tree
    if isinstance(top, exp.With):
        top = top.this
    limit = top.args.get("limit") if hasattr(top, "args") else None
    if limit is None:
        tree = tree.limit(max_limit)
        notes.append(f"LIMIT {max_limit} added")
    else:
        try:
            n = int(limit.expression.name)
            if n > max_limit:
                tree = tree.limit(max_limit)
                notes.append(f"LIMIT lowered to {max_limit}")
        except (ValueError, AttributeError):
            pass

    return tree.sql(dialect="sqlite"), notes