from bot.core.workflow_failures import render_failure_block, unseen_failures


def test_unseen_failures_are_deduplicated_by_run_id():
    runs = [{"id": "new"}, {"id": "seen"}, {"name": "missing-id"}]
    assert unseen_failures(runs, ["seen"]) == [{"id": "new"}]


def test_failure_block_preserves_exact_terminal_event():
    block = render_failure_block(
        [
            {
                "id": "abc-123",
                "name": "bisk-snapshot-abc",
                "state_type": "FAILED",
                "end_time": "2026-07-21T09:10:31Z",
                "state_message": "invalid logs payload",
            }
        ]
    )
    assert "bisk-snapshot-abc: FAILED" in block
    assert "run_id=abc-123" in block
    assert "invalid logs payload" in block
    assert "counted, not delivered" in block
