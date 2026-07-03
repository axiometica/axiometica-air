"""
Unit tests for CMDBService's Neo4j temporal-type sanitization.

Regression coverage for a production incident: mark_ci_degraded/mark_ci_recovered
set started_at/resolved_at via Cypher's native datetime() function, which the
neo4j driver returns as neo4j.time.DateTime — not JSON-serializable. Once such
a value rode into a workflow's context dict via get_historical_incidents(), the
context's persistence crashed with `TypeError: Object of type DateTime is not
JSON serializable`, leaving the incident stuck in_progress forever (the failed
UPDATE poisoned the SQLAlchemy session with a cascading PendingRollbackError).

No real Neo4j connection is used — neo4j.time.DateTime/Date/Time/Duration are
plain importable value types, and a fake "record" is just a dict (dict(record)
on a dict is already a valid no-op copy, matching how the driver's real
Record class behaves under dict()).
"""
import datetime
import json
from unittest.mock import MagicMock

from neo4j.time import Date, DateTime, Duration, Time

from agentic_os.services.cmdb import CMDBService, _sanitize_record, _to_json_safe


class TestToJsonSafe:
    def test_datetime_converts_to_isoformat_string(self):
        dt = DateTime.from_native(datetime.datetime(2026, 6, 29, 20, 1, 13, 963961))
        result = _to_json_safe(dt)
        assert result == "2026-06-29T20:01:13.963961"
        json.dumps(result)  # must not raise

    def test_date_converts_to_isoformat_string(self):
        d = Date.from_native(datetime.date(2026, 6, 25))
        assert _to_json_safe(d) == "2026-06-25"

    def test_time_converts_to_isoformat_string(self):
        t = Time.from_native(datetime.time(10, 30, 0))
        assert _to_json_safe(t) == "10:30:00"

    def test_duration_converts_to_string(self):
        dur = Duration(days=1, seconds=30)
        result = _to_json_safe(dur)
        assert isinstance(result, str)
        json.dumps(result)

    def test_plain_values_pass_through_unchanged(self):
        assert _to_json_safe("hello") == "hello"
        assert _to_json_safe(42) == 42
        assert _to_json_safe(None) is None
        assert _to_json_safe(True) is True

    def test_recurses_into_nested_dict(self):
        dt = DateTime.from_native(datetime.datetime(2026, 6, 29, 20, 1, 13))
        nested = {"outer": {"resolved_at": dt, "id": "abc"}}
        result = _to_json_safe(nested)
        assert result == {"outer": {"resolved_at": "2026-06-29T20:01:13", "id": "abc"}}
        json.dumps(result)

    def test_recurses_into_list(self):
        dt = DateTime.from_native(datetime.datetime(2026, 6, 29, 20, 1, 13))
        result = _to_json_safe([{"resolved_at": dt}, {"resolved_at": "already-a-string"}])
        assert result == [{"resolved_at": "2026-06-29T20:01:13"}, {"resolved_at": "already-a-string"}]
        json.dumps(result)


class TestSanitizeRecord:
    def test_sanitizes_a_dict_record_with_mixed_types(self):
        dt = DateTime.from_native(datetime.datetime(2026, 6, 29, 20, 1, 13))
        record = {"id": "inc-1", "severity": "high", "resolved_at": dt, "root_cause": None}
        result = _sanitize_record(record)
        assert result == {
            "id": "inc-1",
            "severity": "high",
            "resolved_at": "2026-06-29T20:01:13",
            "root_cause": None,
        }
        json.dumps(result)


class TestGetHistoricalIncidentsSanitization:
    """Reproduces the exact crash path: a real datetime()-typed resolved_at
    flowing out of get_historical_incidents() must come back JSON-safe."""

    def _service_with_mocked_session(self, records):
        service = CMDBService.__new__(CMDBService)  # skip __init__'s real connection attempt
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_session.run.return_value = records
        mock_driver.session.return_value.__enter__.return_value = mock_session
        service.driver = mock_driver
        return service

    def test_resolved_at_datetime_is_json_serializable_on_return(self):
        resolved_at = DateTime.from_native(datetime.datetime(2026, 6, 25, 1, 17, 20))
        fake_record = {
            "id": "inc-42",
            "severity": "medium",
            "description": "TCP port closed",
            "resolved_at": resolved_at,
            "root_cause": "manual stop",
        }
        service = self._service_with_mocked_session([fake_record])

        result = service.get_historical_incidents("agentic_os_flower")

        assert len(result) == 1
        assert result[0]["resolved_at"] == "2026-06-25T01:17:20"
        # The original bug: this line raised TypeError before the fix.
        json.dumps(result)

    def test_no_driver_returns_empty_list(self):
        service = CMDBService.__new__(CMDBService)
        service.driver = None
        assert service.get_historical_incidents("anything") == []
