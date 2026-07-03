"""
Integration tests for EnumerationService.
Tests incident number generation, formatting, and database persistence.
"""

import pytest
from uuid import uuid4
from sqlalchemy.orm import Session

from agentic_os.services.enumeration_service import EnumerationService
from agentic_os.db.models import WorkflowStateModel
from agentic_os.core.models import WorkflowType, LifecycleState


@pytest.fixture
def session(db):
    """Provide a database session for tests."""
    return db


@pytest.fixture
def sample_workflow(session: Session):
    """Create a sample workflow for enumeration testing."""
    workflow = WorkflowStateModel(
        workflow_id=uuid4(),
        workflow_type=WorkflowType.INCIDENT.value,
        lifecycle_state=LifecycleState.OPEN.value,
        severity="high",
        title="Test Incident",
        context={"alert_payload": {"type": "high_cpu"}},
    )
    session.add(workflow)
    session.commit()
    return workflow


class TestEnumerationServiceBasics:
    """Test basic enumeration service functionality."""

    def test_generate_first_incident_number(self, session: Session):
        """Test generating the first incident number (INC0001)."""
        workflow_id = str(uuid4())

        # Create workflow
        workflow = WorkflowStateModel(
            workflow_id=workflow_id,
            workflow_type=WorkflowType.INCIDENT.value,
            lifecycle_state=LifecycleState.OPEN.value,
            context={},
        )
        session.add(workflow)
        session.commit()

        # Generate incident number
        incident_str = EnumerationService.generate_incident_number(session, workflow_id)

        # Verify format
        assert incident_str == "INC0001", f"Expected INC0001, got {incident_str}"
        assert incident_str.startswith("INC"), "Incident number should start with INC"

    def test_generate_sequential_incident_numbers(self, session: Session):
        """Test that incident numbers are sequential."""
        incident_strs = []

        for i in range(3):
            workflow_id = str(uuid4())
            workflow = WorkflowStateModel(
                workflow_id=workflow_id,
                workflow_type=WorkflowType.INCIDENT.value,
                lifecycle_state=LifecycleState.OPEN.value,
                context={},
            )
            session.add(workflow)
            session.commit()

            incident_str = EnumerationService.generate_incident_number(session, workflow_id)
            incident_strs.append(incident_str)

        # Verify sequence
        assert incident_strs == ["INC0001", "INC0002", "INC0003"], \
            f"Expected sequential numbers, got {incident_strs}"

    def test_incident_number_persisted_to_database(self, session: Session, sample_workflow):
        """Test that incident number is saved to database."""
        workflow_id = str(sample_workflow.workflow_id)

        # Generate incident number
        incident_str = EnumerationService.generate_incident_number(session, workflow_id)

        # Query workflow from database
        workflow = session.query(WorkflowStateModel).filter(
            WorkflowStateModel.workflow_id == sample_workflow.workflow_id
        ).first()

        assert workflow is not None, "Workflow not found in database"
        assert workflow.incident_number_str == incident_str, \
            f"Expected {incident_str}, got {workflow.incident_number_str}"
        assert workflow.incident_number == 1, "Expected numeric incident_number to be 1"

    def test_incident_number_format_padding(self, session: Session):
        """Test that incident numbers are zero-padded correctly."""
        # Generate 10+ incident numbers to test padding
        for i in range(15):
            workflow_id = str(uuid4())
            workflow = WorkflowStateModel(
                workflow_id=workflow_id,
                workflow_type=WorkflowType.INCIDENT.value,
                lifecycle_state=LifecycleState.OPEN.value,
                context={},
            )
            session.add(workflow)
            session.commit()

            incident_str = EnumerationService.generate_incident_number(session, workflow_id)

            # Verify zero-padding
            expected = f"INC{i+1:04d}"
            assert incident_str == expected, f"Expected {expected}, got {incident_str}"


class TestEnumerationServiceGetters:
    """Test getter methods of EnumerationService."""

    def test_get_next_incident_number(self, session: Session):
        """Test peeking at next incident number without consuming sequence."""
        # Generate first incident
        workflow_id = str(uuid4())
        workflow = WorkflowStateModel(
            workflow_id=workflow_id,
            workflow_type=WorkflowType.INCIDENT.value,
            lifecycle_state=LifecycleState.OPEN.value,
            context={},
        )
        session.add(workflow)
        session.commit()

        EnumerationService.generate_incident_number(session, workflow_id)

        # Peek at next number
        next_num = EnumerationService.get_next_incident_number(session)
        assert next_num == 2, f"Expected next number to be 2, got {next_num}"

    def test_get_incident_number_str(self, session: Session, sample_workflow):
        """Test retrieving incident number string for a workflow."""
        workflow_id = str(sample_workflow.workflow_id)

        # Generate incident number
        incident_str = EnumerationService.generate_incident_number(session, workflow_id)

        # Retrieve it
        retrieved = EnumerationService.get_incident_number_str(session, workflow_id)
        assert retrieved == incident_str, f"Expected {incident_str}, got {retrieved}"

    def test_get_incident_number_str_not_found(self, session: Session):
        """Test retrieving incident number for non-existent workflow."""
        fake_workflow_id = str(uuid4())
        retrieved = EnumerationService.get_incident_number_str(session, fake_workflow_id)
        assert retrieved == "", "Expected empty string for non-existent workflow"


class TestEnumerationServiceEdgeCases:
    """Test edge cases and error conditions."""

    def test_generate_number_for_nonexistent_workflow(self, session: Session):
        """Test generating number for workflow that doesn't exist."""
        # Should still generate the number even if workflow not found
        fake_workflow_id = str(uuid4())
        incident_str = EnumerationService.generate_incident_number(session, fake_workflow_id)

        assert incident_str.startswith("INC"), "Should still generate number"

    def test_unique_constraint_on_incident_number(self, session: Session):
        """Test that incident numbers are unique."""
        incident_strs = set()

        for i in range(5):
            workflow_id = str(uuid4())
            workflow = WorkflowStateModel(
                workflow_id=workflow_id,
                workflow_type=WorkflowType.INCIDENT.value,
                lifecycle_state=LifecycleState.OPEN.value,
                context={},
            )
            session.add(workflow)
            session.commit()

            incident_str = EnumerationService.generate_incident_number(session, workflow_id)
            incident_strs.add(incident_str)

        # All should be unique
        assert len(incident_strs) == 5, f"Expected 5 unique numbers, got {len(incident_strs)}"
