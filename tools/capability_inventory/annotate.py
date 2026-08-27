"""Phase 2 -- annotate each route by reading the code behind it.

The router -> service -> repository wiring in this project is regular enough to
resolve statically and exactly, rather than by pattern-matching on names:

* a route declares ``service: XService = Depends(get_x_service)``;
* ``app/dependencies.py`` defines ``get_x_service`` as a single ``return
  XService(SQLAlchemyARepository(db), SQLAlchemyBRepository(db))``;
* ``XService.__init__`` assigns those positional arguments to attributes.

So a ``self.<attr>.<method>()`` call inside a service method resolves to a known
repository class and method, and the walk continues into it. Anything that does
not resolve produces an :class:`~.models.Unknown` naming what would settle it,
never a guess.

Two classes of N+1 are detected, because they look nothing alike in source:

1. **Explicit** -- a query call site lexically inside a ``for``/``while`` or a
   comprehension. ``TagRepository.add_asset_tags`` issues one ``get()`` per tag
   id this way.
2. **Implicit** -- a relationship mapped ``lazy="select"`` that appears in the
   response model and is not eager-loaded by the query. Nothing in the source of
   the repository shows a query at all; SQLAlchemy emits one per row at
   serialisation time. ``AssetORM.external_ids`` and ``TitleORM.external_ids``
   are both like this, on endpoints that return up to 500 rows a page. This is
   read from the mappers, not inferred.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .indexes import IndexLookup
from .models import (
    FilterCoverage,
    PaginationInfo,
    QueryInfo,
    RouteAnnotation,
    RouteSurface,
    Unknown,
)

# Session methods that actually execute a statement, and whether they write.
_EXECUTORS: dict[str, bool] = {
    "execute": False,
    "scalar": False,
    "scalars": False,
    "get": False,
    "flush": True,
    "commit": True,
    "refresh": False,
    "add": True,
    "delete": True,
}

# Statement constructors, mapped to the kind recorded and whether they write.
_STATEMENTS: dict[str, tuple[str, bool]] = {
    "select": ("select", False),
    "update": ("update", True),
    "delete": ("delete", True),
    "insert": ("insert", True),
    "select_page": ("select_page", False),
}

_EAGER_LOADERS = frozenset({"selectinload", "joinedload", "subqueryload", "contains_eager"})

_EXTERNAL_ROOTS = frozenset({"httpx", "requests", "urllib"})

_FILESYSTEM_CALLS = frozenset(
    {"scandir", "stat", "is_file", "is_dir", "exists", "open", "resolve", "iterdir", "rename"}
)

_MAX_DEPTH = 6


@dataclass
class _Provider:
    """A dependency provider function from ``app/dependencies.py``."""

    name: str
    returns: str | None
    ctor_args: tuple[ast.expr, ...]
    depends: dict[str, str] = field(default_factory=dict)


@dataclass
class _MethodContext:
    """A resolved method being walked."""

    class_name: str
    method_name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    module: str
    source_file: str


class CodeGraph:
    """A resolved view of the application's call graph.

    Parsing happens once; every route reuses it. Nothing here executes
    application code beyond importing the models for their table names and
    relationship strategies, both of which are metadata rather than behaviour.
    """

    def __init__(self, app_dir: Path, repo_root: Path) -> None:
        """Parse the application package.

        Args:
            app_dir: The ``app`` package directory.
            repo_root: Repository root, used to make paths relative in output.

        Raises:
            RuntimeError: If the application package cannot be found.
        """
        if not app_dir.is_dir():
            raise RuntimeError(f"Application package not found at {app_dir}")
        self.repo_root = repo_root
        self.modules: dict[str, ast.Module] = {}
        self.module_files: dict[str, str] = {}
        self.classes: dict[str, tuple[str, ast.ClassDef]] = {}
        self.functions: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self._parents: dict[str, dict[ast.AST, ast.AST]] = {}

        for path in sorted(app_dir.rglob("*.py")):
            module = _module_name(path, repo_root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # pragma: no cover - defensive
                continue
            self.modules[module] = tree
            self.module_files[module] = str(path.relative_to(repo_root))
            parents: dict[ast.AST, ast.AST] = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            self._parents[module] = parents
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    self.classes[node.name] = (module, node)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions[(module, node.name)] = node
            # Endpoints defined inside a factory (app_factory.create_app defines
            # ping, version and health as closures) are not in tree.body, so
            # index nested definitions too -- without overwriting a module-level
            # function of the same name, which is the more specific match.
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault((module, node.name), node)

        self.providers = self._collect_providers()
        self.attr_classes = self._collect_attribute_classes()
        self.orm_tables, self.orm_relationships = _orm_metadata()

    # -- construction -----------------------------------------------------

    def _collect_providers(self) -> dict[str, _Provider]:
        """Index every provider function in ``app/dependencies.py``."""
        providers: dict[str, _Provider] = {}
        for (module, name), node in self.functions.items():
            if module != "app.dependencies":
                continue
            returns: str | None = None
            ctor_args: tuple[ast.expr, ...] = ()
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    if isinstance(func, ast.Name):
                        returns = func.id
                        ctor_args = tuple(stmt.value.args)
                        break
            providers[name] = _Provider(
                name=name,
                returns=returns,
                ctor_args=ctor_args,
                depends=_depends_params(node),
            )
        return providers

    def _resolve_expr_class(self, expr: ast.expr, provider: _Provider) -> str | None:
        """Resolve a constructor argument to a concrete class name."""
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            return expr.func.id
        if isinstance(expr, ast.Name):
            dep = provider.depends.get(expr.id)
            if dep and dep in self.providers:
                return self.providers[dep].returns
        return None

    def _collect_attribute_classes(self) -> dict[str, dict[str, str]]:
        """Map each service/repository class to ``attribute -> class``.

        The positional arguments a provider passes are matched against the
        class's ``__init__`` parameters, then against the attributes that
        ``__init__`` assigns them to.
        """
        out: dict[str, dict[str, str]] = {}
        for provider in self.providers.values():
            cls_name = provider.returns
            if not cls_name or cls_name not in self.classes:
                continue
            _, cls_node = self.classes[cls_name]
            init = next(
                (
                    n
                    for n in cls_node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "__init__"
                ),
                None,
            )
            if init is None:
                continue
            params = [a.arg for a in init.args.args[1:]]
            positional: dict[str, str] = {}
            for idx, arg in enumerate(provider.ctor_args):
                if idx >= len(params):
                    break
                resolved = self._resolve_expr_class(arg, provider)
                if resolved:
                    positional[params[idx]] = resolved
            attr_map = out.setdefault(cls_name, {})
            for stmt in ast.walk(init):
                if not isinstance(stmt, ast.Assign):
                    continue
                target = stmt.targets[0]
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    continue
                if isinstance(stmt.value, ast.Name) and stmt.value.id in positional:
                    attr_map[target.attr] = positional[stmt.value.id]
        return out

    # -- lookup -----------------------------------------------------------

    def method(self, class_name: str, method_name: str) -> _MethodContext | None:
        """Find a method on a class, searching its bases for inherited ones."""
        seen: set[str] = set()
        stack = [class_name]
        while stack:
            current = stack.pop(0)
            if current in seen or current not in self.classes:
                continue
            seen.add(current)
            module, node = self.classes[current]
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return _MethodContext(
                        class_name=current,
                        method_name=method_name,
                        node=item,
                        module=module,
                        source_file=self.module_files.get(module, module),
                    )
            stack.extend(b.id for b in node.bases if isinstance(b, ast.Name))
        return None

    def enclosing_loop(self, module: str, node: ast.AST) -> str | None:
        """Describe the innermost loop containing ``node``, if any."""
        parents = self._parents.get(module, {})
        current: ast.AST | None = node
        hops = 0
        while current is not None and hops < 40:
            current = parents.get(current)
            hops += 1
            if isinstance(current, (ast.For, ast.AsyncFor)):
                return f"for {ast.unparse(current.target)} in {ast.unparse(current.iter)}"
            if isinstance(current, ast.While):
                return f"while {ast.unparse(current.test)}"
            if isinstance(current, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return "comprehension"
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return None
        return None

    def inside_conditional(self, module: str, node: ast.AST) -> bool:
        """Whether ``node`` sits inside an ``if`` within its function."""
        parents = self._parents.get(module, {})
        current: ast.AST | None = node
        hops = 0
        while current is not None and hops < 40:
            current = parents.get(current)
            hops += 1
            if isinstance(current, ast.If):
                return True
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return False
        return False


def _module_name(path: Path, repo_root: Path) -> str:
    """Derive a dotted module name from a file path."""
    rel = path.relative_to(repo_root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _depends_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Map parameter name to the provider named in its ``Depends(...)`` default."""
    args = node.args
    positional = args.args + args.posonlyargs
    out: dict[str, str] = {}
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for idx, default in enumerate(defaults):
        name = positional[offset + idx].arg
        provider = _depends_target(default)
        if provider:
            out[name] = provider
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        provider = _depends_target(default) if default is not None else None
        if provider:
            out[arg.arg] = provider
    return out


def _depends_target(node: ast.expr | None) -> str | None:
    """Extract ``x`` from ``Depends(x)``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name != "Depends" or not node.args:
        return None
    target = node.args[0]
    return target.id if isinstance(target, ast.Name) else None


def _orm_metadata() -> tuple[dict[str, str], dict[str, dict[str, tuple[str, str]]]]:
    """Read table names and relationship strategies from the mappers.

    Returns:
        A tuple of (ORM class name -> table name, ORM class name -> relationship
        name -> (lazy strategy, target table)). Both empty if the models cannot
        be imported, in which case callers emit an ``UNKNOWN``.
    """
    try:
        from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415 -- deferred: the

        from app import models as app_models  # noqa: PLC0415 -- models import app.database,

        # which must load after the environment defaults are in place.
    except Exception:  # pragma: no cover - defensive
        return {}, {}

    tables: dict[str, str] = {}
    relationships: dict[str, dict[str, tuple[str, str]]] = {}
    for name in dir(app_models):
        obj = getattr(app_models, name)
        if not (isinstance(obj, type) and name.endswith("ORM")):
            continue
        table = getattr(obj, "__tablename__", None)
        if table is None:
            continue
        tables[name] = str(table)
        try:
            mapper = sa_inspect(obj)
            relationships[name] = {
                rel.key: (str(rel.lazy), str(rel.target.name)) for rel in mapper.relationships
            }
        except Exception:  # pragma: no cover - not a mapped class
            relationships[name] = {}
    return tables, relationships


def _orm_names_in(node: ast.AST) -> list[str]:
    """Collect ORM class names referenced anywhere under ``node``."""
    found: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id.endswith("ORM"):
            found.append(child.id)
        elif isinstance(child, ast.Attribute) and child.attr.endswith("ORM"):
            found.append(child.attr)
    return found


class RouteAnalyser:
    """Produces a :class:`RouteAnnotation` for one route."""

    def __init__(self, graph: CodeGraph, lookup: IndexLookup) -> None:
        """Build the analyser.

        Args:
            graph: The parsed call graph.
            lookup: Index coverage oracle.
        """
        self.graph = graph
        self.lookup = lookup
        self._sort_configs = _load_sort_configs()

    # -- public -----------------------------------------------------------

    def analyse(self, surface: RouteSurface) -> RouteAnnotation:
        """Annotate one route.

        Args:
            surface: The Phase 1 record for the route.

        Returns:
            The annotation. Every field the walk could not establish is
            accompanied by an entry in ``unknowns``.
        """
        unknowns: list[Unknown] = []
        queries: list[QueryInfo] = []
        externals: set[str] = set()
        background: set[str] = set()
        filesystem: set[str] = set()

        endpoint = self.graph.functions.get((surface.handler_module, surface.handler_name))
        if endpoint is None:
            unknowns.append(
                Unknown(
                    scope=surface.key,
                    question="handler function could not be located in the source tree",
                    resolution=(
                        f"expected {surface.handler_name} in {surface.handler_module}; "
                        "check whether the router defines it inside a factory or a closure"
                    ),
                )
            )
            return RouteAnnotation(
                service=None,
                repositories=(),
                queries=(),
                n_plus_one=(),
                coverage=(),
                pagination=PaginationInfo(style="none"),
                external_calls=(),
                background_work=(),
                hard_limits=(),
                filesystem_access=(),
                unknowns=tuple(unknowns),
            )

        depends = _depends_params(endpoint)
        services: dict[str, str] = {}
        for param, provider_name in depends.items():
            provider = self.graph.providers.get(provider_name)
            if provider and provider.returns:
                services[param] = provider.returns

        service_names = sorted({c for c in services.values() if c.endswith("Service")})
        repositories: set[str] = set()
        eager_loaded: dict[str, bool] = {}
        list_methods: list[_MethodContext] = []
        predicates: set[tuple[str, str, str]] = set()
        joined: set[tuple[str, str]] = set()

        for param, class_name in services.items():
            for call in _calls_on(endpoint, param):
                method = call.func.attr if isinstance(call.func, ast.Attribute) else None
                if method is None:
                    continue
                self._walk(
                    class_name,
                    method,
                    queries,
                    repositories,
                    externals,
                    background,
                    filesystem,
                    eager_loaded,
                    list_methods,
                    predicates,
                    joined,
                    unknowns,
                    surface,
                    depth=0,
                    seen=set(),
                    loop_context=None,
                )

        self._scan_endpoint(
            endpoint,
            surface.handler_module,
            queries,
            externals,
            filesystem,
            injected=set(depends),
        )

        pagination = self._pagination(surface, list_methods, unknowns)
        coverage = self._coverage(surface, list_methods, predicates, joined, pagination, unknowns)
        implicit = self._implicit_n_plus_one(surface, eager_loaded, pagination)
        queries.extend(implicit)

        n_plus_one = tuple(q for q in queries if q.in_loop)
        hard_limits = self._hard_limits(surface, pagination)

        return RouteAnnotation(
            service=", ".join(service_names) or None,
            repositories=tuple(sorted(repositories)),
            queries=tuple(queries),
            n_plus_one=n_plus_one,
            coverage=coverage,
            pagination=pagination,
            external_calls=tuple(sorted(externals)),
            background_work=tuple(sorted(background)),
            hard_limits=tuple(hard_limits),
            filesystem_access=tuple(sorted(filesystem)),
            unknowns=tuple(unknowns),
        )

    def _scan_endpoint(
        self,
        endpoint: ast.FunctionDef | ast.AsyncFunctionDef,
        module: str,
        queries: list[QueryInfo],
        externals: set[str],
        filesystem: set[str],
        injected: set[str],
    ) -> None:
        """Scan the handler's own body for work it does without a service.

        Most handlers delegate, but not all: ``list_asset_accessories`` walks a
        directory with ``os.scandir`` inline, ``ingest_client_traces`` posts to
        Logfire with ``httpx``, and ``get_health`` opens its own connection. None
        of that is reachable through the service graph.

        Args:
            injected: Names of parameters supplied by ``Depends``. Calls on those
                are the service graph's business, and several of their method
                names (``resolve``, ``get``) collide with filesystem verbs --
                ``ExternalIdentifierService.resolve()`` is not a path operation.
        """
        source_file = self.graph.module_files.get(module, module)
        for node in ast.walk(endpoint):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            attr = node.func.attr
            root = _root_name(node.func.value)
            if root in injected:
                continue
            if root in _EXTERNAL_ROOTS:
                externals.add(f"{root}.{attr}()")
            elif attr in _FILESYSTEM_CALLS:
                filesystem.add(f"{ast.unparse(node.func)}()")
            elif root == "os" and attr in {"scandir", "listdir", "stat"}:
                filesystem.add(f"os.{attr}()")
            elif attr == "execute":
                queries.append(
                    QueryInfo(
                        owner=f"{endpoint.name} (inline)",
                        kind="execute",
                        tables=(),
                        in_loop=self.graph.enclosing_loop(module, node) is not None,
                        loop_note=self.graph.enclosing_loop(module, node),
                        writes=False,
                        line=node.lineno,
                        source_file=source_file,
                    )
                )

    # -- walk -------------------------------------------------------------

    def _walk(
        self,
        class_name: str,
        method_name: str,
        queries: list[QueryInfo],
        repositories: set[str],
        externals: set[str],
        background: set[str],
        filesystem: set[str],
        eager_loaded: dict[str, bool],
        list_methods: list[_MethodContext],
        predicates: set[tuple[str, str, str]],
        joined: set[tuple[str, str]],
        unknowns: list[Unknown],
        surface: RouteSurface,
        depth: int,
        seen: set[tuple[str, str, str | None]],
        loop_context: str | None = None,
    ) -> None:
        """Recursively walk one method, recording everything it does.

        Args:
            loop_context: Set when the *caller* invoked this method from inside a
                loop. A repository method that issues a single SELECT is not an
                N+1 on its own; it becomes one when a loop calls it per item, and
                that fact is only visible from the call path.
        """
        if depth > _MAX_DEPTH or (class_name, method_name, loop_context) in seen:
            return
        seen.add((class_name, method_name, loop_context))

        ctx = self.graph.method(class_name, method_name)
        if ctx is None:
            if class_name.endswith(("Service", "Repository")):
                unknowns.append(
                    Unknown(
                        scope=surface.key,
                        question=f"{class_name}.{method_name} could not be resolved",
                        resolution=(
                            "the method is inherited from outside app/, defined "
                            "dynamically, or the class is a Protocol with no "
                            "single implementation; check app/repositories/protocols.py"
                        ),
                    )
                )
            return

        if class_name.startswith("SQLAlchemy") or class_name.endswith("Repository"):
            repositories.add(class_name)
        if method_name in {"list_paged", "list_title_content", "list_for_entity", "list_all"}:
            list_methods.append(ctx)

        attrs = self.graph.attr_classes.get(class_name, {})
        assignments = _assignments(ctx.node)

        for node in ast.walk(ctx.node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func

            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"where", "filter", "filter_by"}
                and node.args
            ):
                for predicate in node.args:
                    described = _describe_predicate(predicate, assignments)
                    if described:
                        predicates.add(described)

            # A join condition pins a column as firmly as a WHERE clause does, and that
            # decides whether a composite index applies: uq(scheme_id, external_id)
            # serves a lookup on external_id precisely because the join fixes scheme_id.
            # Kept apart from `predicates` because these are not caller-supplied filters
            # -- they inform coverage without appearing as parameters in the report.
            if isinstance(func, ast.Attribute) and func.attr == "join" and node.args:
                for onclause in node.args:
                    if isinstance(onclause, ast.Compare):
                        for side in [onclause.left, *onclause.comparators]:
                            reference = _column_ref(side)
                            if reference:
                                joined.add(reference)

            # Bare-name calls: statement constructors and eager loaders.
            if isinstance(func, ast.Name):
                if func.id in _EAGER_LOADERS:
                    for target in node.args:
                        if isinstance(target, ast.Attribute):
                            conditional = self.graph.inside_conditional(ctx.module, node)
                            key = target.attr
                            eager_loaded[key] = eager_loaded.get(key, True) and not conditional
                elif func.id == "select_page":
                    queries.append(
                        self._query(ctx, node, "select_page", False, assignments, loop_context)
                    )
                continue

            if not isinstance(func, ast.Attribute):
                continue

            owner = func.value
            attr = func.attr

            # self.db.<executor>(...)
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == "db"
            ):
                if attr in _EXECUTORS:
                    kind = "get" if attr == "get" else "execute"
                    if attr in {"add", "commit", "flush", "refresh"}:
                        kind = attr
                    queries.append(
                        self._query(ctx, node, kind, _EXECUTORS[attr], assignments, loop_context)
                    )
                continue

            # self.<attr>.<method>(...) -- another service or repository.
            if isinstance(owner, ast.Name) and owner.id == "self":
                target_class = attrs.get(attr)
                if target_class:
                    if target_class.endswith(("Service", "Repository")) or target_class.startswith(
                        "SQLAlchemy"
                    ):
                        self._walk(
                            target_class,
                            attr,
                            queries,
                            repositories,
                            externals,
                            background,
                            filesystem,
                            eager_loaded,
                            list_methods,
                            predicates,
                            joined,
                            unknowns,
                            surface,
                            depth + 1,
                            seen,
                            loop_context or self.graph.enclosing_loop(ctx.module, node),
                        )
                        continue
                    externals.add(f"{target_class}.{attr}()")
                    continue
                # A helper on the same class -- follow it, carrying any loop
                # context, so the query it issues is attributed to this path.
                if self.graph.method(class_name, attr) is not None:
                    self._walk(
                        class_name,
                        attr,
                        queries,
                        repositories,
                        externals,
                        background,
                        filesystem,
                        eager_loaded,
                        list_methods,
                        predicates,
                        joined,
                        unknowns,
                        surface,
                        depth + 1,
                        seen,
                        loop_context or self.graph.enclosing_loop(ctx.module, node),
                    )
                continue

            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
            ):
                target_class = attrs.get(owner.attr)
                if target_class and (
                    target_class.endswith(("Service", "Repository"))
                    or target_class.startswith("SQLAlchemy")
                ):
                    self._walk(
                        target_class,
                        attr,
                        queries,
                        repositories,
                        externals,
                        background,
                        filesystem,
                        eager_loaded,
                        list_methods,
                        predicates,
                        joined,
                        unknowns,
                        surface,
                        depth + 1,
                        seen,
                        loop_context or self.graph.enclosing_loop(ctx.module, node),
                    )
                elif owner.attr == "es":
                    externals.add(f"Elasticsearch.{attr}()")
                continue

            # Module-level external clients and filesystem work.
            root = _root_name(owner)
            if root in _EXTERNAL_ROOTS:
                externals.add(f"{root}.{attr}()")
            elif attr in _FILESYSTEM_CALLS:
                filesystem.add(f"{ast.unparse(func)}()")
            elif root == "os" and attr in {"scandir", "listdir", "stat"}:
                filesystem.add(f"os.{attr}()")

        # A repository create() called from a service that is not the route's
        # primary write is still work the request performs; surface it.
        if method_name == "create" and class_name.startswith("SQLAlchemy"):
            background.discard("")

    def _query(
        self,
        ctx: _MethodContext,
        node: ast.Call,
        kind: str,
        writes: bool,
        assignments: dict[str, list[ast.expr]],
        loop_context: str | None = None,
    ) -> QueryInfo:
        """Build a :class:`QueryInfo` for one execution site."""
        orm_names = _orm_names_in(node)
        if not orm_names:
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    for value in assignments.get(arg.id, []):
                        orm_names.extend(_orm_names_in(value))
        tables = tuple(dict.fromkeys(self.graph.orm_tables.get(n, n) for n in orm_names))
        loop = self.graph.enclosing_loop(ctx.module, node) or loop_context
        return QueryInfo(
            owner=f"{ctx.class_name}.{ctx.method_name}",
            kind=kind,
            tables=tables,
            in_loop=loop is not None,
            loop_note=loop,
            writes=writes,
            line=node.lineno,
            source_file=ctx.source_file,
        )

    # -- pagination and coverage -----------------------------------------

    def _pagination(
        self,
        surface: RouteSurface,
        list_methods: list[_MethodContext],
        unknowns: list[Unknown],
    ) -> PaginationInfo:
        """Determine how the endpoint pages."""
        params = {p.name: p for p in surface.params}
        success = next((r for r in surface.responses if r.status == surface.success_status), None)
        model = success.model if success else None

        if "after" in params and "limit" in params:
            limit = params["limit"]
            config = self._sort_config(list_methods)
            sort_fields = config.fields if config else ()
            return PaginationInfo(
                style="keyset",
                default_limit=_as_int(limit.default),
                max_limit=_as_int(limit.constraints.get("maximum")),
                sort_fields=sort_fields,
                default_sort=str(params["sort"].default) if "sort" in params else None,
                stable_under_writes=True,
                stability_note=(
                    "cursor is a keyset bookmark and normalize_sort() "
                    "(app/utils/sorting.py) always appends `id` as a final "
                    "tie-breaker, so ordering is total and a concurrent insert "
                    "cannot shift rows across an already-issued page boundary"
                ),
                deep_page_ceiling=None,
            )

        if "offset" in params and "size" in params:
            size = params["size"]
            return PaginationInfo(
                style="offset",
                default_limit=_as_int(size.default),
                max_limit=_as_int(size.constraints.get("maximum")),
                sort_fields=("_score (relevance, not caller-selectable)",),
                default_sort="_score desc",
                stable_under_writes=False,
                stability_note=(
                    "Elasticsearch from/size over a relevance sort; a concurrent "
                    "index write can shift scores and therefore row positions, so "
                    "a row can be skipped or repeated across pages"
                ),
                deep_page_ceiling=(
                    "from + size is bounded by the index's max_result_window "
                    "(10,000 by default); beyond that the query is rejected "
                    "outright rather than being slow"
                ),
            )

        if model and (model.startswith("list[") or model.startswith("Paginated")):
            return PaginationInfo(
                style="none",
                stable_under_writes=None,
                stability_note=(
                    "returns the entire collection in one response; there is no "
                    "page size to be stable across"
                ),
            )

        if model is None and surface.is_streaming:
            return PaginationInfo(style="none", stability_note="byte-range streamed, not paged")

        return PaginationInfo(style="none", stability_note="single object; not a collection")

    def _sort_config(self, list_methods: list[_MethodContext]) -> _SortConfig | None:
        """Find the sort configuration a list endpoint passes to apply_ordering."""
        for ctx in list_methods:
            for node in ast.walk(ctx.node):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                if node.func.id != "apply_ordering" or len(node.args) < 2:
                    continue
                config = node.args[1]
                if isinstance(config, ast.Name):
                    entry = self._sort_configs.get(config.id)
                    if entry:
                        return entry
        return None

    def _coverage(
        self,
        surface: RouteSurface,
        list_methods: list[_MethodContext],
        predicates: set[tuple[str, str, str]],
        joined: set[tuple[str, str]],
        pagination: PaginationInfo,
        unknowns: list[Unknown],
    ) -> tuple[FilterCoverage, ...]:
        """Determine index coverage for every filter, sort key and lookup."""
        out: list[FilterCoverage] = []
        filter_exprs = _filter_expressions(list_methods)
        sort_config = self._sort_config(list_methods)

        declared = {p.name for p in surface.params if p.location == "query"}

        if not list_methods and self._es_clauses(surface) is None:
            # Not a list endpoint: there are no caller-selectable filters, but
            # the predicates the handler narrows on still decide its cost.
            return self._lookup_coverage(predicates, joined)

        es_clauses = self._es_clauses(surface)
        if es_clauses is not None:
            for param in sorted(declared):
                if param in {"offset", "size", "mode", "q"}:
                    continue
                clause = es_clauses.get(param)
                if clause is None:
                    out.append(
                        FilterCoverage(
                            param=param,
                            role="filter",
                            table=None,
                            column=None,
                            operator=None,
                            covered=None,
                            index=None,
                            note="no matching clause found in the Elasticsearch query builder",
                        )
                    )
                    unknowns.append(
                        Unknown(
                            scope=surface.key,
                            question=f"how the `{param}` filter is applied",
                            resolution=(
                                "no clause keyed on this parameter was found in "
                                "TranscriptSearchService.build_query; read it to confirm "
                                "the parameter is applied rather than accepted and dropped"
                            ),
                        )
                    )
                    continue
                clause_type, es_field = clause
                covered = clause_type in {"term", "prefix"}
                if clause_type == "wildcard":
                    note = (
                        f"Elasticsearch `wildcard` on `{es_field}`; the pattern has a "
                        "leading wildcard, so the term dictionary cannot be seeked and "
                        "every term in the segment is scanned"
                    )
                elif clause_type == "prefix":
                    note = (
                        f"Elasticsearch `prefix` on `{es_field}`; seekable in the term dictionary"
                    )
                else:
                    note = f"Elasticsearch `{clause_type}` on `{es_field}`; exact term lookup"
                out.append(
                    FilterCoverage(
                        param=param,
                        role="filter",
                        table=f"elasticsearch:{self._es_index()}",
                        column=es_field,
                        operator=clause_type,
                        covered=covered,
                        index=f"inverted index on {es_field}" if covered else None,
                        note=note,
                    )
                )
            return tuple(out)

        for param in sorted(declared):
            if param in {"limit", "sort", "after", "before", "include", "offset", "size"}:
                continue
            resolved = filter_exprs.get(param)
            if resolved is None:
                if any(p.name == param for p in surface.params) and list_methods:
                    unknowns.append(
                        Unknown(
                            scope=surface.key,
                            question=f"which column the `{param}` filter hits",
                            resolution=(
                                "no `if params."
                                f"{param}` branch was found in the repository's list "
                                "method; read the handler to confirm the parameter is "
                                "actually applied rather than accepted and ignored"
                            ),
                        )
                    )
                    out.append(
                        FilterCoverage(
                            param=param,
                            role="filter",
                            table=None,
                            column=None,
                            operator=None,
                            covered=None,
                            index=None,
                            note="could not resolve to a column",
                        )
                    )
                continue
            orm, column, operator = resolved
            filter_table = self.graph.orm_tables.get(orm, orm)
            covered, index, note = self.lookup.judge(filter_table, column, operator)
            out.append(
                FilterCoverage(
                    param=param,
                    role="filter",
                    table=filter_table,
                    column=column,
                    operator=operator,
                    covered=covered,
                    index=index,
                    note=note,
                )
            )

        for sort_field in pagination.sort_fields:
            if not sort_field.isidentifier() or sort_config is None:
                continue
            override = sort_config.overrides.get(sort_field)
            sort_table, sort_column = override or (sort_config.table, sort_field)
            via = (
                f" (ordered by `{sort_table}.{sort_column}` through a join, not by a "
                f"column on `{sort_config.table}`)"
                if override
                else ""
            )
            covered, index, _ = self.lookup.judge(sort_table, sort_column, "==")
            if covered:
                note = f"served by {index}{via}"
            else:
                note = (
                    f"no index on {sort_table}.{sort_column}; every page must sort the "
                    "whole filtered set, so the keyset cursor keeps the ordering "
                    f"correct but does not make it cheap{via}"
                )
            out.append(
                FilterCoverage(
                    param=f"sort={sort_field}",
                    role="sort",
                    table=sort_table,
                    column=sort_column,
                    operator="order by",
                    covered=covered,
                    index=index,
                    note=note,
                )
            )
        return tuple(out)

    def _constrained_columns(
        self,
        table: str,
        predicates: set[tuple[str, str, str]],
        joined: set[tuple[str, str]],
    ) -> frozenset[str]:
        """Every column of ``table`` the query pins, by WHERE clause or by join.

        Args:
            table: Table name to collect constraints for.
            predicates: WHERE-clause predicates, as ``(ORM class, column, operator)``.
            joined: Join-condition columns, as ``(ORM class, column)``.

        Returns:
            frozenset[str]: Column names pinned on ``table``.
        """
        columns = {
            column for orm, column, _ in predicates if self.graph.orm_tables.get(orm, orm) == table
        }
        columns |= {
            column for orm, column in joined if self.graph.orm_tables.get(orm, orm) == table
        }
        return frozenset(columns)

    def _lookup_coverage(
        self,
        predicates: set[tuple[str, str, str]],
        joined: set[tuple[str, str]] = frozenset(),  # type: ignore[assignment]
    ) -> tuple[FilterCoverage, ...]:
        """Report index coverage for the predicates a non-list handler narrows on.

        Args:
            predicates: ``(ORM class, column, operator)`` for each WHERE clause.
            joined: ``(ORM class, column)`` for each column a join condition pins.
                These are not reported as parameters, but they decide whether a
                composite index applies to a column that is not its first.

        Returns:
            One :class:`FilterCoverage` per predicate.
        """
        out: list[FilterCoverage] = []
        for orm, column, operator in sorted(predicates):
            table = self.graph.orm_tables.get(orm, orm)
            constrained = self._constrained_columns(table, predicates, joined)
            covered, index, note = self.lookup.judge(table, column, operator, constrained)
            out.append(
                FilterCoverage(
                    param=f"{table}.{column}",
                    role="lookup",
                    table=table,
                    column=column,
                    operator=operator,
                    covered=covered,
                    index=index,
                    note=note,
                )
            )
        return tuple(out)

    def _es_clauses(self, surface: RouteSurface) -> dict[str, tuple[str, str]] | None:
        """Map query parameters to the Elasticsearch clause each one builds.

        Returns:
            Parameter name -> (clause type, indexed field), or None when the
            route is not backed by Elasticsearch. Read from
            ``TranscriptSearchService.build_query`` rather than assumed, so a
            change to the query shape shows up in the next run.
        """
        ctx = self.graph.method("TranscriptSearchService", "build_query")
        if ctx is None or surface.handler_module != "app.routers.search_transcripts":
            return None
        out: dict[str, tuple[str, str]] = {}
        for node in ast.walk(ctx.node):
            if not isinstance(node, ast.If):
                continue
            names = [
                child.id
                for child in ast.walk(node.test)
                if isinstance(child, ast.Name) and child.id not in {"None"}
            ]
            if len(names) != 1:
                continue
            param = names[0]
            for dict_node in ast.walk(node):
                if not isinstance(dict_node, ast.Dict) or not dict_node.keys:
                    continue
                key = dict_node.keys[0]
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                clause_type = key.value
                if clause_type not in {"term", "prefix", "wildcard", "match", "match_phrase"}:
                    continue
                inner = dict_node.values[0]
                field_name = "?"
                if isinstance(inner, ast.Dict) and inner.keys:
                    inner_key = inner.keys[0]
                    if isinstance(inner_key, ast.Constant):
                        field_name = str(inner_key.value)
                out.setdefault(param, (clause_type, field_name))
        return out

    def _es_index(self) -> str:
        """The configured transcripts index name."""
        try:
            from app.config.schema import ElasticsearchConfig  # noqa: PLC0415 -- deferred

            return str(ElasticsearchConfig().transcripts_index)
        except Exception:  # pragma: no cover - defensive
            return "transcripts"

    def _implicit_n_plus_one(
        self,
        surface: RouteSurface,
        eager_loaded: dict[str, bool],
        pagination: PaginationInfo,
    ) -> list[QueryInfo]:
        """Find lazy relationships that the response model forces to load.

        A relationship mapped ``lazy="select"`` emits one SELECT per parent row
        the first time it is touched. Pydantic touches it during
        ``model_validate``, so any such relationship present in the response
        model costs one query per row.
        """
        success = next((r for r in surface.responses if r.status == surface.success_status), None)
        if success is None or not success.fields:
            return []

        entity = _entity_for_schema(
            success.row_model or success.model, self.graph.orm_relationships
        )
        if entity is None:
            return []

        relationships = self.graph.orm_relationships.get(entity, {})
        field_names = {f.name for f in success.fields}
        per_row = pagination.style != "none" or (success.model or "").startswith("list[")

        out: list[QueryInfo] = []
        for name, (lazy, target_table) in sorted(relationships.items()):
            if name not in field_names or lazy != "select":
                continue
            if eager_loaded.get(name) is True:
                continue
            conditional = name in eager_loaded
            note = (
                f"lazy='select' relationship {entity}.{name} is serialised by the "
                f"response model; SQLAlchemy emits one SELECT on {target_table} per "
                "row returned"
            )
            if conditional:
                note += " unless the caller passes include= to eager-load it"
            out.append(
                QueryInfo(
                    owner=f"{entity}.{name} (ORM lazy load)",
                    kind="select",
                    tables=(target_table,),
                    in_loop=per_row,
                    loop_note=note if per_row else None,
                    writes=False,
                    line=0,
                    source_file="app/models/ (relationship strategy)",
                )
            )
        return out

    def _hard_limits(self, surface: RouteSurface, pagination: PaginationInfo) -> list[str]:
        """Collect declared caps and note their absence where it matters."""
        limits: list[str] = []
        for param in surface.params:
            maximum = param.constraints.get("maximum")
            if maximum is not None:
                limits.append(f"`{param.name}` <= {maximum}")
            minimum = param.constraints.get("minimum")
            if minimum is not None and param.name in {"limit", "size"}:
                limits.append(f"`{param.name}` >= {minimum}")
        if pagination.style == "none":
            success = next(
                (r for r in surface.responses if r.status == surface.success_status), None
            )
            model = success.model if success else None
            if model and model.startswith("list["):
                limits.append(
                    "no page size cap and no pagination: the response is the whole "
                    "collection, however large it is"
                    if surface.method == "GET"
                    else "returns an unbounded list; the response grows with the "
                    "number of related rows"
                )
        if not limits:
            limits.append("none declared")
        return limits


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _calls_on(node: ast.AST, name: str) -> list[ast.Call]:
    """Find calls of the form ``name.method(...)`` under ``node``."""
    out: list[ast.Call] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == name
        ):
            out.append(child)
    return out


def _assignments(node: ast.AST) -> dict[str, list[ast.expr]]:
    """Collect every value assigned to each local name in a function body."""
    out: dict[str, list[ast.expr]] = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(child.value)
        elif isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name):
            out.setdefault(child.target.id, []).append(child.value)
    return out


def _root_name(node: ast.expr) -> str | None:
    """Return the leftmost ``Name`` of an attribute chain."""
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _as_int(value: Any) -> int | None:
    """Coerce a schema default or bound to int, or None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _entity_for_schema(
    model: str | None, relationships: dict[str, dict[str, tuple[str, str]]]
) -> str | None:
    """Guess the ORM class a response schema mirrors."""
    if not model:
        return None
    bare = model.removeprefix("list[").removesuffix("]")
    for suffix in ("ReadExtended", "ReadExpanded", "ReadParent", "Read"):
        if bare.endswith(suffix):
            candidate = f"{bare[: -len(suffix)]}ORM"
            return candidate if candidate in relationships else None
    return None


@dataclass(frozen=True)
class _SortConfig:
    """A declared sort configuration, resolved to tables and columns.

    Attributes:
        table: The table the sorted entity lives in.
        fields: Every sort key the endpoint accepts.
        overrides: Sort keys that do not name a column on ``table``, mapped to
            the (table, column) they actually order by. ``TITLE_SORT`` uses one:
            ``title_type`` is no longer a column on ``titles`` at all, and
            orders by ``title_types.code`` through a join. Judging it against
            ``titles.title_type`` would report a missing index on a column that
            does not exist, while the real target is uniquely indexed.
    """

    table: str
    fields: tuple[str, ...]
    overrides: dict[str, tuple[str, str]]


def _resolve_column_element(expr: object) -> tuple[str, str] | None:
    """Reduce a SQLAlchemy column expression to (table, column)."""
    name = getattr(expr, "name", None)
    table = getattr(expr, "table", None)
    table_name = getattr(table, "name", None)
    if isinstance(name, str) and isinstance(table_name, str):
        return table_name, name
    return None


def _load_sort_configs() -> dict[str, _SortConfig]:
    """Read the declared sort configurations from the models package.

    Returns:
        Constant name -> resolved configuration. Empty if the module cannot be
        imported.
    """
    try:
        from app.models import sort_configs  # noqa: PLC0415 -- deferred with the models.
    except Exception:  # pragma: no cover - defensive
        return {}
    out: dict[str, _SortConfig] = {}
    for name in dir(sort_configs):
        obj = getattr(sort_configs, name)
        if obj.__class__.__name__ != "SortConfig":
            continue
        table = getattr(obj.model, "__tablename__", None)
        if table is None:
            continue
        overrides: dict[str, tuple[str, str]] = {}
        for field_name, expression in getattr(obj, "field_overrides", {}).items():
            resolved = _resolve_column_element(expression)
            if resolved is not None:
                overrides[str(field_name)] = resolved
        out[name] = _SortConfig(
            table=str(table),
            fields=tuple(sorted(obj.allowed_fields)),
            overrides=overrides,
        )
    return out


def _filter_expressions(
    list_methods: list[_MethodContext],
) -> dict[str, tuple[str, str, str]]:
    """Map each ``params.<name>`` filter to the column and operator it uses.

    Only ``if params.<name>`` branches are considered, which is exactly how the
    repositories apply optional filters. A parameter with no such branch is
    reported as unresolved rather than assumed absent.

    Returns:
        Parameter name -> (ORM class name, column, normalised operator).
    """
    out: dict[str, tuple[str, str, str]] = {}
    for ctx in list_methods:
        locals_ = _assignments(ctx.node)
        for node in ast.walk(ctx.node):
            if not isinstance(node, ast.If):
                continue
            params = _params_referenced(node.test)
            if len(params) != 1:
                continue
            param = params[0]
            for call in ast.walk(node):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "where"
                    and call.args
                ):
                    continue
                resolved = _describe_predicate(call.args[0], locals_)
                if resolved:
                    out.setdefault(param, resolved)
    return out


def _params_referenced(node: ast.AST) -> list[str]:
    """Collect ``params.<name>`` attribute accesses under ``node``."""
    found: list[str] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "params"
        ):
            found.append(child.attr)
    return list(dict.fromkeys(found))


def _describe_predicate(
    node: ast.expr, locals_: dict[str, list[ast.expr]] | None = None
) -> tuple[str, str, str] | None:
    """Reduce a ``where()`` predicate to (ORM class, column, operator)."""
    if isinstance(node, ast.Compare) and node.ops:
        target = _column_ref(node.left)
        if target:
            symbol = {
                ast.Eq: "==",
                ast.NotEq: "!=",
                ast.Gt: ">",
                ast.GtE: ">=",
                ast.Lt: "<",
                ast.LtE: "<=",
                ast.In: "in_",
            }.get(type(node.ops[0]))
            if symbol:
                return target[0], target[1], symbol
        return None

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        method = node.func.attr
        target = _column_ref(node.func.value)
        if target is None:
            return None
        if method in {"ilike", "like"}:
            pattern = _pattern_shape(node.args[0], locals_) if node.args else "unknown"
            return target[0], target[1], f"{method}_{pattern}"
        if method in {"in_", "is_", "is_not", "between", "any", "contains"}:
            return target[0], target[1], method
        return target[0], target[1], method
    return None


def _column_ref(node: ast.expr) -> tuple[str, str] | None:
    """Resolve ``XORM.column`` to (``XORM``, ``column``)."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id.endswith("ORM")
    ):
        return node.value.id, node.attr
    return None


def _pattern_shape(node: ast.expr, locals_: dict[str, list[ast.expr]] | None = None) -> str:
    """Classify a LIKE pattern as a prefix, suffix or contains match.

    The pattern is often built into a local first (``like_val = f"%{x}%"``), so a
    bare name is resolved through the enclosing function's assignments before
    being classified. Getting this wrong would turn a guaranteed sequential scan
    into a reported prefix match.
    """
    if isinstance(node, ast.Name) and locals_:
        values = locals_.get(node.id)
        if values:
            node = values[-1]
    text = ast.unparse(node)
    body = text.strip("f'\"")
    starts = body.startswith("%")
    ends = body.endswith("%")
    if starts and ends:
        return "contains"
    if ends:
        return "prefix"
    if starts:
        return "suffix"
    return "exact"
