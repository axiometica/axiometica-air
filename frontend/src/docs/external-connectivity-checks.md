# External Connectivity Checks

External Connectivity Checks perform scheduled HTTP/HTTPS probes against a URL from the Watcher's own network position — a genuine outside-in reachability check, independent of what the application itself reports. Configure them from **Monitoring Setup → External Connectivity Checks**.

## Fields

| Field | Description |
|---|---|
| Check Name | Display name |
| URL | Target URL — must be reachable from the backend/watcher container's network |
| Interval (s) | Probe frequency. Default: 60. |
| Timeout (s) | HTTP request timeout. Default: 10. |
| Expected Status | HTTP status code that counts as healthy. Default: 200. |
| Event Type | Event type raised on failure. Default: `network.connectivity.packet_loss`. |
| CI Override | Attach the generated incident to a specific CI instead of relying on auto-resolution |

## When to use this vs. Synthetic Monitoring

External Connectivity Checks answer "is this URL up and returning the expected status code?" — a single request, no login, no page content validation. If you need to verify an actual multi-step user journey (login, navigate, submit) or check that a page's *content* is correct — not just its status code — use the **Synthetic Transaction Monitoring** guide in this Help panel instead. Connectivity checks are the right tool for simple reachability, health-endpoint, or third-party dependency monitoring.

## Failure alerting

A failure raises the configured event type through the same qualification and incident pipeline as every other anomaly type. Like all watcher-sourced conditions, it clears automatically the next time the probe succeeds.
