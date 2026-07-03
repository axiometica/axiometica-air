"""
ServiceNow CMDB class definitions and field mappings.

Each entry describes:
  - table:       ServiceNow REST table name
  - label:       Human-readable name for the UI
  - ci_class:    Value stored in snow_ci_cache.ci_class
  - fields:      Fields to request from SN Table API
  - display_key: Field used as the primary label in list views
"""

from typing import Any

CI_CLASSES: list[dict[str, Any]] = [
    {
        "table":        "cmdb_ci_service",
        "label":        "Service Instances",
        "ci_class":     "cmdb_ci_service",
        "display_key":  "name",
        # Skip Retired (7) and Absent (8) install_status CIs
        "encoded_query": "install_statusNOTIN7,8",
        "fields": [
            "sys_id", "name", "short_description", "description",
            "operational_status", "install_status",
            "business_criticality", "service_classification",
            "used_for", "environment",
            "owned_by", "managed_by", "support_group", "assignment_group",
            "location",
            "portfolio_status", "service_offering",
            "sys_updated_on", "sys_created_on",
        ],
    },
    {
        "table":        "cmdb_ci_service_offering",
        "label":        "Service Offerings",
        "ci_class":     "cmdb_ci_service_offering",
        "display_key":  "name",
        "encoded_query": "install_statusNOTIN7,8",
        "fields": [
            "sys_id", "name", "short_description",
            "parent", "vendor", "contract",
            "operational_status", "install_status",
            "service_classification", "service_commitment",
            "sys_updated_on", "sys_created_on",
        ],
    },
    {
        "table":        "cmdb_ci_server",
        "label":        "Servers",
        "ci_class":     "cmdb_ci_server",
        "display_key":  "name",
        "encoded_query": "install_statusNOTIN7,8",
        "fields": [
            "sys_id", "name", "host_name", "fqdn", "ip_address",
            "mac_address", "os", "os_version", "os_service_pack",
            "cpu_count", "cpu_speed", "cpu_type",
            "ram", "disk_space",
            "virtual", "environment", "classification",
            "operational_status", "install_status",
            "location", "managed_by", "assigned_to", "support_group",
            "business_criticality",
            "sys_updated_on", "sys_created_on",
        ],
    },
    {
        "table":        "cmdb_ci_linux_server",
        "label":        "Linux Servers",
        "ci_class":     "cmdb_ci_linux_server",
        "display_key":  "name",
        "encoded_query": "install_statusNOTIN7,8",
        "fields": [
            "sys_id", "name", "host_name", "fqdn", "ip_address",
            "os", "os_version", "kernel_release",
            "cpu_count", "ram", "disk_space",
            "virtual", "environment",
            "operational_status", "install_status",
            "location", "managed_by", "assigned_to", "support_group",
            "business_criticality",
            "sys_updated_on", "sys_created_on",
        ],
    },
    {
        "table":        "cmdb_ci_win_server",
        "label":        "Windows Servers",
        "ci_class":     "cmdb_ci_win_server",
        "display_key":  "name",
        "encoded_query": "install_statusNOTIN7,8",
        "fields": [
            "sys_id", "name", "host_name", "fqdn", "ip_address",
            "os", "os_version", "os_service_pack",
            "domain", "cpu_count", "ram", "disk_space",
            "virtual", "environment",
            "operational_status", "install_status",
            "location", "managed_by", "assigned_to", "support_group",
            "business_criticality",
            "sys_updated_on", "sys_created_on",
        ],
    },
    {
        "table":        "cmdb_rel_ci",
        "label":        "CI Relationships",
        "ci_class":     "cmdb_rel_ci",
        "display_key":  "type",
        # cmdb_rel_ci has no install_status — no filter applied
        "encoded_query": "",
        "fields": [
            "sys_id", "parent", "child", "type",
            "sys_updated_on",
        ],
    },
]

# Lookup map: ci_class → definition
CI_CLASS_MAP: dict[str, dict] = {c["ci_class"]: c for c in CI_CLASSES}


# ── Incident Sync — default configuration ─────────────────────────────────
#
# Stored under ConnectorConfigModel.config_json["incident_sync"].
# Any key absent from the stored config falls back to this default.

INCIDENT_SYNC_DEFAULTS: dict = {
    # Master switch — set False to disable all automatic pushes
    "enabled": False,

    # Create a SN incident automatically when a platform incident opens
    "auto_create": True,

    # Lifecycle states that trigger an automatic update to the SN incident.
    # Matches LifecycleState enum values.
    "auto_update_on_states": [
        "in_progress",
        "waiting_approval",
        "resolved",
        "failed",
        "rejected",
    ],

    # Include the AI-generated summary in the SN incident description
    "include_ai_summary": True,

    # Append agent work-note entries (trace steps) to SN work_notes on each update
    "append_agent_notes": True,

    # Base URL of this platform — used to build the back-link in SN work_notes
    # Override per-deployment via the connector config UI
    "platform_url": "http://localhost:3000",
}


def get_incident_sync_config(connector_config_json: dict) -> dict:
    """
    Merge stored incident_sync settings with defaults.
    Returns a fully-populated config dict safe to read without key guards.
    """
    stored = (connector_config_json or {}).get("incident_sync", {})
    return {**INCIDENT_SYNC_DEFAULTS, **stored}


# ── Severity / Priority mappings (Platform → ServiceNow) ──────────────────

SEVERITY_TO_SN: dict[str, dict] = {
    "critical": {"impact": "1", "urgency": "1", "priority": "1"},
    "high":     {"impact": "2", "urgency": "1", "priority": "2"},
    "medium":   {"impact": "2", "urgency": "2", "priority": "3"},
    "low":      {"impact": "3", "urgency": "3", "priority": "4"},
}

PRIORITY_TO_SN: dict[str, str] = {
    "P1": "1", "P2": "2", "P3": "3", "P4": "4", "P5": "5",
}

# Platform lifecycle → SN incident state (numeric)
LIFECYCLE_TO_SN_STATE: dict[str, str] = {
    "open":             "1",   # New
    "in_progress":      "2",   # In Progress
    "waiting_approval": "2",   # In Progress (hold)
    "resolved":         "6",   # Resolved
    "failed":           "2",   # In Progress (agent failed, SN stays open)
    "rejected":         "7",   # Closed
    "closed":           "7",   # Closed
}

# SN operational_status numeric → readable
OPERATIONAL_STATUS: dict[str, str] = {
    "1": "Operational",
    "2": "Non-Operational",
    "3": "Repair in Progress",
    "4": "DR Standby",
    "5": "Ready",
    "6": "Retired",
    "7": "Pipeline",
    "8": "Catalogued",
}
