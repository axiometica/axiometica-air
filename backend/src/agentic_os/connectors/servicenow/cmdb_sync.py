"""
ServiceNow CMDB sync — pulls CI classes and writes them into Neo4j as
ConfigurationItem nodes + DEPENDS_ON relationships.

Pull order:
  1. cmdb_ci_service          (Service Instances   → type: service)
  2. cmdb_ci_service_offering (Service Offerings   → type: service-offering)
  3. cmdb_ci_server           (Generic Servers     → type: server)
  4. cmdb_ci_linux_server     (Linux Servers       → type: linux-server)
  5. cmdb_ci_win_server       (Windows Servers     → type: windows-server)
  6. cmdb_rel_ci              (CI Relationships    → DEPENDS_ON edges)

CI records are MERGEd by name into Neo4j ConfigurationItem nodes.
Governance properties set by manual seeding (is_spof, failover_available,
sla_percent, etc.) are NOT overwritten when discovery_source = 'manually_seeded'.

CI Relationships are written as DEPENDS_ON edges.  The direction follows
ServiceNow semantics: child -[:DEPENDS_ON]-> parent (child relies on parent).
"""

from __future__ import annotations
import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any

from agentic_os.connectors.servicenow.client import ServiceNowClient, ServiceNowError
from agentic_os.connectors.servicenow.field_maps import CI_CLASSES
from agentic_os.db.models import SNowSyncLogModel, ConnectorConfigModel

logger = logging.getLogger(__name__)


# ── ServiceNow operational_status → platform status ──────────────────────────

_OP_STATUS: dict[str, str] = {
    "1": "operational",    "Operational":       "operational",
    "2": "non-operational","Non-Operational":   "non-operational",
    "3": "repair-in-progress","Repair in Progress":"repair-in-progress",
    "4": "dr-standby",     "DR Standby":        "dr-standby",
    "5": "ready",          "Ready":             "ready",
    "6": "retired",        "Retired":           "retired",
}

# ServiceNow business_criticality → platform tier string
_CRITICALITY: dict[str, str] = {
    "1 - most critical":      "tier_1",
    "2 - somewhat critical":  "tier_2",
    "3 - moderately critical":"tier_2",
    "4 - less critical":      "tier_3",
    "5 - not critical":       "tier_3",
    "Critical":               "tier_1",
    "High":                   "tier_2",
    "Medium":                 "tier_2",
    "Low":                    "tier_3",
}

_CI_TYPE: dict[str, str] = {
    "cmdb_ci_service":          "service",
    "cmdb_ci_service_offering": "service-offering",
    "cmdb_ci_server":           "server",
    "cmdb_ci_linux_server":     "linux-server",
    "cmdb_ci_win_server":       "windows-server",
}

_CI_TIER: dict[str, int] = {
    "cmdb_ci_service":          2,   # services sit below application tier (tier_1)
    "cmdb_ci_service_offering": 2,
    "cmdb_ci_server":           3,
    "cmdb_ci_linux_server":     3,
    "cmdb_ci_win_server":       3,
}

# Neo4j sub-labels to stamp per SN class (in addition to :ConfigurationItem)
_CI_SUBLABEL: dict[str, str] = {
    "cmdb_ci_service":          "Service",
    "cmdb_ci_service_offering": "Service",
    "cmdb_ci_server":           "Server",
    "cmdb_ci_linux_server":     "Server",
    "cmdb_ci_win_server":       "Server",
}


class CMDBSync:
    """Orchestrates pulling CMDB data from ServiceNow and writing to Neo4j."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self._ci_records:  list[dict[str, Any]] = []
        self._rel_records: list[dict[str, Any]] = []

    # ── Main entry point ─────────────────────────────────────────────────

    async def sync_all(self, db_session: Any) -> dict[str, Any]:
        """
        Pull all CI classes from ServiceNow and upsert into Neo4j.
        Returns a summary dict and writes a SNowSyncLogModel row.
        """
        from neo4j import GraphDatabase

        started_at = datetime.utcnow()
        t0         = time.monotonic()
        errors: list[str] = []
        total_records      = 0

        # ── Step 1: Pull from ServiceNow ──────────────────────────────────
        async with ServiceNowClient(self.base_url, self.username, self.password) as client:
            ci_defs  = [c for c in CI_CLASSES if c["ci_class"] != "cmdb_rel_ci"]
            rel_def  = next(c for c in CI_CLASSES if c["ci_class"] == "cmdb_rel_ci")

            tasks = [self._pull_class(client, ci_def) for ci_def in ci_defs]
            tasks.append(self._pull_class(client, rel_def))
            results = await asyncio.gather(*tasks, return_exceptions=True)

        class_counts: dict[str, int] = {}
        all_defs = ci_defs + [rel_def]
        for ci_def, result in zip(all_defs, results):
            if isinstance(result, Exception):
                msg = f"{ci_def['ci_class']}: {result}"
                errors.append(msg)
                logger.error(f"✗ Pull failed — {msg}")
            else:
                class_counts[ci_def["ci_class"]] = result
                total_records += result
                logger.info(f"  ↳ {ci_def['label']}: {result} records")

        # ── Step 2: Write to Neo4j ────────────────────────────────────────
        neo4j_uri  = os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687")
        neo4j_user = os.getenv("NEO4J_USER",     "neo4j")
        neo4j_pass = os.getenv("NEO4J_PASSWORD")

        try:
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
            driver.verify_connectivity()
            ci_count  = self._write_cis_to_neo4j(driver, self._ci_records)
            self._stamp_sublabels(driver)
            rel_count = self._write_rels_to_neo4j(driver, self._rel_records)
            driver.close()
            logger.info(f"  ↳ Neo4j: {ci_count} CIs merged, {rel_count} relationships created")
        except Exception as e:
            msg = f"Neo4j write failed: {e}"
            errors.append(msg)
            logger.error(f"✗ {msg}")

        duration = round(time.monotonic() - t0, 2)
        status   = "error" if len(errors) >= len(all_defs) else ("partial" if errors else "ok")

        # ── Step 3: Write sync log (Postgres) ─────────────────────────────
        log = SNowSyncLogModel(
            connector_id   = "servicenow",
            started_at     = started_at,
            finished_at    = datetime.utcnow(),
            records_pulled = total_records,
            status         = status,
            error_message  = "; ".join(errors) if errors else None,
        )
        db_session.add(log)

        cfg = db_session.query(ConnectorConfigModel).filter_by(id="servicenow").first()
        if cfg:
            cfg.last_sync_at     = datetime.utcnow()
            cfg.last_sync_status = status

        db_session.commit()

        summary = {
            "status":           status,
            "total_records":    total_records,
            "by_class":         class_counts,
            "duration_seconds": duration,
            "errors":           errors,
        }
        logger.info(f"✓ CMDB sync complete: {total_records} records in {duration}s [{status}]")
        return summary

    # ── Pull helpers ─────────────────────────────────────────────────────

    async def _pull_class(self, client: ServiceNowClient, ci_def: dict) -> int:
        """Pull one CI class from ServiceNow and store in memory."""
        try:
            records = await client.query_table(
                table         = ci_def["table"],
                fields        = ci_def["fields"],
                encoded_query = ci_def.get("encoded_query", ""),
                limit         = 5000,
            )
            for r in records:
                r["_ci_class"] = ci_def["ci_class"]

            if ci_def["ci_class"] == "cmdb_rel_ci":
                self._rel_records.extend(records)
            else:
                self._ci_records.extend(records)

            return len(records)
        except ServiceNowError as e:
            if e.status_code == 403:
                logger.warning(f"  ⚠ {ci_def['ci_class']}: access denied (403), skipping")
                return 0
            raise

    # ── Neo4j write helpers ───────────────────────────────────────────────

    def _write_cis_to_neo4j(self, driver: Any, records: list[dict]) -> int:
        """
        MERGE each CI record into Neo4j as a ConfigurationItem node.

        Governance properties (is_spof, failover_available, sla_percent,
        user_count, compliance_scope, avg_mttr_minutes) set by manual seeding
        are preserved — only ServiceNow fields are updated for SN-sourced nodes.
        """
        count = 0
        with driver.session() as session:
            for r in records:
                ci_class = r.pop("_ci_class", "unknown")
                name = r.get("name", "").strip()
                if not name:
                    continue

                props = self._map_sn_to_neo4j(r, ci_class)

                # MERGE on name (unique constraint).
                # Only SET SN-sourced props; preserve governance fields on seeded nodes.
                session.run(
                    """
                    MERGE (ci:ConfigurationItem {name: $name})
                    ON CREATE SET
                        ci += $props,
                        ci.status   = $status,
                        ci.ci_tier  = $ci_tier,
                        ci.business_criticality = $criticality
                    ON MATCH SET
                        ci.sn_sys_id        = $props.sn_sys_id,
                        ci.sn_class         = $props.sn_class,
                        ci.sn_synced_at     = $props.sn_synced_at,
                        ci.type             = CASE
                                                WHEN ci.discovery_source = 'manually_seeded'
                                                THEN ci.type
                                                ELSE $props.type
                                              END,
                        ci.status           = $status,
                        ci.environment      = coalesce(nullif($props.environment,''), ci.environment),
                        ci.owner            = coalesce(nullif($props.owner,''),       ci.owner),
                        ci.description      = coalesce(nullif($props.description,''), ci.description),
                        ci.host_name        = $props.host_name,
                        ci.ip_address       = $props.ip_address,
                        ci.os               = $props.os,
                        ci.os_version       = $props.os_version,
                        ci.support_group    = coalesce(nullif($props.support_group,''),  ci.support_group),
                        ci.managed_by       = coalesce(nullif($props.managed_by,''),     ci.managed_by),
                        ci.data_center      = coalesce(nullif($props.data_center,''),    ci.data_center),
                        ci.discovery_source = CASE
                                                WHEN ci.discovery_source = 'manually_seeded'
                                                THEN 'manually_seeded'
                                                ELSE 'servicenow'
                                              END
                    """,
                    {
                        "name":        name,
                        "props":       props,
                        "status":      props["status"],
                        "ci_tier":     props["ci_tier"],
                        "criticality": props["business_criticality"],
                    },
                )
                count += 1

        return count

    def _stamp_sublabels(self, driver: Any) -> None:
        """
        Bulk-stamp Neo4j sub-labels (:Service, :Server) onto SN-synced CIs.

        Runs one query per class after all CIs have been merged so that
        SN-sourced nodes are indistinguishable from manually-seeded ones in
        label-based CMDB graph queries.
        """
        # Map: sn_class → Cypher that adds the sub-label
        label_queries = [
            ("cmdb_ci_service",          "Service"),
            ("cmdb_ci_service_offering", "Service"),
            ("cmdb_ci_server",           "Server"),
            ("cmdb_ci_linux_server",     "Server"),
            ("cmdb_ci_win_server",       "Server"),
        ]
        with driver.session() as session:
            for sn_class, label in label_queries:
                # SET ci:Service / SET ci:Server — idempotent, no-op if already set
                session.run(
                    f"MATCH (ci:ConfigurationItem {{sn_class: $cls}}) SET ci:{label}",
                    {"cls": sn_class},
                )

    def _write_rels_to_neo4j(self, driver: Any, records: list[dict]) -> int:
        """
        Write cmdb_rel_ci rows as DEPENDS_ON relationships in Neo4j.

        Direction: child -[:DEPENDS_ON]-> parent
        (child service relies on the parent service/server)
        Only creates the edge if BOTH nodes already exist in the graph.
        """
        count = 0
        with driver.session() as session:
            for r in records:
                parent = (r.get("parent") or "").strip()
                child  = (r.get("child")  or "").strip()
                if not parent or not child or parent == child:
                    continue

                result = session.run(
                    """
                    MATCH (parent:ConfigurationItem {name: $parent})
                    MATCH (child:ConfigurationItem  {name: $child})
                    MERGE (child)-[rel:DEPENDS_ON {source: 'servicenow'}]->(parent)
                    ON CREATE SET rel.sn_type = $sn_type
                    RETURN count(rel) as created
                    """,
                    {
                        "parent":  parent,
                        "child":   child,
                        "sn_type": r.get("type", ""),
                    },
                )
                rec = result.single()
                if rec and rec["created"]:
                    count += 1

        return count

    # ── Field mapping ────────────────────────────────────────────────────

    @staticmethod
    def _map_sn_to_neo4j(r: dict, ci_class: str) -> dict:
        """Map a flattened ServiceNow CI record to Neo4j ConfigurationItem props."""
        status = _OP_STATUS.get(str(r.get("operational_status", "1")), "operational")

        crit_raw     = str(r.get("business_criticality", "") or "")
        criticality  = _CRITICALITY.get(crit_raw, "tier_3")

        owner = (r.get("owned_by") or r.get("managed_by") or "").strip()
        desc  = (r.get("short_description") or r.get("description") or "").strip()
        env   = (r.get("environment") or "production").strip() or "production"

        props: dict[str, Any] = {
            "sn_sys_id":          r.get("sys_id", ""),
            "sn_class":           ci_class,
            "type":               _CI_TYPE.get(ci_class, "unknown"),
            "status":             status,
            "environment":        env,
            "owner":              owner,
            "description":        desc,
            "business_criticality": criticality,
            "ci_tier":            _CI_TIER.get(ci_class, 3),
            "discovery_source":   "servicenow",
            "sn_synced_at":       datetime.utcnow().isoformat(),
            # Server-specific (empty string when not applicable)
            "host_name":          (r.get("host_name") or r.get("fqdn") or "").strip(),
            "ip_address":         (r.get("ip_address") or "").strip(),
            "os":                 (r.get("os") or "").strip(),
            "os_version":         (r.get("os_version") or "").strip(),
            # ITOps routing fields
            "support_group":      (r.get("support_group") or "").strip(),
            "managed_by":         (r.get("managed_by") or "").strip(),
            "data_center":        (r.get("location") or "").strip(),
        }
        return props

    # ── Read helpers (used by API routes to query Neo4j) ─────────────────

    @staticmethod
    def get_summary(driver: Any) -> list[dict]:
        """Return CI counts per SN class from Neo4j."""
        counts: dict[str, int] = {}
        with driver.session() as session:
            result = session.run(
                """
                MATCH (ci:ConfigurationItem)
                WHERE ci.sn_class IS NOT NULL
                RETURN ci.sn_class AS ci_class, count(ci) AS cnt
                """
            )
            for rec in result:
                counts[rec["ci_class"]] = rec["cnt"]

        return [
            {
                "ci_class":    c["ci_class"],
                "label":       c["label"],
                "table":       c["table"],
                "display_key": c["display_key"],
                "count":       counts.get(c["ci_class"], 0),
            }
            for c in CI_CLASSES
            if c["ci_class"] != "cmdb_rel_ci"
        ]

    @staticmethod
    def get_by_class(
        driver: Any,
        ci_class: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return paginated CI nodes for a given SN class from Neo4j."""
        with driver.session() as session:
            total_res = session.run(
                "MATCH (ci:ConfigurationItem {sn_class: $cls}) RETURN count(ci) AS n",
                {"cls": ci_class},
            )
            total = total_res.single()["n"]

            rows = session.run(
                """
                MATCH (ci:ConfigurationItem {sn_class: $cls})
                RETURN ci {.*} AS props
                ORDER BY ci.name
                SKIP $skip LIMIT $limit
                """,
                {"cls": ci_class, "skip": offset, "limit": limit},
            )
            items = [_neo4j_to_dict(r["props"]) for r in rows]

        return items, total

    @staticmethod
    def search(
        driver: Any,
        q: str,
        ci_class: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Case-insensitive name search across ServiceNow CIs in Neo4j."""
        if ci_class:
            cypher = """
                MATCH (ci:ConfigurationItem {sn_class: $cls})
                WHERE toLower(ci.name) CONTAINS toLower($q)
                RETURN ci {.*} AS props
                ORDER BY ci.name LIMIT $limit
            """
            params: dict = {"cls": ci_class, "q": q, "limit": limit}
        else:
            cypher = """
                MATCH (ci:ConfigurationItem)
                WHERE ci.sn_class IS NOT NULL
                  AND toLower(ci.name) CONTAINS toLower($q)
                RETURN ci {.*} AS props
                ORDER BY ci.name LIMIT $limit
            """
            params = {"q": q, "limit": limit}

        with driver.session() as session:
            result = session.run(cypher, params)
            return [_neo4j_to_dict(r["props"]) for r in result]

    @staticmethod
    def get_by_sys_id(driver: Any, sys_id: str) -> dict | None:
        """Return a single CI by its ServiceNow sys_id."""
        with driver.session() as session:
            result = session.run(
                "MATCH (ci:ConfigurationItem {sn_sys_id: $sid}) RETURN ci {.*} AS props",
                {"sid": sys_id},
            )
            rec = result.single()
            return _neo4j_to_dict(rec["props"]) if rec else None


# ── Helper ────────────────────────────────────────────────────────────────────

def _neo4j_to_dict(props: dict) -> dict:
    """Normalise a Neo4j property map for the API response."""
    out = {k: v for k, v in props.items() if v is not None and v != ""}
    # Remap sn_sys_id → sys_id so frontend code stays the same
    if "sn_sys_id" in out:
        out.setdefault("sys_id", out.pop("sn_sys_id"))
    if "sn_synced_at" in out:
        out.setdefault("synced_at", out.pop("sn_synced_at"))
    return out
