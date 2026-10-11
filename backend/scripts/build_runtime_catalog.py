"""Compile reviewed application statements into a sealed server-side operation catalog.

No arbitrary SQL is accepted over HTTP. Regenerate and deploy after statement changes.
"""

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build():
    operations = {}
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        if path.name == "setup.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("one", "many", "execute") or not node.args:
                continue
            value = node.args[0]
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                if (
                    path.name in ("repository.py", "supabase.py")
                    and isinstance(value, ast.Name)
                    and value.id == "sql"
                ):
                    continue
                raise ValueError(f"Dynamic statement rejected: {path}:{node.lineno}")
            sql = " ".join(value.value.split())
            if not sql.lower().startswith(("select ", "with ", "insert ", "update ", "delete ")):
                continue
            key = hashlib.sha256(sql.encode()).hexdigest()
            returns = sql.lower().startswith("select ") or " returning " in sql.lower()
            # WITH read statements have a final SELECT; mutation CTEs use RETURNING.
            if sql.lower().startswith("with ") and " returning " not in sql.lower():
                returns = True
            operations[key] = (sql, returns)
    statements = [
        "-- Generated from literal application operations. Do not edit by hand.",
        "delete from agent_private.operation_catalog;",
    ]
    for key, (sql, returns) in sorted(operations.items()):
        quoted = sql.replace("'", "''")
        statements.append(
            f"insert into agent_private.operation_catalog values ('{key}','{quoted}',{str(returns).lower()});"
        )
    output = ROOT / "supabase" / "runtime_catalog.sql"
    output.write_text("\n".join(statements), encoding="utf-8")
    print(f"Compiled {len(operations)} fixed operations")
    return output


if __name__ == "__main__":
    build()
