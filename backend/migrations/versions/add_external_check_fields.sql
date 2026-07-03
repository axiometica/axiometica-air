-- Migration: Add container_name and service_name to watcher_external_checks
-- These fields link an external check to a specific Docker container for targeted remediation.
ALTER TABLE watcher_external_checks ADD COLUMN IF NOT EXISTS container_name VARCHAR(200) NOT NULL DEFAULT '';
ALTER TABLE watcher_external_checks ADD COLUMN IF NOT EXISTS service_name   VARCHAR(100) NOT NULL DEFAULT '';
