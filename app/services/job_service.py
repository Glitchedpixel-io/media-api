# app/services/job_service.py
from fastapi import HTTPException

from app.repositories import JobRepository
from app.schemas import JobRead
from app.services.errors import translate_repository_errors


class JobService:
    def __init__(
        self,
        job_repository: JobRepository,
    ) -> None:
        self.repo = job_repository

    @translate_repository_errors
    def create_job(self, job_key: str) -> JobRead:
        return self.repo.create(job_key=job_key)

    @translate_repository_errors(not_found_message="Job not found")
    def mark_heartbeat(self, job_key: str) -> JobRead:
        try:
            return self.repo.heartbeat(job_key=job_key)
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail="Heartbeat rejected as job was already marked as complete"
            ) from e

    @translate_repository_errors(not_found_message="Job not found")
    def mark_completed(self, job_key: str) -> JobRead:
        try:
            return self.repo.mark_complete(job_key=job_key)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Job was already marked as complete") from e
