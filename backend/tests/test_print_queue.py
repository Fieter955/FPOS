import hashlib
import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import models
from app.auth import get_local_datetime
from app.database import Base
from app.routes.print_queue import (
    AgentClaim,
    AgentResult,
    claim_jobs,
    enqueue_print_job,
    report_job_result,
)


def request_with_token(token):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/print/agent/claim",
        "headers": [(b"x-printer-token", token.encode("ascii"))],
    })


class PrintQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.Session()
        self.db.add_all([
            models.Branch(id=1, code="A", name="Cabang A"),
            models.Branch(id=2, code="B", name="Cabang B"),
        ])
        self.db.flush()
        token = "token-cabang-a"
        self.token = token
        self.db.add(models.PrinterAgent(
            branch_id=1,
            name="Printer A",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            token_last4=token[-4:],
        ))
        enqueue_print_job(self.db, branch_id=1, content="JOB A", document_type="sale")
        enqueue_print_job(self.db, branch_id=2, content="JOB B", document_type="sale")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_claim_is_authenticated_and_scoped_to_agent_branch(self):
        jobs = claim_jobs(AgentClaim(limit=5), request_with_token(self.token), self.db)

        self.assertEqual([job["content"] for job in jobs], ["JOB A"])
        self.assertEqual(self.db.query(models.PrintJob).filter_by(branch_id=1).one().status, "processing")
        self.assertEqual(self.db.query(models.PrintJob).filter_by(branch_id=2).one().status, "pending")

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            claim_jobs(AgentClaim(limit=1), request_with_token("wrong-token"), self.db)
        self.assertEqual(error.exception.status_code, 401)

    def test_result_completion_is_idempotent(self):
        job = claim_jobs(AgentClaim(limit=1), request_with_token(self.token), self.db)[0]

        first = report_job_result(
            job["id"], AgentResult(success=True), request_with_token(self.token), self.db
        )
        second = report_job_result(
            job["id"], AgentResult(success=True), request_with_token(self.token), self.db
        )

        self.assertEqual(first["status"], "done")
        self.assertTrue(second["idempotent"])

    def test_failure_retries_are_bounded(self):
        statuses = []
        for _ in range(3):
            job = claim_jobs(AgentClaim(limit=1), request_with_token(self.token), self.db)[0]
            result = report_job_result(
                job["id"],
                AgentResult(success=False, error="printer offline"),
                request_with_token(self.token),
                self.db,
            )
            statuses.append(result["status"])

        self.assertEqual(statuses, ["pending", "pending", "failed"])
        self.assertEqual(claim_jobs(AgentClaim(limit=1), request_with_token(self.token), self.db), [])

    def test_agent_cannot_report_another_branch_job(self):
        other_job = self.db.query(models.PrintJob).filter_by(branch_id=2).one()
        other_job.status = "processing"
        self.db.commit()

        with self.assertRaises(HTTPException) as error:
            report_job_result(
                other_job.id,
                AgentResult(success=True),
                request_with_token(self.token),
                self.db,
            )
        self.assertEqual(error.exception.status_code, 404)

    def test_expired_lease_is_reclaimed_without_touching_other_branch(self):
        job_a = self.db.query(models.PrintJob).filter_by(branch_id=1).one()
        job_a.status = "processing"
        job_a.attempt_count = 1
        job_a.lease_until = get_local_datetime() - timedelta(seconds=1)
        self.db.commit()

        jobs = claim_jobs(AgentClaim(limit=1), request_with_token(self.token), self.db)

        self.assertEqual(jobs[0]["id"], job_a.id)
        self.assertEqual(jobs[0]["attempt_count"], 2)
        self.assertEqual(self.db.query(models.PrintJob).filter_by(branch_id=2).one().status, "pending")


if __name__ == "__main__":
    unittest.main()
