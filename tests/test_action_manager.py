"""Tests for the ActionManager."""
from accessmate.core.action_manager import Action, ActionManager


def test_register_and_fire():
    am = ActionManager()
    called = []
    am.register(Action("test.action", "Test", lambda: called.append(True)))
    am.assign_trigger("test.action", "Key.f1")
    result = am.fire("Key.f1")
    assert result is True
    assert called == [True]


def test_fire_unknown_trigger():
    am = ActionManager()
    assert am.fire("Key.unknown") is False


def test_reassign_trigger():
    am = ActionManager()
    am.register(Action("a", "A", lambda: None))
    am.assign_trigger("a", "Key.f1")
    am.assign_trigger("a", "Key.f2")
    assert am.fire("Key.f1") is False
    assert am.fire("Key.f2") is True


def test_dump_and_load():
    am = ActionManager()
    am.register(Action("a", "A", lambda: None))
    am.assign_trigger("a", "Key.f5")
    dump = am.dump_for_profile()
    assert dump == {"a": "Key.f5"}

    am2 = ActionManager()
    am2.register(Action("a", "A", lambda: None))
    am2.load_from_profile(dump)
    assert am2.fire("Key.f5") is True
