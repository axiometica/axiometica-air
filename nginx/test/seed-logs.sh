#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# seed-logs.sh — pre-populate log files with realistic content
#
# Run at image build time so runbooks that scan logs (grep errors, check disk
# usage, rotate logs) have something to work with immediately.
# ─────────────────────────────────────────────────────────────────────────────

set -e

NOW=$(date -u +"%d/%b/%Y:%H:%M:%S +0000")
YESTERDAY=$(date -u -d "yesterday" +"%d/%b/%Y:%H:%M:%S +0000" 2>/dev/null || \
            date -u -v-1d  +"%d/%b/%Y:%H:%M:%S +0000" 2>/dev/null || echo "$NOW")

mkdir -p /var/log/nginx /var/log/app /var/log/postgresql

# ── nginx access log ──────────────────────────────────────────────────────────
cat > /var/log/nginx/access.log << EOF
10.0.0.1 - - [$YESTERDAY] "GET /health HTTP/1.1" 200 42 "-" "curl/7.88"
10.0.0.2 - - [$YESTERDAY] "GET / HTTP/1.1" 200 1234 "-" "Mozilla/5.0"
10.0.0.1 - - [$YESTERDAY] "GET /metrics HTTP/1.1" 200 587 "-" "prometheus/2.45"
10.0.0.3 - - [$YESTERDAY] "POST /api/events HTTP/1.1" 404 154 "-" "python-requests/2.31"
10.0.0.1 - - [$NOW] "GET /health HTTP/1.1" 200 42 "-" "curl/7.88"
10.0.0.4 - - [$NOW] "GET / HTTP/1.1" 200 1234 "-" "Mozilla/5.0"
10.0.0.2 - - [$NOW] "GET /metrics HTTP/1.1" 200 587 "-" "prometheus/2.45"
10.0.0.5 - - [$NOW] "GET /nonexistent HTTP/1.1" 404 154 "-" "python-requests/2.31"
EOF

# ── nginx error log ───────────────────────────────────────────────────────────
cat > /var/log/nginx/error.log << EOF
$YESTERDAY [warn] 12#12: *1 upstream response time exceeded, client: 10.0.0.3, request: "GET /slow"
$YESTERDAY [error] 12#12: *2 connect() failed (111: Connection refused) while connecting to upstream
$NOW [warn] 12#12: *3 upstream response time exceeded, client: 10.0.0.4, request: "GET /api"
EOF

# ── application log ───────────────────────────────────────────────────────────
cat > /var/log/app/testapp.log << EOF
[${YESTERDAY%% *}T${YESTERDAY#* }] [INFO] testapp: starting on 127.0.0.1:8080 (pid=1)
[${YESTERDAY%% *}T${YESTERDAY#* }] [INFO] testapp: 10.0.0.1 - GET /health 200
[${YESTERDAY%% *}T${YESTERDAY#* }] [WARN] testapp: upstream database connection slow (238ms)
[${YESTERDAY%% *}T${YESTERDAY#* }] [ERROR] testapp: application exception: timeout waiting for DB pool
[${NOW%% *}T${NOW#* }] [INFO] testapp: starting on 127.0.0.1:8080 (pid=1)
[${NOW%% *}T${NOW#* }] [INFO] testapp: 10.0.0.1 - GET /health 200
[${NOW%% *}T${NOW#* }] [ERROR] testapp: application exception: NullPointerException in RequestHandler.process()
[${NOW%% *}T${NOW#* }] [WARN] testapp: retrying failed downstream call (attempt 3/3)
EOF

# ── Some old log files to give logrotate / disk-usage runbooks something to find ──
for i in 1 2 3; do
    touch -d "${i} days ago" /var/log/nginx/access.log.$i 2>/dev/null || \
    touch /var/log/nginx/access.log.$i
    dd if=/dev/urandom bs=1K count=256 2>/dev/null | base64 >> /var/log/nginx/access.log.$i
done

echo "[seed-logs] Log files pre-populated."
