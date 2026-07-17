# Log Monitors

Log Monitors continuously tail a container's stdout/stderr and raise an incident when a configured regex pattern matches — catching error conditions visible in application logs that resource-metric monitoring (CPU/memory/disk) can't see. Configure them from **Monitoring Setup → Log Monitors**.

## Fields

| Field | Description |
|---|---|
| Monitor Name | Display name |
| Target Container | Container name to tail (e.g. `agentic_os_backend`) |
| Pattern | Regex checked against each log line |
| Event Type | Platform event type raised on a match |
| Cooldown (s) | Minimum seconds between incidents for the same pattern, so one noisy burst doesn't create a flood. Default: 120. |
| CI Override | Attach the generated incident to a specific CI |

## Recommended starting monitors

| Target | Pattern | Event Type | Cooldown |
|---|---|---|---|
| `celery_worker` | `CRITICAL\|Exception\|Traceback` | `log.pattern.exception_flood` | 120 |
| `agentic_os_backend` | `OperationalError\|pool.*timeout` | `database.connectivity.connection_pool_exhausted` | 300 |
| `agentic_os_backend` | `50[0-9] Internal Server Error` | `application.availability.service_unresponsive` | 60 |

## Failure alerting

A pattern match raises the configured event type through the same qualification and incident pipeline as every other anomaly type. The cooldown window prevents the same recurring pattern from opening a new incident every time it matches — it suppresses duplicates rather than raising and auto-clearing per line.
