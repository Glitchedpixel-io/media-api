"""Phase 6 -- the write-probe runner.

Reads the ``write_probes`` section of ``probes.yaml`` and executes it against the
disposable target that :mod:`.write_semantics` has already validated and bound.

Why this is a separate section rather than entries in ``probes`` with the method
allowlisted: :func:`probes.load_config` refuses any non-GET probe absent from
``allowlist``, and that empty list is what makes Phase 4 structurally incapable of
mutating the production-backed instance it runs against. Putting write probes
through it would trade a guarantee for a convenience. The Phase 4 loader never
reads this key, so ``allowlist`` stays empty and stays meaningful.

Every scenario is responsible for its own fixtures. It creates what it needs
through the API, exercises one thing, and deletes what it created in reverse
order. What it could not remove is reported rather than forgotten -- a probe that
leaks rows into the target quietly makes the next run's results wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Unknown
from .probes import _pick
from .write_semantics import WriteTarget, psycopg_dsn

# The classifications 6b asks for. Read off behaviour, never off the verb: a PUT
# that inserts a second row is duplicating however idempotent the method is
# supposed to be, and that is precisely the thing a retrying UI needs to know.
IDEMPOTENT = "idempotent"
GUARDED = "guarded"
DUPLICATING = "duplicating"


@dataclass(frozen=True)
class WriteProbeResult:
    """One executed write scenario."""

    name: str
    endpoint_key: str
    kind: str
    status: str
    statuses: tuple[int, ...] = ()
    classification: str | None = None
    detail: str = ""
    body_excerpt: str = ""
    constraint: str | None = None
    cleaned_up: bool = True
    reason: str | None = None


# Tables a scenario may clean up directly. Deliberately a closed list: the value
# reaches an identifier position in a DELETE, where no parameter binding is
# possible, so it is checked against this set rather than escaped.
_CLEANABLE_TABLES = frozenset(
    {
        "titles",
        "assets",
        "tags",
        "id_schemes",
        "title_contents",
        "external_identifiers",
        "external_asset_ids",
        "artwork",
    }
)

# Columns a cleanup may match on, for the same reason: an identifier position.
_CLEANABLE_COLUMNS = frozenset({"id", "name", "code", "path", "filename"})

# Row ids inside a probed response body. See :func:`_excerpt`.
_DIGITS = re.compile(r"\d+")


@dataclass
class _Ledger:
    """What a scenario created, so it can be undone in reverse order.

    Two undo channels, because the API is not a complete inverse of itself.
    There is no ``DELETE /api/titles/{id}``, no ``DELETE /api/assets/{id}``, no
    ``DELETE /api/tags/{id}`` and no ``DELETE /api/id_schemes/{id}`` -- the
    primary objects of the library cannot be deleted over HTTP at all. A probe
    that creates a Title therefore cannot undo itself through the interface it
    is probing, which is exactly why Phase 6 requires a database URL alongside
    the base URL rather than only for verification.
    """

    entries: list[tuple[str, str]] = field(default_factory=list)
    sql_entries: list[tuple[str, str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def record(self, method: str, path: str) -> None:
        self.entries.append((method, path))

    def record_sql(self, table: str, row_id: Any, column: str = "id") -> None:
        self.sql_entries.append((table, column, row_id))


class WriteProbeRunner:
    """Executes write scenarios against a validated, disposable target."""

    def __init__(self, target: WriteTarget, timeout: float = 30.0) -> None:
        """Open a client against the write target.

        Args:
            target: A target that has already passed :func:`write_semantics.bind_check`.
            timeout: Per-request timeout in seconds.

        Raises:
            RuntimeError: If httpx is unavailable.
        """
        try:
            import httpx  # noqa: PLC0415 -- deferred so --skip-writes needs no client.
        except ImportError as exc:  # pragma: no cover - httpx is a runtime dependency
            raise RuntimeError(f"httpx is required for Phase 6: {exc}") from exc
        self.target = target
        headers = {"Authorization": f"Bearer {target.token}"} if target.token else {}
        self._client = httpx.Client(base_url=target.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        """Release the client."""
        self._client.close()

    # -- request plumbing ------------------------------------------------

    def _send(self, spec: dict[str, Any], context: dict[str, Any]) -> tuple[int, Any]:
        """Issue one request from a scenario step.

        Returns:
            ``(status code, decoded body or None)``.
        """
        method = str(spec.get("method") or "GET").upper()
        path = _substitute(str(spec["path"]), context)
        body = _substitute_deep(spec.get("body"), context)
        response = self._client.request(method, path, json=body if body is not None else None)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload

    def _steps(
        self, steps: list[dict[str, Any]], context: dict[str, Any], ledger: _Ledger
    ) -> tuple[bool, str]:
        """Run setup steps, capturing values and registering cleanups.

        Returns:
            ``(ok, detail)``. A setup step that fails aborts the scenario -- the
            act would otherwise measure something other than what it claims to.
        """
        for step in steps:
            status, payload = self._send(step, context)
            if status >= 400:
                return False, f"setup step {step.get('path')} returned {status}"
            for name, expression in (step.get("capture") or {}).items():
                try:
                    context[name] = _pick(payload, expression)
                except Exception:  # pragma: no cover - defensive
                    return False, f"could not capture {name} from {step.get('path')}"
            cleanup = step.get("cleanup")
            if cleanup:
                ledger.record(
                    str(cleanup.get("method") or "DELETE"),
                    _substitute(str(cleanup["path"]), context),
                )
            sql_cleanup = step.get("sql_cleanup")
            if sql_cleanup:
                table = str(sql_cleanup["table"])
                if table not in _CLEANABLE_TABLES:
                    return False, f"sql_cleanup names an unlistable table {table!r}"
                row_id = context.get(str(sql_cleanup["id_var"]))
                if row_id is not None:
                    ledger.record_sql(table, row_id)
        return True, ""

    def _undo(self, ledger: _Ledger) -> bool:
        """Undo a scenario's fixtures in reverse order, best effort.

        HTTP first, then SQL. A row the API can delete is deleted the way a real
        client would, so the teardown exercises the same cascades the interface
        does; only what has no route falls through to the database.
        """
        ok = True
        for method, path in reversed(ledger.entries):
            try:
                response = self._client.request(method, path)
                if response.status_code >= 400 and response.status_code != 404:
                    ledger.failures.append(f"{method} {path} -> {response.status_code}")
                    ok = False
            except Exception as exc:  # pragma: no cover - network dependent
                ledger.failures.append(f"{method} {path} -> {exc}")
                ok = False
        if ledger.sql_entries:
            ok = self._undo_sql(ledger) and ok
        return ok

    def _undo_sql(self, ledger: _Ledger) -> bool:
        """Remove rows the API offers no route to delete."""
        import psycopg  # noqa: PLC0415 -- Phase 6 only.

        ok = True
        try:
            with psycopg.connect(psycopg_dsn(self.target.database_url), connect_timeout=10) as conn:
                for table, column, value in reversed(ledger.sql_entries):
                    if table not in _CLEANABLE_TABLES:  # pragma: no cover - checked earlier
                        continue
                    if column not in _CLEANABLE_COLUMNS:  # pragma: no cover - checked earlier
                        continue
                    try:
                        with conn.cursor() as cur:
                            cur.execute(f"DELETE FROM {table} WHERE {column} = %s", (value,))
                    except Exception as exc:
                        ledger.failures.append(f"DELETE {table}.{column}={value} -> {exc}")
                        ok = False
                conn.commit()
        except Exception as exc:  # pragma: no cover - network dependent
            ledger.failures.append(f"sql cleanup connection -> {exc}")
            ok = False
        return ok

    # -- scenario kinds --------------------------------------------------

    def run(self, scenario: dict[str, Any]) -> WriteProbeResult:
        """Execute one scenario and classify what happened."""
        name = str(scenario["name"])
        endpoint_key = str(scenario["endpoint"])
        kind = str(scenario.get("kind") or "violation")
        context: dict[str, Any] = {}
        ledger = _Ledger()

        if scenario.get("needs_media_root") and self.target.media_root is None:
            return WriteProbeResult(
                name=name,
                endpoint_key=endpoint_key,
                kind=kind,
                status="skipped",
                reason=(
                    "touches the filesystem and no scratch media root is configured; "
                    "not run against a real one"
                ),
            )

        try:
            ok, detail = self._steps(list(scenario.get("setup") or ()), context, ledger)
            if not ok:
                self._undo(ledger)
                return WriteProbeResult(
                    name=name,
                    endpoint_key=endpoint_key,
                    kind=kind,
                    status="unavailable",
                    reason=detail,
                    cleaned_up=not ledger.failures,
                )
            handler = {
                "repeat": self._repeat,
                "violation": self._violation,
                "omission": self._omission,
            }.get(kind)
            if handler is None:
                return WriteProbeResult(
                    name=name,
                    endpoint_key=endpoint_key,
                    kind=kind,
                    status="error",
                    reason=f"unknown scenario kind {kind!r}",
                )
            result = handler(scenario, context, ledger, name, endpoint_key)
            # Rows the act created as a side effect, which no captured id names --
            # the tag `POST /titles/{id}/tags` creates from a free-text name is the
            # reference case. Registered after the act so a failed act still tidies.
            for entry in scenario.get("sql_cleanup") or ():
                table = str(entry["table"])
                column = str(entry.get("column") or "id")
                if table in _CLEANABLE_TABLES and column in _CLEANABLE_COLUMNS:
                    ledger.record_sql(table, _substitute(str(entry["value"]), context), column)
        except Exception as exc:  # pragma: no cover - network dependent
            result = WriteProbeResult(
                name=name,
                endpoint_key=endpoint_key,
                kind=kind,
                status="error",
                reason=str(exc)[:200],
            )
        finally:
            cleaned = self._undo(ledger)

        return WriteProbeResult(
            **{
                **result.__dict__,
                "cleaned_up": cleaned and result.cleaned_up,
                "reason": result.reason
                or ("; ".join(ledger.failures) if ledger.failures else None),
            }
        )

    def _repeat(
        self,
        scenario: dict[str, Any],
        context: dict[str, Any],
        ledger: _Ledger,
        name: str,
        endpoint_key: str,
    ) -> WriteProbeResult:
        """Send the same request twice and classify the second response.

        6b in one method. The three outcomes are materially different for a UI
        that retries on a dropped connection: an idempotent route can be retried
        blindly, a guarded one needs the conflict treated as success, and a
        duplicating one needs the retry suppressed or the duplicate cleaned up.
        """
        act = scenario["act"]
        first_status, first_body = self._send(act, context)
        second_status, second_body = self._send(act, context)

        cleanup = scenario.get("act_cleanup")
        act_table = scenario.get("act_sql_table")
        for payload in (first_body, second_body):
            if not isinstance(payload, dict) or payload.get("id") is None:
                continue
            if cleanup:
                ledger.record(
                    str(cleanup.get("method") or "DELETE"),
                    _substitute(str(cleanup["path"]), {**context, "id": payload["id"]}),
                )
            if act_table and str(act_table) in _CLEANABLE_TABLES:
                ledger.record_sql(str(act_table), payload["id"])

        first_id = first_body.get("id") if isinstance(first_body, dict) else None
        second_id = second_body.get("id") if isinstance(second_body, dict) else None

        if second_status >= 400:
            classification = GUARDED
            detail = (
                f"the second identical request is refused with {second_status}; a retry "
                "after a dropped connection must treat that conflict as success, not as "
                "a new error to show the user"
            )
        elif first_id is not None and second_id is not None and first_id != second_id:
            classification = DUPLICATING
            detail = (
                f"the second identical request creates a second row (id {first_id} then "
                f"{second_id}); nothing in the database prevents it, so a retrying UI "
                "silently duplicates and the user has to find and remove the extra"
            )
        else:
            classification = IDEMPOTENT
            detail = (
                f"the second identical request returns {second_status} and changes "
                "nothing; safe to retry blindly"
            )

        return WriteProbeResult(
            name=name,
            endpoint_key=endpoint_key,
            kind="repeat",
            status="ok",
            statuses=(first_status, second_status),
            classification=classification,
            detail=detail,
            body_excerpt=_excerpt(second_body),
        )

    def _violation(
        self,
        scenario: dict[str, Any],
        context: dict[str, Any],
        ledger: _Ledger,
        name: str,
        endpoint_key: str,
    ) -> WriteProbeResult:
        """Provoke a constraint violation and record exactly what comes back.

        The question 6c and 6d exist to answer: does the API turn a database
        constraint into a clean, distinguishable error, or does it leak a 500 that
        a front end can neither branch on nor show anyone.
        """
        status, body = self._send(scenario["act"], context)
        cleanup = scenario.get("act_cleanup")
        act_table = scenario.get("act_sql_table")
        if isinstance(body, dict) and body.get("id") is not None:
            if cleanup:
                ledger.record(
                    str(cleanup.get("method") or "DELETE"),
                    _substitute(str(cleanup["path"]), {**context, "id": body["id"]}),
                )
            if act_table and str(act_table) in _CLEANABLE_TABLES:
                ledger.record_sql(str(act_table), body["id"])
        distinguishable = status < 500
        detail = f"returns {status}" + (
            " -- a clean client error the interface can branch on and show"
            if distinguishable
            else " -- a server error, indistinguishable from any other fault. A front "
            "end cannot tell the user what went wrong, and cannot work around it"
        )
        return WriteProbeResult(
            name=name,
            endpoint_key=endpoint_key,
            kind="violation",
            status="ok",
            statuses=(status,),
            classification="distinguishable" if distinguishable else "generic-500",
            detail=detail,
            body_excerpt=_excerpt(body),
            constraint=scenario.get("constraint"),
        )

    def _omission(
        self,
        scenario: dict[str, Any],
        context: dict[str, Any],
        ledger: _Ledger,
        name: str,
        endpoint_key: str,
    ) -> WriteProbeResult:
        """Confirm by experiment what an omitted field and an explicit null do.

        The static trace reads ``exclude_none`` out of the call site, which is
        derivation rather than observation. This checks the derivation against the
        running system, because the whole phase turns on it being right.
        """
        field_name = str(scenario["field"])
        read = scenario["read"]
        act = scenario["act"]
        # Some routes reject a body that omits an unrelated required field, so the
        # probe would never reach the question it is asking. `base_body` carries
        # whatever the route insists on, and the field under test is the only
        # thing that varies between the two requests.
        base = dict(scenario.get("base_body") or {})

        _, before = self._send(read, context)
        original = before.get(field_name) if isinstance(before, dict) else None

        omitted_body = {k: v for k, v in base.items() if k != field_name}
        empty_status, empty_error = self._send({**act, "body": omitted_body}, context)
        _, after_empty = self._send(read, context)
        after_empty_value = after_empty.get(field_name) if isinstance(after_empty, dict) else None

        null_status, null_error = self._send(
            {**act, "body": {**omitted_body, field_name: None}}, context
        )
        _, after_null = self._send(read, context)
        after_null_value = after_null.get(field_name) if isinstance(after_null, dict) else None

        # A request that was refused proves nothing about omission semantics. The
        # field surviving a 422 is the field surviving *nothing having happened*,
        # and reporting that as "a partial form is safe" would be exactly wrong.
        if empty_status >= 400:
            return WriteProbeResult(
                name=name,
                endpoint_key=endpoint_key,
                kind="omission",
                status="ok",
                statuses=(empty_status, null_status),
                classification="rejects a partial body",
                detail=(
                    f"a body omitting `{field_name}` is refused outright with "
                    f"{empty_status}, so this route cannot be driven from a partial "
                    "form at all -- the caller must send a complete object"
                ),
                body_excerpt=_excerpt(empty_error),
            )

        survived_empty = after_empty_value == original
        survived_null = after_null_value == original

        if survived_empty and survived_null:
            classification = "omission-safe, cannot clear"
            detail = (
                f"`{field_name}` survives both an empty body and an explicit null, so a "
                "partial form is safe and there is no request that clears the field"
            )
        elif survived_empty and not survived_null:
            classification = "omission-safe, null clears"
            detail = (
                f"`{field_name}` survives an empty body and is cleared by an explicit "
                "null -- full PATCH semantics"
            )
        else:
            classification = "omission destroys"
            detail = (
                f"`{field_name}` did not survive a body that omitted it (was "
                f"{original!r}, now {after_empty_value!r}): a partial form erases it"
            )

        return WriteProbeResult(
            name=name,
            endpoint_key=endpoint_key,
            kind="omission",
            status="ok",
            statuses=(empty_status, null_status),
            classification=classification,
            detail=detail,
            body_excerpt=_excerpt(
                {
                    "before": original,
                    "after_empty": after_empty_value,
                    "after_null": after_null_value,
                }
            ),
        )


def _substitute(text: str, context: dict[str, Any]) -> str:
    """Fill ``{name}`` placeholders from the scenario's captured values."""
    out = text
    for key, value in context.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def _substitute_deep(value: Any, context: dict[str, Any]) -> Any:
    """Fill placeholders throughout a nested request body."""
    if isinstance(value, str):
        filled = _substitute(value, context)
        if filled != value and filled.lstrip("-").isdigit():
            return int(filled)
        return filled
    if isinstance(value, dict):
        return {k: _substitute_deep(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_deep(v, context) for v in value]
    return value


def _excerpt(payload: Any, limit: int = 200) -> str:
    """A short, committed-report-safe, run-stable rendering of a response body.

    Row ids are replaced with ``N``. The good error messages on this API quote
    them -- "Title 3 already has an intrinsic parent, recorded by containment row
    3" -- and those ids come from a scratch database whose sequences are not
    reset between runs. Committed verbatim, every re-run of Phase 6 would produce
    a diff saying nothing about the API, which is the exact failure the sorting
    and rounding elsewhere in this harness exist to prevent.

    What the report needs from an excerpt is the *shape* of the message and
    whether it names anything a user could act on. Neither depends on the id.
    """
    import json  # noqa: PLC0415 -- only needed when a probe actually ran.

    try:
        text = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        text = str(payload)
    text = _DIGITS.sub("N", text)
    return text[:limit] + ("..." if len(text) > limit else "")


def run_suite(
    scenarios: list[dict[str, Any]], target: WriteTarget
) -> tuple[tuple[WriteProbeResult, ...], tuple[Unknown, ...]]:
    """Execute every declared scenario.

    Args:
        scenarios: The ``write_probes.scenarios`` list from ``probes.yaml``.
        target: A validated, bound write target.

    Returns:
        ``(results, unknowns)``. Anything a scenario could not clean up is
        reported as an :class:`Unknown` rather than left silent.
    """
    runner = WriteProbeRunner(target)
    results: list[WriteProbeResult] = []
    unknowns: list[Unknown] = []
    try:
        for scenario in scenarios:
            result = runner.run(scenario)
            results.append(result)
            if not result.cleaned_up:
                unknowns.append(
                    Unknown(
                        scope=result.endpoint_key,
                        question=(
                            f"write probe `{result.name}` could not remove everything it " "created"
                        ),
                        resolution=(
                            f"inspect the write target and remove the leftovers "
                            f"({result.reason}); the next run's results are otherwise "
                            "measured against a dirty database"
                        ),
                    )
                )
    finally:
        runner.close()
    return tuple(results), tuple(unknowns)
