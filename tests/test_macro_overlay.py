"""P3: macro command overlay – data model, categories, and grouping/sorting."""
import types

from withease.app import WithEaseApp
from withease.modules import macros as M


def _module(macros):
    mod = M.MacrosModule()
    mod.load_settings({"macros": macros})
    return mod


def test_macro_from_dict_ignores_unknown_keys_and_defaults():
    m = M.macro_from_dict({"id": "x", "label": "L", "trigger_key": "'a'",
                           "type": "text", "FUTURE_FIELD": 1})
    assert (m.id, m.label, m.category, m.uses) == ("x", "L", "", 0)
    # missing required keys get safe defaults (and a generated id)
    m2 = M.macro_from_dict({"label": "only"})
    assert m2.id and m2.type == "text" and m2.trigger_key == ""


def test_categories_first_seen_order():
    mod = _module([
        {"id": "1", "label": "a", "trigger_key": "", "type": "text",
         "category": "Word"},
        {"id": "2", "label": "b", "trigger_key": "", "type": "text",
         "category": "E-Mail"},
        {"id": "3", "label": "c", "trigger_key": "", "type": "text",
         "category": "Word"},
        {"id": "4", "label": "d", "trigger_key": "", "type": "text"},
    ])
    assert mod.categories() == ["Word", "E-Mail"]


def test_uses_survive_dump_roundtrip():
    mod = _module([{"id": "1", "label": "a", "trigger_key": "", "type": "text",
                    "uses": 7}])
    dumped = mod.dump_settings()["macros"][0]
    assert dumped["uses"] == 7 and dumped["category"] == ""


def _groups(mod, favorites, sort="manual"):
    mod._settings["cmd_overlay"] = {"sort": sort}
    fake = types.SimpleNamespace(
        _macros_module=lambda: mod,
        get_favorites=lambda: favorites,
        _sort_macros=WithEaseApp._sort_macros,
    )
    return WithEaseApp.get_macro_command_groups(fake)


def _sample():
    return _module([
        {"id": "g", "label": "Gruß", "trigger_key": "'g'", "type": "text",
         "category": "E-Mail", "uses": 5},
        {"id": "s", "label": "Signatur", "trigger_key": "'s'", "type": "text",
         "category": "E-Mail", "uses": 20},
        {"id": "w", "label": "Word", "trigger_key": "'w'", "type": "app",
         "category": "Word"},
        {"id": "z", "label": "Zettel", "trigger_key": "'z'", "type": "text"},
    ])


def test_favourites_group_first_and_not_duplicated():
    groups = _groups(_sample(), favorites=["macro:z"])
    assert groups[0][0].endswith("Favoriten") or "Favourite" in groups[0][0]
    fav_rows = groups[0][1]
    assert fav_rows == [("Zettel", "Z", True, 0)]
    # the favourite must not reappear in the category groups
    later = [label for _h, rows in groups[1:] for label, _k, _f, _u in rows]
    assert "Zettel" not in later


def test_category_groups_and_uncategorised_last():
    groups = _groups(_sample(), favorites=[])
    headers = [h for h, _ in groups]
    assert headers[0] == "E-Mail" and headers[1] == "Word"
    assert headers[-1] not in ("E-Mail", "Word")   # uncategorised group last


def test_sort_alpha_and_usage():
    alpha = _groups(_sample(), favorites=[], sort="alpha")
    email_alpha = [label for label, _k, _f, _u in alpha[0][1]]
    assert email_alpha == ["Gruß", "Signatur"]

    usage = _groups(_sample(), favorites=[], sort="usage")
    email_usage = [label for label, _k, _f, _u in usage[0][1]]
    assert email_usage == ["Signatur", "Gruß"]   # 20 uses before 5


def test_sort_reorders_category_groups():
    # _sample: E-Mail (Gruß 5 + Signatur 20 = 25 uses), Word (0), uncategorised.
    manual = [h for h, _ in _groups(_sample(), favorites=[], sort="manual")]
    assert manual[:2] == ["E-Mail", "Word"]          # first-seen order

    alpha = [h for h, _ in _groups(_sample(), favorites=[], sort="alpha")]
    assert alpha.index("E-Mail") < alpha.index("Word")   # alphabetical

    usage = [h for h, _ in _groups(_sample(), favorites=[], sort="usage")]
    assert usage.index("E-Mail") < usage.index("Word")   # 25 uses before 0


def test_sort_applies_within_favourites_group():
    groups = _groups(_sample(), favorites=["macro:g", "macro:s"], sort="usage")
    fav_rows = [label for label, _k, _f, _u in groups[0][1]]
    assert fav_rows == ["Signatur", "Gruß"]   # 20 before 5, even in favourites
