"""
Canonical event-type taxonomy for the Agentic OS platform.

Hierarchy: domain.resource.symptom
  - domain   : top-level classification (infrastructure, container, application, …)
  - resource  : affected component within the domain
  - symptom   : observable condition (what is wrong)

Each entry is a dict with:
  code         : the canonical type string (primary key in DB)
  label        : short human-readable name shown in the UI
  description  : one-sentence explanation of what the event means
  category     : top-level domain (first segment of code)
  aliases      : list of legacy flat event-type strings that map to this code
                 (used by the normalizer for backward compatibility)
  is_system    : True = shipped with the platform, cannot be deleted

Naming rules:
  - All lowercase, dot-separated, underscores within segments
  - 3 segments for most types: domain.resource.symptom
  - 4 segments allowed for cloud-provider-specific types: cloud.provider.resource.symptom
  - 'custom' is the only single-segment type (catch-all)
"""

from typing import TypedDict


class TaxonomyEntry(TypedDict):
    code: str
    label: str
    description: str
    category: str
    aliases: list[str]
    is_system: bool
    default_severity: str | None  # info | warning | critical | None — watcher-native types only


def _e(
    code: str, label: str, description: str,
    aliases: list[str] | None = None,
    default_severity: str | None = None,
) -> TaxonomyEntry:
    """Helper to build an entry — derives category from the first code segment."""
    return TaxonomyEntry(
        code=code,
        label=label,
        description=description,
        category=code.split(".")[0],
        aliases=aliases or [],
        is_system=True,
        default_severity=default_severity,
    )


# ─────────────────────────────────────────────────────────────────────────────
# INFRASTRUCTURE — host / OS / bare-metal / VM level
# ─────────────────────────────────────────────────────────────────────────────

INFRASTRUCTURE: list[TaxonomyEntry] = [
    # Compute
    _e("infrastructure.compute.cpu_high",
       "High CPU Utilization",
       "Sustained CPU utilization above threshold (e.g., >85% for >5 min).",
       aliases=["high_cpu"], default_severity="warning"),
    _e("infrastructure.compute.cpu_iowait_high",
       "High CPU I/O Wait",
       "CPU is spending excessive time waiting for disk or network I/O."),
    _e("infrastructure.compute.cpu_steal_high",
       "High CPU Steal",
       "Hypervisor is stealing CPU time from this VM (noisy-neighbour on shared host)."),
    _e("infrastructure.compute.memory_high",
       "High Memory Utilization",
       "RAM utilization sustained above threshold; risk of OOM or swapping.",
       aliases=["high_memory"], default_severity="critical"),
    _e("infrastructure.compute.memory_oom_kill",
       "OOM Kill Detected",
       "The Linux kernel OOM killer terminated one or more processes."),
    _e("infrastructure.compute.swap_high",
       "High Swap Usage",
       "Swap utilization is elevated, indicating memory pressure."),
    _e("infrastructure.compute.process_crash",
       "Process / Service Crashed",
       "A managed process or systemd service exited unexpectedly."),
    _e("infrastructure.compute.load_high",
       "High System Load",
       "System load average exceeds the number of CPU cores for a sustained period."),
    _e("infrastructure.compute.syscall_intensity_high",
       "High Syscall Intensity",
       "System call rate is abnormally high (detected by eBPF/Sentinel monitoring).",
       aliases=["high_syscall_intensity"], default_severity="critical"),
    _e("infrastructure.compute.reboot_required",
       "Reboot Required",
       "A kernel update or patch requires a host reboot to take effect."),
    _e("infrastructure.compute.clock_skew",
       "Clock Skew Detected",
       "System clock is drifting or not synchronised with NTP."),

    # Storage
    _e("infrastructure.storage.disk_full",
       "Disk Full",
       "Filesystem usage has reached a critical threshold (e.g., >90%).",
       aliases=["disk_full"], default_severity="critical"),
    _e("infrastructure.storage.disk_filling_fast",
       "Disk Filling Rapidly",
       "At current write rate, the disk will be full within 4–24 hours."),
    _e("infrastructure.storage.inode_exhausted",
       "Inode Table Exhausted",
       "No free inodes remain; new files cannot be created despite available disk space."),
    _e("infrastructure.storage.io_latency_high",
       "High Disk I/O Latency",
       "Read or write latency for disk operations is above acceptable threshold."),
    _e("infrastructure.storage.io_saturation",
       "Disk I/O Saturation",
       "Disk throughput is at capacity; I/O operations are queuing."),
    _e("infrastructure.storage.mount_failure",
       "Filesystem Mount Failure",
       "A local or remote filesystem (NFS, CIFS) failed to mount or was unmounted unexpectedly."),

    # Network (host-level — NICs, bonds, routes)
    _e("infrastructure.network.interface_down",
       "Network Interface Down",
       "A physical or virtual NIC is in a link-down state.",
       aliases=["network_issue"]),
    _e("infrastructure.network.latency_high",
       "High Network Latency",
       "Round-trip time between hosts is elevated beyond expected baseline."),
    _e("infrastructure.network.packet_loss",
       "Packet Loss Detected",
       "Network packet loss rate is above acceptable threshold."),
    _e("infrastructure.network.bandwidth_saturation",
       "Network Bandwidth Saturation",
       "NIC or uplink is at or near maximum throughput capacity."),
    _e("infrastructure.network.dns_failure",
       "DNS Resolution Failure",
       "DNS lookups are failing or returning incorrect results."),
    _e("infrastructure.network.port_unreachable",
       "Port Unreachable",
       "A TCP/UDP port that should be open is not accepting connections."),
    _e("infrastructure.network.bond_degraded",
       "Network Bond Degraded",
       "One or more members of a network bond/team are down, reducing redundancy."),

    # Hardware
    _e("infrastructure.hardware.temperature_high",
       "Hardware Temperature High",
       "CPU, GPU, or chassis temperature exceeds safe operating threshold."),
    _e("infrastructure.hardware.raid_failure",
       "RAID Array Failure",
       "A RAID member disk has failed or the array has entered a degraded/inactive state."),
    _e("infrastructure.hardware.power_failure",
       "Power Supply Failure",
       "A PSU has failed or UPS is running on battery."),
]

# ─────────────────────────────────────────────────────────────────────────────
# CONTAINER — Kubernetes / Docker workloads
# ─────────────────────────────────────────────────────────────────────────────

CONTAINER: list[TaxonomyEntry] = [
    # Pod
    _e("container.pod.crash_looping",
       "Pod Crash Loop",
       "A container is repeatedly crashing and being restarted (CrashLoopBackOff).",
       aliases=["pod_crash"]),
    _e("container.pod.not_ready",
       "Pod Not Ready",
       "Pod has been in a non-ready state beyond the expected startup window."),
    _e("container.pod.oom_killed",
       "Pod OOM Killed",
       "Container was terminated by the kernel OOM killer due to memory limit breach."),
    _e("container.pod.image_pull_error",
       "Image Pull Error",
       "Kubernetes cannot pull the container image (bad tag, registry unreachable, auth failure)."),
    _e("container.pod.pending_stuck",
       "Pod Stuck Pending",
       "Pod cannot be scheduled — insufficient resources, node selector, or PVC not bound."),

    # Deployment
    _e("container.deployment.replicas_mismatch",
       "Deployment Replicas Mismatch",
       "Actual replica count does not match the desired count for a Deployment."),
    _e("container.deployment.rollout_stuck",
       "Deployment Rollout Stuck",
       "A rolling update has not progressed within the expected time window."),

    # StatefulSet
    _e("container.statefulset.replicas_mismatch",
       "StatefulSet Replicas Mismatch",
       "Actual replica count does not match the desired count for a StatefulSet."),
    _e("container.statefulset.rollout_stuck",
       "StatefulSet Rollout Stuck",
       "A StatefulSet rolling update has stalled."),

    # DaemonSet
    _e("container.daemonset.not_scheduled",
       "DaemonSet Not Scheduled",
       "DaemonSet pods are not scheduled on one or more eligible nodes."),
    _e("container.daemonset.rollout_stuck",
       "DaemonSet Rollout Stuck",
       "A DaemonSet update has not completed within the expected time window."),

    # Node
    _e("container.node.not_ready",
       "Node Not Ready",
       "A Kubernetes node has entered the NotReady condition."),
    _e("container.node.memory_pressure",
       "Node Memory Pressure",
       "Node is under memory pressure; pods may be evicted."),
    _e("container.node.disk_pressure",
       "Node Disk Pressure",
       "Node disk usage is high; pod scheduling may be blocked."),
    _e("container.node.pid_pressure",
       "Node PID Pressure",
       "Node process ID count is near the kernel limit."),
    _e("container.node.network_unavailable",
       "Node Network Unavailable",
       "Node's network plugin is not configured correctly."),

    # Persistent Volumes
    _e("container.pvc.filling_up",
       "PVC Filling Up",
       "A PersistentVolumeClaim is approaching capacity."),
    _e("container.pvc.errors",
       "PVC Errors",
       "A PersistentVolume is in a failed or error state."),
    _e("container.pvc.pending",
       "PVC Pending",
       "A PersistentVolumeClaim has been pending without binding for too long."),

    # Jobs / HPA
    _e("container.job.failed",
       "Kubernetes Job Failed",
       "A Kubernetes Job (or CronJob run) has failed."),
    _e("container.hpa.maxed_out",
       "HPA at Maximum Replicas",
       "Horizontal Pod Autoscaler has reached its maximum replica count and cannot scale further."),
    _e("container.hpa.unable_to_scale",
       "HPA Unable to Scale",
       "HPA cannot fetch metrics or the target is unavailable, preventing scaling decisions."),

    # Control Plane
    _e("container.controlplane.etcd_no_leader",
       "etcd No Leader",
       "The etcd cluster has no elected leader; the cluster is unavailable."),
    _e("container.controlplane.etcd_member_down",
       "etcd Member Down",
       "One or more etcd cluster members are unreachable."),
    _e("container.controlplane.apiserver_latency",
       "API Server High Latency",
       "Kubernetes API server request latency is elevated."),
    _e("container.controlplane.apiserver_errors",
       "API Server Error Rate High",
       "Kubernetes API server is returning a high rate of error responses."),

    # Runtime Security (Falco)
    _e("container.runtime.shell_in_container",
       "Shell Spawned in Container",
       "An interactive shell was opened inside a running container (Falco rule)."),
    _e("container.runtime.privileged_container",
       "Privileged Container Started",
       "A container was started with the privileged flag set."),
    _e("container.runtime.container_drift",
       "Container Drift Detected",
       "A new executable not present in the original image was executed (Falco)."),
    _e("container.runtime.sensitive_file_read",
       "Sensitive File Read in Container",
       "A process read a sensitive file (/etc/shadow, credentials) inside a container."),

    # Policy (OPA / Admission)
    _e("container.policy.no_resource_limits",
       "No Resource Limits Set",
       "A workload was deployed without CPU/memory resource limits (OPA policy violation)."),
    _e("container.policy.image_not_pinned",
       "Image Tag Not Pinned",
       "A workload is using the ':latest' image tag instead of a pinned digest."),
]

# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION — services, APIs, messaging, deployments, runtime
# ─────────────────────────────────────────────────────────────────────────────

APPLICATION: list[TaxonomyEntry] = [
    # Availability
    _e("application.availability.service_down",
       "Service Down",
       "Service is completely unreachable — all health checks failing.",
       aliases=["service_down"]),
    _e("application.availability.service_unresponsive",
       "Service Unresponsive",
       "Service is reachable but not responding correctly to health checks.",
       aliases=["service_unresponsive"], default_severity="critical"),
    _e("application.availability.health_check_failing",
       "Health Check Failing",
       "Service health endpoint is returning errors or unexpected responses.",
       aliases=["health_check_failed"], default_severity="warning"),
    _e("application.availability.dependency_unavailable",
       "Dependency Unavailable",
       "An upstream service or external dependency this service relies on is down."),
    _e("application.availability.circuit_breaker_open",
       "Circuit Breaker Open",
       "A circuit breaker has tripped, blocking calls to a downstream dependency."),

    # Performance
    _e("application.performance.error_rate_high",
       "High Error Rate",
       "HTTP 5xx or application error rate is sustained above threshold.",
       aliases=["high_error_rate"], default_severity="info"),
    _e("application.performance.error_rate_spike",
       "Error Rate Spike",
       "Sudden, sharp increase in error rate above normal baseline.",
       aliases=["error_rate_spike"]),
    _e("application.performance.latency_high",
       "High Response Latency",
       "p95/p99 response latency is sustained above SLO threshold.",
       aliases=["high_latency"], default_severity="warning"),
    _e("application.performance.latency_spike",
       "Latency Spike",
       "Sudden spike in response latency above normal operating baseline.",
       aliases=["latency_spike"]),
    _e("application.performance.throughput_low",
       "Low Throughput",
       "Request rate (RPS/TPS) is below expected operational baseline."),
    _e("application.performance.throughput_drop",
       "Throughput Drop",
       "Sudden, significant drop in request rate — may indicate upstream routing issue."),
    _e("application.performance.timeout_rate_high",
       "High Timeout Rate",
       "A high proportion of requests are timing out before completing."),
    _e("application.performance.apdex_degraded",
       "Apdex Score Degraded",
       "Application Performance Index (Apdex) score has dropped below acceptable level."),
    _e("application.performance.saturation_high",
       "Service Saturation High",
       "Service is approaching capacity limits (thread pool, connection pool, queue)."),

    # Deployment
    _e("application.deployment.deploy_failed",
       "Deployment Failed",
       "A release or deployment pipeline failed to complete successfully."),
    _e("application.deployment.rollback_triggered",
       "Rollback Triggered",
       "An automated or manual rollback has been initiated."),
    _e("application.deployment.config_error",
       "Configuration Error",
       "A configuration change caused an application error or startup failure."),

    # Runtime (JVM, CLR, serverless)
    _e("application.runtime.jvm_heap_high",
       "JVM Heap High",
       "JVM heap utilization is critically high; GC pressure or OOM risk."),
    _e("application.runtime.jvm_gc_pressure",
       "JVM GC Pressure",
       "Excessive time spent in garbage collection, impacting throughput."),
    _e("application.runtime.thread_pool_exhausted",
       "Thread Pool Exhausted",
       "Application thread pool is saturated; requests are being queued or rejected."),
    _e("application.runtime.crash_rate_high",
       "Application Crash Rate High",
       "Mobile app or serverless function crash rate exceeds acceptable threshold."),

    # Messaging / Queues
    _e("application.messaging.queue_depth_critical",
       "Queue Depth Critical",
       "Message queue depth is at a critical level, indicating consumer lag or outage.",
       aliases=["queue_depth_critical"]),
    _e("application.messaging.consumer_lag_high",
       "Consumer Lag High",
       "Message consumer group is falling significantly behind the producer."),
    _e("application.messaging.dead_letter_high",
       "Dead-Letter Queue Accumulating",
       "Messages are accumulating in the dead-letter queue indicating processing failures."),
    _e("application.messaging.broker_down",
       "Message Broker Down",
       "The message broker (RabbitMQ, Kafka, etc.) is unavailable."),
    _e("application.messaging.unroutable_messages",
       "Unroutable Messages",
       "Messages cannot be routed to any queue (RabbitMQ exchange misconfiguration)."),
]

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE — all engines: RDBMS, NoSQL, cache, search
# ─────────────────────────────────────────────────────────────────────────────

DATABASE: list[TaxonomyEntry] = [
    # Availability
    _e("database.availability.down",
       "Database Down",
       "Database engine is not running or completely unreachable.",
       aliases=["database_error"]),
    _e("database.availability.connection_failed",
       "Database Connection Failed",
       "Application cannot establish a connection to the database."),
    _e("database.availability.primary_failover",
       "Primary Failover",
       "The primary database node has failed over to a replica."),
    _e("database.availability.replica_failed",
       "Replica Failed",
       "A replica / standby database node is down or unreachable."),

    # Performance
    _e("database.performance.slow_query",
       "Slow Query Detected",
       "One or more queries are exceeding the slow query threshold."),
    _e("database.performance.query_timeout",
       "Query Timeout",
       "Queries are timing out before completing execution."),
    _e("database.performance.deadlock",
       "Deadlock Detected",
       "Transactions are deadlocking, causing one or both to be rolled back."),
    _e("database.performance.lock_contention",
       "Lock Contention High",
       "Excessive lock waits are degrading database throughput."),
    _e("database.performance.high_rollback_rate",
       "High Transaction Rollback Rate",
       "A significant proportion of transactions are being rolled back."),
    _e("database.performance.commit_rate_low",
       "Commit Rate Low",
       "Transaction commit rate has dropped significantly below baseline."),

    # Connections
    _e("database.connections.pool_exhausted",
       "Connection Pool Exhausted",
       "Application-side connection pool has no available connections.",
       aliases=["db_connection_pool_exhausted"]),
    _e("database.connections.max_connections_reached",
       "Max Connections Reached",
       "Database server has hit its maximum allowed connection count."),
    _e("database.connections.connection_refused",
       "Connection Refused",
       "Database is actively refusing new connections."),

    # Replication
    _e("database.replication.lag_high",
       "Replication Lag High",
       "Replica is significantly behind the primary; data may be stale."),
    _e("database.replication.replica_not_running",
       "Replication Not Running",
       "Replication thread/process has stopped on the replica.",
       aliases=["db_replication_stopped"]),
    _e("database.replication.split_brain",
       "Replication Split Brain",
       "Two or more nodes believe they are the primary (split-brain scenario)."),
    _e("database.replication.headroom_low",
       "Replication Headroom Low",
       "Replica is catching up but the gap is growing faster than it can close."),

    # Storage
    _e("database.storage.disk_full",
       "Database Disk Full",
       "The disk hosting database files is critically full."),
    _e("database.storage.tablespace_full",
       "Tablespace Full",
       "A database tablespace (Oracle, PostgreSQL) has reached capacity."),
    _e("database.storage.table_bloat_high",
       "Table Bloat High",
       "Dead tuple accumulation has significantly bloated table storage (PostgreSQL)."),
    _e("database.storage.index_bloat_high",
       "Index Bloat High",
       "Index has become significantly bloated with dead entries (PostgreSQL)."),
    _e("database.storage.backup_failed",
       "Database Backup Failed",
       "Scheduled database backup did not complete successfully."),
    _e("database.storage.wal_disk_high",
       "WAL Disk Usage High",
       "Write-Ahead Log files are consuming excessive disk space (PostgreSQL)."),
    _e("database.storage.autovacuum_blocked",
       "Autovacuum Blocked",
       "Autovacuum cannot run due to long-running transactions holding back the horizon (PostgreSQL)."),

    # Cache (Redis, Memcached)
    _e("database.cache.hit_ratio_low",
       "Cache Hit Ratio Low",
       "Cache hit rate has dropped; more requests are hitting the database directly."),
    _e("database.cache.memory_high",
       "Cache Memory High",
       "Cache memory usage is near or at the configured maxmemory limit (Redis)."),
    _e("database.cache.eviction_rate_high",
       "Cache Eviction Rate High",
       "Cache is evicting keys at a high rate due to memory pressure."),
    _e("database.cache.replication_broken",
       "Cache Replication Broken",
       "Cache replication between master and replica has been interrupted (Redis Sentinel)."),

    # Cluster (Elasticsearch, MongoDB, Redis Cluster)
    _e("database.cluster.node_down",
       "Cluster Node Down",
       "A node in a distributed database cluster is unreachable."),
    _e("database.cluster.unassigned_shards",
       "Unassigned Shards",
       "Elasticsearch cluster has shards that cannot be assigned to any node."),
    _e("database.cluster.split_brain",
       "Cluster Split Brain",
       "Database cluster has split into two partitions that cannot see each other."),
]

# ─────────────────────────────────────────────────────────────────────────────
# CLOUD — cloud-provider-managed resource events
# ─────────────────────────────────────────────────────────────────────────────

CLOUD: list[TaxonomyEntry] = [
    # AWS — EC2
    _e("cloud.aws.ec2.status_check_failed",
       "EC2 Status Check Failed",
       "AWS EC2 instance or system status check is failing."),
    _e("cloud.aws.ec2.network_saturation",
       "EC2 Network Saturation",
       "EC2 instance network bandwidth is saturated."),

    # AWS — RDS
    _e("cloud.aws.rds.storage_low",
       "RDS Storage Low",
       "RDS instance free storage space is critically low."),
    _e("cloud.aws.rds.replication_lag",
       "RDS Replication Lag",
       "RDS read replica is significantly behind the primary instance."),
    _e("cloud.aws.rds.connection_high",
       "RDS Connections High",
       "RDS database connections are approaching the maximum limit."),
    _e("cloud.aws.rds.cpu_high",
       "RDS CPU High",
       "RDS instance CPU utilization is sustained above threshold."),
    _e("cloud.aws.rds.burst_balance_low",
       "RDS Burst Balance Low",
       "RDS gp2 I/O burst credits are depleted; performance will be throttled."),

    # AWS — ALB / ELB
    _e("cloud.aws.alb.unhealthy_hosts",
       "ALB Unhealthy Targets",
       "One or more ALB target group members are failing health checks."),
    _e("cloud.aws.alb.5xx_elevated",
       "ALB 5xx Errors Elevated",
       "Application Load Balancer is returning elevated HTTP 5xx error rates."),
    _e("cloud.aws.alb.latency_high",
       "ALB Latency High",
       "ALB target response time is above acceptable threshold."),
    _e("cloud.aws.alb.connection_rejected",
       "ALB Connections Rejected",
       "ALB is rejecting connections due to surge queue or listener limits."),

    # AWS — Lambda
    _e("cloud.aws.lambda.error_rate_high",
       "Lambda Error Rate High",
       "AWS Lambda function invocation error rate is above threshold."),
    _e("cloud.aws.lambda.throttled",
       "Lambda Throttled",
       "Lambda invocations are being throttled due to concurrency limits."),
    _e("cloud.aws.lambda.duration_high",
       "Lambda Duration High",
       "Lambda function execution duration is approaching the configured timeout."),
    _e("cloud.aws.lambda.dlq_errors",
       "Lambda DLQ Errors",
       "Lambda function is failing to send messages to its dead-letter queue."),
    _e("cloud.aws.lambda.iterator_age_high",
       "Lambda Iterator Age High",
       "Stream trigger (Kinesis/DynamoDB) processing is falling behind."),

    # AWS — SQS
    _e("cloud.aws.sqs.queue_age_high",
       "SQS Message Age High",
       "Oldest message in the SQS queue is too old, indicating consumer lag."),
    _e("cloud.aws.sqs.dlq_messages",
       "SQS Dead-Letter Queue Messages",
       "Messages are accumulating in the SQS dead-letter queue."),

    # AWS — ECS
    _e("cloud.aws.ecs.memory_high",
       "ECS Task Memory High",
       "ECS task or service memory utilization is above threshold."),
    _e("cloud.aws.ecs.task_stopped",
       "ECS Task Stopped",
       "ECS task has stopped unexpectedly outside of a deployment."),

    # AWS — CloudFront
    _e("cloud.aws.cloudfront.error_rate_high",
       "CloudFront Error Rate High",
       "CloudFront distribution is returning elevated 4xx or 5xx error rates."),

    # Azure — VM
    _e("cloud.azure.vm.cpu_high",
       "Azure VM CPU High",
       "Azure Virtual Machine CPU percentage is above threshold."),
    _e("cloud.azure.vm.disk_latency",
       "Azure VM Disk Latency",
       "Azure VM OS or data disk latency is elevated."),

    # Azure — App Service
    _e("cloud.azure.appservice.http5xx",
       "Azure App Service HTTP 5xx",
       "Azure App Service is returning elevated HTTP 5xx error rates."),

    # Azure — Service Health
    _e("cloud.azure.servicehealth.outage",
       "Azure Service Outage",
       "Microsoft Azure has declared a service outage affecting this region or service."),
    _e("cloud.azure.resourcehealth.unavailable",
       "Azure Resource Unavailable",
       "An Azure resource is reporting an Unavailable health status."),

    # GCP — GCE
    _e("cloud.gcp.gce.cpu_high",
       "GCE Instance CPU High",
       "GCP Compute Engine instance CPU utilization is above threshold."),

    # GCP — Cloud SQL
    _e("cloud.gcp.cloudsql.disk_high",
       "Cloud SQL Disk High",
       "GCP Cloud SQL instance disk utilization is critically high."),
    _e("cloud.gcp.cloudsql.replication_lag",
       "Cloud SQL Replication Lag",
       "GCP Cloud SQL read replica is significantly behind the primary."),

    # GCP — Uptime / Monitoring
    _e("cloud.gcp.uptime.probe_failed",
       "GCP Uptime Probe Failed",
       "A GCP Cloud Monitoring uptime check has failed for a target."),
]

# ─────────────────────────────────────────────────────────────────────────────
# NETWORK — network devices, load balancers, proxies, TLS
# ─────────────────────────────────────────────────────────────────────────────

NETWORK: list[TaxonomyEntry] = [
    # Physical / Virtual Interface
    _e("network.interface.down",
       "Network Interface Down",
       "A physical or virtual network interface has gone down (linkDown trap)."),
    _e("network.interface.utilization_high",
       "Interface Utilization High",
       "Network interface is approaching bandwidth saturation."),
    _e("network.interface.error_rate_high",
       "Interface Error Rate High",
       "Network interface is generating excessive CRC, input, or output errors."),
    _e("network.interface.flapping",
       "Interface Flapping",
       "Network interface is repeatedly cycling between up and down states."),

    # Routing (BGP, OSPF)
    _e("network.bgp.session_down",
       "BGP Session Down",
       "A BGP peering session has gone down (bgpBackwardTransition trap)."),
    _e("network.bgp.prefix_limit_exceeded",
       "BGP Prefix Limit Exceeded",
       "A BGP peer has sent more prefixes than the configured limit."),
    _e("network.ospf.neighbor_down",
       "OSPF Neighbor Down",
       "An OSPF adjacency with a neighbour has been lost."),

    # DNS
    _e("network.dns.resolution_failure",
       "DNS Resolution Failure",
       "DNS queries are failing for one or more domains.",
       aliases=["dns_failure"]),
    _e("network.dns.latency_high",
       "DNS Latency High",
       "DNS resolution latency is elevated above acceptable threshold."),

    # TLS / Certificates
    _e("network.tls.certificate_expiring",
       "TLS Certificate Expiring",
       "A TLS certificate will expire within the warning threshold (e.g., 30 days).",
       aliases=["certificate_expiry"], default_severity="warning"),
    _e("network.tls.certificate_expired",
       "TLS Certificate Expired",
       "A TLS certificate has already expired; connections will fail."),
    _e("network.tls.certificate_invalid",
       "TLS Certificate Invalid",
       "TLS certificate validation is failing (wrong hostname, untrusted CA, revoked)."),

    # Proxy / Load Balancer (Nginx, HAProxy, etc.)
    _e("network.proxy.http5xx_rate_high",
       "Proxy HTTP 5xx Rate High",
       "A reverse proxy or load balancer is returning elevated 5xx error rates."),
    _e("network.proxy.latency_high",
       "Proxy Latency High",
       "Request latency through the proxy layer is above acceptable threshold."),
    _e("network.proxy.backend_errors",
       "Proxy Backend Errors",
       "The proxy is receiving errors from backend servers (connection refused, timeout)."),
    _e("network.proxy.session_limit",
       "Proxy Session Limit Reached",
       "Proxy has reached its maximum concurrent connection or session limit."),

    # Uptime / Blackbox
    _e("network.uptime.probe_failed",
       "Uptime Probe Failed",
       "A blackbox/uptime probe (ping, HTTP, TCP) for an external target has failed."),
    _e("network.uptime.slow_probe",
       "Uptime Probe Slow",
       "A blackbox probe succeeded but response time exceeded the latency threshold."),
]

# ─────────────────────────────────────────────────────────────────────────────
# SECURITY — auth, access, endpoint threats, compliance, vulnerability
# ─────────────────────────────────────────────────────────────────────────────

SECURITY: list[TaxonomyEntry] = [
    # Authentication
    _e("security.auth.failure_spike",
       "Authentication Failure Spike",
       "Authentication failure rate has spiked above normal baseline.",
       aliases=["auth_failure_spike"]),
    _e("security.auth.brute_force",
       "Brute Force Attack Detected",
       "Repeated failed login attempts from one or more sources indicate a brute force attempt."),
    _e("security.auth.credential_stuffing",
       "Credential Stuffing Detected",
       "Login attempts using lists of known breached credentials detected."),
    _e("security.auth.impossible_travel",
       "Impossible Travel Login",
       "Successful logins from geographically distant locations within an impossible time window."),
    _e("security.auth.mfa_bypass_attempt",
       "MFA Bypass Attempt",
       "Attempts to bypass multi-factor authentication detected."),
    _e("security.auth.dormant_account_active",
       "Dormant Account Activated",
       "An account that has been inactive for a long period suddenly became active."),
    _e("security.auth.password_spray",
       "Password Spray Attack",
       "A small number of passwords tried across many accounts to avoid lockout."),
    _e("security.auth.privileged_account_use",
       "Privileged Account Use",
       "An administrative or service account was used interactively or unexpectedly."),

    # Access Control
    _e("security.access.unauthorized_access",
       "Unauthorized Access Attempt",
       "An entity attempted to access a resource it is not authorized for.",
       aliases=["unauthorized_access"]),
    _e("security.access.privilege_escalation",
       "Privilege Escalation Detected",
       "A process or user account gained elevated privileges unexpectedly."),
    _e("security.access.admin_action_unauthorized",
       "Unauthorized Admin Action",
       "An administrative action was performed by an account without the required authorization."),

    # Endpoint / Host Threats
    _e("security.endpoint.malware_detected",
       "Malware Detected",
       "Antivirus or EDR solution detected malware on an endpoint."),
    _e("security.endpoint.ransomware_behavior",
       "Ransomware Behavior Detected",
       "Mass file encryption or ransomware-characteristic behavior detected on a host."),
    _e("security.endpoint.suspicious_process",
       "Suspicious Process Execution",
       "A process with suspicious characteristics was launched (unusual parent, obfuscated args)."),
    _e("security.endpoint.config_tampering",
       "Configuration Tampering",
       "A system configuration file was modified outside of a change window.",
       aliases=["config_tampering"]),
    _e("security.endpoint.lolbin_abuse",
       "Living-Off-The-Land Binary Abuse",
       "A legitimate system binary (certutil, powershell, wmic) is being abused for malicious activity."),

    # Network Threats
    _e("security.network.port_scan",
       "Port Scan Detected",
       "A systematic sweep of ports on one or more hosts has been detected."),
    _e("security.network.lateral_movement",
       "Lateral Movement Detected",
       "Traffic patterns indicate an attacker moving between internal systems."),
    _e("security.network.c2_beacon",
       "C2 Beacon Detected",
       "Periodic outbound connections characteristic of command-and-control beaconing detected."),
    _e("security.network.dns_tunneling",
       "DNS Tunneling Detected",
       "DNS queries appear to be used as a covert data exfiltration or C2 channel."),
    _e("security.network.unusual_outbound",
       "Unusual Outbound Connection",
       "An internal host is making outbound connections to an unusual external destination."),

    # Data / Exfiltration
    _e("security.data.large_transfer",
       "Large Data Transfer",
       "An unusually large volume of data was transferred outbound."),
    _e("security.data.sensitive_file_access",
       "Sensitive File Accessed",
       "A file containing sensitive data was accessed by an unauthorized entity."),
    _e("security.data.dlp_violation",
       "DLP Policy Violation",
       "Data Loss Prevention policy was violated (e.g., PII sent via email or USB)."),
    _e("security.data.unauthorized_db_query",
       "Unauthorized Database Query",
       "A database query was executed by an account without appropriate permissions."),

    # Compliance
    _e("security.compliance.audit_log_cleared",
       "Audit Log Cleared",
       "Security audit logs were cleared or tampered with, potentially hiding activity."),
    _e("security.compliance.firewall_rule_changed",
       "Firewall Rule Changed",
       "A firewall rule was added, modified, or removed outside of a change window."),
    _e("security.compliance.encryption_disabled",
       "Encryption Disabled",
       "Encryption was disabled on a resource that requires it (disk, transport, backup)."),
    _e("security.compliance.privileged_account_created",
       "Privileged Account Created",
       "A new administrative or privileged account was created unexpectedly."),

    # Vulnerability
    _e("security.vulnerability.critical_cve_detected",
       "Critical CVE Detected",
       "A CVSS 9.0–10.0 vulnerability was detected in a running software component."),
    _e("security.vulnerability.high_cve_detected",
       "High CVE Detected",
       "A CVSS 7.0–8.9 vulnerability was detected in a running software component."),
    _e("security.vulnerability.unpatched_component",
       "Unpatched Component Detected",
       "A software component with known vulnerabilities has not been patched within SLA."),
    _e("security.vulnerability.exposed_port",
       "Unexpected Port Exposed",
       "A network scan revealed a port is exposed that should not be accessible externally."),
    _e("security.vulnerability.weak_cipher",
       "Weak Cipher in Use",
       "A service is using a deprecated or weak TLS cipher suite."),
]

# ─────────────────────────────────────────────────────────────────────────────
# LOG — log-file monitoring and pattern-based detection
# ─────────────────────────────────────────────────────────────────────────────

LOG: list[TaxonomyEntry] = [
    _e("log.error.spike",
       "Log Error Spike",
       "Error log rate has spiked significantly above the rolling baseline.",
       aliases=["log_error_detected"]),
    _e("log.error.pattern_detected",
       "Log Error Pattern Detected",
       "A configured error pattern or regex was matched in a monitored log file."),
    _e("log.exception.rate_high",
       "Exception Rate High",
       "Application exception rate in logs exceeds the configured threshold."),
    _e("log.warning.pattern_detected",
       "Log Warning Pattern Detected",
       "A configured warning pattern was matched in a monitored log file."),
    _e("log.access.unusual_pattern",
       "Unusual Access Log Pattern",
       "Access log analysis detected an unusual request pattern (scanner, scraper, abuse)."),
]

# ─────────────────────────────────────────────────────────────────────────────
# SYNTHETIC — SLO monitoring, uptime probes, external checks
# ─────────────────────────────────────────────────────────────────────────────

SYNTHETIC: list[TaxonomyEntry] = [
    _e("synthetic.transaction.failed",
       "Synthetic Transaction Failed",
       "A scripted multi-page transaction monitor (HAR-based login/journey replay) "
       "failed a status check or content assertion.",
       aliases=["synthetic_monitor_failed"], default_severity="critical"),
    _e("synthetic.uptime.probe_failed",
       "Uptime Probe Failed",
       "An external uptime check (HTTP, TCP, DNS, ping) has failed for a target URL or host."),
    _e("synthetic.uptime.ssl_expiry",
       "SSL Certificate Expiry Warning",
       "An uptime probe detected that the target's SSL certificate will expire soon."),
    _e("synthetic.uptime.slow_response",
       "Uptime Probe Slow Response",
       "Target responded to uptime probe but latency exceeded the configured threshold."),
    _e("synthetic.slo.error_budget_burn_fast",
       "SLO Error Budget Burning Fast",
       "Error budget is being consumed at a rate that will exhaust it within hours."),
    _e("synthetic.slo.error_budget_burn_slow",
       "SLO Error Budget Burning Slow",
       "Error budget is being consumed at a rate that will exhaust it within days."),
    _e("synthetic.slo.availability_breach",
       "SLO Availability Breach",
       "Service availability has dropped below the committed SLO threshold."),
]

# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM — user-defined / unmapped event types
# ─────────────────────────────────────────────────────────────────────────────

CUSTOM: list[TaxonomyEntry] = [
    _e("custom",
       "Custom / User-Defined",
       "User-defined event type that does not map to a canonical platform category.",
       aliases=["custom"]),
]

# ─────────────────────────────────────────────────────────────────────────────
# Full taxonomy — single source of truth
# ─────────────────────────────────────────────────────────────────────────────

ALL_ENTRIES: list[TaxonomyEntry] = (
    INFRASTRUCTURE
    + CONTAINER
    + APPLICATION
    + DATABASE
    + CLOUD
    + NETWORK
    + SECURITY
    + LOG
    + SYNTHETIC
    + CUSTOM
)

# Fast lookup by code
BY_CODE: dict[str, TaxonomyEntry] = {e["code"]: e for e in ALL_ENTRIES}

# Alias → canonical code map (for the normalizer)
ALIAS_MAP: dict[str, str] = {
    alias: entry["code"]
    for entry in ALL_ENTRIES
    for alias in entry["aliases"]
}

# All canonical codes as a frozenset (for O(1) membership tests)
CANONICAL_CODES: frozenset[str] = frozenset(BY_CODE.keys())

# All category/domain names
DOMAINS: frozenset[str] = frozenset(e["category"] for e in ALL_ENTRIES)
