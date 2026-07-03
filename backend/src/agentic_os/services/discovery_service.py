"""
Discovery Service - Container Configuration Discovery for CMDB

Runs periodically inside the watcher loop to collect real runtime data from
Docker containers and push it into the Neo4j CMDB.  Acts as a lightweight
discovery agent: inspects each running container, extracts OS, CPU/memory limits,
IP, ports, health status and live metrics, then writes them as properties on the
matching ConfigurationItem node.

New containers that have no CI node yet are auto-created with sensible defaults
and tagged with discovery_source='watcher_discovery' so they're distinguishable
from manually seeded CIs.

Governance properties (ci_tier, business_criticality, is_spof, sla_percent,
failover_available) are NEVER overwritten on existing nodes — discovery only
touches 'discovered_*' and live state fields.
"""

import subprocess
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# Containers to skip entirely — not platform CIs, no value in the CMDB
DISCOVERY_EXCLUDE: frozenset = frozenset()

# Env-var names that reveal the runtime environment
ENV_KEYS = ("ENVIRONMENT", "ENV", "DEPLOY_ENV", "APP_ENV", "NODE_ENV", "RAILS_ENV")


class DiscoveryService:
    """
    Lightweight container discovery agent.

    Connects directly to Neo4j (bolt) and calls docker CLI via subprocess,
    consistent with the rest of the watcher codebase.
    """

    def __init__(
        self,
        neo4j_uri: str = None,
        neo4j_user: str = None,
        neo4j_password: str = None,
        inspect_cache_ttl: int = 10,  # Phase 1: 10-second TTL on docker inspect cache
    ):
        import os
        _explicit = neo4j_uri is not None or neo4j_password is not None
        self._neo4j_uri      = neo4j_uri      or os.getenv("NEO4J_URI",      os.getenv("NEO4J_BOLT_URL", "bolt://neo4j:7687"))
        self._neo4j_user     = neo4j_user     or os.getenv("NEO4J_USER",     "neo4j")
        self._neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD")
        self._driver = None
        # Phase 1: Docker inspect caching to reduce subprocess calls
        self._inspect_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}  # {container_name: (data, timestamp)}
        self._inspect_cache_ttl = inspect_cache_ttl
        # Skip Neo4j connect when called in Docker-only (collector) mode
        if _explicit or os.getenv("NEO4J_PASSWORD"):
            self._connect()
        else:
            logger.info("🔍 [DISCOVERY] Docker-collector mode — no Neo4j connection")
        logger.info("🔍 [DISCOVERY] Discovery Service initialized")

    # ──────────────────────────────────────────────────────────────────────────
    # Neo4j connection
    # ──────────────────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._neo4j_uri,
                auth=(self._neo4j_user, self._neo4j_password),
            )
            self._driver.verify_connectivity()
            logger.info("🔍 [DISCOVERY] Connected to Neo4j")
        except Exception as e:
            logger.warning(f"⚠️  [DISCOVERY] Neo4j unavailable, will retry: {e}")
            self._driver = None

    def _ensure_connected(self) -> bool:
        if self._driver:
            try:
                self._driver.verify_connectivity()
                return True
            except Exception:
                self._driver = None
        self._connect()
        return self._driver is not None

    # ──────────────────────────────────────────────────────────────────────────
    # Docker inspection
    # ──────────────────────────────────────────────────────────────────────────

    def get_running_containers(self) -> List[str]:
        """Return names of all currently running Docker containers."""
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
        except Exception as e:
            logger.error(f"❌ [DISCOVERY] docker ps failed: {e}")
        return []

    def inspect_container(self, container_name: str) -> Optional[Dict[str, Any]]:
        """
        Run `docker inspect <name>` and return the parsed JSON object.

        Phase 1 Optimization: Cache results with 10-second TTL to avoid redundant
        subprocess calls. Container metadata changes infrequently during operation.

        Returns None on any error or cache miss.
        """
        # Phase 1: Check cache first
        now = time.time()
        if container_name in self._inspect_cache:
            data, ts = self._inspect_cache[container_name]
            if now - ts < self._inspect_cache_ttl:
                logger.debug(f"[DISCOVERY] Cache hit for {container_name}")
                return data

        try:
            result = subprocess.run(
                ["docker", "inspect", container_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            parsed = data[0] if data else None

            # Phase 1: Store in cache
            if parsed:
                self._inspect_cache[container_name] = (parsed, now)
                logger.debug(f"[DISCOVERY] Cached inspect result for {container_name}")

            return parsed
        except Exception as e:
            logger.debug(f"[DISCOVERY] inspect failed for {container_name}: {e}")
            return None

    def extract_properties(
        self,
        inspect: Dict[str, Any],
        live_stats: Optional[Any] = None,  # ContainerMetrics from DockerStatsService
    ) -> Dict[str, Any]:
        """
        Extract CMDB-relevant properties from docker inspect output.

        Collected fields
        ────────────────
        docker_image         – image name (e.g. agenticplatform_v2-backend:latest)
        platform             – linux / windows
        cpu_limit_cores      – CPU limit as float cores; None = unlimited
        memory_limit_mb      – memory limit in MB;        None = unlimited
        ip_address           – first non-empty IP from container networks
        exposed_ports        – comma-separated port specs (e.g. "8000/tcp, 5432/tcp")
        container_status     – running / paused / exited / dead
        health_status        – healthy / unhealthy / starting / none
        started_at           – ISO timestamp of last container start
        detected_environment – value of ENV / ENVIRONMENT var, if present
        current_cpu_percent  – live CPU % (from stats, if available)
        current_memory_mb    – live memory used in MB
        current_memory_pct   – live memory % of limit
        current_pids         – process count inside container
        last_discovered_at   – UTC ISO timestamp of this discovery run
        """
        config = inspect.get("Config", {})
        host_cfg = inspect.get("HostConfig", {})
        state = inspect.get("State", {})
        network = inspect.get("NetworkSettings", {})

        # ── Image ─────────────────────────────────────────────────────────────
        docker_image = config.get("Image") or inspect.get("Image") or None

        # ── Platform ──────────────────────────────────────────────────────────
        platform = inspect.get("Platform", "linux")

        # ── CPU limit ─────────────────────────────────────────────────────────
        nanocpus = host_cfg.get("NanoCpus", 0) or 0
        cpu_limit_cores = round(nanocpus / 1_000_000_000, 2) if nanocpus > 0 else None

        # ── Memory limit ──────────────────────────────────────────────────────
        memory_bytes = host_cfg.get("Memory", 0) or 0
        memory_limit_mb = round(memory_bytes / (1024 * 1024)) if memory_bytes > 0 else None

        # ── IP address (first non-empty network) ──────────────────────────────
        ip_address = None
        for _net_name, net_data in (network.get("Networks") or {}).items():
            ip = (net_data or {}).get("IPAddress", "")
            if ip:
                ip_address = ip
                break

        # ── Exposed ports ─────────────────────────────────────────────────────
        ports_dict = network.get("Ports") or {}
        if ports_dict:
            exposed_ports = ", ".join(sorted(ports_dict.keys()))
        else:
            raw_exposed = config.get("ExposedPorts") or {}
            exposed_ports = ", ".join(sorted(raw_exposed.keys())) if raw_exposed else None

        # ── Container state ───────────────────────────────────────────────────
        container_status = state.get("Status", "unknown")

        # ── Health status ─────────────────────────────────────────────────────
        health = state.get("Health") or {}
        health_status = health.get("Status") if health else None  # healthy/unhealthy/starting/None

        # ── Started at ────────────────────────────────────────────────────────
        started_at = state.get("StartedAt")  # ISO 8601 with nanoseconds

        # ── Detect environment from env vars ──────────────────────────────────
        detected_environment = None
        env_list = config.get("Env") or []
        for entry in env_list:
            if "=" in entry:
                key, _, val = entry.partition("=")
                if key.upper() in ENV_KEYS and val:
                    detected_environment = val.lower()
                    break

        props: Dict[str, Any] = {
            "docker_image": docker_image,
            "platform": platform,
            "cpu_limit_cores": cpu_limit_cores,
            "memory_limit_mb": memory_limit_mb,
            "ip_address": ip_address,
            "exposed_ports": exposed_ports,
            "container_status": container_status,
            "health_status": health_status,
            "started_at": started_at,
            "detected_environment": detected_environment,
            "last_discovered_at": datetime.now(timezone.utc).isoformat(),
            # Live stats — filled below if available
            "current_cpu_percent": None,
            "current_memory_mb": None,
            "current_memory_pct": None,
            "current_pids": None,
        }

        # ── Augment with live metrics if caller passed them in ─────────────────
        if live_stats:
            props["current_cpu_percent"] = round(live_stats.cpu_percent, 1)
            props["current_memory_mb"] = round(live_stats.memory_used_mb, 1)
            props["current_memory_pct"] = round(live_stats.memory_percent, 1)
            props["current_pids"] = live_stats.pids

        return props

    # ──────────────────────────────────────────────────────────────────────────
    # Neo4j update
    # ──────────────────────────────────────────────────────────────────────────

    def update_cmdb(self, container_name: str, props: Dict[str, Any], watcher_id: Optional[str] = None) -> bool:
        """
        MERGE the CI node for *container_name* and apply discovered properties.

        The container name IS the CMDB node name — CMDB nodes are seeded using
        the same names as the Docker containers so discovery reconciles directly.

        - ON CREATE: populates baseline governance defaults (tier_3, etc.) so
          newly discovered containers get a sensible starting profile.
        - SET (always): only touches 'discovered' and live-state fields —
          never overwrites ci_tier, business_criticality, is_spof, sla_percent,
          failover_available on existing seeded CIs.
        - Always adds the :Container sub-label so the CMDB graph can
          distinguish containers from logical services and servers.
        - MERGE a :Server node for the Docker host and create a :RUNS_ON
          relationship so the topology shows which host each container is on.

        Returns True if the update succeeded.
        """
        if not self._ensure_connected():
            logger.warning(f"⚠️  [DISCOVERY] Neo4j unavailable, skipping CI update for {container_name}")
            return False

        env_default = props.get("detected_environment") or "prod"
        host_name = os.getenv("DOCKER_HOST_CI_NAME", "agenticplatform-host")

        # ── Step 1: MERGE the container CI and set :Container sub-label ──────
        ci_query = """
        MERGE (ci:ConfigurationItem {name: $name})
        ON CREATE SET
            ci.type                 = 'container',
            ci.status               = 'operational',
            ci.owner                = 'platform',
            ci.environment          = $env_default,
            ci.discovery_source     = 'watcher_discovery',
            ci.ci_tier              = 3,
            ci.business_criticality = 'tier_3',
            ci.user_count           = 0,
            ci.is_spof              = false,
            ci.sla_percent          = 95.0,
            ci.failover_available   = false
        SET
            ci:Container,
            ci.type                 = 'container',
            ci.docker_image         = $docker_image,
            ci.platform             = $platform,
            ci.cpu_limit_cores      = $cpu_limit_cores,
            ci.memory_limit_mb      = $memory_limit_mb,
            ci.ip_address           = $ip_address,
            ci.exposed_ports        = $exposed_ports,
            ci.container_status     = $container_status,
            ci.health_status        = coalesce(ci.incident_health_override, $health_status),
            ci.started_at           = $started_at,
            ci.last_discovered_at   = $last_discovered_at,
            ci.last_metrics_update  = $last_discovered_at,
            ci.current_cpu_percent  = $current_cpu_percent,
            ci.current_memory_mb    = $current_memory_mb,
            ci.current_memory_pct   = $current_memory_pct,
            ci.current_pids         = $current_pids,
            ci.watcher_source_id    = CASE WHEN $watcher_id IS NOT NULL THEN $watcher_id ELSE ci.watcher_source_id END
        RETURN ci.name AS name
        """

        # ── Step 2: MERGE the host :Server CI and link container to it ───────
        host_query = """
        MATCH  (ci:ConfigurationItem  {name: $container_name})
        MERGE  (host:ConfigurationItem {name: $host_name})
        ON CREATE SET
            host:Server,
            host.type                 = 'linux-server',
            host.status               = 'operational',
            host.owner                = 'platform-team',
            host.environment          = 'production',
            host.ci_tier              = 1,
            host.business_criticality = 'tier_1',
            host.compliance_scope     = 'general',
            host.failover_available   = false,
            host.is_spof              = true,
            host.sla_percent          = 99.5,
            host.discovery_source     = 'watcher_discovery',
            host.description          = 'Docker host — auto-created by discovery'
        SET host:Server
        MERGE (ci)-[:RUNS_ON]->(host)
        """

        try:
            with self._driver.session() as session:
                result = session.run(
                    ci_query,
                    name=container_name,
                    env_default=env_default,
                    watcher_id=watcher_id or None,
                    **{k: v for k, v in props.items() if k != "detected_environment"},
                )
                record = result.single()
                if record is None:
                    return False

                try:
                    session.run(host_query, container_name=container_name, host_name=host_name)
                except Exception as link_err:
                    logger.debug(f"[DISCOVERY] RUNS_ON link skipped for {container_name}: {link_err}")

                return True
        except Exception as e:
            logger.error(f"❌ [DISCOVERY] Neo4j update failed for {container_name}: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Main entry point called from the watcher loop
    # ──────────────────────────────────────────────────────────────────────────

    def run_discovery(self, container_stats: Optional[Dict] = None) -> Dict[str, int]:
        """
        Discover all running containers and update the CMDB.

        Args:
            container_stats: Pre-fetched dict of {name: ContainerMetrics} from
                             DockerStatsService — avoids a second `docker stats`
                             call since the watcher already collects this every poll.

        Returns:
            Summary dict: {discovered, updated, new_cis, errors}
        """
        containers = self.get_running_containers()
        summary = {"discovered": 0, "updated": 0, "new_cis": 0, "errors": 0}

        if not containers:
            logger.warning("⚠️  [DISCOVERY] No running containers found")
            return summary

        logger.info(f"🔍 [DISCOVERY] Starting discovery run — {len(containers)} containers")

        for container_name in containers:
            if container_name in DISCOVERY_EXCLUDE:
                continue

            try:
                inspect = self.inspect_container(container_name)
                if not inspect:
                    logger.debug(f"[DISCOVERY] Could not inspect {container_name}")
                    summary["errors"] += 1
                    continue

                stats = (container_stats or {}).get(container_name)
                props = self.extract_properties(inspect, stats)
                summary["discovered"] += 1

                is_new = not self._ci_exists(container_name)

                if self.update_cmdb(container_name, props):
                    summary["updated"] += 1
                    if is_new:
                        summary["new_cis"] += 1
                        logger.info(
                            f"🆕 [DISCOVERY] New CI auto-created: {container_name} "
                            f"(image: {props.get('docker_image', '?')}, "
                            f"env: {props.get('detected_environment', '?')})"
                        )
                    else:
                        logger.debug(
                            f"✓ [DISCOVERY] Updated: {container_name} "
                            f"(cpu: {props.get('current_cpu_percent', '?')}%, "
                            f"mem: {props.get('current_memory_mb', '?')}MB, "
                            f"status: {props.get('container_status', '?')}, "
                            f"health: {props.get('health_status', 'none')})"
                        )
                else:
                    summary["errors"] += 1

            except Exception as e:
                logger.error(f"❌ [DISCOVERY] Error processing {container_name}: {e}")
                summary["errors"] += 1

        logger.info(
            f"✅ [DISCOVERY] Run complete — "
            f"{summary['updated']}/{summary['discovered']} updated, "
            f"{summary['new_cis']} new CIs, "
            f"{summary['errors']} errors"
        )
        return summary

    def _ci_exists(self, name: str) -> bool:
        """Check whether a CI node exists (used only for new-vs-update logging)."""
        if not self._driver:
            return True  # Assume exists to avoid noisy 'new CI' logs when disconnected
        try:
            with self._driver.session() as session:
                result = session.run(
                    "MATCH (ci:ConfigurationItem {name: $name}) RETURN ci.name LIMIT 1",
                    name=name,
                )
                return result.single() is not None
        except Exception:
            return True

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
