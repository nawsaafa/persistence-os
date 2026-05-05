"""Phase 2.1c G10 — curated SDK discipline (Design §10 G10).

Neither persistence.claim/ nor persistence.http/ may bypass the curated SDK
via `s.escape.*` — they should use s.fact, s.txn, s.claim, etc. directly OR
import from the underlying modules without going through the escape hatch.
"""
import ast
import pathlib


def _gather_attribute_paths(tree: ast.AST) -> list[str]:
    paths = []
    class V(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute):
            chain = []
            cur = node
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                chain.append(cur.id)
                paths.append(".".join(reversed(chain)))
            self.generic_visit(node)
    V().visit(tree)
    return paths


def test_no_s_escape_in_claim_module():
    for py in pathlib.Path("src/persistence/claim").rglob("*.py"):
        tree = ast.parse(py.read_text())
        paths = _gather_attribute_paths(tree)
        assert not any(p.startswith("s.escape.") for p in paths), \
            f"{py}: uses s.escape.* (curated SDK bypass — see Design §10 G10)"


def test_no_s_escape_in_http_module():
    for py in pathlib.Path("src/persistence/http").rglob("*.py"):
        tree = ast.parse(py.read_text())
        paths = _gather_attribute_paths(tree)
        assert not any(p.startswith("s.escape.") for p in paths), \
            f"{py}: uses s.escape.* (curated SDK bypass — see Design §10 G10)"
