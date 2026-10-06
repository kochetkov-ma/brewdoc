"""Check the fixed capture width through ordinary page code and fresh workers."""

import sys

import pytest

from test_html_js_bootstrap import ENGINE_AVAILABLE
from test_html_js_inner_text import observe


pytestmark = pytest.mark.skipif(sys.platform != "linux" or not ENGINE_AVAILABLE,
                               reason="Viewport/native proofs require the pinned engine in isolated Linux.")


def test_responsive_page_uses_its_desktop_branch_before_any_width_assignment():
    # GIVEN an ordinary responsive consumer with its own narrow and wide counts.
    script = """
const count=window.innerWidth>768?30:10;
document.querySelector('#result').textContent=JSON.stringify(count);
"""
    # WHEN the first page script reads the capture width.
    count = observe(script)
    # THEN the declared desktop input selects the page's own thirty-result branch.
    assert count == 30, "the capture width must select the ordinary desktop branch before page assignments"


def test_fresh_capture_exposes_numeric_width_through_window_and_global():
    # GIVEN a new page without geometry setup or a site-specific width override.
    script = """
document.querySelector('#result').textContent=JSON.stringify(
  [typeof window.innerWidth,window.innerWidth,globalThis.innerWidth]);
"""
    # WHEN actual page code reads both global views before doing any work.
    width = observe(script)
    # THEN the fixed desktop width is a number and has one shared initial value.
    assert width == ["number", 1024, 1024], "every fresh capture must expose the declared numeric width through both global views"


@pytest.mark.parametrize("assignment,width", [
    pytest.param("window.innerWidth", 320, id="window-assignment"),
    pytest.param("innerWidth", 480, id="bare-global-assignment"),
])
def test_page_width_assignment_updates_window_and_bare_global(assignment, width):
    # GIVEN page code writing either alias of its capture-local width.
    script = assignment + "=" + str(width) + ";" + """
document.querySelector('#result').textContent=JSON.stringify([window.innerWidth,innerWidth]);
"""
    # WHEN the page writes its ordinary mutable global before reading both aliases.
    observed = observe(script)
    # THEN writes through either alias produce one page-local shared value.
    assert observed == [width, width], "page width assignment must stay visible through window and the bare global"


def test_width_assignment_in_one_capture_does_not_change_the_next_capture():
    # GIVEN a completed capture whose page changed its width to a narrow value.
    changed = observe("window.innerWidth=320;document.querySelector('#result').textContent=JSON.stringify(innerWidth);")
    assert changed == 320, "the first capture must actually change its page-local width before testing reset"
    # WHEN a fresh capture reads width without assigning it.
    fresh = observe("document.querySelector('#result').textContent=JSON.stringify([window.innerWidth,globalThis.innerWidth]);")
    # THEN the previous page's assignment cannot leak into fresh worker state.
    assert fresh == [1024, 1024], "every fresh capture must reset the desktop width instead of inheriting a page assignment"
