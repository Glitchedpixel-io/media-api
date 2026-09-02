# app/title_content_service.py
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from fastapi import HTTPException

from app.repositories import MediaRepository, TitleContentRepository, TitleRepository
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    MembershipKind,
    TitleContentBatchResult,
    TitleContentInsert,
    TitleContentPatchPublic,
    TitleContentRead,
    TitleContentReadExtended,
    TitleContentReadParent,
    TitleContentUpdateInternal,
)
from app.services.errors import (
    conflict_detail,
    domain_error_detail,
    translate_repository_errors,
)

#: Discriminators a move's 409 carries in ``detail[0]["type"]``.
#:
#: A drag-and-drop interface has to respond differently to each: a cycle is a refusal to
#: explain, a second home is an offer to add the edge as curated instead, and a taken
#: position is worth retrying at the next slot. Matching on the prose is not an
#: interface, which is what #178 asked to fix.
_CYCLE_CODE = "containment_cycle"
_INTRINSIC_PARENT_CODE = "intrinsic_parent_conflict"
_POSITION_CODE = "position_conflict"


class TitleContentService:
    def __init__(
        self,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
        media_repository: MediaRepository,
    ) -> None:
        self.title_repository = title_repository
        self.title_content_repository = title_content_repository
        self.media_repository = media_repository

    def get_titles_with_asset(self, asset_id: int) -> list[TitleContentReadParent]:
        if not self.media_repository.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.title_content_repository.get_titles_with_asset(asset_id)

    def get_parents_of_title(self, title_id: int) -> list[TitleContentReadParent]:
        """The titles that directly contain this one.

        The existence check is what separates "this title has no parents" from "there is
        no such title": both would otherwise return an empty list, and a breadcrumb
        built on the second would silently render a root instead of failing.

        Args:
            title_id: The title whose parents to list.

        Returns:
            list[TitleContentReadParent]: Immediate parents, empty if the title is a
                root. Empty is an ordinary answer -- 504 of 1,585 titles have a parent,
                so most are roots.

        Raises:
            HTTPException: 404 if the title does not exist.
        """
        if not self.title_repository.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.title_content_repository.get_parents_of_title(title_id)

    def _reject_cycle(self, parent_title_id: int, child_title_id: int | None) -> None:
        """Refuse an edge that would make a title contain itself, directly or not.

        Containment is a DAG, and nothing in the schema can say so: Postgres cannot
        express reachability as a constraint, so the only declarative half is the
        self-edge case (``no_self_containment_chk``). The rest is here, in the same
        place the artwork service owns the integrity checks its own table cannot.

        A cycle is not a cosmetic problem. Any consumer walking containment for a
        breadcrumb or a tree hangs on one unless it carries its own defence -- the
        poster resolution has to, and that machinery exists only because this guard
        did not.

        409 rather than 422: the payload is well formed and the referenced titles both
        exist. What it conflicts with is the structure already stored, which is what
        409 is for.

        Args:
            parent_title_id: The title that would do the containing.
            child_title_id: The title that would be contained, or None for an asset
                entry, which cannot form a cycle because assets are leaves.

        Raises:
            HTTPException: 409 if the edge would close a containment cycle.
        """
        if child_title_id is None:
            return
        if child_title_id == parent_title_id:
            raise HTTPException(
                status_code=409,
                detail="A title cannot contain itself.",
            )
        if self.title_content_repository.can_reach(child_title_id, parent_title_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Title {child_title_id} already contains title {parent_title_id}, "
                    "directly or through its contents, so this would create a cycle."
                ),
            )

    def _edge_under(self, parent_title_id: int, title_content_id: int) -> TitleContentRead:
        """The containment row, confirmed to sit under this parent.

        ``{parent_title_id}`` in the contents routes names **the edge's current
        parent**, and this is the one place that is enforced. Before #185 nothing
        checked it, and the segment had drifted into meaning three different things:
        the destination on ``PATCH .../contents/{id}`` and on ``.../reorder``, both of
        which relocated the edge to whatever title the URL named, and nothing at all on
        ``DELETE``, which removed by id alone.

        That was not merely untidy. ``reorder`` moved an edge across parents without
        calling :meth:`_reject_cycle`, so an edge ``POST`` refuses to create could be
        arrived at by moving one that already existed -- measured, and the substance of
        #185. Scoping every write to the edge's own parent closes that by construction
        rather than by adding a fourth guard: a route that cannot change the parent
        cannot open a cycle. Moving an edge deliberately is a separate operation with
        its own endpoint and its own checks (#178).

        404 rather than 403 for a mismatch, and the same 404 as an edge that does not
        exist: from the caller's position the two are the same statement -- there is no
        such entry under that title -- and distinguishing them would confirm the
        existence of a row addressed through a title that does not own it.

        Args:
            parent_title_id: The title the caller addressed the write to.
            title_content_id: The containment row it named.

        Returns:
            TitleContentRead: The row, which is guaranteed to be under
                ``parent_title_id``.

        Raises:
            HTTPException: 404 if the title does not exist, the row does not exist, or
                the row is not under that title.
        """
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        edge = self.title_content_repository.get(title_content_id)
        if edge is None or edge.parent_title_id != parent_title_id:
            raise HTTPException(status_code=404, detail="Title Content not found")
        return edge

    def _reject_second_intrinsic_parent(
        self,
        child_title_id: int | None,
        membership: MembershipKind,
        *,
        excluding_edge_id: int | None = None,
    ) -> None:
        """Refuse an intrinsic edge for a title that already has one.

        A title has one home, so that a breadcrumb has one path upward. Curated edges
        are unlimited and skip this entirely -- appearing in many lists is the point of
        the distinction -- and so do asset edges, which draw no breadcrumb.

        ``uq_one_intrinsic_parent`` is the real enforcement, and has to be, because rows
        arrive in this table from a producer that never goes near this service (#125).
        This check exists so that a caller who *does* come through the API is told which
        edge it collided with, instead of the bare "unique constraint violated" the
        index alone would produce.

        409 rather than 422, for the same reason ``_reject_cycle`` uses it: the payload
        is well formed and both titles exist. What it conflicts with is the structure
        already stored.

        Args:
            child_title_id: The title that would be contained, or None for an asset
                entry, which has no home to be ambiguous about.
            membership: Whether the proposed edge is the child's home or a curated
                listing.
            excluding_edge_id: A containment row to ignore when looking for a conflict,
                so that patching a row does not collide with itself.

        Raises:
            HTTPException: 409 if the title already has an intrinsic parent.
        """
        if child_title_id is None or membership is not MembershipKind.intrinsic:
            return
        existing = self.title_content_repository.intrinsic_parent_edge_id(child_title_id)
        if existing is None or existing == excluding_edge_id:
            return
        raise HTTPException(
            status_code=409,
            detail=(
                f"Title {child_title_id} already has an intrinsic parent, recorded by "
                f"containment row {existing}. A title has one home; to list it "
                "elsewhere as well, add the edge as curated."
            ),
        )

    @translate_repository_errors
    def insert_positioned(
        self,
        parent_title_id: int,
        insert: TitleContentInsert,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        anchor: str | None = None,
    ) -> TitleContentRead:
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        self._reject_cycle(parent_title_id, insert.child_title_id)
        self._reject_second_intrinsic_parent(insert.child_title_id, insert.membership)
        return self.title_content_repository.create_positioned(  # type: ignore
            parent_title_id,
            insert,
            before_id=before_id,
            after_id=after_id,
            anchor=anchor,
        )

    def get_title_content(self, parent_title_id: int) -> list[TitleContentReadExtended]:
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.title_content_repository.list_title_content(parent_title_id, True)

    @translate_repository_errors(not_found_message="Title Content not found")
    def update_title_content(
        self,
        parent_title_id: int,
        title_contents_id: int,
        update: TitleContentPatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleContentRead:
        existing_row = self._edge_under(parent_title_id, title_contents_id)
        # A patch can repoint an existing row at a different child, which reaches the
        # same invalid state as inserting one. Guarding only the insert would leave
        # the shorter path to a cycle open.
        new_child_title_id = getattr(update, "child_title_id", None)
        self._reject_cycle(parent_title_id, new_child_title_id)
        # Repointing an existing intrinsic edge at a different child reaches the same
        # invalid state as inserting one, so the patch path needs the guard too -- with
        # the row itself excluded, or it would collide with its own edge. The membership
        # comes from the stored row rather than the patch: a patch cannot carry one,
        # which is why TitleContentPatchPublic omits the field.
        if new_child_title_id is not None:
            self._reject_second_intrinsic_parent(
                new_child_title_id,
                existing_row.membership,
                excluding_edge_id=title_contents_id,
            )
        # `parent_title_id` is deliberately **not** forwarded. It used to be, from the
        # URL, on every patch -- so a request that changed only a label relocated the
        # edge to whichever title the caller happened to address, which is #185. The
        # segment is now read as the edge's current parent and verified above, so
        # there is nothing left to write; changing a parent is a move, and has its own
        # endpoint.
        return self.title_content_repository.update(
            title_contents_id,
            TitleContentUpdateInternal(
                **update.model_dump(exclude_none=exclude_none),  # type: ignore
            ),
        )

    def reorder_content(
        self,
        parent_title_id: int,
        *,
        title_content_id: int,
        before_id: int | None = None,
        after_id: int | None = None,
        anchor: str | None = None,
    ) -> TitleContentRead:
        # Same-parent only. The repository's `reorder` can move a row between parents,
        # and this route used to reach that with no cycle check at all (#185); scoping
        # the edge to the parent named in the URL means the id passed below is always
        # the parent the row already has, so this call can only reposition.
        self._edge_under(parent_title_id, title_content_id)
        try:
            updated = self.title_content_repository.reorder(
                parent_title_id,
                title_content_id,
                before_id=before_id,
                after_id=after_id,
                anchor=anchor,
            )
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail="Title Content not found") from e
        except UniqueViolation as e:
            raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal server error") from e
        if not updated:
            raise HTTPException(status_code=404, detail="Title Content not found")
        else:
            return updated

    def _batch_failures(self, problems: list[tuple[int, str, str]]) -> list[dict]:
        """Shape a batch's per-item problems into a detail body a UI can render.

        Deliberately the same ``[{loc, msg, type}]`` list FastAPI's own validation
        errors use, and that :func:`domain_error_detail` already produces -- with the
        item's index in ``loc``. A client parses one error shape rather than two, and
        the index is what lets a form highlight row 17 rather than saying "something in
        this batch was wrong".

        Args:
            problems: ``(index, message, code)`` per failing item.

        Returns:
            list[dict]: One entry per problem, in item order.
        """
        return [
            {"loc": ["items", index], "msg": message, "type": code}
            for index, message, code in sorted(problems)
        ]

    def _guard_problems(
        self,
        parent_title_id: int,
        index: int,
        child_title_id: int | None,
        membership: MembershipKind,
        *,
        excluding_edge_id: int | None = None,
    ) -> list[tuple[int, str, str]]:
        """Run the single-write guards for one item and collect rather than raise.

        The guards are reused exactly as the single writes call them, so a batch cannot
        drift from what one-at-a-time would have allowed. Only the failure handling
        differs: a batch reports every bad item at once instead of stopping at the first.
        """
        problems: list[tuple[int, str, str]] = []
        try:
            self._reject_cycle(parent_title_id, child_title_id)
        except HTTPException as e:
            problems.append((index, str(e.detail), _CYCLE_CODE))
        try:
            self._reject_second_intrinsic_parent(
                child_title_id, membership, excluding_edge_id=excluding_edge_id
            )
        except HTTPException as e:
            problems.append((index, str(e.detail), _INTRINSIC_PARENT_CODE))
        return problems

    @translate_repository_errors(not_found_message="Title Content not found")
    def attach_many(
        self, parent_title_id: int, inserts: Sequence[TitleContentInsert]
    ) -> TitleContentBatchResult:
        """Append several entries to one parent, all-or-nothing (#179).

        **Every item is validated before anything is written**, and a failure names all
        of them rather than the first. That is the half of "bulk" that matters to an
        interface: a caller placing a 156-file directory wants one response listing the
        three files that are wrong, not three round trips discovering them one at a time.

        All-or-nothing rather than per-item commits, following #52 -- by-name tagging
        committed once per tag, so a failed batch left an arbitrary prefix written with
        no way for the caller to tell which. Choosing per-item here would recreate that
        defect on a second table.

        **Cycles cannot arise from the combination, only from individual items**, which
        is why there is no whole-batch reachability check. Every edge this creates leaves
        ``parent_title_id``; a cycle needs a path back *into* it, and adding more edges
        out of a node cannot create one. So the per-item guard is complete here. What
        does need a whole-batch check is duplicate targets: two items naming one child,
        or one asset, collide on ``uq_parent_child_title_once`` /
        ``uq_parent_asset_once`` and each looks perfectly valid alone.

        Args:
            parent_title_id: The parent to append under.
            inserts: The entries to create, in the order they should land.

        Returns:
            TitleContentBatchResult: The created rows, in the order given.

        Raises:
            HTTPException: 404 if the parent does not exist; 422 naming every item whose
                target is missing or duplicated within the batch; 409 naming every item
                that would close a cycle or give a title a second home.
        """
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")

        invalid: list[tuple[int, str, str]] = []
        conflicts: list[tuple[int, str, str]] = []
        seen: dict[tuple[str, int], int] = {}

        for index, insert in enumerate(inserts):
            target: tuple[str, int] | None = None
            if insert.child_title_id is not None:
                target = ("title", insert.child_title_id)
            elif insert.asset_id is not None:
                target = ("asset", insert.asset_id)

            if target is not None:
                first = seen.get(target)
                if first is not None:
                    invalid.append(
                        (
                            index,
                            f"{target[0].capitalize()} {target[1]} is already item {first} "
                            "of this batch; it can appear once under a parent.",
                            "duplicate_in_batch",
                        )
                    )
                    continue
                seen[target] = index

                exists = (
                    self.title_repository.exists(target[1])
                    if target[0] == "title"
                    else self.media_repository.exists(target[1])
                )
                if not exists:
                    invalid.append(
                        (
                            index,
                            f"{target[0].capitalize()} {target[1]} does not exist.",
                            "target_missing",
                        )
                    )
                    continue

            conflicts.extend(
                self._guard_problems(
                    parent_title_id, index, insert.child_title_id, insert.membership
                )
            )

        if invalid:
            raise HTTPException(status_code=422, detail=self._batch_failures(invalid))
        if conflicts:
            raise HTTPException(status_code=409, detail=self._batch_failures(conflicts))

        created = self.title_content_repository.create_many_positioned(parent_title_id, inserts)
        return TitleContentBatchResult(count=len(created), items=created)

    @translate_repository_errors(not_found_message="Title Content not found")
    def detach_many(
        self, parent_title_id: int, title_content_ids: Sequence[int]
    ) -> TitleContentBatchResult:
        """Remove several entries from one parent, all-or-nothing (#179).

        Every id must be a row under this parent, checked the way a single delete checks
        it (#185). Repeats are collapsed rather than refused: asking twice for a row to
        be gone is not a conflicting instruction.

        Args:
            parent_title_id: The parent whose entries these are.
            title_content_ids: The rows to remove.

        Returns:
            TitleContentBatchResult: How many rows were removed, and no items -- they are
                gone, so there is nothing to return.

        Raises:
            HTTPException: 404 if the parent does not exist; 422 naming every id that is
                not an entry under it.
        """
        wanted = list(dict.fromkeys(title_content_ids))
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")

        problems: list[tuple[int, str, str]] = []
        for index, edge_id in enumerate(wanted):
            edge = self.title_content_repository.get(edge_id)
            if edge is None or edge.parent_title_id != parent_title_id:
                problems.append(
                    (
                        index,
                        f"Title content {edge_id} is not an entry of title {parent_title_id}.",
                        "not_under_parent",
                    )
                )
        if problems:
            raise HTTPException(status_code=422, detail=self._batch_failures(problems))

        removed = self.title_content_repository.delete_many(parent_title_id, wanted)
        return TitleContentBatchResult(count=removed, items=[])

    @translate_repository_errors(not_found_message="Title Content not found")
    def move_many(
        self, destination_title_id: int, title_content_ids: Sequence[int]
    ) -> TitleContentBatchResult:
        """Move several entries under one destination, all-or-nothing (#179).

        The bulk drag: multi-select in one list, drop into another. One transaction, one
        set of parent locks, and the same guards a single move applies (#178).

        **One destination is what keeps this tractable.** A batch that could send each
        item somewhere different would need whole-batch reachability -- moving A under B
        and B under A is a cycle neither item creates alone. Every item here lands under
        the same parent, so as in :meth:`attach_many` every new edge *leaves* that
        parent and the per-item guard is complete. That is a reason to keep this shape,
        not an accident of it.

        Args:
            destination_title_id: The parent the entries should belong to afterwards.
            title_content_ids: The rows to move.

        Returns:
            TitleContentBatchResult: The moved rows, in the order given.

        Raises:
            HTTPException: 404 if the destination does not exist; 422 naming every id
                that does not exist or whose target another item already moves under the
                destination; 409 naming every item that would close a cycle or give a
                title a second home.
        """
        if not self.title_repository.exists(destination_title_id):
            raise HTTPException(status_code=404, detail="Title not found")

        wanted = list(dict.fromkeys(title_content_ids))
        invalid: list[tuple[int, str, str]] = []
        conflicts: list[tuple[int, str, str]] = []
        found: list[tuple[int, TitleContentRead]] = []

        for index, edge_id in enumerate(wanted):
            edge = self.title_content_repository.get(edge_id)
            if edge is None:
                invalid.append((index, f"Title content {edge_id} does not exist.", "not_found"))
                continue
            found.append((index, edge))

        seen: dict[tuple[str, int], int] = {}
        for index, edge in found:
            target = (
                ("title", edge.child_title_id)
                if edge.child_title_id is not None
                else ("asset", edge.asset_id)
            )
            first = seen.get(target)  # type: ignore[arg-type]
            if first is not None:
                invalid.append(
                    (
                        index,
                        f"This batch already moves {target[0]} {target[1]} under the "
                        f"destination, as item {first}.",
                        "duplicate_in_batch",
                    )
                )
                continue
            seen[target] = index  # type: ignore[index]

            conflicts.extend(
                self._guard_problems(
                    destination_title_id,
                    index,
                    edge.child_title_id,
                    edge.membership,
                    excluding_edge_id=edge.id,
                )
            )

        if invalid:
            raise HTTPException(status_code=422, detail=self._batch_failures(invalid))
        if conflicts:
            raise HTTPException(status_code=409, detail=self._batch_failures(conflicts))

        moved = self.title_content_repository.move_many(destination_title_id, wanted)
        return TitleContentBatchResult(count=len(moved), items=moved)

    def move_content(
        self,
        destination_title_id: int,
        title_content_id: int,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        anchor: str | None = None,
    ) -> TitleContentRead:
        """Move a containment edge under a different parent, in one transaction.

        The primary gesture of a drag-and-drop tree. Done as detach-then-attach it is
        two requests with no transaction between them: a failure in the gap leaves the
        item attached to nothing, and the front end cannot tell that from a successful
        move it failed to observe.

        Atomic because the repository does the whole thing in one unit of work -- it
        takes both parents' lists with a single ``FOR UPDATE`` statement, so the rows are
        always locked in one deterministic order and two opposing moves cannot deadlock,
        and ``uq_parent_position`` is ``DEFERRABLE INITIALLY DEFERRED``, so the
        intermediate states of renumbering two lists are never checked. That machinery
        already existed; what did not exist was a route that reached it safely.

        **``destination_title_id`` is the destination**, which is the one place in the
        contents routes where the path's title is not the edge's current parent. That is
        deliberate and it is why this is a separate route rather than a flag on another:
        after #185 every other write reads the segment as "where this edge lives", so a
        route that reads it as "where this edge is going" has to be impossible to
        confuse with them. The edge's current parent is not named in the request at all
        -- an edge id identifies exactly one row, and requiring the caller to restate
        where it already is would be a precondition it can only get wrong.

        Position under the new parent is **explicitly reassigned**, never carried over.
        A position means "the nth entry in this list", so a row arriving from elsewhere
        has no meaningful claim to its old number; absent an anchor it appends, which is
        what dropping onto a parent rather than between two siblings means.

        Idempotent. Moving an edge to the parent it is already under repositions it
        within that parent and is otherwise a no-op, so a client that retries after a
        dropped connection converges rather than compounding.

        Args:
            destination_title_id: The title the edge should belong to afterwards.
            title_content_id: The containment row to move.
            before_id: Place it immediately before this row under the destination.
            after_id: Place it immediately after this row under the destination.
            anchor: ``"start"`` or ``"end"``.

        Returns:
            TitleContentRead: The moved row, with its new parent and position.

        Raises:
            HTTPException: 404 if the destination title or the edge does not exist;
                409 with ``containment_cycle``, ``intrinsic_parent_conflict`` or
                ``position_conflict`` in ``detail[0]["type"]``; 423 if the database is
                read-only.
        """
        if not self.title_repository.exists(destination_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        edge = self.title_content_repository.get(title_content_id)
        if edge is None:
            raise HTTPException(status_code=404, detail="Title Content not found")

        # Both guards are the ones `insert_positioned` applies, because a move reaches
        # exactly the states an insert can: this is the same edge arriving under a new
        # parent. Skipping them is what made the old cross-parent path a defect (#185).
        #
        # Recoded rather than reworded. The guards own the explanation -- which titles,
        # which existing row -- and this only attaches the discriminator a UI needs, so
        # the two cannot drift apart. The edge is excluded from the intrinsic check so
        # that re-issuing a move does not collide with the row it already wrote.
        with self._as_conflict(_CYCLE_CODE):
            self._reject_cycle(destination_title_id, edge.child_title_id)
        with self._as_conflict(_INTRINSIC_PARENT_CODE):
            self._reject_second_intrinsic_parent(
                edge.child_title_id,
                edge.membership,
                excluding_edge_id=title_content_id,
            )

        try:
            moved = self.title_content_repository.reorder(
                destination_title_id,
                title_content_id,
                before_id=before_id,
                after_id=after_id,
                anchor=anchor,
            )
        except UniqueViolation as e:
            # Reaching this means the renumber produced a list that still collides at
            # commit, which the repository's own arithmetic should make impossible --
            # so in practice it is a concurrent writer, or rows that predate the
            # service. Distinguishable from a cycle because the two call for opposite
            # responses: a cycle is a refusal, a taken position is worth retrying.
            raise HTTPException(
                status_code=409,
                detail=conflict_detail(
                    "That position under the destination title is already taken.",
                    _POSITION_CODE,
                ),
            ) from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e
        if moved is None:
            raise HTTPException(status_code=404, detail="Title Content not found")
        return moved

    @contextmanager
    def _as_conflict(self, code: str) -> Iterator[None]:
        """Re-raise a guard's 409 carrying ``code``, leaving its message intact.

        Args:
            code: The discriminator to put in ``detail[0]["type"]``.

        Yields:
            None: The guard runs inside.
        """
        try:
            yield
        except HTTPException as e:
            if e.status_code != 409 or not isinstance(e.detail, str):
                raise
            raise HTTPException(status_code=409, detail=conflict_detail(e.detail, code)) from e

    def unlink_content(self, parent_title_id: int, title_content_id: int) -> None:
        # The edge has to be under this parent. Before #185 only the title's existence
        # was checked and the row was then deleted by id alone, so any title in the
        # library served as a URL for removing any containment edge in it.
        self._edge_under(parent_title_id, title_content_id)
        try:
            self.title_content_repository.delete_title_content(title_content_id)
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=500, detail="Internal server error during content unlinking"
            ) from e
