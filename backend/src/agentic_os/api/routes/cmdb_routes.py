"""
CMDB API Routes — Neo4j graph data for the CMDB visualization page.

Endpoints:
  GET  /api/cmdb/graph          → nodes + links (react-force-graph-2d format)
  GET  /api/cmdb/nodes          → flat list of all CIs with all properties
  GET  /api/cmdb/nodes/{name}   → single CI detail + dependencies
  POST /api/cmdb/discovery      → watcher batch-upsert of discovered container CIs
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import logging
import os
import re

from sqlalchemy.orm import Session
from agentic_os.db.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["CMDB"])


def _get_driver():
    """Lazy Neo4j driver creation."""
    try:
        from neo4j import GraphDatabase
        uri = os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        return driver
    except Exception as e:
        logger.error(f"[CMDB] Failed to connect to Neo4j: {e}")
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {e}")


@router.get("/cmdb/graph")
async def get_cmdb_graph(
    service: Optional[str] = Query(
        None,
        description="Focus on this service and its 1-hop neighbors (upstream + downstream)",
    ),
    max_nodes: int = Query(
        75,
        ge=1,
        le=1000,
        description="Maximum number of CI nodes to return (ignored when service filter is set)",
    ),
):
    """
    Returns the CMDB dependency graph in react-force-graph-2d format.

    When `service` is supplied the response contains only the named CI plus
    every node one DEPENDS_ON hop away in either direction — giving a focused
    neighbourhood view without loading the entire graph.

    When `service` is omitted, nodes are ordered by tier then name and capped
    at `max_nodes` (default 75) so the canvas stays performant.

    Response shape:
    {
      "nodes": [{ "id": "...", "tier": 1, "is_spof": true, ... }],
      "links": [{ "source": "...", "target": "..." }],
      "meta": { "total_nodes": N, "db_total": M, "truncated": bool, ... }
    }
    """
    # ── Cypher fragment shared by both query paths ────────────────────────────
    _CI_RETURN = """
        RETURN
            ci.name                AS id,
            ci.name                AS name,
            ci.type                AS type,
            ci.status              AS status,
            ci.owner               AS owner,
            ci.environment         AS environment,
            ci.business_criticality AS business_criticality,
            ci.ci_tier             AS tier,
            ci.user_count          AS user_count,
            ci.is_spof             AS is_spof,
            ci.sla_percent         AS sla_percent,
            ci.failover_available  AS failover_available,
            ci.discovery_source    AS discovery_source,
            ci.docker_image        AS docker_image,
            ci.platform            AS platform,
            ci.cpu_limit_cores     AS cpu_limit_cores,
            ci.memory_limit_mb     AS memory_limit_mb,
            ci.ip_address          AS ip_address,
            ci.exposed_ports       AS exposed_ports,
            ci.container_status    AS container_status,
            ci.health_status       AS health_status,
            ci.started_at          AS started_at,
            ci.current_cpu_percent AS cpu_percent,
            ci.current_memory_mb   AS memory_mb,
            ci.current_memory_pct  AS memory_pct,
            ci.current_pids        AS pids,
            ci.last_discovered_at  AS last_discovered_at,
            ci.last_metrics_update AS last_metrics_update,
            ci.support_group       AS support_group,
            ci.assignment_group    AS assignment_group,
            ci.managed_by          AS managed_by,
            ci.data_center         AS data_center,
            incident_count,
            CASE sev_rank
                WHEN 4 THEN 'critical'
                WHEN 3 THEN 'high'
                WHEN 2 THEN 'medium'
                WHEN 1 THEN 'low'
                ELSE null
            END AS max_incident_severity,
            [lbl IN labels(ci) WHERE lbl <> 'ConfigurationItem' | lbl] AS ci_labels
        ORDER BY ci.ci_tier ASC, ci.name ASC
    """
    _INC_AGG = """
        OPTIONAL MATCH (inc:Incident {status: 'active'})-[:AFFECTED_BY]->(ci)
        WITH ci,
             COUNT(inc) AS incident_count,
             MAX(CASE inc.severity
                     WHEN 'critical' THEN 4
                     WHEN 'high'     THEN 3
                     WHEN 'medium'   THEN 2
                     WHEN 'low'      THEN 1
                     ELSE 0
                 END) AS sev_rank
    """

    driver = _get_driver()
    try:
        with driver.session() as session:

            # ── Total count in DB (for truncation indicator) ──────────────────
            db_total: int = session.run(
                "MATCH (ci:ConfigurationItem) RETURN count(ci) AS n"
            ).single()["n"]

            # ── Node query: service subgraph vs. capped full graph ────────────
            if service:
                # 1-hop neighbourhood: focus node + all directly connected CIs
                # Traverses DEPENDS_ON (services) and PART_OF (application members)
                # in both directions so both Service and Application focus nodes work.
                node_result = session.run(
                    f"""
                    MATCH (focus:ConfigurationItem {{name: $service}})
                    OPTIONAL MATCH (focus)-[:DEPENDS_ON|PART_OF]->(out:ConfigurationItem)
                    OPTIONAL MATCH (in:ConfigurationItem)-[:DEPENDS_ON|PART_OF]->(focus)
                    WITH collect(DISTINCT focus)
                       + collect(DISTINCT out)
                       + collect(DISTINCT in) AS pool
                    UNWIND pool AS ci
                    {_INC_AGG}
                    {_CI_RETURN}
                    """,
                    service=service,
                )
            else:
                # Full graph, ordered Tier 1 → 3, capped at max_nodes
                node_result = session.run(
                    f"""
                    MATCH (ci:ConfigurationItem)
                    {_INC_AGG}
                    {_CI_RETURN}
                    LIMIT $max_nodes
                    """,
                    max_nodes=max_nodes,
                )

            nodes_raw = [dict(record) for record in node_result]
            # Flatten ci_labels list → ci_class string (most specific label wins)
            # Priority: Container > Database > Server > Application > Service
            _CLASS_PRIORITY = ["Container", "Database", "Server", "Application", "Service"]
            for node in nodes_raw:
                lbls = set(node.pop("ci_labels", None) or [])
                node["ci_class"] = next(
                    (c for c in _CLASS_PRIORITY if c in lbls), "Service"
                )
            nodes = nodes_raw
            node_names = [n["name"] for n in nodes]

            # ── All relationship types — only between nodes in the result set ─
            # Includes DEPENDS_ON, RUNS_ON, HOSTED_ON, PART_OF so the graph
            # shows the full CI class topology, not just logical dependencies.
            link_result = session.run(
                """
                MATCH (a:ConfigurationItem)-[r:DEPENDS_ON|RUNS_ON|HOSTED_ON|PART_OF]->(b:ConfigurationItem)
                WHERE a.name IN $names AND b.name IN $names
                RETURN a.name AS source, b.name AS target, type(r) AS rel_type
                """,
                names=node_names,
            )
            links = [dict(record) for record in link_result]

        # ── Override health_status & incident_count from Postgres ─────────────
        for node in nodes:
            _reconcile_health(node, node.get("id") or node.get("name", ""))

        truncated = (not service) and (len(nodes) < db_total)

        return {
            "nodes": nodes,
            "links": links,
            "meta": {
                "total_nodes": len(nodes),
                "total_links": len(links),
                "db_total": db_total,
                "truncated": truncated,
                "service_filter": service,
                "max_nodes": max_nodes,
                "tier_counts": _count_tiers(nodes),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Graph query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.get("/cmdb/nodes")
async def get_cmdb_nodes():
    """
    Returns a flat list of all CI nodes with all properties.
    Used for the table view below the graph.
    """
    driver = _get_driver()
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (ci:ConfigurationItem)
                RETURN ci {.*} AS ci
                ORDER BY ci.ci_tier ASC, ci.name ASC
                """
            )
            nodes = [dict(record["ci"]) for record in result]
        return {"nodes": nodes, "total": len(nodes)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Nodes query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.get("/cmdb/nodes/{name}")
async def get_cmdb_node(name: str):
    """
    Returns a single CI with all properties plus its dependency graph (1 hop).
    Used when a node is selected in the graph.
    """
    driver = _get_driver()
    try:
        with driver.session() as session:
            # CI properties
            ci_result = session.run(
                """
                MATCH (ci:ConfigurationItem {name: $name})
                RETURN ci {.*,
                    cpu_percent:        ci.current_cpu_percent,
                    memory_mb:          ci.current_memory_mb,
                    memory_pct:         ci.current_memory_pct,
                    pids:               ci.current_pids,
                    last_metrics_update: coalesce(ci.last_metrics_update, ci.last_discovered_at)
                } AS ci
                """,
                name=name,
            )
            record = ci_result.single()
            if not record:
                raise HTTPException(status_code=404, detail=f"CI '{name}' not found")
            ci = dict(record["ci"])

            # What this CI depends ON
            dep_result = session.run(
                """
                MATCH (ci:ConfigurationItem {name: $name})-[:DEPENDS_ON]->(dep)
                RETURN dep.name AS name, dep.ci_tier AS tier,
                       dep.container_status AS status, dep.health_status AS health
                """,
                name=name,
            )
            ci["depends_on"] = [dict(r) for r in dep_result]

            # What depends ON this CI (impact radius)
            rev_result = session.run(
                """
                MATCH (consumer)-[:DEPENDS_ON]->(ci:ConfigurationItem {name: $name})
                RETURN consumer.name AS name, consumer.ci_tier AS tier,
                       consumer.container_status AS status, consumer.health_status AS health
                """,
                name=name,
            )
            ci["depended_on_by"] = [dict(r) for r in rev_result]

        # Reconcile health_status from Postgres — Neo4j property can be stale
        _reconcile_health(ci, name)

        return ci
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Node detail query failed for '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


# ── Discovery batch ingest (called by WatcherService._run_discovery_via_api) ─

class _ContainerDiscoveryItem(BaseModel):
    container_name: str
    props: Dict[str, Any] = {}


class _DiscoveryBatch(BaseModel):
    source: str = "unknown"
    watcher_id: Optional[str] = None
    containers: List[_ContainerDiscoveryItem] = []


@router.post("/cmdb/discovery")
async def post_cmdb_discovery(payload: _DiscoveryBatch):
    """
    Receives a discovery batch from the watcher and upserts each container
    as a :ConfigurationItem node in the Neo4j CMDB.

    Called by: WatcherService._run_discovery_via_api() every N poll cycles.
    Auth: X-API-Key (Watcher Bot automation principal via _any dependency).

    Payload shape:
      { "source": "watcher_brain",
        "containers": [{"container_name": "backend", "props": {...}}, ...] }

    Response shape:
      { "updated": N, "new_cis": N, "errors": N, "total": N }
    """
    if not payload.containers:
        return {"updated": 0, "new_cis": 0, "errors": 0, "total": 0}

    try:
        from agentic_os.services.discovery_service import DiscoveryService
        svc = DiscoveryService()
    except Exception as exc:
        logger.error(f"[CMDB] Discovery service init failed: {exc}")
        raise HTTPException(status_code=503, detail=f"Discovery service unavailable: {exc}")

    updated = errors = 0
    for item in payload.containers:
        if not item.container_name:
            continue
        try:
            ok = svc.update_cmdb(item.container_name, item.props, watcher_id=payload.watcher_id)
            if ok:
                updated += 1
            else:
                errors += 1
        except Exception as exc:
            logger.error(f"[CMDB] CI update failed for '{item.container_name}': {exc}")
            errors += 1

    total = len(payload.containers)
    logger.info(
        f"[CMDB] Discovery batch from '{payload.source}': "
        f"{updated} updated, {errors} errors / {total} total"
    )
    return {"updated": updated, "new_cis": 0, "errors": errors, "total": total}


def _count_tiers(nodes: list) -> dict:
    counts: dict = {}
    for n in nodes:
        t = str(n.get("tier") or "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


# ── Lifecycle states that represent an in-flight incident ────────────────────
_ACTIVE_INCIDENT_STATES = (
    'open', 'in_progress', 'waiting_approval',
    'approved', 'executing', 'monitoring',
)
_SEVERITY_RANK = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}


def _reconcile_health(node: dict, resource_name: str) -> None:
    """
    Overwrite a node's health_status and incident_count using Postgres as the
    authoritative source.  Neo4j health_status can become stale when a workflow
    finishes without calling mark_ci_recovered (e.g. rejected, failed, diagnostics-only).
    """
    try:
        from agentic_os.db.database import SessionLocal
        from sqlalchemy import text

        pg = SessionLocal()
        try:
            rows = pg.execute(text("""
                SELECT severity::text AS severity
                FROM workflow_states
                WHERE workflow_type::text = 'incident'
                  AND lifecycle_state::text = ANY(:states)
                  AND (
                      context->'alert_payload'->>'resource_name' = :name
                      OR context->'cmdb'->>'resource_name' = :name
                  )
            """), {"states": list(_ACTIVE_INCIDENT_STATES), "name": resource_name}).fetchall()
        finally:
            pg.close()

        sev_list = [r.severity or 'low' for r in rows]
        count = len(sev_list)
        node['incident_count'] = count
        if count > 0:
            node['max_incident_severity'] = max(sev_list, key=lambda s: _SEVERITY_RANK.get(s, 0))
            node['health_status'] = 'degraded'
        else:
            node['max_incident_severity'] = None
            # No active incidents — clear any stale degraded flag
            if node.get('health_status') == 'degraded' or node.get('container_status') == 'running':
                node['health_status'] = 'healthy'

    except Exception as err:
        logger.warning(f"[CMDB] Health reconciliation skipped for '{resource_name}': {err}")


# ── CMDB Editor write endpoints ───────────────────────────────────────────────
# Require admin or itom_admin role — operators and viewers get read-only access.

from fastapi import Request, Depends
from agentic_os.api.auth import require_role, Principal

# Custom fields must match u_<lowercase_alphanumeric_underscore>, max 50 chars total.
# Validated before allowing through the PATCH whitelist.
_CUSTOM_FIELD_RE = re.compile(r'^u_[a-z][a-z0-9_]{0,48}$')

# Fields that humans manage — watcher-injected metrics are excluded from editing.
_EDITABLE_FIELDS = {
    "type", "status", "environment", "owner", "description",
    "business_criticality", "ci_tier", "is_spof", "failover_available",
    "user_count", "sla_percent", "platform",
    "support_group", "assignment_group", "managed_by", "data_center",
    "_custom_meta",  # JSON blob storing custom field labels + types
}


class CIUpdateBody(BaseModel):
    fields: Dict[str, Any]  # only keys in _EDITABLE_FIELDS are applied


class CICreateBody(BaseModel):
    name: str
    type: str = "Service"
    status: str = "active"
    environment: Optional[str] = None
    owner: Optional[str] = None
    description: Optional[str] = None
    business_criticality: Optional[str] = None
    ci_tier: Optional[int] = None
    is_spof: Optional[bool] = False
    failover_available: Optional[bool] = False
    user_count: Optional[int] = None
    sla_percent: Optional[float] = None
    platform: Optional[str] = None
    support_group: Optional[str] = None
    assignment_group: Optional[str] = None
    managed_by: Optional[str] = None
    data_center: Optional[str] = None


@router.patch("/cmdb/nodes/{name}")
async def update_cmdb_node(
    name: str,
    body: CIUpdateBody,
    db: Session = Depends(get_session),
    actor: Principal = Depends(require_role("admin", "itom_admin")),
):
    """
    Update editable fields on a CI node.  Watcher-injected metrics are excluded.
    Requires admin or itom_admin role.
    """
    # Standard editable fields (whitelist) + validated u_ custom fields
    safe_standard = {k: v for k, v in body.fields.items() if k in _EDITABLE_FIELDS}
    safe_custom    = {k: v for k, v in body.fields.items() if _CUSTOM_FIELD_RE.match(k or "")}
    safe = {**safe_standard, **safe_custom}
    if not safe:
        raise HTTPException(status_code=400, detail="No editable fields provided")

    # Custom fields with None/"" value are removals, not updates
    custom_to_remove = [k for k, v in safe_custom.items() if v is None or v == ""]
    fields_to_set    = {k: v for k, v in safe.items() if k not in custom_to_remove}

    driver = _get_driver()
    try:
        with driver.session() as session:
            exists = session.run(
                "MATCH (ci:ConfigurationItem {name: $name}) RETURN count(ci) AS n",
                name=name,
            ).single()["n"]
            if not exists:
                raise HTTPException(status_code=404, detail=f"CI '{name}' not found")

            # SET standard + non-empty custom fields
            if fields_to_set:
                set_clauses = ", ".join(f"ci.`{k}` = ${k}" for k in fields_to_set)
                session.run(
                    f"MATCH (ci:ConfigurationItem {{name: $name}}) SET {set_clauses}",
                    name=name, **fields_to_set,
                )

            # REMOVE deleted custom fields (u_ only — standard fields can never be removed)
            for k in custom_to_remove:
                session.run(
                    f"MATCH (ci:ConfigurationItem {{name: $name}}) REMOVE ci.`{k}`",
                    name=name,
                )

        logger.info(f"[CMDB] CI '{name}' updated by {actor.name}: set={list(fields_to_set)}, removed={custom_to_remove}")

        # When CMDB data changes (especially environment, criticality), close any
        # *dismissed* open conditions for this CI so the next alert re-scores with
        # the updated values.  Qualified conditions (active incidents) are left open
        # — they should be resolved through the normal incident workflow.
        if any(k in safe for k in ("environment", "criticality", "service_class")):
            try:
                from agentic_os.db.repositories import EventConditionStateRepository
                from agentic_os.db.models import EventConditionStateModel
                now_dt = __import__('datetime').datetime.utcnow()
                dismissed_rows = db.query(EventConditionStateModel).filter(
                    EventConditionStateModel.resource_name == name,
                    EventConditionStateModel.status        == 'open',
                    EventConditionStateModel.qualified     == False,  # noqa: E712
                ).all()
                for row in dismissed_rows:
                    row.status     = 'closed'
                    row.closed_at  = now_dt
                    row.updated_at = now_dt
                if dismissed_rows:
                    db.commit()
                    logger.info(
                        "[CMDB] Closed %d dismissed condition(s) for '%s' after CMDB update "
                        "(fields changed: %s) — next alert will re-score with updated data",
                        len(dismissed_rows), name, list(safe.keys()),
                    )
            except Exception as _cond_err:
                logger.warning("[CMDB] Could not reset dismissed conditions for '%s': %s", name, _cond_err)

        return {"status": "updated", "name": name, "updated_fields": list(safe.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Update failed for '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.post("/cmdb/nodes", status_code=201)
async def create_cmdb_node(
    body: CICreateBody,
    actor: Principal = Depends(require_role("admin", "itom_admin")),
):
    """
    Create a new CI node in the Neo4j CMDB.
    Requires admin or itom_admin role.
    """
    driver = _get_driver()
    try:
        with driver.session() as session:
            exists = session.run(
                "MATCH (ci:ConfigurationItem {name: $name}) RETURN count(ci) AS n",
                name=body.name,
            ).single()["n"]
            if exists:
                raise HTTPException(status_code=409, detail=f"CI '{body.name}' already exists")

            props = {k: v for k, v in body.model_dump().items() if v is not None}
            props["discovery_source"] = "manual"

            session.run(
                """
                CREATE (ci:ConfigurationItem)
                SET ci = $props
                """,
                props=props,
            )

        logger.info(f"[CMDB] CI '{body.name}' created by {actor.name}")
        return {"status": "created", "name": body.name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Create failed for '{body.name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()


@router.delete("/cmdb/nodes/{name}")
async def decommission_cmdb_node(
    name: str,
    actor: Principal = Depends(require_role("admin")),
):
    """
    Soft-delete a CI by setting status=decommissioned.
    Hard delete is not supported — use Neo4j browser for that.
    Requires admin role.
    """
    driver = _get_driver()
    try:
        with driver.session() as session:
            exists = session.run(
                "MATCH (ci:ConfigurationItem {name: $name}) RETURN count(ci) AS n",
                name=name,
            ).single()["n"]
            if not exists:
                raise HTTPException(status_code=404, detail=f"CI '{name}' not found")

            session.run(
                "MATCH (ci:ConfigurationItem {name: $name}) SET ci.status = 'decommissioned'",
                name=name,
            )

        logger.info(f"[CMDB] CI '{name}' decommissioned by {actor.name}")
        return {"status": "decommissioned", "name": name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[CMDB] Decommission failed for '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        driver.close()
