"""Names a function's closures share with the function itself, rebound more than once.

    python tools/closure_names.py mac/standby.py run_session

run_session is one long function: its nested functions (end_turn, whose, poll_mic...)
read its local names, and its main loop rebinds some of them. A name a closure reads
and the function binds twice is one closure that may see the other's value: the
heartbeat's `parts = []` replaced item 11's `parts` dict, and the first voice turn
after a heartbeat crashed the Room channel (2026-10-08). Prints each such name, where
it is bound, and which closures read it; exits 1 if there is one not in ALLOWED.
"""
import ast
import sys

# Bound more than once on purpose, each checked by hand: the same thing, set again.
ALLOWED = {
    "keep_audio": "a talk keeps none: set before any closure runs",
    "kind": "the resumed session's kind: set before any closure runs",
    "shipper": "None, then the shipper when there is one: before any closure runs",
    "t_offset": "0, then where a resumed session goes on: before any closure runs",
    "watch": "the Mac's mics watched, or None: before any closure runs",
}


class Bindings(ast.NodeVisitor):
    """The names bound directly in one function's own scope (not in nested ones)."""

    def __init__(self):
        self.bound = {}

    def _bind(self, name, node):
        self.bound.setdefault(name, []).append(node.lineno)

    def _targets(self, target, node):
        if isinstance(target, ast.Name):
            self._bind(target.id, node)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self._targets(t, node)
        elif isinstance(target, ast.Starred):
            self._targets(target.value, node)

    def visit_Assign(self, node):
        for t in node.targets:
            self._targets(t, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._targets(node.target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._targets(node.target, node)
        self.generic_visit(node)

    def visit_For(self, node):
        self._targets(node.target, node)
        self.generic_visit(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars is not None:
                self._targets(item.optional_vars, node)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self._bind(node.name, node)
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self._bind((a.asname or a.name).split(".")[0], node)

    visit_ImportFrom = visit_Import

    def visit_FunctionDef(self, node):           # a nested def binds its name, its body is its own
        self._bind(node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._bind(node.name, node)

    def visit_Lambda(self, node):
        pass

    def visit_ListComp(self, node):              # comprehensions have their own scope
        pass

    visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_ListComp


def reads(func):
    """{nested function name: the names it reads from outside it}, for every function
    nested in func: those it reads and doesn't bind itself (its own loop's `c` is its own)."""
    out = {}
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = Bindings()
            for stmt in node.body:
                own.visit(stmt)
            params = {a.arg for a in node.args.args + node.args.kwonlyargs}
            params |= {a.arg for a in (node.args.vararg, node.args.kwarg) if a is not None}
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            comp = {t.id for g in ast.walk(node) if isinstance(g, ast.comprehension)    # [c for c in ...]: its own
                    for t in ast.walk(g.target) if isinstance(t, ast.Name)}
            out[node.name] = used - set(own.bound) - params - comp
    return out


def main(path, name):
    tree = ast.parse(open(path).read(), path)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    b = Bindings()
    for stmt in func.body:
        b.visit(stmt)
    for a in func.args.args + func.args.kwonlyargs:
        b.bound.setdefault(a.arg, []).insert(0, func.lineno)
    nested = reads(func)
    found = []
    for var, lines in sorted(b.bound.items()):
        if len(lines) < 2:
            continue
        readers = sorted(f for f, names in nested.items() if var in names and f != var)
        if readers:
            found.append((var, lines, readers))
    bad = [f for f in found if f[0] not in ALLOWED]
    for var, lines, readers in found:
        print("%s %-14s bound at %s; read by %s" % ("ok " if var in ALLOWED else "!! ", var,
                                                    ", ".join(map(str, lines)), ", ".join(readers)))
    print("%d shared name(s) bound more than once, %d not checked" % (len(found), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
