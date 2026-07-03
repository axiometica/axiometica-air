# Connector Setup

Connectors link the platform to your external systems — pulling CMDB data, receiving alerts, and pushing incident state back to your ITSM. All connector configuration lives in **Connector Hub**.

---

## ServiceNow

The ServiceNow connector is the primary CMDB data source. It syncs Configuration Items (CIs) from your ServiceNow instance into the platform's Neo4j graph, enriching every incident with environment, criticality, platform, and dependency data.

### Capabilities

| Capability | Description |
|---|---|
| `cmdb_pull` | Pulls CI data from ServiceNow CMDB tables into Neo4j |
| `incident_push` | Automatically creates and updates ServiceNow incidents when platform incidents open/close |

### Configuration

| Field | Description |
|---|---|
| Base URL | Your ServiceNow instance URL, e.g. `https://yourcompany.service-now.com` |
| Username | ServiceNow user with read access to CMDB tables and write access to incident table |
| Password | ServiceNow user password |

### CMDB Sync

After configuring the connector, use **Sync Now** to perform an initial full sync. This is a background task — the page returns immediately and the sync runs via Celery worker. Subsequent syncs can be scheduled or triggered manually.

**What gets synced**: CI name, class, environment, criticality, service class, platform, IP address, location, assigned team, and CMDB relationships (dependencies).

### Browse Data

The **Browse Data** button on the ServiceNow card opens an explorer showing all CIs currently cached in the platform from your last sync. Use this to verify sync results or look up specific CIs before an incident occurs.

### Incident Push

When `incident_push` is enabled, the platform automatically:
- Creates a ServiceNow incident when a platform incident is opened
- Updates the SNOW incident state when the platform incident lifecycle changes (investigating → remediating → resolved → closed)

This runs asynchronously via Celery and does not block the platform workflow.

---

## Splunk

Splunk is a log pull connector. It allows the platform's monitoring watchers to query Splunk for log-based anomalies and trigger incidents when thresholds are exceeded.

### Configuration

| Field | Description |
|---|---|
| Base URL | Splunk instance URL, e.g. `https://splunk.yourcompany.com:8089` |
| Token | Splunk API token with search permissions |

### Usage

Once configured, create monitoring watchers that query Splunk searches on a schedule. The watcher evaluates results against a threshold and fires an event if the threshold is crossed.

---

## Alert Ingest Connectors

The following connectors receive inbound webhooks from your existing monitoring tools. No outbound connection is made — the platform exposes a webhook endpoint that your tool pushes to.

| Connector | Description |
|---|---|
| **Datadog** | Receives Datadog monitor alerts via webhook |
| **Dynatrace** | Receives Dynatrace problem notifications |
| **Prometheus** | Receives Alertmanager webhook notifications |
| **PagerDuty** | Receives PagerDuty incident webhooks |
| **Zabbix** | Receives Zabbix action notifications |
| **Grafana** | Receives Grafana Unified Alerting webhook batches |
| **Generic Webhook** | Receives events from any source — no per-source config required |

### Configuration

Each alert ingest connector requires:

| Field | Description |
|---|---|
| Webhook Secret | Used to verify the payload signature. Set this same value in your monitoring tool's webhook config. |
| Enabled | Toggle to start/stop receiving events from this source |

### Webhook Endpoint

Your monitoring tool should be configured to POST to:

```
https://<your-platform-url>/api/alerts/ingest/<connector-id>
```

For example, for Datadog: `/api/alerts/ingest/datadog`

### Alert Normalization

Inbound webhooks are normalised into the platform's standard alert format. The connector extracts:
- `resource_name` — the affected host, service, or pod
- `type` — the alert type (mapped to a platform event type)
- `severity` — normalised to low / medium / high / critical
- `message` — human-readable description

Normalised alerts enter the standard event pipeline: dedup gate → CMDB enrichment → risk scoring → policy evaluation.

---

## Grafana

Receives Grafana Unified Alerting webhook batches. Each POST may contain multiple alerts — the platform processes all firing alerts in the batch and drops resolved alerts (recovery events do not create platform incidents).

### Webhook Endpoint

```
POST https://<your-platform-url>/api/connectors/grafana/webhook
Header: X-Grafana-Webhook-Secret: <your-secret>
```

### Grafana Setup

1. In Grafana, go to **Alerting → Contact points → New contact point**
2. Set type to **Webhook**
3. Set URL to your platform webhook endpoint above
4. Under **Optional Webhook settings**, add header `X-Grafana-Webhook-Secret` with the secret you configured in the connector
5. Save and assign the contact point to an alert rule's notification policy
6. Use **Test** in Grafana to send a sample payload and confirm delivery

### Field Mapping

| Grafana field | Platform field |
|---|---|
| `alerts[].labels.alertname` | `event_type` |
| `alerts[].labels.instance` or `labels.job` | `resource_name` |
| `alerts[].labels.severity` | `severity` |
| `alerts[].annotations.summary` | `message` |
| `alerts[].values` (dict) | `signal_value` (first numeric value) |
| `alerts[].status = "firing"` | Alert ingested |
| `alerts[].status = "resolved"` | Dropped — no platform incident |

---

## Generic Webhook

A multi-source catch-all connector. Any system can POST events to the generic endpoint as long as the payload includes a `source` field. A single enabled connector handles events from any number of different source systems simultaneously — no per-source configuration is needed.

### Webhook Endpoint

```
POST https://<your-platform-url>/api/connectors/generic/webhook
Header: X-Webhook-Secret: <your-secret>
```

### Required Payload Fields

| Field | Type | Description |
|---|---|---|
| `source` | string | Identifies the originating system, e.g. `"nagios"`, `"custom_monitor"` |
| `event_type` | string | Platform event type code, e.g. `"infrastructure.compute.cpu_high"` |
| `resource_name` | string | Affected resource, e.g. `"web-server-01"` |
| `severity` | string | `low`, `medium`, `high`, or `critical` |

### Optional Payload Fields

| Field | Type | Description |
|---|---|---|
| `message` | string | Human-readable description of the event |
| `service` | string | Service name for runbook matching |
| `environment` | string | `production`, `staging`, `development` |
| `signal_value` | number | Numeric reading (e.g. CPU %, memory MB) |
| `timestamp` | string | ISO 8601 event time (defaults to ingest time) |
| `tags` | object | Additional key-value metadata |

### Example Payload

```json
{
  "source": "nagios",
  "event_type": "infrastructure.compute.cpu_high",
  "resource_name": "web-server-01",
  "severity": "high",
  "message": "CPU usage at 94% for 10 minutes",
  "service": "checkout",
  "environment": "production",
  "signal_value": 94.2
}
```

The `source` field is taken from the payload — the same endpoint handles Nagios, Zabbix, custom scripts, and any other tool simultaneously. Missing a required field returns HTTP 400 with a description of which field is absent.

---

## Testing a Connector

Use the **Test** button on any configured connector card to verify connectivity. The test performs a lightweight read operation (e.g. a CMDB query for ServiceNow, a search ping for Splunk) and reports success or the specific error.

> Alert ingest connectors do not have a test button — they are passive receivers. Verify them by sending a test webhook from your monitoring tool.

---

## Connection Health

Each connector card shows a sync status dot:

| Colour | Meaning |
|---|---|
| 🟢 Green | Last sync succeeded |
| 🟡 Amber | Last sync had partial failures |
| 🔴 Red | Last sync failed |
| ⚫ Grey | Never synced |

The timestamp shows when the last sync completed. If a sync is overdue or showing errors, use **Test** to check connectivity and **Sync Now** to retry.
