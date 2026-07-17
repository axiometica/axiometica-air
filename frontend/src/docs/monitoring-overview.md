# Monitoring Overview

The platform monitors your environment through four independent mechanisms, all configured from the **Monitoring Setup** page and all feeding the same qualification and incident pipeline. They answer different questions — most environments use several together.

| Type | Question it answers | Source |
|---|---|---|
| **Basic (resource) monitoring** | Is this container's CPU/memory/disk/syscall activity healthy? | `sentinel_senses` (eBPF) + `watcher_brain`, always-on per host |
| **External Connectivity Checks** | Is this URL reachable and returning the expected status code? | Scheduled HTTP/HTTPS probe from the watcher |
| **Log Monitors** | Did this container's logs just show an error pattern? | Tailed container stdout/stderr, regex match |
| **Synthetic Transaction Monitoring** | Can a real user actually log in and complete this journey, with correct page content? | Recorded browser session (HAR), replayed as a scripted request sequence |

---

## Basic monitoring

Always-on, host-wide resource and kernel telemetry — CPU, memory, disk, network connections, and (via eBPF) syscall-level anomaly detection — with no per-container configuration required. See the **Watcher Setup** and **Watcher Quick Start** guides in this Help panel for how this layer works and how to tune its thresholds.

## External Connectivity Checks

A simple scheduled probe: hit a URL, check the status code. No login, no content validation — the right tool for "is this endpoint up" reachability and third-party dependency monitoring. See the **External Connectivity Checks** guide.

## Log Monitors

Regex pattern matching against a container's live log stream, with a cooldown to prevent duplicate incidents from a noisy burst. Good for catching error conditions that never show up as a resource spike — an exception flood, a connection pool exhaustion message, a burst of 500s. See the **Log Monitors** guide.

## Synthetic Transaction Monitoring

The most involved of the four: replays an actual recorded multi-page user journey (login, navigate, submit) and can assert on page *content*, not just status codes — catching a page that returns `200` but renders broken. Built from a HAR file recorded in Chrome DevTools; requires care in how that recording is made. See the **Synthetic Monitoring** guide for the full recording walkthrough and troubleshooting.

## Choosing which to use

- Need to know if a service is simply up? **External Connectivity Check.**
- Need to catch an error condition visible only in logs? **Log Monitor.**
- Need to verify an actual authenticated user journey works end-to-end, including page content? **Synthetic Transaction Monitor.**
- Need baseline container health (CPU/memory/disk) with zero configuration? Already covered by **basic monitoring** — nothing to set up.

All four raise incidents through the same governance, qualification, and (where applicable) automated remediation pipeline as everything else on the platform — there's no separate "monitoring alert" system to learn.
