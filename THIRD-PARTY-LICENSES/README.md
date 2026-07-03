# Third-Party Licenses

This directory contains license information for all open source software
bundled with AgenticPlatform, in compliance with the redistribution
requirements of each respective license.

---

## Contents

| File | Covers |
|---|---|
| `python-licenses.txt` | Full license texts for all Python dependencies |
| `python-summary.md` | Summary table: package name, version, license type |
| `npm-licenses.txt` | Full license texts for all frontend (npm) dependencies |

---

## Python Dependencies — License Summary

All MIT and BSD licensed. Notable Apache 2.0 packages:

| Package | License | Notes |
|---|---|---|
| aiohttp | Apache 2.0 | Async HTTP client |
| asyncpg | Apache 2.0 | PostgreSQL async driver |
| boto3 / botocore | Apache 2.0 | AWS SDK (optional cloud features) |
| anthropic | MIT | Anthropic SDK for LLM integration |
| celery | BSD | Distributed task queue |
| FastAPI | MIT | Web framework |
| SQLAlchemy | MIT | ORM / database layer |
| neo4j | Apache 2.0 | Neo4j Python driver (CMDB) |
| reportlab | BSD | PDF generation |

Full license texts: `python-licenses.txt`

---

## Frontend (npm) Dependencies — License Summary

Predominantly MIT licensed. Full details: `npm-licenses.txt`

---

## Regenerating

Run these commands from the project root whenever dependencies change:

```bash
# Python
pip install pip-licenses
pip-licenses --format=plain-vertical \
             --with-license-file \
             --no-license-path \
             --output-file=THIRD-PARTY-LICENSES/python-licenses.txt

pip-licenses --format=markdown \
             --output-file=THIRD-PARTY-LICENSES/python-summary.md

# Frontend (npm)
cd frontend
npx license-checker \
    --out ../THIRD-PARTY-LICENSES/npm-licenses.txt \
    --plainVertical
cd ..
```

---

Copyright (c) 2026 Powowa Inc.
Each third-party package listed here is the property of its respective
copyright holder and is used under the terms of its stated license.
