#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint.sh — agentic_nginx_prod_test
#
# One-time bootstrap (skipped if already done) then hands off to supervisord:
#   1. Generate self-signed TLS certificate for nginx (443)
#   2. Initialise PostgreSQL data directory and create test DB/user
#   3. Generate SSH host keys for sshd
#   4. Exec supervisord (becomes PID 1 replacement for the container lifetime)
# ─────────────────────────────────────────────────────────────────────────────

set -e

PG_DATA=/var/lib/postgresql/data
PG_BIN=/usr/lib/postgresql/15/bin
CERT=/etc/nginx/certs/cert.pem
KEY=/etc/nginx/certs/key.pem

# ── 1. TLS certificate ────────────────────────────────────────────────────────
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "[entrypoint] Generating self-signed TLS certificate..."
    mkdir -p /etc/nginx/certs
    openssl req -x509 -nodes -days 730 -newkey rsa:2048 \
        -keyout "$KEY" \
        -out  "$CERT" \
        -subj "/C=US/ST=Local/L=Local/O=AgenticTest/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,DNS:agentic_nginx_prod_test,IP:127.0.0.1" \
        2>/dev/null
    echo "[entrypoint] TLS certificate written to $CERT"
fi

# ── 2. PostgreSQL ─────────────────────────────────────────────────────────────
if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    echo "[entrypoint] Initialising PostgreSQL data directory..."
    mkdir -p "$PG_DATA"
    chown postgres:postgres "$PG_DATA"
    su -c "$PG_BIN/initdb -D $PG_DATA --auth-local trust --auth-host md5" postgres

    # Allow connections from the docker network
    cat >> "$PG_DATA/postgresql.conf" << 'PGCONF'
listen_addresses = '*'
log_min_messages = WARNING
logging_collector = on
log_directory = '/var/log/postgresql'
log_filename = 'postgresql.log'
PGCONF

    echo "host all all 0.0.0.0/0 md5" >> "$PG_DATA/pg_hba.conf"
    mkdir -p /var/log/postgresql
    chown postgres:postgres /var/log/postgresql

    # Start postgres temporarily to create test database/user
    echo "[entrypoint] Creating test database and user..."
    su -c "$PG_BIN/pg_ctl start -D $PG_DATA -l /tmp/pg_init.log -w -t 30" postgres

    su -c "psql -v ON_ERROR_STOP=1 << 'SQL'
CREATE USER testuser WITH PASSWORD 'testpass';
CREATE DATABASE testdb OWNER testuser;
GRANT ALL PRIVILEGES ON DATABASE testdb TO testuser;
SQL" postgres

    # Create a test table and seed some rows
    su -c "psql testdb -v ON_ERROR_STOP=1 << 'SQL'
CREATE TABLE IF NOT EXISTS test_events (
    id         SERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    payload    JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
INSERT INTO test_events (event_type, payload) VALUES
    ('health_check', '{"result":"ok"}'),
    ('cpu_spike',    '{"cpu_pct":92.4,"pid":1234}'),
    ('disk_warning', '{"path":"/var","used_pct":87}');
SQL" postgres

    su -c "$PG_BIN/pg_ctl stop -D $PG_DATA -w" postgres
    echo "[entrypoint] PostgreSQL initialised."
fi

# ── 3. SSH host keys + ops test keypair ──────────────────────────────────────
if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
    echo "[entrypoint] Generating SSH host keys..."
    ssh-keygen -A
fi

# Test keypair for the ops user (generated once, persisted in /etc/ssh/).
# Not baked into the image so it does not appear in image layer history.
if [ ! -f /etc/ssh/test_id_ed25519 ]; then
    echo "[entrypoint] Generating test SSH keypair for ops user..."
    ssh-keygen -t ed25519 -f /etc/ssh/test_id_ed25519 -N "" -C "agentic-runbook-test"
    cp /etc/ssh/test_id_ed25519.pub /home/ops/.ssh/authorized_keys
    chmod 600 /home/ops/.ssh/authorized_keys
    chown ops:ops /home/ops/.ssh/authorized_keys
fi

# ── 4. Print test SSH private key path for reference ──────────────────────────
echo "──────────────────────────────────────────────────────────────────"
echo " agentic_nginx_prod_test ready"
echo " SSH key (ops user): docker cp agentic_nginx_prod_test:/etc/ssh/test_id_ed25519 ./test_key"
echo " PostgreSQL:  host=agentic_nginx_prod_test port=5432 user=testuser password=testpass dbname=testdb"
echo " HTTP health: http://agentic_nginx_prod_test/health"
echo " HTTPS:       https://agentic_nginx_prod_test/health (self-signed cert)"
echo "──────────────────────────────────────────────────────────────────"

# ── 5. Hand off to supervisord ────────────────────────────────────────────────
exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
