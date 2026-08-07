"""P4: the module store index carries an optional per-module preview icon."""
from withease.core import module_store


def test_parse_index_reads_icon_with_default():
    mods = module_store._parse_index({"modules": [
        {"id": "a", "name": "A", "download_url": "x", "icon": "🎙️"},
        {"id": "b", "name": "B", "download_url": "x"},   # no icon → empty
    ]})
    by_id = {m.id: m for m in mods}
    assert by_id["a"].icon == "🎙️"
    assert by_id["b"].icon == ""     # the card falls back to a generic icon
