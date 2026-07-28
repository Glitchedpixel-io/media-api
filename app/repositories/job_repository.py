# app/repositories/job_repository.py
from datetime import UTC, datetime

from app.models import JobORM
from app.repositories.protocols import JobRepository
from app.schemas import JobRead

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError


class SQLAlchemyJobRepository(SQLAlchemyBaseRepository, JobRepository):
    def create(self, job_key: str) -> JobRead:
        """
        Creates a new job entry in the database and returns the created job object.

        This method interacts with the database to add a new job record, commits the
        operation, and refreshes the object's state to ensure it represents the
        current database state. The created job is then returned as an instance of
        the `JobRead` model.

        :param job_key: The unique identifier for the job.
        :type job_key: str
        :return: The created JobRead object representing the new job entry.
        :rtype: JobRead
        """
        orm = JobORM(job_key=job_key)
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return JobRead.model_validate(orm)

    def get(self, job_key: str) -> JobRead | None:
        """
        Retrieve a job instance as a read model from the database using its key.

        This method attempts to fetch a job record from the database using the
        specified job key. If a record is found, it converts the database ORM
        object to the JobRead model. If no record is found, the method returns
        None.

        :param job_key: The unique identifier key for the job to be retrieved.
        :type job_key: str
        :return: A JobRead model if the job exists; otherwise None.
        :rtype: JobRead | None
        """
        orm = self.db.get(JobORM, job_key)
        return JobRead.model_validate(orm) if orm else None

    def exists(self, job_key: str) -> bool:
        """
        Check if a job with the specified job_key exists in the database.

        :param job_key: Identifier of the job to check.
        :type job_key: str
        :return: True if the job exists, False otherwise.
        :rtype: bool
        """
        return self.db.get(JobORM, job_key) is not None

    def heartbeat(self, job_key: str) -> JobRead:
        """
        Update the heartbeat timestamp for a job to indicate that it is still
        active and ensure that the job is not already completed. If the job is
        not found, raises a `NotFoundError`.

        :param job_key: The unique identifier of the job.
        :type job_key: str
        :return: A read model representation of the updated job.
        :rtype: JobRead
        :raises NotFoundError: If the job with the specified key is not found.
        :raises ValueError: If the job has already been completed.
        """
        orm = self.db.get(JobORM, job_key)
        if not orm:
            raise NotFoundError
        if orm.completed_at:
            raise ValueError("Job already completed")
        orm.heartbeat_at = datetime.now(UTC)
        self._safe_commit()
        self.db.refresh(orm)
        return JobRead.model_validate(orm, from_attributes=True)

    def mark_complete(self, job_key: str) -> JobRead:
        """
        Marks a job as completed based on the provided job key. Updates the job's
        completion timestamp if it has not already been completed, persists the
        changes to the database, and returns the updated job record.

        :param job_key: The unique key identifying the job to be marked as complete.
        :type job_key: str
        :return: The updated job object after being marked as complete.
        :rtype: JobRead
        :raises NotFoundError: If the job with the given key is not found in the database.
        :raises ValueError: If the job is already marked as completed.
        """
        orm = self.db.get(JobORM, job_key)
        if not orm:
            raise NotFoundError
        if orm.completed_at:
            raise ValueError("Job already completed")
        orm.completed_at = datetime.now(UTC)
        self._safe_commit()
        self.db.refresh(orm)
        return JobRead.model_validate(orm, from_attributes=True)
