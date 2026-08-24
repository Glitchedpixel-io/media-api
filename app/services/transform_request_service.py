# app/services/transform_request_service.py
from dataclasses import asdict

import logfire
from fastapi import HTTPException

from app.orchestration.registry import ProviderRegistry
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    RecordCannotBeChanged,
)
from app.repositories.protocols import (
    MediaRepository,
    TransformRequestRepository,
)
from app.runners.protocols import JobDispatch
from app.schemas import (
    PaginatedResponse,
    TransformRequestCreateInternal,
    TransformRequestCreatePublic,
    TransformRequestListParams,
    TransformRequestPatchPublic,
    TransformRequestRead,
    TransformRequestReadExpanded,
    TransformRequestUpdateInternal,
)
from app.services.errors import domain_error_detail, translate_repository_errors


class TransformRequestService:
    def __init__(
        self,
        transform_request_repository: TransformRequestRepository,
        media_repository: MediaRepository,
        provider_registry: ProviderRegistry,
    ) -> None:
        self.repo = transform_request_repository
        self.media_repo = media_repository
        self._providers = provider_registry

    def get_transform_request(self, request_id: int) -> TransformRequestRead:
        transform_request = self.repo.get(request_id)
        if transform_request is None:
            raise HTTPException(status_code=404, detail="Transform request not found")
        return transform_request

    def get_transform_request_logs(self, request_id: int) -> list[dict]:
        transform_request = self.repo.get(request_id)
        if transform_request is None:
            raise HTTPException(status_code=404, detail="Transform request not found")
        if not transform_request.actioned:
            raise HTTPException(
                status_code=409,
                detail="Cannot retrieve logs for a transform request that has not been actioned",
            )
        if not transform_request.external_job_id:
            raise HTTPException(
                status_code=409,
                detail="Cannot retrieve logs for a transform request that has no external job id",
            )
        entries = self._providers.fetch_logs(
            transform_request.transform_type, transform_request.external_job_id
        )
        return [asdict(entry) for entry in entries]

    def retry_transform_request(self, request_id: int) -> TransformRequestRead:
        parent_request = self.repo.get(request_id)
        if not parent_request:
            raise HTTPException(status_code=404, detail="Transform request not found")
        if not parent_request.actioned:
            raise HTTPException(
                status_code=409,
                detail="Cannot retry a transform request that has not been actioned",
            )
        return self._create_request(
            TransformRequestCreateInternal(
                actioned=False,
                processed_at=None,
                outcome=None,
                worker=None,
                worker_notes=None,
                parent_transform_request_id=request_id,
                **parent_request.model_dump(
                    exclude={
                        "actioned",
                        "processed_at",
                        "outcome",
                        "worker",
                        "worker_notes",
                        "id",
                        "created_at",
                        "parent_transform_request_id",
                        "first_heartbeat",
                        "last_heartbeat",
                    }
                ),
            )
        )

    def get_transform_requests(
        self,
        params: TransformRequestListParams,
    ) -> PaginatedResponse[TransformRequestReadExpanded]:
        return self.repo.list_paged(params)

    def mark_heartbeat(self, request_id: int) -> None:
        try:
            self.repo.mark_heartbeat(request_id)
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail="Transform Request not found") from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except RecordCannotBeChanged as e:
            raise HTTPException(
                status_code=400, detail="Transform Request cannot receive heartbeats"
            ) from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

    @translate_repository_errors(not_found_message="Transform Request not found")
    def update_transform_request(
        self,
        request_id: int,
        update: TransformRequestPatchPublic,  # type: ignore
        exclude_none: bool = True,
    ) -> TransformRequestRead:
        return self.repo.update(
            request_id,
            TransformRequestUpdateInternal(
                **update.model_dump(exclude_none=exclude_none)  # type: ignore
            ),
        )

    def get_asset_transform_requests(self, asset_id: int) -> list[TransformRequestRead]:
        asset = self.media_repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.get_asset_transform_requests(asset_id)

    def create_linked_request(
        self, request_id: int, request: TransformRequestCreatePublic
    ) -> TransformRequestRead:
        parent_request = self.get_transform_request(request_id)
        if not parent_request:
            raise HTTPException(
                status_code=404, detail="Transform request given as parent not found"
            )
        return self._create_request(
            TransformRequestCreateInternal(
                asset_id=parent_request.asset_id,
                parent_transform_request_id=request_id,
                **request.model_dump(),
            )
        )

    def create_asset_transform_request(
        self, asset_id: int, request: TransformRequestCreatePublic
    ) -> TransformRequestRead:
        return self._create_request(
            TransformRequestCreateInternal(asset_id=asset_id, **request.model_dump())
        )

    def _dispatch(self, request: TransformRequestRead) -> None:
        if not isinstance(request, TransformRequestRead):
            return
        with logfire.span("dispatch_job") as span:
            try:
                self._providers.dispatch(
                    JobDispatch(
                        job_id=request.id,
                        job_type=request.transform_type,
                        parameters=request.parameters,
                    )
                )
            except Exception as e:
                span.record_exception(e)

    @translate_repository_errors
    def _create_request(self, request: TransformRequestCreateInternal) -> TransformRequestRead:
        req = self.repo.create(request)
        self._dispatch(req)
        return req

    def claim_next_request(
        self, transform_type: str, worker: str, external_job_id: str | None
    ) -> TransformRequestReadExpanded:
        try:
            return self.repo.claim_next(
                transform_type=transform_type, worker=worker, external_job_id=external_job_id
            )
        except NotFoundError:
            raise HTTPException(status_code=204, detail="No tasks available") from None
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except HTTPException:
            raise
        except Exception as e:
            # Bubble up as 500 to API layer
            raise HTTPException(
                status_code=500,
                detail="Internal server error while claiming next task",
            ) from e
