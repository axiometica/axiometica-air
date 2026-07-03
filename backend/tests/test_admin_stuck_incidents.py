"""
Regression test for the /admin/workers stuck-incidents check.

The query used to compare the lifecycle_state enum column against a plain
text array without a cast (UndefinedFunction: lifecyclestate = text), and
separately filtered on two state names ("triaging", "verification") that
don't exist in LifecycleState at all — so the check silently never matched
anything even once the cast was fixed.
"""
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from agentic_os.db.models import WorkflowStateModel
from agentic_os.core.models import WorkflowType, LifecycleState


def _make_workflow(session: Session, lifecycle_state: str, updated_at: datetime) -> WorkflowStateModel:
    workflow = WorkflowStateModel(
        workflow_id=uuid4(),
        workflow_type=WorkflowType.INCIDENT.value,
        lifecycle_state=lifecycle_state,
        severity="high",
        title="Test Incident",
        context={"alert_payload": {"type": "high_cpu"}},
    )
    session.add(workflow)
    session.commit()
    # updated_at has an onupdate=utcnow default — overwrite it directly post-insert
    session.query(WorkflowStateModel).filter_by(workflow_id=workflow.workflow_id).update(
        {"updated_at": updated_at}
    )
    session.commit()
    return workflow


class TestStuckIncidentsCheck:
    def test_workers_endpoint_does_not_error(self, client_authenticated):
        """The enum/text comparison no longer raises UndefinedFunction."""
        response = client_authenticated.get("/api/admin/workers")
        assert response.status_code == 200
        body = response.json()
        assert "stuck_incidents" in body
        assert isinstance(body["stuck_incidents"], list)

    def test_detects_incident_stuck_in_progress(self, client_authenticated, db: Session):
        """A real LifecycleState value, stuck >15 min, is actually detected."""
        stale = datetime.utcnow() - timedelta(minutes=20)
        workflow = _make_workflow(db, LifecycleState.IN_PROGRESS.value, stale)

        response = client_authenticated.get("/api/admin/workers")
        assert response.status_code == 200
        stuck_ids = [s["workflow_id"] for s in response.json()["stuck_incidents"]]
        assert str(workflow.workflow_id) in stuck_ids

    def test_ignores_recent_in_progress_incident(self, client_authenticated, db: Session):
        """Updated within the last 15 minutes — not stuck yet."""
        recent = datetime.utcnow() - timedelta(minutes=2)
        workflow = _make_workflow(db, LifecycleState.IN_PROGRESS.value, recent)

        response = client_authenticated.get("/api/admin/workers")
        assert response.status_code == 200
        stuck_ids = [s["workflow_id"] for s in response.json()["stuck_incidents"]]
        assert str(workflow.workflow_id) not in stuck_ids

    def test_ignores_resolved_incident(self, client_authenticated, db: Session):
        """A terminal state, even if stale, was never 'stuck' in a pipeline sense."""
        stale = datetime.utcnow() - timedelta(minutes=30)
        workflow = _make_workflow(db, LifecycleState.RESOLVED.value, stale)

        response = client_authenticated.get("/api/admin/workers")
        assert response.status_code == 200
        stuck_ids = [s["workflow_id"] for s in response.json()["stuck_incidents"]]
        assert str(workflow.workflow_id) not in stuck_ids
