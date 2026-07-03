"""
Tests for ToolRegistryAgent._resolve_step_references — the {{...}} template
substitution applied to every runbook step's args before execution.

Covers the four reference patterns, in particular the newest one:
{{step_id.field}} — a named reference to a specific step's output by its
editor-assigned id (e.g. {{verify_service.http_code}}). This is the same
step_id.field syntax run_if/decision conditions already use (and the graph
editor's VariableHelper chips already display) — previously that syntax
silently did nothing when typed into an args/message field, since the only
supported formats were {{steps.N.field}} (by numeric index) and {{field}}
(bare, no step prefix, ambiguous when multiple steps produce the same key).
"""
from agentic_os.agents.incident_agents import ToolRegistryAgent


def _resolve(args, step_outputs, extra_context=None):
    return ToolRegistryAgent._resolve_step_references(args, step_outputs, extra_context=extra_context)


class TestNamedStepIdReference:
    def test_substitutes_known_step_id_and_field(self):
        step_outputs = {"verify_service": {"http_code": 200, "reachable": True}}
        resolved = _resolve({"message": "code={{verify_service.http_code}}"}, step_outputs)
        assert resolved["message"] == "code=200"

    def test_multiple_named_references_in_one_string(self):
        step_outputs = {
            "diag_http_check": {"http_code": 200, "reachable": True},
            "verify_service":  {"http_code": 503, "reachable": False},
        }
        resolved = _resolve(
            {"message": "before={{diag_http_check.http_code}} after={{verify_service.http_code}}"},
            step_outputs,
        )
        assert resolved["message"] == "before=200 after=503"

    def test_disambiguates_same_field_name_across_steps(self):
        """The bug report this fixes: two steps both produce `http_code`, and the
        bare {{http_code}} flat-lookup can't tell them apart. Named references can."""
        step_outputs = {
            "diag_http_check": {"http_code": 200},
            "verify_service":  {"http_code": 503},
        }
        resolved = _resolve({"message": "{{diag_http_check.http_code}}"}, step_outputs)
        assert resolved["message"] == "200"
        resolved2 = _resolve({"message": "{{verify_service.http_code}}"}, step_outputs)
        assert resolved2["message"] == "503"

    def test_unresolved_named_reference_left_untouched(self):
        resolved = _resolve({"message": "{{unknown_step.unknown_field}}"}, {"a": {"x": 1}})
        assert resolved["message"] == "{{unknown_step.unknown_field}}"

    def test_unresolved_field_on_known_step_left_untouched(self):
        step_outputs = {"verify_service": {"http_code": 200}}
        resolved = _resolve({"message": "{{verify_service.nonexistent_field}}"}, step_outputs)
        assert resolved["message"] == "{{verify_service.nonexistent_field}}"

    def test_works_alongside_indexed_and_flat_patterns_in_different_keys(self):
        step_outputs = {
            1: {"http_code": 200},
            "verify_service": {"http_code": 503},
        }
        resolved = _resolve(
            {
                "by_index": "{{steps.1.http_code}}",
                "by_name":  "{{verify_service.http_code}}",
                "flat":     "{{http_code}}",
            },
            step_outputs,
        )
        assert resolved["by_index"] == "200"
        assert resolved["by_name"] == "503"
        # flat pattern picks whichever step's output was merged last — documented,
        # not asserted to a specific value here since dict iteration order is the
        # only determinant and isn't the point of this test.
        assert resolved["flat"] in ("200", "503")

    def test_non_string_args_are_left_alone(self):
        resolved = _resolve({"count": 5, "enabled": True}, {"verify_service": {"x": 1}})
        assert resolved["count"] == 5
        assert resolved["enabled"] is True
