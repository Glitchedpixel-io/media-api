"""Phase 6 -- folding probe results into contracts, and building the two appendices.

Static derivation says what the code intends. The probes say what the system does.
This module is where the second overrides the first, and where the per-endpoint
findings are inverted into the two run-wide appendices:

- **Error taxonomy** -- one row per distinct status and condition, listing the
  endpoints that emit it, so a front end can build one error handler rather than
  sixty-one.
- **Constraint map** -- one row per constraint a user can reach through the
  interface, mapped to the response it produces and the message a UI would show.

The column that earns the constraint map its place is the last one. A violation
that arrives as a clean 409 with a sentence in it is something a form can handle;
one that arrives with an empty ``loc`` and the words "CHECK constraint violated"
is not, and no amount of front-end work recovers it. Saying which is which is the
point.
"""

from __future__ import annotations

from dataclasses import replace

from .models import ConstraintMapping, ErrorCase, WriteContract
from .write_probes import DUPLICATING, GUARDED, IDEMPOTENT, WriteProbeResult

# A body a UI can show a user, versus one it can only log. Judged on the message
# rather than the status: `{"detail": "A title cannot contain itself."}` is a
# sentence; `{"loc": [], "msg": "CHECK constraint violated."}` names neither the
# field nor anything a person did.
_UNUSABLE_MESSAGES = (
    "check constraint violated",
    "unique constraint violated.",
    "not null constraint violated",
    "invalid enum value",
    "integrity error",
)


def _message_is_usable(body_excerpt: str) -> bool:
    """Whether a response body carries something worth showing a user."""
    lowered = body_excerpt.lower()
    if '"loc": []' in lowered and any(bad in lowered for bad in _UNUSABLE_MESSAGES):
        return False
    return not any(
        lowered.strip().endswith(bad) or f'"{bad}' in lowered for bad in _UNUSABLE_MESSAGES
    )


def apply_probes(contract: WriteContract, results: tuple[WriteProbeResult, ...]) -> WriteContract:
    """Overlay what the probes observed onto what the code implied.

    Args:
        contract: The statically-derived contract.
        results: Every probe result attached to this endpoint.

    Returns:
        The contract with idempotency, probed errors and any corrected omission
        semantics folded in, and ``probed`` set when at least one probe ran.
    """
    if not results:
        return contract

    executed = [r for r in results if r.status == "ok"]
    if not executed:
        skipped = [r for r in results if r.status in ("skipped", "unavailable")]
        if skipped:
            return replace(
                contract,
                idempotency="UNKNOWN",
                idempotency_evidence=f"probe `{skipped[0].name}` did not run: {skipped[0].reason}",
            )
        return contract

    idempotency = contract.idempotency
    evidence = contract.idempotency_evidence
    omission = contract.omission_semantics
    errors = list(contract.errors)

    for result in executed:
        if result.kind == "repeat" and result.classification:
            idempotency = result.classification
            evidence = f"probed -- {result.detail}"
            if result.classification in (GUARDED, DUPLICATING) and len(result.statuses) > 1:
                errors.append(
                    ErrorCase(
                        status=str(result.statuses[-1]),
                        condition="the same request is sent a second time",
                        body=f"`{result.body_excerpt}`",
                        usable_message=_message_is_usable(result.body_excerpt),
                        source="probed",
                        note=f"observed by write probe `{result.name}`",
                    )
                )
        elif result.kind == "violation" and result.statuses:
            errors.append(
                ErrorCase(
                    status=str(result.statuses[0]),
                    condition=(
                        f"violates `{result.constraint}`"
                        if result.constraint
                        else "violates a database constraint"
                    ),
                    body=f"`{result.body_excerpt}`",
                    usable_message=_message_is_usable(result.body_excerpt),
                    source="probed",
                    note=f"observed by write probe `{result.name}`",
                )
            )
        elif result.kind == "omission":
            # Not `.capitalize()`: it lower-cases everything after the first
            # character, which turns the observed value `None` into `none` and a
            # field name into something that is no longer the field name.
            detail = result.detail
            omission = f"{detail[:1].upper()}{detail[1:]}. (Confirmed by probe `{result.name}`.)"

    return replace(
        contract,
        idempotency=idempotency,
        idempotency_evidence=evidence,
        omission_semantics=omission,
        errors=tuple(errors),
        probed=True,
    )


def error_taxonomy(
    contracts: dict[str, WriteContract],
) -> tuple[tuple[str, str, str, bool | None, tuple[str, ...]], ...]:
    """Invert the per-endpoint errors into one row per status and condition.

    Returns:
        Rows of ``(status, condition, body, usable_message, endpoints)``, sorted
        by status then condition, so the document renders deterministically.
    """
    bodies: dict[tuple[str, str], str] = {}
    usable: dict[tuple[str, str], bool | None] = {}
    endpoints: dict[tuple[str, str], set[str]] = {}

    for key, contract in contracts.items():
        for error in contract.errors:
            group = (error.status, error.condition)
            endpoints.setdefault(group, set()).add(key)
            # A probed body outranks a declared one: it is what the system
            # actually returned, rather than what the route says it might. The
            # first writer wins otherwise, which keeps the output deterministic.
            if error.source == "probed" or group not in bodies:
                bodies[group] = error.body
                usable[group] = error.usable_message

    return tuple(
        (group[0], group[1], bodies[group], usable[group], tuple(sorted(endpoints[group])))
        for group in sorted(endpoints)
    )


def constraint_map(
    results: tuple[WriteProbeResult, ...], declared: tuple[ConstraintMapping, ...]
) -> tuple[ConstraintMapping, ...]:
    """Attach observed responses to the declared constraint inventory.

    Args:
        results: Every write probe result from this run.
        declared: The constraint inventory read from the models.

    Returns:
        The inventory with status, body and distinguishability filled in for
        every constraint a probe actually reached. Constraints no probe reached
        keep ``distinguishable=None`` and are reported as gaps rather than
        assumed benign.
    """
    observed: dict[str, WriteProbeResult] = {}
    for result in results:
        if result.constraint and result.status == "ok" and result.statuses:
            observed.setdefault(result.constraint, result)

    out: list[ConstraintMapping] = []
    for mapping in declared:
        matched: WriteProbeResult | None = observed.get(mapping.name)
        if matched is None:
            out.append(mapping)
            continue
        result = matched
        status = result.statuses[-1]
        usable = _message_is_usable(result.body_excerpt)
        out.append(
            replace(
                mapping,
                endpoints=tuple(sorted({*mapping.endpoints, result.endpoint_key})),
                status=status,
                body=result.body_excerpt,
                distinguishable=status < 500 and usable,
                ui_message=(
                    result.body_excerpt
                    if usable
                    else "nothing usable -- the body names neither the field nor the cause"
                ),
                note=f"observed by write probe `{result.name}`",
            )
        )
    return tuple(out)


def verdict_for(contract: WriteContract, method: str) -> tuple[str, str]:
    """The verdict for a write endpoint, said in terms of what a form must handle.

    This replaces the boilerplate every uniform write used to share. That
    sentence -- "single-row write with no loops, safe to treat optimistically" --
    was true about the query shape and silent about everything a form actually
    gets wrong: whether a partial submit erases fields, whether a retry
    duplicates, whether a failure is legible. Those are what decide the verdict
    now.

    Args:
        contract: The endpoint's assembled write contract.
        method: The HTTP method, which decides how a null-writing body reads.

    Returns:
        ``(verdict sentence, severity class)``.
    """
    concerns: list[str] = []
    severity = "safe"

    omission = contract.omission_semantics.lower()
    # Matches both the derived wording and the probed one. The probe overrides the
    # static sentence when it runs, so a rule keyed only on the derived phrasing
    # would stop firing on exactly the routes where the hazard was *confirmed*.
    if any(
        phrase in omission
        for phrase in ("erases the fields it did not include", "set to null", "erases it")
    ):
        severity = "unsafe"
        concerns.append(
            f"a partial {method} body is written as nulls, so a form that submits only "
            "the fields it changed erases the rest"
        )
    elif "cannot be cleared" in omission:
        concerns.append(
            "omitted fields are left unchanged, so a partial form is safe -- but an "
            "explicit null is discarded too, so the interface must not offer a way to "
            "clear an optional field it cannot actually clear"
        )
    elif "refused outright" in omission or "rejects a partial body" in omission:
        severity = "caution" if severity != "unsafe" else severity
        concerns.append(
            "a partial body is refused, so the form must send a complete object read "
            "immediately beforehand"
        )

    if contract.atomic is False:
        severity = "unsafe" if severity == "unsafe" else "caution"
        concerns.append(
            "it writes outside the database as well as inside it and can half-succeed, "
            "so the interface must re-read rather than trust the response"
        )

    if contract.idempotency == DUPLICATING:
        severity = "unsafe" if severity == "unsafe" else "caution"
        concerns.append(
            "sending it twice creates a second row, so a retry after a dropped "
            "connection silently duplicates"
        )
    elif contract.idempotency == GUARDED:
        concerns.append(
            "a repeat is refused with a conflict, which a retrying client must treat as "
            "success rather than as a new error"
        )

    illegible = [e for e in contract.errors if e.usable_message is False]
    if illegible:
        severity = "caution" if severity == "safe" else severity
        statuses = ", ".join(sorted({e.status for e in illegible}))
        concerns.append(
            f"some failures ({statuses}) come back with nothing a user could be shown, "
            "so the form needs its own message for them"
        )

    if contract.audience == "worker fleet":
        concerns.append(
            "this is a worker-fleet route rather than a front-end one, and a UI should "
            "not be designed against it"
        )

    if not contract.probed:
        # CAUTION rather than UNKNOWN, deliberately. Repetition is genuinely
        # unmeasured for this route and the sentence says so -- but the contract
        # around it is derived, not missing: what an omitted field does, whether
        # the write is atomic, what it detaches, and who it is for are all known.
        # Marking the whole endpoint UNKNOWN for one unmeasured property would put
        # that token on most of the write surface, and a report where half the
        # verdicts read UNKNOWN gets skimmed exactly like one that cries wolf.
        concerns.append(
            "no write probe exercised it, so repetition is unverified — treat a retry "
            "after a dropped connection as unsafe until it is"
        )
        if severity == "safe":
            severity = "caution"

    if not concerns:
        return (
            "Nothing a form has to work around: a partial submit is safe, a repeat is "
            "harmless, and failures come back legible.",
            "safe",
        )
    lead = {
        "unsafe": "Not safe to drive from a partial form",
        "caution": "Usable, with handling",
        "unknown": "Derived from the code, not exercised",
        "safe": "Safe to build on",
    }[severity]
    return (f"{lead} — " + "; ".join(concerns) + ".", severity)


def summarise(results: tuple[WriteProbeResult, ...]) -> str:
    """A one-line summary of what the write phase established."""
    ran = [r for r in results if r.status == "ok"]
    guarded = sum(1 for r in ran if r.classification == GUARDED)
    duplicating = sum(1 for r in ran if r.classification == DUPLICATING)
    idempotent = sum(1 for r in ran if r.classification == IDEMPOTENT)
    return (
        f"{len(ran)} of {len(results)} write scenarios executed; "
        f"{idempotent} idempotent, {guarded} guarded, {duplicating} duplicating"
    )
