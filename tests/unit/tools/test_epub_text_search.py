import pytest

from transoria.tools.epub_text_search import VisibleText


def test_visible_mapping_keeps_markup_and_decodes_entities():
    source = '<html><head><title>Secret</title></head><body><p title="Secret">Hello <em>world</em> &amp; &#x1f338;</p><script>Secret</script><p>Next</p></body></html>'
    view = VisibleText(source)
    assert view.text == "Hello world & 🌸\nNext\n"
    start, end = view.source_range(0, 11)
    assert source[start:end] == "Hello <em>world"
    updated = source
    for left, right, value in reversed(view.replacement_edits(0, 11, "<Welcome>&")):
        updated = updated[:left] + value + updated[right:]
    assert '&lt;Welcome&gt;&amp;<em></em>' in updated
    assert '<p title="Secret">' in updated


def test_replacement_refuses_partial_entities_and_block_crossing():
    view = VisibleText('<p>&NotEqualTilde;</p><p>Next</p>')
    with pytest.raises(ValueError, match="entity"):
        view.replacement_edits(0, 1, "x")
    with pytest.raises(ValueError, match="boundaries"):
        view.replacement_edits(0, len(view.text), "x")


def test_legacy_unclosed_inline_and_whitespace_mapping():
    view = VisibleText('<body><p>Hello    <b>world<p>Next')
    assert view.text == 'Hello world\nNext'
    assert len(view.text) == len(view.positions)
