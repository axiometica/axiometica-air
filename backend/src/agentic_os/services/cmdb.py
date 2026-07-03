"""
CMDB Service - Query Neo4j for configuration items and dependencies.
Provides agents with access to infrastructure topology and relationships.
"""

import os

from neo4j import GraphDatabase
from neo4j.time import Date, DateTime, Duration, Time
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def _to_json_safe(value: Any) -> Any:
    """Recursively convert neo4j driver temporal types to JSON-serializable values.

    Any property set via Cypher's datetime()/date()/time()/duration() (e.g.
    mark_ci_degraded's started_at, mark_ci_recovered's resolved_at) comes back
    from the driver as neo4j.time.DateTime/Date/Time/Duration — none of which
    json.dumps can handle. Left unconverted, this crashes persistence the
    moment such a value rides along in a workflow's context dict.
    """
    if isinstance(value, (DateTime, Date, Time)):
        return value.to_native().isoformat()
    if isinstance(value, Duration):
        return str(value)
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return value


def _sanitize_record(record: Any) -> Dict[str, Any]:
    """dict(record) with every value passed through _to_json_safe."""
    return {k: _to_json_safe(v) for k, v in dict(record).items()}


class CMDBService:
    """Query Neo4j CMDB for CI relationships and dependencies"""

    def __init__(self, uri: str = None, user: str = None, password: str = None):
        """Initialize connection to Neo4j — credentials default to environment variables."""
        self._uri = uri or os.getenv("NEO4J_URI", os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687"))
        self._user = user or os.getenv("NEO4J_USER", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD")
        try:
            self.driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
            self.driver.verify_connectivity()
            logger.info("✓ Connected to Neo4j CMDB")
        except Exception as e:
            logger.warning(f"⚠ Could not connect to Neo4j: {e}")
            self.driver = None

    def close(self):
        """Close database connection"""
        if self.driver:
            self.driver.close()

    def get_resource_info(self, resource_name: str) -> Optional[Dict[str, Any]]:
        """
        Get CI (Configuration Item) info for a resource.

        Args:
            resource_name: Name of the resource (e.g., "payment-service")

        Returns:
            Dictionary with resource properties or None if not found
        """
        # Lazy initialization: reconnect if driver is not initialized
        if not self.driver:
            logger.info(f"[CMDB] Driver not initialized, attempting lazy reconnection...")
            try:
                self.driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
                self.driver.verify_connectivity()
                logger.info(f"[CMDB] ✓ Successfully reconnected to Neo4j")
            except Exception as e:
                logger.error(f"[CMDB] Failed to reconnect: {e}")
                return None

        try:
            # Verify connection is still alive
            self.driver.verify_connectivity()

            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})
                    OPTIONAL MATCH (ci)-[:RUNS_ON|HOSTED_ON|PART_OF|DEPENDS_ON*1..3]->(svc:ConfigurationItem)
                    WHERE svc.environment IS NOT NULL AND svc.environment <> ci.environment
                    RETURN ci.name as name, ci.type as type, ci.status as status,
                           ci.owner as owner,
                           COALESCE(ci.environment, svc.environment) as environment,
                           ci.platform as platform,
                           ci.business_criticality as business_criticality,
                           ci.ci_tier as ci_tier,
                           ci.user_count as user_count,
                           ci.is_spof as is_spof,
                           ci.sla_percent as sla_percent,
                           ci.failover_available as failover_available,
                           ci.compliance_scope as compliance_scope,
                           ci.description as description,
                           ci.support_group as support_group,
                           ci.assignment_group as assignment_group,
                           ci.managed_by as managed_by,
                           ci.data_center as data_center
                    LIMIT 1
                    """,
                    {"name": resource_name}
                )
                record = result.single()
                if record is None:
                    logger.debug(f"CMDB query for '{resource_name}': not found")
                    return None
                data = _sanitize_record(record)
                data["ci_found"] = True
                logger.debug(f"CMDB query for '{resource_name}': {data}")
                return data
        except Exception as e:
            logger.error(f"Error querying resource {resource_name}: {e}")
            # Try to reconnect on failure
            try:
                logger.info(f"Attempting to reconnect to Neo4j...")
                self.driver.close()
                self.driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
                self.driver.verify_connectivity()
                logger.info(f"Reconnected to Neo4j successfully")
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect to Neo4j: {reconnect_error}")
            return None

    def get_dependencies(self, resource_name: str, depth: int = 2) -> List[Dict[str, Any]]:
        """
        Get upstream dependencies for a resource — both service-level and
        infrastructure-level.

        Traverses four relationship types so that shared host/platform CIs
        are visible to the storm agent's root-cause analysis:

          DEPENDS_ON*1..2  — application-layer service dependencies
                             (e.g. backend → postgres, worker → redis)
          HOSTED_ON        — logical host link created by neo4j_init seed
                             (e.g. agentic_os_backend → agenticplatform-host)
          RUNS_ON          — host link added by discovery_service at runtime
                             (e.g. agentic_os_backend → agenticplatform-host)
          PART_OF          — logical application or cluster membership
                             (e.g. agentic_os_backend → agentic-platform)

        HOSTED_ON and RUNS_ON represent the same physical relationship but
        are written by different code paths (seed vs. live discovery) — both
        must be traversed so the storm agent finds agenticplatform-host
        regardless of whether the graph was freshly seeded or built purely
        by discovery.
        """
        if not self.driver:
            return []

        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})
                    MATCH (ci)-[:DEPENDS_ON|HOSTED_ON|RUNS_ON|PART_OF*1..2]->(dep:ConfigurationItem)
                    WHERE dep.name <> $name
                    RETURN DISTINCT dep.name       AS name,
                                    dep.type       AS type,
                                    dep.business_criticality AS criticality,
                                    dep.status     AS status,
                                    dep.environment AS environment
                    """,
                    {"name": resource_name},
                )
                return [_sanitize_record(record) for record in result]
        except Exception as e:
            logger.error(f"Error querying dependencies for {resource_name}: {e}")
            return []

    def get_impacted_services(self, resource_name: str) -> List[Dict[str, Any]]:
        """
        Get services that depend on this resource (impact analysis).

        Args:
            resource_name: Name of the resource

        Returns:
            List of services that would be impacted
        """
        if not self.driver:
            return []

        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})<-[r:DEPENDS_ON]-(dependent:ConfigurationItem)
                    RETURN DISTINCT dependent.name as name, dependent.type as type,
                                     dependent.business_criticality as criticality,
                                     dependent.user_count as users,
                                     dependent.environment as environment
                    """,
                    {"name": resource_name}
                )
                return [_sanitize_record(record) for record in result]
        except Exception as e:
            logger.error(f"Error querying impacted services for {resource_name}: {e}")
            return []

    def get_historical_incidents(self, resource_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get recent incidents for a resource.

        Args:
            resource_name: Name of the resource
            limit: Max incidents to return

        Returns:
            List of recent incidents
        """
        if not self.driver:
            return []

        try:
            with self.driver.session() as session:
                result = session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})<-[r:AFFECTED_BY]-(inc:Incident)
                    RETURN inc.id as id, inc.severity as severity, inc.description as description,
                           inc.resolved_at as resolved_at, inc.root_cause as root_cause
                    ORDER BY inc.resolved_at DESC
                    LIMIT $limit
                    """,
                    {"name": resource_name, "limit": limit}
                )
                return [_sanitize_record(record) for record in result]
        except Exception as e:
            logger.error(f"Error querying incidents for {resource_name}: {e}")
            return []

    def mark_ci_degraded(
        self,
        resource_name: str,
        workflow_id: str,
        severity: str = "medium",
        anomaly_type: str = "unknown",
    ) -> bool:
        """
        Mark a CI as degraded in Neo4j and create/merge a live Incident node.

        Called by LibrarianAgent as soon as the affected resource is identified.
        Sets health_status = 'degraded' and creates an Incident node (status='active')
        linked via AFFECTED_BY so the CMDB graph shows the incident badge immediately.
        """
        if not self.driver:
            logger.warning("[CMDB] mark_ci_degraded skipped — Neo4j driver not available")
            return False
        try:
            with self.driver.session() as session:
                session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})
                    SET ci.health_status            = 'degraded',
                        ci.incident_health_override = 'degraded'
                    MERGE (inc:Incident {workflow_id: $workflow_id})
                    ON CREATE SET
                        inc.severity     = $severity,
                        inc.anomaly_type = $anomaly_type,
                        inc.started_at   = datetime(),
                        inc.status       = 'active'
                    ON MATCH SET
                        inc.status = 'active'
                    MERGE (inc)-[:AFFECTED_BY]->(ci)
                    """,
                    {
                        "name": resource_name,
                        "workflow_id": workflow_id,
                        "severity": severity,
                        "anomaly_type": anomaly_type,
                    },
                )
            logger.info(
                f"[CMDB] Marked '{resource_name}' degraded — "
                f"incident {workflow_id} ({severity} {anomaly_type})"
            )
            return True
        except Exception as e:
            logger.error(f"[CMDB] mark_ci_degraded failed for '{resource_name}': {e}")
            return False

    def mark_ci_recovered(
        self,
        resource_name: str,
        workflow_id: str,
        resolved: bool = True,
    ) -> bool:
        """
        Clear the active incident flag on a CI after remediation completes.

        Called by VerifierAgent once it knows the final outcome.
        - resolved=True  → health_status = 'healthy', incident status = 'resolved'
        - resolved=False → health_status = 'degraded', incident status = 'failed'
          (still degraded — needs human follow-up)
        """
        if not self.driver:
            logger.warning("[CMDB] mark_ci_recovered skipped — Neo4j driver not available")
            return False
        try:
            new_health = "healthy" if resolved else "degraded"
            new_status = "resolved" if resolved else "failed"
            with self.driver.session() as session:
                session.run(
                    """
                    MATCH (ci:ConfigurationItem {name: $name})
                    SET ci.health_status            = $health,
                        ci.incident_health_override = null
                    WITH ci
                    OPTIONAL MATCH (inc:Incident {workflow_id: $workflow_id})-[:AFFECTED_BY]->(ci)
                    WHERE inc IS NOT NULL
                    SET inc.status      = $status,
                        inc.resolved_at = datetime()
                    """,
                    {
                        "name": resource_name,
                        "workflow_id": workflow_id,
                        "health": new_health,
                        "status": new_status,
                    },
                )
            logger.info(
                f"[CMDB] Marked '{resource_name}' {new_health} — "
                f"incident {workflow_id} → {new_status}"
            )
            return True
        except Exception as e:
            logger.error(f"[CMDB] mark_ci_recovered failed for '{resource_name}': {e}")
            return False

    def get_playbooks(self, resource_type: str, incident_type: str = None) -> List[Dict[str, Any]]:
        """
        Get remediation playbooks for a resource type.

        Args:
            resource_type: Type of resource (e.g., "service", "database")
            incident_type: Type of incident (e.g., "high_cpu", "service_down")

        Returns:
            List of applicable playbooks
        """
        if not self.driver:
            return []

        try:
            with self.driver.session() as session:
                if incident_type:
                    result = session.run(
                        """
                        MATCH (pb:Playbook {applies_to: $resource_type})
                        WHERE pb.incident_type = $incident_type OR pb.incident_type = "ALL"
                        RETURN pb.id as id, pb.name as name, pb.steps as steps,
                               pb.success_rate as success_rate, pb.estimated_time_min as estimated_time_min
                        """,
                        {"resource_type": resource_type, "incident_type": incident_type}
                    )
                else:
                    result = session.run(
                        """
                        MATCH (pb:Playbook {applies_to: $resource_type})
                        RETURN pb.id as id, pb.name as name, pb.steps as steps,
                               pb.success_rate as success_rate, pb.estimated_time_min as estimated_time_min
                        """,
                        {"resource_type": resource_type}
                    )
                return [_sanitize_record(record) for record in result]
        except Exception as e:
            logger.error(f"Error querying playbooks: {e}")
            return []

    def get_ci_watcher_id(self, resource_name: str) -> Optional[str]:
        """
        Return the watcher_source_id (UUID string) for the CI that matches
        resource_name. Returns None if the CI doesn't exist or has never been
        discovered by a watcher.
        """
        if not self.driver:
            return None
        try:
            with self.driver.session() as session:
                result = session.run(
                    "MATCH (ci:ConfigurationItem {name: $name}) "
                    "RETURN ci.watcher_source_id AS watcher_source_id",
                    {"name": resource_name},
                )
                record = result.single()
                if record:
                    return record.get("watcher_source_id")
        except Exception as e:
            logger.debug(f"[CMDB] get_ci_watcher_id failed for '{resource_name}': {e}")
        return None


# Global CMDB service instance
_cmdb_service: Optional[CMDBService] = None


def get_cmdb() -> CMDBService:
    """Get or create the CMDB service"""
    global _cmdb_service
    if _cmdb_service is None:
        _cmdb_service = CMDBService("bolt://neo4j:7687")
    return _cmdb_service
