import json

import pytest

from transoria.tools.epub_search_library import SearchLibrary


def rule(id="one"):
    return dict(id=id, name="Hello", query="Hello", replacement="World", scope="text", caseSensitive=False, regularExpression=False)


def test_persistent_library_and_order(tmp_path):
    path = tmp_path / "searches.json"
    SearchLibrary(path).save([rule("two"), rule()])
    assert SearchLibrary(path).load() == [rule("two"), rule()]
    assert not list(tmp_path.glob(".epub-search-*"))


@pytest.mark.parametrize("value", [[rule(), rule()], [{**rule(), "query": ""}], [{**rule(), "scope": "unknown"}], [{**rule(), "caseSensitive": 1}], [rule()] * 101])
def test_invalid_save_does_not_replace_library(tmp_path, value):
    library = SearchLibrary(tmp_path / "searches.json")
    library.save([rule()])
    with pytest.raises(ValueError):
        library.save(value)
    assert json.loads(library.path.read_text()) == [rule()]
