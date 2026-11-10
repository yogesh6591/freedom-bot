from acquisition.services.assessment_extraction import (
    EXTRACTOR_INSTRUCTIONS,
    close_stalled_fields,
    get_ask_state,
    missing_required_assessment_fields,
    parse_assistant_turn_response,
    record_questions_asked,
)


def test_closes_after_one_ask_when_writer_failed():
    st = record_questions_asked(None, ["change_readiness"])
    assert get_ask_state(st)["counts"]["change_readiness"] == 1
    state, closed = close_stalled_fields(st, latest_user="not planning any changes", user_message_id="m1")
    assert closed == []  # normal guard still needs two asks
    state, closed = close_stalled_fields(
        st, latest_user="not planning any changes", user_message_id="m1", min_asks=1
    )
    assert closed == ["change_readiness"]
    assert "change_readiness" not in missing_required_assessment_fields(state)


def test_empty_question_is_flagged_unavailable():
    turn = parse_assistant_turn_response('{"assistant_message":"","asked_fields":[]}')
    assert turn["available"] is False and turn["assistant_message"]


def test_second_hand_rule_in_extractor():
    assert "second-hand answer counts" in EXTRACTOR_INSTRUCTIONS.lower()
