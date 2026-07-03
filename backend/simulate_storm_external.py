"""
Storm simulation -- external-connector scenario.

This round specifically tests whether external-source events participate in
storm detection.  We send events tagged with a source_connector so the
platform's storm_eligible / allow_storm_detection logic is exercised.

Scenario: "Database tier meltdown" -- 5 resources across DB + cache layer
show simultaneous high-load and connection-saturation signals.  Events come
from a fictional external connector ("splunk_prod") to prove external sources
ARE included in storm detection (exclude_external_events defaults to false).
"""

import argparse
import os
import time
import requests
import json
from datetime import datetime, timedelta, timezone

BASE = "http://localhost:8000/api"

# Five events across the DB/cache tier -- classic resource-exhaustion pattern
EVENTS = [
    {
        "source":         "splunk_prod",
        "event_type":     "connection_pool_exhausted",
        "resource_name":  "db-primary",
        "raw_criticality":"critical",
        "signal_value":   512.0,
        "signal_threshold": 400.0,
        "raw_payload": {
            "host":       "db-primary.internal",
            "connector":  "splunk_prod",
            "message":    "PostgreSQL connection pool exhausted -- 512/400 active connections",
            "index":      "infra_db",
        },
    },
    {
        "source":         "splunk_prod",
        "event_type":     "connection_pool_exhausted",
        "resource_name":  "db-replica-01",
        "raw_criticality":"critical",
        "signal_value":   398.0,
        "signal_threshold": 400.0,
        "raw_payload": {
            "host":       "db-replica-01.internal",
            "connector":  "splunk_prod",
            "message":    "PostgreSQL replica approaching connection limit -- 398/400 active",
            "index":      "infra_db",
        },
    },
    {
        "source":         "splunk_prod",
        "event_type":     "high_memory_usage",
        "resource_name":  "cache-cluster",
        "raw_criticality":"critical",
        "signal_value":   94.2,
        "signal_threshold": 85.0,
        "raw_payload": {
            "host":       "cache-cluster.internal",
            "connector":  "splunk_prod",
            "message":    "Redis memory usage at 94.2% -- eviction risk imminent",
            "index":      "infra_cache",
        },
    },
    {
        "source":         "splunk_prod",
        "event_type":     "high_cpu_usage",
        "resource_name":  "db-primary",
        "raw_criticality":"warning",
        "signal_value":   88.5,
        "signal_threshold": 80.0,
        "raw_payload": {
            "host":       "db-primary.internal",
            "connector":  "splunk_prod",
            "message":    "Sustained CPU spike 88.5% -- long-running query suspected",
            "index":      "infra_db",
        },
    },
    {
        "source":         "splunk_prod",
        "event_type":     "service_unresponsive",
        "resource_name":  "api-gateway",
        "raw_criticality":"critical",
        "signal_value":   None,
        "signal_threshold": None,
        "raw_payload": {
            "host":       "api-gateway.internal",
            "connector":  "splunk_prod",
            "message":    "API Gateway health checks failing -- DB upstream timeout suspected",
            "index":      "infra_api",
        },
    },
]

def submit_event(ev: dict, headers: dict) -> dict | None:
    try:
        r = requests.post(f"{BASE}/monitoring-events", headers=headers, json=ev, timeout=30)
        if r.status_code == 201:
            d = r.json()
            qualified = d.get("qualified_as_incident")
            score     = d.get("qualification_score", 0)
            wf_id     = d.get("incident_workflow_id", "--")
            print(f"  OK {ev['resource_name']:20s} {ev['event_type']:30s}  score={score:.0f}  qualified={qualified}  wf={str(wf_id)[:8]}")
            return d
        else:
            print(f"  ERR {ev['resource_name']} -> HTTP {r.status_code}: {r.text[:120]}")
            return None
    except Exception as e:
        print(f"  ERR {ev['resource_name']} -> {e}")
        return None


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-key",
        default=os.getenv("STORM_SIM_API_KEY"),
        help="Principal API key (X-API-Key header). Falls back to STORM_SIM_API_KEY env var.",
    )
    parser.add_argument(
        "--base-url",
        default=BASE,
        help=f"API base URL (default: {BASE})",
    )
    args = parser.parse_args()
    if not args.api_key:
        parser.error("an API key is required: pass --api-key or set STORM_SIM_API_KEY")
    return args


def main():
    args = parse_args()
    headers = {"X-API-Key": args.api_key, "Content-Type": "application/json"}
    global BASE
    BASE = args.base_url

    print("=" * 70)
    print("Storm Simulation -- External Connector (Splunk) Scenario")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 70)
    print()
    print("Submitting 5 events from 'splunk_prod' (external connector)...")
    print()

    results = []
    for ev in EVENTS:
        result = submit_event(ev, headers)
        results.append(result)
        time.sleep(0.4)  # small stagger

    qualified = [r for r in results if r and r.get("qualified_as_incident")]
    print()
    print(f"Events submitted: {len([r for r in results if r])}/{ len(EVENTS)}")
    print(f"Qualified as incidents: {len(qualified)}")
    print()

    if qualified:
        print("Waiting 8s for storm detection to process...")
        time.sleep(8)

        print()
        print("Checking for active storms...")
        try:
            r = requests.get(f"{BASE}/storms?active_only=true", headers=headers, timeout=15)
            if r.status_code == 200:
                storms = r.json()
                if storms:
                    for s in storms:
                        print(f"  STORM {s.get('storm_id','?')[:8]}  '{s.get('title','?')[:60]}'")
                        print(f"     children={s.get('child_count')}  resources={s.get('affected_count')}  confidence={s.get('confidence', 0)*100:.0f}%  state={s.get('lifecycle_state')}")
                else:
                    print("  No storms detected yet - storm analysis runs async, check the UI in ~30s")
            else:
                print(f"  Storms API -> HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"  Could not query storms: {e}")
    else:
        print("No events qualified -- storm detection will not trigger.")
        print("Check event qualification thresholds in platform settings.")

    print()
    print("Done. Open Event Storms in the UI to see the result.")


if __name__ == "__main__":
    main()
