from lxml import etree
import pytest

from transoria.tools.epub_rendition import rendition

OPF = "http://www.idpf.org/2007/opf"


def package(metadata='', props=''):
    return etree.fromstring(f'<package xmlns="{OPF}"><metadata>{metadata}</metadata><manifest><item id="chapter" href="Text/one.xhtml"/></manifest><spine page-progression-direction="rtl"><itemref idref="chapter" properties="{props}"/></spine></package>')


def test_rendition_global_properties_and_item_overrides():
    opf = package('<meta property="rendition:layout">pre-paginated</meta><meta property="rendition:spread">landscape</meta>', 'rendition:layout-reflowable rendition:spread-none rendition:page-spread-center')
    result = rendition(opf, etree.fromstring('<html/>'), 'OEBPS/book.opf', 'OEBPS/Text/one.xhtml')
    assert result == dict(layout="reflowable", spread="none", position="center", direction="rtl")
    assert rendition(opf, etree.fromstring('<html/>'), 'OEBPS/book.opf', 'OEBPS/Text/other.xhtml')["layout"] == "pre-paginated"


@pytest.mark.parametrize('content,width,height', [('width=1200,height=600', 1200, 600), ('width=600; height=900; width=700', 600, 900), ('WIDTH = device-width HEIGHT = device-height', 'device-width', 'device-height'), ('width=nan,height=-1', None, None)])
def test_viewport_dimensions_and_invalid_values(content, width, height):
    document = etree.fromstring(f'<html><head><meta name="viewport" content="{content}"/><meta name="viewport" content="width=1,height=1"/></head><body/></html>')
    result = rendition(package(), document, 'OEBPS/book.opf', 'OEBPS/Text/one.xhtml')
    assert result.get('width') == width and result.get('height') == height


def test_legacy_fixed_layout_and_svg_dimensions():
    result = rendition(package('<meta name="fixed-layout" content="true"/>'), etree.fromstring('<svg viewBox="0 0 600 900"/>'), 'OEBPS/book.opf', 'OEBPS/Text/one.xhtml')
    assert result['layout'] == 'pre-paginated' and result['width'] == 600 and result['height'] == 900
