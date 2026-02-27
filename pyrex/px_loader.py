"""
Pyrex .px file loader

Handles loading, transforming, and executing .px files:

  1. Read the source
  2. Inject automatic imports (jsx, pyrex helpers)
  3. Transform JSX → Python via pyjsx.transpile()
  4. Apply PyrexASTTransformer:
       - useState(init) → useState(init, _var_name="x", _setter_name="setX")
       - onClick={lambda:…} → onClick={__pyrex_handler(lambda:…, "lambda:…")}
       - [jsx(…) for x in state_var] → __pyrex_list_comp(…)
       - jsx(…) if cond else jsx(…) → __pyrex_ternary(…)
       - async def handler(): … → handler(); __pyrex_register_handler(…)
  5. compile() and exec() in a controlled namespace
"""

from __future__ import annotations
import ast
import threading
from pathlib import Path

import pyjsx


# ── Module registry (per-execution, thread-local) ────────────────────────────

_registry_local = threading.local()


def _get_registry():
    return getattr(_registry_local, "current", None)


def _set_registry(reg):
    _registry_local.current = reg


def _clear_registry():
    _registry_local.current = None


# ── Automatic imports prepended to every .px file ────────────────────────────

_AUTO_IMPORTS = """\
from pyrex.jsx_runtime import (
    jsx,
    __pyrex_handler,
    __pyrex_list_comp,
    __pyrex_ternary,
    __pyrex_register_handler,
)
"""


# ── AST Transformer ───────────────────────────────────────────────────────────

class PyrexASTTransformer(ast.NodeTransformer):
    """
    Post-pyjsx AST transformation that enables Pyrex's Alpine reactivity.

    Runs on the Python AST produced by pyjsx.transpile().  All of these
    transforms are purely syntactic — they change what functions are called,
    not the developer's intent.
    """

    # ── useState tuple unpacking ──────────────────────────────────────────────

    def visit_Assign(self, node: ast.Assign) -> ast.AST:
        """
        Transform:
            count, setCount = useState(0)
        Into:
            count, setCount = useState(0, _var_name="count", _setter_name="setCount")

        This lets useState() know the names the developer chose, so it can
        register them in the per-component state registry.
        """
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Tuple)
            and len(node.targets[0].elts) >= 2
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "useState"
            and all(isinstance(e, ast.Name) for e in node.targets[0].elts)
        ):
            elts = node.targets[0].elts
            var_name = elts[0].id
            setter_name = elts[1].id
            node.value = ast.Call(
                func=node.value.func,
                args=node.value.args,
                keywords=[
                    ast.keyword(arg="_var_name", value=ast.Constant(var_name)),
                    ast.keyword(arg="_setter_name", value=ast.Constant(setter_name)),
                ],
            )
            ast.fix_missing_locations(node.value)
        return self.generic_visit(node)

    # ── Event handler lambda wrapping ─────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> ast.AST:
        """
        In jsx(tag, props_dict, children_list) calls:
          - Wrap event-handler lambdas: onClick={λ} → onClick={__pyrex_handler(λ, "src")}
          - Wrap list comprehensions in children: [jsx(…) for x in var] → __pyrex_list_comp(…)
          - Wrap ternaries in children: jsx(…) if cond else jsx(…) → __pyrex_ternary(…)
        """
        # First let inner nodes be transformed
        self.generic_visit(node)

        if not (isinstance(node.func, ast.Name) and node.func.id == "jsx"):
            return node

        # ── Props dict: wrap event-handler lambdas ────────────────────────────
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Dict):
            props = node.args[1]
            for i, (k, v) in enumerate(zip(props.keys, props.values)):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and k.value.startswith("on")
                    and len(k.value) > 2
                    and k.value[2].isupper()
                    and isinstance(v, ast.Lambda)
                ):
                    source_str = ast.unparse(v)
                    props.values[i] = ast.Call(
                        func=ast.Name(id="__pyrex_handler", ctx=ast.Load()),
                        args=[v, ast.Constant(source_str)],
                        keywords=[],
                    )
                    ast.fix_missing_locations(props.values[i])

        # ── Children list: wrap list comps and ternaries ──────────────────────
        if len(node.args) >= 3 and isinstance(node.args[2], ast.List):
            children = node.args[2]
            for i, elt in enumerate(children.elts):
                # [jsx(…) for item in iterable]
                if (
                    isinstance(elt, ast.ListComp)
                    and len(elt.generators) == 1
                    and isinstance(elt.generators[0].iter, ast.Name)
                ):
                    gen = elt.generators[0]
                    iterable_name = gen.iter.id
                    item_name = ast.unparse(gen.target)
                    template_lambda = ast.Lambda(
                        args=ast.arguments(
                            posonlyargs=[],
                            args=[ast.arg(arg=item_name)],
                            vararg=None,
                            kwonlyargs=[],
                            kw_defaults=[],
                            kwarg=None,
                            defaults=[],
                        ),
                        body=elt.elt,
                    )
                    children.elts[i] = ast.Call(
                        func=ast.Name(id="__pyrex_list_comp", ctx=ast.Load()),
                        args=[
                            ast.Constant(iterable_name),
                            ast.Constant(item_name),
                            template_lambda,
                            gen.iter,
                        ],
                        keywords=[],
                    )
                    ast.fix_missing_locations(children.elts[i])

                # jsx(…) if simple_cond else jsx(…)
                elif (
                    isinstance(elt, ast.IfExp)
                    and isinstance(elt.test, ast.Name)
                ):
                    cond_name = elt.test.id
                    # Wrap both branches in lambdas so __pyrex_ternary
                    # can call them lazily (avoids executing the False branch)
                    true_lam = ast.Lambda(
                        args=ast.arguments(
                            posonlyargs=[], args=[], vararg=None,
                            kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
                        ),
                        body=elt.body,
                    )
                    false_lam = ast.Lambda(
                        args=ast.arguments(
                            posonlyargs=[], args=[], vararg=None,
                            kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
                        ),
                        body=elt.orelse,
                    )
                    children.elts[i] = ast.Call(
                        func=ast.Name(id="__pyrex_ternary", ctx=ast.Load()),
                        args=[
                            ast.Constant(cond_name),
                            true_lam,
                            false_lam,
                            elt.test,
                        ],
                        keywords=[],
                    )
                    ast.fix_missing_locations(children.elts[i])

        return node

    # ── Async handler registration ────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """
        For every async def inside a function body, append a
        __pyrex_register_handler(name, fn, source) call so the transpiler
        can later emit the handler as an async method in the x-data object.
        """
        # First let children transform themselves
        node = self.generic_visit(node)  # type: ignore[assignment]

        new_body: list[ast.stmt] = []
        for stmt in node.body:
            new_body.append(stmt)
            if isinstance(stmt, ast.AsyncFunctionDef):
                source_str = ast.unparse(stmt)
                reg = ast.Expr(
                    value=ast.Call(
                        func=ast.Name(id="__pyrex_register_handler", ctx=ast.Load()),
                        args=[
                            ast.Constant(stmt.name),
                            ast.Name(id=stmt.name, ctx=ast.Load()),
                            ast.Constant(source_str),
                        ],
                        keywords=[],
                    )
                )
                ast.fix_missing_locations(reg)
                new_body.append(reg)
        node.body = new_body
        return node


# ── Main transform pipeline ───────────────────────────────────────────────────

def transform_px_source(source: str, filepath: str) -> "code":  # type: ignore[name-defined]
    """
    Full transform pipeline for a .px file source string.

    Returns a compiled code object ready for exec().
    """
    # 1. Prepend automatic imports
    full_source = _AUTO_IMPORTS + source

    # 2. JSX → Python via pyjsx
    py_source = pyjsx.transpile(full_source)

    # 3. Parse → AST
    tree = ast.parse(py_source, filename=filepath)

    # 4. Apply Pyrex transforms
    transformer = PyrexASTTransformer()
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)

    # 5. Compile
    return compile(tree, filepath, "exec")


def load_px_file(filepath: str, registry: dict) -> dict:
    """
    Load, transform, and execute a .px file.

    The registry dict is set as the current module registry for the
    duration of execution so that @page, @component, etc. decorators
    can register into it.

    Returns the execution namespace (module globals).
    """
    source = Path(filepath).read_text(encoding="utf-8")
    code = transform_px_source(source, filepath)

    ns: dict = {"__builtins__": __builtins__}
    _set_registry(registry)
    try:
        exec(code, ns)
    finally:
        _clear_registry()

    return ns
