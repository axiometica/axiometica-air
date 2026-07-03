-- Seed runbook for high-intensity calls on Yes service
-- This runbook handles traffic spikes and elevated CPU/latency

INSERT INTO runbooks (
  id, name, description, event_type, service, environment,
  diagnostics, actions, verification_steps,
  confidence, blast_radius, enabled, created_at, updated_at
) VALUES (
  '550e8400-e29b-41d4-a716-446655440001',
  'Yes Service - High Intensity Calls Remediation',
  'Automated response for high-intensity call patterns on Yes service. Includes traffic mitigation, resource scaling, and dependency protection.',
  'high_cpu',
  'yes-service',
  'prod',

  -- DIAGNOSTICS (2 steps)
  '[
    {
      "order": 1,
      "type": "diagnostic",
      "name": "Analyze Request Patterns",
      "description": "Check current request queue depth, latency percentiles, and traffic rate",
      "tool": "prometheus_query",
      "args": {
        "queries": [
          "rate(http_requests_total{service=\"yes-service\"}[1m])",
          "histogram_quantile(0.95, http_request_duration_seconds_bucket{service=\"yes-service\"})",
          "queue_depth{service=\"yes-service\"}"
        ]
      }
    },
    {
      "order": 2,
      "type": "diagnostic",
      "name": "Check Dependency Health",
      "description": "Verify database connections, cache hit rate, and downstream service availability",
      "tool": "health_check",
      "args": {
        "targets": [
          "db-primary:5432",
          "redis-cache:6379",
          "auth-service:8000",
          "data-service:8000"
        ],
        "timeout": 5
      }
    }
  ]',

  -- REMEDIATION ACTIONS (8 steps)
  '[
    {
      "order": 1,
      "type": "remediation",
      "name": "Scale Up Service Replicas",
      "description": "Increase Yes service pod replicas by 50% to handle traffic spike",
      "tool": "kubectl_scale",
      "args": {
        "deployment": "yes-service",
        "namespace": "production",
        "scale_factor": 1.5,
        "max_replicas": 50
      }
    },
    {
      "order": 2,
      "type": "remediation",
      "name": "Clear Connection Pools",
      "description": "Flush stale database and cache connections, reset connection counters",
      "tool": "connection_pool_reset",
      "args": {
        "targets": ["postgres", "redis"],
        "force": false,
        "drain_timeout": 30
      }
    },
    {
      "order": 3,
      "type": "remediation",
      "name": "Increase Connection Limits",
      "description": "Temporarily increase database and service connection pool sizes",
      "tool": "connection_config_update",
      "args": {
        "pool_size": 500,
        "max_overflow": 100,
        "service": "yes-service"
      }
    },
    {
      "order": 4,
      "type": "remediation",
      "name": "Enable Request Coalescing",
      "description": "Batch similar requests to reduce duplicate work and improve throughput",
      "tool": "feature_flag_set",
      "args": {
        "feature": "request_coalescing",
        "service": "yes-service",
        "enabled": true,
        "batch_window_ms": 50
      }
    },
    {
      "order": 5,
      "type": "remediation",
      "name": "Activate Circuit Breaker",
      "description": "Enable circuit breaker for non-critical dependencies to fail fast and prevent cascading failures",
      "tool": "circuit_breaker_enable",
      "args": {
        "dependencies": ["optional-data-service", "analytics-service"],
        "failure_threshold": 0.5,
        "timeout_seconds": 2
      }
    },
    {
      "order": 6,
      "type": "remediation",
      "name": "Enable Response Compression",
      "description": "Activate gzip compression on responses to reduce bandwidth usage",
      "tool": "middleware_config",
      "args": {
        "middleware": "compression",
        "enabled": true,
        "min_size": 1024
      }
    },
    {
      "order": 7,
      "type": "remediation",
      "name": "Route Traffic to Secondary Region",
      "description": "Redirect portion of traffic to backup region to distribute load",
      "tool": "traffic_routing",
      "args": {
        "primary_weight": 70,
        "secondary_weight": 30,
        "regions": ["us-east-1", "us-west-2"]
      }
    },
    {
      "order": 8,
      "type": "remediation",
      "name": "Enable Auto-Scaling Alerts",
      "description": "Activate aggressive auto-scaling policy for rapid response to further spikes",
      "tool": "autoscaling_policy",
      "args": {
        "target_cpu": 60,
        "target_memory": 75,
        "scale_up_cooldown": 30,
        "scale_down_cooldown": 300
      }
    }
  ]',

  -- VERIFICATION STEPS (3 steps)
  '[
    {
      "order": 1,
      "name": "Verify Request Latency Improvement",
      "description": "Check that P95 latency has decreased below baseline",
      "metric": "http_request_duration_seconds",
      "threshold": 0.5,
      "check": "p95_latency < baseline"
    },
    {
      "order": 2,
      "name": "Verify Error Rate Acceptable",
      "description": "Ensure error rate remains below 1%",
      "metric": "http_requests_failed_total",
      "threshold": 0.01,
      "check": "error_rate < 1%"
    },
    {
      "order": 3,
      "name": "Verify Resource Health",
      "description": "Confirm CPU and memory usage are within acceptable ranges",
      "metric": "node_cpu_utilization",
      "threshold": 80,
      "check": "cpu < 80% AND memory < 85%"
    }
  ]',

  0.92,  -- confidence: high confidence in this runbook
  2,     -- blast_radius: medium (affects multiple pods/connections)
  true,  -- enabled
  NOW(),
  NOW()
) ON CONFLICT DO NOTHING;

-- Seed runbook for high syscall intensity anomalies
-- This runbook handles excessive syscall activity by identifying and killing the problematic process
INSERT INTO runbooks (
  id, name, description, event_type, service, environment,
  diagnostics, actions, verification_steps,
  confidence, blast_radius, enabled, created_at, updated_at
) VALUES (
  '550e8400-e29b-41d4-a716-446655440201',
  'High Syscall Intensity - Process Termination',
  'Handles excessive syscall activity by profiling the source process, gathering diagnostic data, and safely terminating it. Uses anomaly_process from watcher context to identify the specific process.',
  'high_syscall_intensity',
  NULL,
  'prod',

  -- DIAGNOSTICS (3 steps)
  '[
    {
      "order": 1,
      "type": "diagnostic",
      "name": "Syscall Rate Analysis",
      "description": "Measure current syscall rate and count for the anomalous process",
      "tool": "syscall_profiler",
      "args": {
        "process_name_from_context": "anomaly_process",
        "timeframe_seconds": 10,
        "show_syscall_count": true,
        "show_call_rate": true
      }
    },
    {
      "order": 2,
      "type": "diagnostic",
      "name": "Process Analysis",
      "description": "Get full details about the anomalous process - command line, resource usage, open files, network connections",
      "tool": "process_info",
      "args": {
        "process_name_from_context": "anomaly_process",
        "include_command_line": true,
        "include_resource_usage": true,
        "include_open_files": true,
        "include_network": true,
        "include_stack_trace": true
      }
    },
    {
      "order": 3,
      "type": "diagnostic",
      "name": "Dependency Check",
      "description": "Verify the process is not critical infrastructure or required by other services",
      "tool": "dependency_check",
      "args": {
        "process_name_from_context": "anomaly_process",
        "check_if_required": true,
        "check_parent_process": true
      }
    }
  ]',

  -- REMEDIATION ACTIONS (3 steps)
  '[
    {
      "order": 1,
      "type": "remediation",
      "name": "Terminate Process",
      "description": "Kill the anomalous process with SIGKILL (uses anomaly_process from context)",
      "tool": "process_kill",
      "args": {
        "process_name_from_context": "anomaly_process",
        "signal": "SIGKILL",
        "force": true
      }
    },
    {
      "order": 2,
      "type": "remediation",
      "name": "Verify Termination",
      "description": "Confirm the process is no longer running",
      "tool": "process_verify",
      "args": {
        "process_name_from_context": "anomaly_process",
        "should_exist": false,
        "timeout_seconds": 5
      }
    },
    {
      "order": 3,
      "type": "remediation",
      "name": "Post-Kill Monitoring",
      "description": "Monitor for process restart loops or related anomalies",
      "tool": "container_monitor",
      "args": {
        "monitor_process": true,
        "process_name_from_context": "anomaly_process",
        "check_interval": 2,
        "duration_seconds": 30,
        "alert_on_restart": true
      }
    }
  ]',

  -- VERIFICATION STEPS (3 steps)
  '[
    {
      "order": 1,
      "name": "Process Termination Confirmed",
      "description": "Process should no longer exist",
      "metric": "process_exists",
      "threshold": 0,
      "check": "equal"
    },
    {
      "order": 2,
      "name": "Syscall Rate Normalized",
      "description": "Syscall rate should return to baseline (<1000 syscalls/sec)",
      "metric": "syscalls_per_second",
      "threshold": 1000,
      "check": "less_than"
    },
    {
      "order": 3,
      "name": "Container Health",
      "description": "Container should remain running and stable",
      "metric": "container_healthy",
      "threshold": 1,
      "check": "equal"
    }
  ]',

  0.94,  -- confidence: very high confidence in this remediation
  1,     -- blast_radius: low (only kills one process)
  true,  -- enabled
  NOW(),
  NOW()
) ON CONFLICT DO NOTHING;
