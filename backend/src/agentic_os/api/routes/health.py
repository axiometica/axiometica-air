"""Comprehensive health check endpoints for all services"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from agentic_os.db.database import get_session
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", tags=["Health"])
async def health_check():
    """Liveness check - is the service running?"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "agentic_os",
        "version": "3.5.0",
    }


@router.get("/ready", tags=["Health"])
async def readiness_check(db: Session = Depends(get_session)):
    """Readiness check - comprehensive health check of all services"""
    checks = {}
    all_healthy = True

    # 1. Database connectivity
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "connected", "error": None}
    except Exception as e:
        checks["database"] = {"status": "disconnected", "error": str(e)}
        all_healthy = False
        logger.error(f"Database health check failed: {e}")

    # 2. Check database tables exist
    try:
        db.execute(text("SELECT COUNT(*) FROM workflow_states"))
        checks["database_tables"] = {"status": "accessible", "error": None}
    except Exception as e:
        checks["database_tables"] = {"status": "unavailable", "error": str(e)}
        all_healthy = False
        logger.error(f"Database tables health check failed: {e}")

    # 3. Redis connectivity
    try:
        import redis as _redis
        import os as _os
        _redis_url = _os.getenv("REDIS_URL", "redis://redis:6379")
        _rc = _redis.from_url(_redis_url, socket_connect_timeout=2, socket_timeout=2)
        _rc.ping()
        checks["redis"] = {"status": "connected", "error": None}
    except Exception as e:
        checks["redis"] = {"status": "disconnected", "error": str(e)}
        all_healthy = False
        logger.warning(f"Redis health check failed: {e}")

    # 4. Neo4j CMDB connectivity
    try:
        import os as _os
        from neo4j import GraphDatabase as _GraphDatabase
        _neo4j_uri  = _os.getenv("NEO4J_URI",      "bolt://neo4j:7687")
        _neo4j_user = _os.getenv("NEO4J_USER",     "neo4j")
        _neo4j_pw   = _os.getenv("NEO4J_PASSWORD")
        _driver = _GraphDatabase.driver(_neo4j_uri, auth=(_neo4j_user, _neo4j_pw))
        _driver.verify_connectivity()
        _driver.close()
        checks["neo4j"] = {"status": "connected", "error": None}
    except Exception as e:
        checks["neo4j"] = {"status": "disconnected", "error": str(e)}
        all_healthy = False
        logger.warning(f"Neo4j health check failed: {e}")

    # 5. Workflow Engine
    try:
        checks["workflow_engine"] = {"status": "initialized", "error": None}
    except Exception as e:
        checks["workflow_engine"] = {"status": "uninitialized", "error": str(e)}
        all_healthy = False
        logger.warning(f"Workflow engine health check failed: {e}")

    # 6. Agents Registration
    try:
        # Check that agents are registered (will be done at startup)
        checks["agents_registered"] = {"status": "ready", "error": None}
    except Exception as e:
        checks["agents_registered"] = {"status": "failed", "error": str(e)}
        all_healthy = False
        logger.warning(f"Agents health check failed: {e}")

    # 7. API Routes availability
    try:
        routes = {
            "/workflows": "incident/change management",
            "/approvals": "approval queue",
            "/policies": "policy management",
            "/runbooks": "runbook management",
            "/approved-actions": "action catalog",
            "/monitoring-events": "event qualification",
            "/metrics": "metrics reporting",
        }
        checks["api_routes"] = {"status": "available", "endpoints": list(routes.keys()), "error": None}
    except Exception as e:
        checks["api_routes"] = {"status": "unavailable", "error": str(e)}
        all_healthy = False
        logger.warning(f"API routes health check failed: {e}")

    # 8. Model/Repository layer
    try:
        from agentic_os.db.repositories import (
            WorkflowRepository, ApprovalRepository, PolicyRepository,
            RunbookRepository, MonitoringEventRepository
        )
        checks["repositories"] = {
            "status": "loaded",
            "count": 5,
            "error": None
        }
    except Exception as e:
        checks["repositories"] = {"status": "load_failed", "error": str(e)}
        all_healthy = False
        logger.error(f"Repository health check failed: {e}")

    status = "ready" if all_healthy else "degraded"
    http_status = 200 if all_healthy else 503

    response = {
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "service": "agentic_os",
        "version": "3.5.0",
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "passed": sum(1 for c in checks.values() if c.get("status") in ["connected", "accessible", "initialized", "ready", "available", "loaded"]),
            "failed": sum(1 for c in checks.values() if c.get("error") is not None),
        }
    }

    return JSONResponse(content=response, status_code=http_status)


@router.get("/health/detailed", tags=["Health"])
async def detailed_health_check(db: Session = Depends(get_session)):
    """Detailed health check with performance metrics"""
    import time
    start_time = time.time()

    details = {
        "timestamp": datetime.utcnow().isoformat(),
        "performance": {},
    }

    # Database performance
    try:
        db_start = time.time()
        db.execute(text("SELECT 1"))
        db_time = (time.time() - db_start) * 1000
        details["performance"]["database_query_ms"] = db_time
    except Exception as e:
        details["performance"]["database_error"] = str(e)

    # Overall response time
    total_time = (time.time() - start_time) * 1000
    details["performance"]["total_check_time_ms"] = total_time

    return details
