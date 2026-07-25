"""The people pass — one scheduled slot pointed at people, not systems.

Every other scheduled wake pointed phi at machine state: workflow health,
market position, her own metrics. So even the posts she chose the subject
of read like status reports, and the [SELF-AWARENESS] inventory correctly
reported `mode: mostly operational alerts and incident reports`. Nothing
ever woke her up to go read a person. This is that wake.
"""

from bot.config import settings


def test_people_hours_are_a_subset_of_thought_hours():
    """A people hour that isn't a thought hour never fires — the slot
    scheduler only wakes on thought hours."""
    assert set(settings.people_pass_hours) <= set(settings.thought_post_hours)


def test_not_every_slot_is_a_people_pass():
    """The general cycle still has to happen; this replaces one slot, not
    the rhythm."""
    assert set(settings.thought_post_hours) - set(settings.people_pass_hours)


def test_people_hours_land_in_operator_waking_hours():
    assert all(8 <= h <= 22 for h in settings.people_pass_hours)


def test_slot_routing_picks_the_people_pass_only_on_its_hour():
    """Mirrors the poller's branch: a slot is either a cycle or a people
    pass, decided by the operator-local hour."""
    people = set(settings.people_pass_hours)
    routed = {
        h: ("people" if h in people else "cycle") for h in settings.thought_post_hours
    }
    assert "people" in routed.values()
    assert "cycle" in routed.values()


def test_the_pass_is_registered_as_a_trigger_slot():
    """Prefect (or the operator) can fire it on demand, like every other
    scheduled pass."""
    from bot.main import _TRIGGER_SLOTS

    assert "people" in _TRIGGER_SLOTS


def test_every_trigger_slot_resolves_to_a_real_handler_method():
    """Registration alone proves nothing: the slots hold lambdas that read
    `handler.<name>` at call time, so a misnamed method stays invisible
    until that slot next fires — which for a daily pass means a day.
    """
    import inspect

    from bot.main import _TRIGGER_SLOTS
    from bot.services.message_handler import MessageHandler

    for slot, fn in _TRIGGER_SLOTS.items():
        attr = (
            inspect.getsource(fn).split("handler.")[1].strip().rstrip(",").rstrip(")")
        )
        assert callable(getattr(MessageHandler, attr, None)), (
            f"trigger slot {slot!r} points at MessageHandler.{attr}, which does not exist"
        )


def test_the_task_leaves_scope_to_phi():
    """The point is that phi picks narrow-vs-wide and knows why. If this
    prompt ever starts dictating which people to read, that's the thing it
    was written to avoid."""
    import inspect

    from bot.agent import PhiAgent

    src = inspect.getsource(PhiAgent.process_people)
    assert "pick your own scope" in src
    # and it must not drag her back to the machine-state diet
    assert "no infrastructure" in src
