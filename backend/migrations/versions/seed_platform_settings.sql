-- Migration: Seed default platform_settings (watcher thresholds)
-- Date: 2026-05-17
-- Purpose: Ensure all watcher configuration keys exist with sensible defaults.
--          Uses INSERT … ON CONFLICT DO NOTHING so existing customised values
--          are never overwritten.

INSERT INTO platform_settings (key, value, value_type, category, label, description, updated_at)
VALUES
  -- ── Polling & timing ──────────────────────────────────────────────────
  ('watcher.poll_interval',            '20',    'int',   'watcher', 'Poll Interval (s)',
   'How often the watcher samples Docker stats and runs health checks (seconds).', NOW()),

  ('watcher.cooldown_seconds',         '30',    'int',   'watcher', 'Incident Cooldown (s)',
   'Minimum gap between incidents for the same resource + event_type pair (seconds).', NOW()),

  ('watcher.min_consecutive_polls',    '2',     'int',   'watcher', 'Min Consecutive Polls',
   'Number of consecutive threshold breaches required before an incident is created.', NOW()),

  -- ── Resource thresholds ───────────────────────────────────────────────
  ('watcher.cpu_threshold',            '90.0',  'float', 'watcher', 'CPU Alert Threshold (%)',
   'Container CPU usage percentage above which a high_cpu event is generated.', NOW()),

  ('watcher.memory_threshold',         '90.0',  'float', 'watcher', 'Memory Alert Threshold (%)',
   'Container memory usage percentage above which a high_memory event is generated.', NOW()),

  ('watcher.disk_threshold',           '90.0',  'float', 'watcher', 'Disk Alert Threshold (%)',
   'Host disk usage percentage above which a disk_full event is generated.', NOW()),

  ('watcher.syscall_threshold',        '9000',  'int',   'watcher', 'Syscall Anomaly Threshold',
   'Syscall rate (per second) above which a high_syscall_intensity event is generated.', NOW()),

  ('watcher.connection_threshold',     '1000',  'int',   'watcher', 'Network Connection Threshold',
   'Open TCP connection count above which a network_anomaly event is generated.', NOW()),

  -- ── CMDB discovery ────────────────────────────────────────────────────
  ('watcher.discovery_enabled',        'true',  'bool',  'watcher', 'CMDB Discovery Enabled',
   'When true, newly discovered containers are automatically registered in Neo4j CMDB.', NOW()),

  ('watcher.discovery_interval_polls', '60',    'int',   'watcher', 'Discovery Interval (polls)',
   'How many poll cycles between full container discovery scans.', NOW())

ON CONFLICT (key) DO NOTHING;
