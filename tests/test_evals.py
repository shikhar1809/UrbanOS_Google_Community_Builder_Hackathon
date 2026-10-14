"""Tests for the eval scoring itself.

A metric that is quietly wrong is worse than no metric, because it converts
"we do not know" into false confidence. These pin the two properties the suite
depends on: macro-F1 must punish a class the model never predicts, and the two
guardrail error types must never be averaged together.
"""

from evals import metrics as M


def test_accuracy_and_macro_f1_agree_when_everything_is_right():
    score = M.score_field("category", [
        ("A", "A", "c1"), ("B", "B", "c2"), ("C", "C", "c3"),
    ])

    assert score.accuracy == 1.0
    assert score.macro_f1() == 1.0


def test_macro_f1_punishes_a_class_the_model_never_predicts():
    """Accuracy hides this: a rare category is exactly the one an MP has not
    already heard about, so it must not be averaged away."""
    pairs = [("common", "common", f"c{i}") for i in range(9)]
    pairs.append(("rare", "common", "rare-1"))

    score = M.score_field("category", pairs)

    assert score.accuracy == 0.9, "accuracy looks healthy"
    assert score.macro_f1() < 0.6, "macro-F1 must expose the missed class"


def test_a_missing_prediction_counts_as_wrong_not_absent():
    """An outage returning nothing must not score as perfect."""
    score = M.score_field("category", [("A", None, "c1"), ("B", "B", "c2")])

    assert score.missing == 1
    assert score.correct == 1
    assert score.accuracy == 0.5
    assert score.failures[0]["actual"] is None


def test_gate_keeps_the_two_error_types_apart():
    cases = [
        ("accept", "reject", "a real complaint"),      # silences a citizen
        ("accept", "accept", "another real one"),
        ("reject", "accept", "out of area"),           # junk let through
        ("reject", "reject", "also out of area"),
    ]

    score = M.score_gate("geofence", cases)

    assert score.false_reject_rate == 0.5
    assert score.false_accept_rate == 0.5
    assert len(score.failures) == 2
    assert "silenced" in score.failures[0]["cost"]


def test_accept_flagged_counts_as_accepted():
    """An outer-district report still reaches an aide, so it is not a reject."""
    score = M.score_gate("geofence", [("accept", "accept_flagged", "outskirts")])

    assert score.false_reject_rate == 0.0


def test_unknown_defers_rather_than_failing_either_way():
    """The cheap text check returning 'unknown' hands the decision to the model.
    That is the designed behaviour, not a miss."""
    accept_side = M.score_gate("g", [("accept", "unknown", "unrecognised place")])
    reject_side = M.score_gate("g", [("reject", "unknown", "unrecognised place")])

    assert accept_side.false_reject_rate == 0.0
    assert reject_side.false_accept_rate == 0.0, "deferring is not letting junk through"


def test_tag_consistency_requires_one_tag_for_one_issue():
    result = M.tag_consistency({
        "street_lighting": ["street_lighting", "street_lighting"],
        "water_supply": ["water_supply", "water_shortage"],
    })

    assert result["issues"] == 2
    assert result["consistent"] == 1
    assert result["rate"] == 0.5
    assert result["detail"]["water_supply"]["consistent"] is False


def test_a_missing_tag_breaks_consistency():
    """An untagged report cannot be clustered into a project at all."""
    result = M.tag_consistency({"road_repair": ["road_repair", None]})

    assert result["detail"]["road_repair"]["consistent"] is False


def test_fragmentation_counts_tags_per_bucket():
    records = [
        {"constituency_zone": "North", "category": "Infra", "semantic_tag": "road_repair"},
        {"constituency_zone": "North", "category": "Infra", "semantic_tag": "pothole_repair"},
        {"constituency_zone": "North", "category": "Infra", "semantic_tag": "road_repair"},
        {"constituency_zone": "South", "category": "Infra", "semantic_tag": "water_supply"},
        {"constituency_zone": "South", "category": "Infra", "semantic_tag": ""},
    ]

    result = M.fragmentation(records)

    assert result["buckets"] == 2
    assert result["untagged_reports"] == 1
    assert result["max_tags_in_one_bucket"] == 2


def test_threshold_check_flags_the_right_direction():
    from evals.run import check_thresholds

    # A rate is a ceiling; stability is a floor.
    assert check_thresholds({"geofence_text_false_reject_rate": 0.0}) == []
    assert check_thresholds({"geofence_text_false_reject_rate": 0.1}) != []
    assert check_thresholds({"ward_mapping_stability": 1.0}) == []
    assert check_thresholds({"ward_mapping_stability": 0.9}) != []


def test_guardrail_suite_runs_without_a_model():
    """The gates must stay verifiable during an outage, which is when they
    carry the most weight."""
    from evals.run import run_guardrails

    result = run_guardrails()

    assert result["metrics"]["geofence_text_false_reject_rate"] == 0.0
    assert result["metrics"]["ward_mapping_stability"] == 1.0
