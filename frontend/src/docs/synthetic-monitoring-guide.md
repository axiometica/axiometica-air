# Synthetic Transaction Monitoring

Synthetic Transaction Monitors replay a scripted multi-page user journey against a real target (login, navigate, submit) and validate both HTTP status codes and page content — catching failures a plain up/down health check misses, like a page that returns `200` but renders broken or empty. They're built from a **HAR file** — a recording of a real browser session captured in Chrome DevTools — and run from the Watcher container on their own schedule, independent of basic resource monitoring or external connectivity checks.

---

## How it works

1. **Record** the journey in Chrome DevTools (see [Recording settings](#recording-settings) below).
2. **Upload** the HAR in **Monitoring Setup → Synthetic Transaction Monitoring → New Monitor**. The platform parses it into pages and requests and suggests credential keys (base URL, email, password, tokens) found in the recording.
3. **Fill in credentials** — stored encrypted, injected into the replay as environment variables at run time, never written into the script itself.
4. **Add page assertions** (optional) — a regex checked against every response body captured on that page; leave blank to check status codes only.
5. **Generate Script** compiles the parsed pages into a runnable Python script deterministically — no LLM call on this path. **Test Script** runs it once immediately.
6. **Save** with a **Run every (minutes)** interval (default 15).

## Recording settings

Open Chrome DevTools (`F12`) → **Network** tab, and set these **before** you start the journey you want to monitor:

![Chrome DevTools Network panel configured for recording a synthetic monitor HAR — Preserve log and Disable cache checked, tracker domains excluded via an inverted filter](/docs-images/synthetic-monitoring-har-settings.png)

| Setting | Value | Why |
|---|---|---|
| **Preserve log** | ✅ On | Without this, navigating between pages (e.g. login → landing page) clears everything captured so far. Multi-page journeys need this on. |
| **Disable cache** | ✅ On | Responses served from Chrome's cache usually can't have their body exported into the HAR — you get a request with a real size but zero captured content. This silently breaks page assertions and credential detection on any page that reuses a cached response. |
| **Filter box + Invert** | Type tracker domains, then check **Invert** | The filter box is an *include* filter by default — typing tracker domains in without checking Invert hides everything **except** those domains (the opposite of what you want). With Invert checked, it hides everything **matching** those domains instead. A good starting list: `*walkme*, *doubleclick*, *google-analytics*, *adobedtm*, *trustarc*, *hotjar*` |
| **Type filter** | **All** | Not just Fetch/XHR — the platform also needs the `Doc` (HTML page) requests for CSRF token detection and page-content assertions. |

Record the full journey you want the monitor to replay — typically: land on the login page → submit credentials → navigate to the page(s) you want to assert on → log out. Keep the filter/settings above active for the whole recording.

## Exporting

Right-click anywhere in the request list → **"Save all as HAR with content"**.

This is the one setting most likely to silently break a recording if missed: exporting *without* "with content" captures request/response headers and sizes but never the actual response bodies — page assertions and credential auto-detection both need real content to work against.

## Execution model

Synthetic monitors are **not Celery-scheduled**. `watcher_brain` evaluates every enabled monitor on each of its own poll cycles (`WATCHER_POLL_INTERVAL`, default 10s) but only actually runs a monitor's script once `schedule_mins` has elapsed since its last run — so the watcher's fast internal poll loop doesn't mean the monitor runs that often. Each run executes as a subprocess with a 120-second timeout.

## Failure alerting

After `WATCHER_SYNTHETIC_MIN_CONSECUTIVE_FAILS` consecutive failing runs (default 1), the watcher raises a `synthetic.transaction.failed` monitoring event at `critical` criticality through the normal qualification and incident pipeline — a failed transaction (bad status, failed assertion, rejected login) is treated the same as a hard script error or timeout, since both mean the monitored journey is broken right now. It auto-clears the next time the monitor passes.

## Verifying a monitor

```bash
# Tail per-request/per-page detail as the watcher runs a monitor
docker logs watcher_brain -f | grep "\[SYNTHETIC\]"
```

**Expected output** (one line per request, one summary line per page):
```
🔬 [SYNTHETIC] Running monitor 'Axiometica WebSite'
🔬 [SYNTHETIC]     Start Page 1: Login
🔬 [SYNTHETIC]       GET   /                     [200]  143ms
🔬 [SYNTHETIC]       POST  /api/auth/login        [200]  385ms
🔬 [SYNTHETIC]     End Page 1 - PASSED (729ms)
🔬 [SYNTHETIC]     RESULT : PASS -- 1/1 pages passed
🔬 [SYNTHETIC] 'Axiometica WebSite' → pass
```

The same output is available in the UI via the **Log** button next to each monitor, without needing container log access.

---

## Troubleshooting

### A page assertion fails on text you can clearly see in the browser

Before assuming the assertion pattern is wrong, check whether the content was actually captured. In the exported HAR, compare `response.content.size` (the real byte count) against the length of `response.content.text` (what was actually captured) for the specific request the assertion targets. A real size with empty captured text means the content never made it into the HAR — almost always caused by **Disable cache** being off during recording, occasionally by a very large response or a Service Worker intercepting the request. Re-record with Disable cache on and confirm you used "Save all as HAR with content".

### Credentials weren't auto-detected

The platform recognizes common field name conventions (`username`, `email`, `password`, `user_name`, `user_password`, Okta's `identifier`/`passcode`, etc.). If a site uses a genuinely custom field name, it won't be auto-suggested — add the credential manually on the Details tab; it'll still be injected as an environment variable and substituted into the replay script the same way.

If no credential fields show up at all, check whether a real login actually happened during the recording — if the browser already had a valid session when you started, there's nothing to extract.

### Login goes through an SSO/SAML identity provider (Okta, ADFS, etc.) and can't be replayed

This is a hard limitation, not a settings issue. SAML and OIDC logins involve the identity provider issuing a fresh, cryptographically signed, single-use assertion for every real login — a HAR replay can only resend old, already-expired bytes, which the identity provider will reject. If your login flow redirects to an external identity provider, build the monitor around the pages that don't require completing that handshake, or use real browser automation if the fully authenticated journey needs coverage.

### Monitor never runs / stays stuck on an old "Last Run"

```bash
docker logs watcher_brain -f | grep "\[SYNTHETIC\]"
```
- Confirm the monitor is **Enabled** and has a saved script (an unsaved/never-generated script is skipped)
- The watcher only executes a monitor once `schedule_mins` has elapsed since `last_run_at` — a 15-minute monitor will not run again 2 minutes after its last run, even though the watcher itself polls every ~10 seconds
- In development, `watcher_brain` bind-mounts backend source but does **not** hot-reload — after a code change under `backend/src`, restart it: `docker restart watcher_brain`

### Your password is in the HAR file

This is unavoidable and by design in how HAR recording works — DevTools captures the literal request body sent over the wire, so any recording that includes a real login will contain that password in plain text. Treat the exported `.har` file as a credential and delete it once the monitor is built. This is also a good reason to use a dedicated, low-privilege account for recording synthetic monitors rather than a personal admin account.
