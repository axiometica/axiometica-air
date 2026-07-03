"""
Load and manage workflow definitions from YAML files.
"""

import yaml
from pathlib import Path
from typing import Dict, Optional

from agentic_os.core.models import (
    WorkflowDefinition, WorkflowStep, WorkflowType
)


class WorkflowDefinitionLoader:
    """Load workflow definitions from YAML files"""

    def __init__(self, definitions_dir: str = "workflows"):
        self.definitions_dir = Path(definitions_dir)
        self.cache: Dict[WorkflowType, WorkflowDefinition] = {}

    def load_definition(self, workflow_type: WorkflowType, version: str = "v1") -> Optional[WorkflowDefinition]:
        """
        Load workflow definition from YAML file.

        Args:
            workflow_type: Type of workflow (incident, change, problem, request)
            version: Version suffix (default: v1)

        Returns:
            WorkflowDefinition, or None if not found
        """
        # Check cache
        if workflow_type in self.cache:
            return self.cache[workflow_type]

        # Determine file path
        filename = f"{workflow_type.value}_{version}.yaml"
        file_path = self.definitions_dir / filename

        if not file_path.exists():
            print(f"✗ Workflow definition not found: {file_path}")
            return None

        # Load YAML
        try:
            with open(file_path, 'r') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(f"✗ Error loading {file_path}: {e}")
            return None

        # Parse definition
        definition = self._parse_definition(workflow_type, data)
        if definition:
            self.cache[workflow_type] = definition

        return definition

    @staticmethod
    def _parse_definition(workflow_type: WorkflowType, data: dict) -> Optional[WorkflowDefinition]:
        """Parse YAML data into WorkflowDefinition"""

        try:
            definition = WorkflowDefinition(
                workflow_type=workflow_type,
                version=data.get('version', '1.0'),
                start_step=data.get('start_step'),
                steps={},
                end_steps=data.get('end_steps', [])
            )

            # Parse steps
            for step_id, step_data in data.get('steps', {}).items():
                step = WorkflowStep(
                    step_id=step_id,
                    step_type=step_data.get('type', 'agent'),
                    name=step_data.get('name', step_id),
                    handler=step_data.get('agent') or step_data.get('handler'),
                    next_steps=step_data.get('next_steps', {}),
                    timeout_seconds=step_data.get('timeout_seconds'),
                    retry_count=step_data.get('retry_count', 0),
                    fallback_step=step_data.get('fallback_step'),
                )
                definition.steps[step_id] = step

            return definition

        except Exception as e:
            print(f"✗ Error parsing workflow definition: {e}")
            return None


def load_all_definitions(definitions_dir: str = "workflows") -> Dict[WorkflowType, WorkflowDefinition]:
    """
    Load all workflow definitions from directory.

    Args:
        definitions_dir: Directory containing YAML files

    Returns:
        Dictionary mapping workflow types to definitions
    """
    loader = WorkflowDefinitionLoader(definitions_dir)
    definitions = {}

    for workflow_type in WorkflowType:
        definition = loader.load_definition(workflow_type, "v1")
        if definition:
            definitions[workflow_type] = definition
            print(f"✓ Loaded {workflow_type.value} workflow")

    return definitions
