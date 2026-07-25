from bot.core.workflow_failures import unseen_failures


def test_unseen_failures_are_deduplicated_by_run_id():
    runs = [{"id": "new"}, {"id": "seen"}, {"name": "missing-id"}]
    assert unseen_failures(runs, ["seen"]) == [{"id": "new"}]
