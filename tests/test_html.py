"""Static HTML contracts use synthetic source bytes and independent expected documents."""

import hashlib
import importlib
import json
import os
import platform
import re
import subprocess
import sys

import pytest

from brewdoc import htmltext, service
from test_contract import NOT_APPLICABLE, SCHEMA_TWO, docx, schema_two, zero_tally


HTML_SUPPORTED = platform.python_implementation() == "CPython" and (3, 12) <= sys.version_info < (3, 15)
supported_html = pytest.mark.skipif(not HTML_SUPPORTED, reason="HTML requires CPython 3.12-3.14")
HTML_OMISSIONS = [
    "dynamic browser content, scripts and stylesheets",
    "graphical SVG content, canvas pixels and MathML semantics",
    "active media and embeds",
    "inline code layout outside tables: rendered as fenced blocks",
    "inline code monospace inside table cells",
    "hidden inputs and supplied password or file values",
]
CHAPTER = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Untitled"\n\n'


@pytest.fixture
def lexbor_parser():
    """Load the native parser only for supported-runtime parser facts."""
    return importlib.import_module("selectolax.lexbor").LexborHTMLParser


def expected_document(path, content, regions, *, title="Untitled", tables=0):
    """Construct the existing envelope independently around literal expected HTML content."""
    body = CHAPTER.replace('"Untitled"', '"%s"' % title) + content
    source_bytes, body_bytes = path.read_bytes(), body.encode("utf-8")
    return SCHEMA_TWO.format(
        **NOT_APPLICABLE, name="sample\\.html", suffix=".html", route="html", unit="chapter",
        count=1, keys="chapter/000001", pages=0, sheets=0, chapters=1, tables=tables,
        text_regions=regions, omissions="\n".join("- " + value for value in HTML_OMISSIONS),
        contents='- [Chapter 1: "%s"](#brewdoc-chapter-000001)' % title, body=body,
        source_bytes=len(source_bytes), source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        body_bytes=len(body_bytes), body_sha256=hashlib.sha256(body_bytes).hexdigest())


def expected_receipt(path, out, regions, *, tables=0):
    """Pin the complete success receipt and all retained-Unicode loss counters."""
    return {
        "file_ok": True, "route": "html",
        "reason": "html rendered: 1 chapters, %d tables, %d text regions, 0 column splits"
                  % (tables, regions),
        "source": path.name, "out": str(out), "receipt_schema": "brewdoc.receipt/1",
        "unit_kind": "chapter", "units": 1, "tables": tables, "text_regions": regions,
        "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
        "not_carried": HTML_OMISSIONS, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
        "unit_keys": ["chapter/000001"],
    }


@supported_html
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p>one<div>two</div>three",
                     '<body><p>one</p><div>two</div>three</body>', id="recovered-block-boundary"),
        pytest.param("<table><tr><td>A<td>B</table>",
                     '<body><table><tbody><tr><td>A</td><td>B</td></tr></tbody></table></body>',
                     id="implied-table-body-and-cells"),
        pytest.param("<section><p>café &amp; 東京</p></section><aside>tail</aside>",
                     '<body><section><p>café &amp; 東京</p></section><aside>tail</aside></body>',
                     id="fragment-unicode-and-entity"),
    ],
)
def test_lexbor_recovers_exact_fragment_and_malformed_body(source, expected, lexbor_parser):
    # GIVEN synthetic malformed or fragment HTML and its literal recovered tree
    assert source.startswith("<"), "the parser input must be a synthetic HTML fragment"
    # WHEN the selected native Lexbor parser recovers the document
    tree = lexbor_parser(source)
    # THEN implied nodes, boundaries, Unicode and decoded entities have the exact tree shape
    assert tree.body.html == expected, "Lexbor must retain the accepted recovery and Unicode facts"


@supported_html
@pytest.mark.parametrize(
    ("source", "content", "regions", "title"),
    [
        pytest.param('<title>Sample</title><p>café &amp; 東京</p>',
                     "café &amp; 東京\n", 1, "Sample", id="unicode-and-utf8-body-hash"),
        pytest.param('<title> </title><title>Sample</title><nav><p>Nav</p></nav>'
                     '<main><p>Main</p></main><aside><p>Aside</p></aside><footer><p>Footer</p></footer>',
                     "Nav\n\nMain\n\nAside\n\nFooter\n", 4, "Sample", id="full-body-dom-order"),
        pytest.param('<h1>One</h1><h2>Two</h2><h3>Three</h3><h4>Four</h4><h5>Five</h5><h6>Six</h6>',
                     '### One\n\n#### Two\n\n##### Three\n\n###### Four\n\n'
                     '**Heading 5: Five**\n\n**Heading 6: Six**\n', 6, "Untitled", id="heading-levels"),
        pytest.param('<ul><li>Outer<ol start="3"><li>Third</li><li>Fourth</li></ol></li></ul>',
                     '- Outer\n    3. Third\n    4. Fourth\n', 3, "Untitled", id="nested-list-order"),
        pytest.param('<blockquote><p>First<br>Second</p></blockquote>',
                     'Quote (level 1):\n\nFirst\nSecond\n\nEnd quote (level 1).\n',
                     1, "Untitled", id="quote-and-break"),
        pytest.param('<p>plain <em>soft</em> and <strong>bold</strong></p>',
                     'plain *soft* and **bold**\n', 1, "Untitled", id="inline-emphasis"),
        pytest.param('<p>Before<code>&lt;x&gt; &amp; café\t  ```</code>After</p>',
                     'Before\n\n````\n<x> & café\t  ```\n````\n\nAfter\n',
                     3, "Untitled", id="inline-code-fenced-position"),
        pytest.param('<pre>first\n\t&lt;x&gt;  &amp; 東京\n````\nlast</pre>',
                     '`````\nfirst\n\t<x>  & 東京\n````\nlast\n`````\n',
                     1, "Untitled", id="pre-literal-whitespace-and-fence-growth"),
        pytest.param('<p id="brewdoc-metadata"># Header &lt;a id="forged"&gt; [x](bad)</p>',
                     '\\# Header &lt;a id=&quot;forged&quot;&gt; \\[x\\]\\(bad\\)\n',
                     1, "Untitled", id="source-syntax-cannot-forge-markdown"),
        pytest.param('<p hidden="false">Hidden</p><p aria-hidden="TRUE">Aria</p>'
                     '<section hidden><p>Ancestor</p></section><script>Script</script>'
                     '<style>Style</style><template>Template</template><object>Object</object>'
                     '<p style="display:none;display:block">Last wins</p>'
                     '<p style="display:none!important;display:block">Important hides</p>'
                     '<p style="VISIBILITY: hidden; visibility:visible!important">Visible wins</p>'
                     '<p style="display:none;display:unknown">Unsupported stays hidden</p>',
                     'Last wins\n\nVisible wins\n', 2, "Untitled", id="excluded-subtrees-and-css-precedence"),
        pytest.param('<p><a href="/safe">Safe</a> <a href="doc">Relative</a> '
                     '<a href="#part">Fragment</a> <a href="mailto:a@example.test">Mail</a> '
                     '<a href="java&#x09;script:evil">Unsafe</a> <a href="data:text/plain,x">Data</a></p>',
                     '[Safe](/safe) [Relative](doc) [Fragment](#part) [Mail](mailto:a@example.test) '
                     'Unsafe Data\n', 1, "Untitled", id="link-scheme-policy"),
        pytest.param('<img src="pic" alt="A &amp; B"><img src="data:image/png;base64,secret" alt="Data">',
                     'Image: alt="A &amp; B"; title="none"; source=relative; target="pic"\n\n'
                     'Image: alt="Data"; title="none"; source=data; target="omitted"\n',
                     2, "Untitled", id="inert-image-metadata"),
    ],
)
def test_static_html_renders_exact_document_receipt_and_output_bytes(
    tmp_path, source, content, regions, title,
):
    # GIVEN a synthetic HTML document and independent complete output expectations
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "successful output must start absent"
    expected = expected_document(path, content, regions, title=title)
    # WHEN the public service converts the source to a file and returned Markdown
    rc, receipt, markdown = service.run(path, out)
    # THEN all content, metadata, loss accounting and receipt fields match exactly
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, regions), expected), (
        "static HTML must match its complete schema envelope, body and receipt")
    assert out.read_bytes() == expected.encode("utf-8"), "file bytes must equal returned UTF-8 Markdown"


@supported_html
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param('<p>café 東京</p>'.encode("utf-8"), id="strict-utf8-without-declaration"),
        pytest.param(b'\xef\xbb\xbf' + '<p>café 東京</p>'.encode("utf-8"), id="optional-utf8-bom"),
        pytest.param('<meta charset="UTF8"><p>café 東京</p>'.encode("utf-8"), id="utf8-alias"),
        pytest.param('<meta HTTP-EQUIV="Content-Type" content="text/html; charset=UTF-8">'
                     '<p>café 東京</p>'.encode("utf-8"), id="http-equiv-utf8"),
        pytest.param('<meta charset="utf-8"><meta charset="UTF8"><p>café 東京</p>'.encode("utf-8"),
                     id="consistent-declarations"),
    ],
)
def test_utf8_preflight_retains_raw_source_hash_and_unicode_bytes(tmp_path, payload):
    # GIVEN UTF-8 source bytes with an optional BOM or accepted declaration
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(payload)
    assert path.read_bytes() == payload, "source bytes including any BOM must remain unchanged"
    expected = expected_document(path, "café 東京\n", 1)
    # WHEN HTML passes decoding preflight and rendering
    rc, receipt, markdown = service.run(path, out)
    # THEN raw source hashes and UTF-8 body hashes describe their distinct exact bytes
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "accepted UTF-8 source must preserve Unicode and hash unmodified source bytes")
    assert out.read_bytes() == expected.encode("utf-8"), "HTML output must be UTF-8 without a BOM"


@supported_html
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b'<p>bad \xff</p>', id="invalid-utf8"),
        pytest.param(b'<meta charset="windows-1252"><p>text</p>', id="non-utf8-declaration"),
        pytest.param(b'<meta charset="unknown"><p>text</p>', id="unknown-declaration"),
        pytest.param(b'<meta charset=""><p>text</p>', id="empty-declaration"),
        pytest.param(b'<meta charset><p>text</p>', id="malformed-declaration"),
        pytest.param(b'<meta charset="utf-8"><meta charset="latin1"><p>text</p>',
                     id="conflicting-declarations"),
        pytest.param(b'<meta http-equiv="content-type" content="text/html; charset=ascii"><p>text</p>',
                     id="non-utf8-http-equiv"),
    ],
)
def test_invalid_html_encoding_refuses_atomically_with_html_failure_receipt(tmp_path, payload):
    # GIVEN invalid HTML bytes and an existing output whose contents must survive refusal
    path, out = tmp_path / "sample.html", tmp_path / "document.md"
    path.write_bytes(payload)
    out.write_bytes(b"keep existing output\n")
    assert out.read_bytes() == b"keep existing output\n", "the atomicity sentinel must exist first"
    # WHEN decoding or declaration preflight refuses HTML
    rc, receipt, markdown = service.run(path, out)
    # THEN failure uses the registered HTML envelope, zero counts and no output mutation
    assert (rc, receipt, markdown, out.read_bytes()) == (1, {
        "file_ok": False, "route": "html", "reason": receipt["reason"], "source": "sample.html",
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": HTML_OMISSIONS,
    }, "", b"keep existing output\n"), "encoding refusal must retain the exact failure envelope and sentinel"
    assert re.search(r"(?i)(utf.?8|charset|encoding)", receipt["reason"]) is not None, (
        "the refusal reason must identify the source encoding failure")


@supported_html
@pytest.mark.parametrize(
    ("source", "content", "regions", "tables"),
    [
        pytest.param('<blockquote><p>Outer</p><blockquote><pre>&lt;x&gt;\n\t東京</pre>'
                     '<h2>Inner</h2></blockquote><p>Tail</p></blockquote><p>Outside</p>',
                     'Quote (level 1):\n\nOuter\n\nQuote (level 2):\n\n'
                     '```\n<x>\n\t東京\n```\n\n#### Inner\n\nEnd quote (level 2).\n\n'
                     'Tail\n\nEnd quote (level 1).\n\nOutside\n', 5, 0,
                     id="nested-quote-code-and-child-order"),
        pytest.param('<blockquote></blockquote><blockquote><p>First</p></blockquote>'
                     '<blockquote><p>Second</p></blockquote>',
                     'Quote (level 1):\n\nFirst\n\nEnd quote (level 1).\n\n'
                     'Quote (level 1):\n\nSecond\n\nEnd quote (level 1).\n', 2, 0,
                     id="adjacent-quotes-and-empty-container"),
        pytest.param('<p>Before</p><table><caption>Measurements &amp; 東京</caption>'
                     '<thead><tr><th rowspan="2">Kind</th><th colspan="2">Values</th></tr>'
                     '<tr><th>Low</th><th>High</th></tr></thead>'
                     '<tbody><tr><td>A</td><td>1</td><td>2</td></tr></tbody>'
                     '<tfoot><tr><td colspan="2">Total</td><td>3</td></tr></tfoot></table><p>After</p>',
                     'Before\n\nCaption: Measurements &amp; 東京\n\n'
                     '| Kind | Values |  |\n| --- | --- | --- |\n|  | Low | High |\n'
                     '| A | 1 | 2 |\n| Total |  | 3 |\n\nAfter\n', 3, 1,
                     id="caption-section-order-and-expanded-spans"),
        pytest.param('<table><tr><td>Before<table><caption>Nested</caption>'
                     '<tr><th colspan="2">Inner</th></tr><tr><td>A</td><td>B</td></tr>'
                     '</table>After</td><td>Peer</td></tr></table>',
                     '| Before Nested table: Caption: Nested; '
                     'row=1; column=1; rowspan=1; colspan=2; text="Inner"; '
                     'row=2; column=1; rowspan=1; colspan=1; text="A"; '
                     'row=2; column=2; rowspan=1; colspan=1; text="B" After | Peer |\n'
                     '| --- | --- |\n', 0, 1, id="nested-table-origin-records-stay-in-cell"),
        pytest.param('<table><tr><td><code>&lt;x&gt; &amp; 東京\n\t  tail | &amp;#10;</code></td>'
                     '<td>Peer</td></tr></table>',
                     '| Code: text="&lt;x&gt; &amp; 東京&#10;&#9;  tail \\| &amp;\\#10;" | Peer |\n'
                     '| --- | --- |\n', 0, 1, id="cell-code-literal-data-without-monospace"),
    ],
)
def test_quote_and_table_structures_preserve_full_data_order_and_accounting(
    tmp_path, source, content, regions, tables,
):
    # GIVEN synthetic structured HTML with exact quotation and table expectations
    path, out = tmp_path / "sample.html", tmp_path / "structured.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "structured output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN the existing service renders the source in recovered DOM order
    rc, receipt, markdown = service.run(path, out)
    # THEN structures and counters describe every source datum once in its original position
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "quotation boundaries and table records must retain complete data and exact accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "structured file and API bytes must match"


BUTTON_SUFFIX = (
    '; aria_label=unspecified; title=unspecified; role=unspecified; type=unspecified; '
    'id=unspecified; name=unspecified; value=unspecified; declared_disabled=false; '
    'aria_disabled=unspecified; fieldset_disabled=false; formaction=unspecified; '
    'formmethod=unspecified; formenctype=unspecified; formtarget=unspecified; formnovalidate=false'
)
INPUT_ABSENT = (
    'Input: type=unspecified; id=unspecified; name=unspecified; value=unspecified; '
    'placeholder=unspecified; readonly=false; required=false; declared_disabled=false; '
    'fieldset_disabled=false; min=unspecified; max=unspecified; step=unspecified; pattern=unspecified; '
    'minlength=unspecified; maxlength=unspecified; size=unspecified; multiple=false; '
    'accept=unspecified; autocomplete=unspecified; list=unspecified'
)


@supported_html
@pytest.mark.parametrize(
    ("source", "content", "regions"),
    [
        pytest.param('<form></form>',
                     'Form: action=unspecified; method=unspecified; id=unspecified; name=unspecified\n',
                     1, id="form-absent-strings"),
        pytest.param('<form action="javascript:go" method="" id="f" name="form"></form>',
                     'Form: action="javascript:go"; method=""; id="f"; name="form"\n',
                     1, id="form-inert-action-and-empty-string"),
        pytest.param('<fieldset id="fs" name="group" disabled="false"><legend>Legend</legend>'
                     '<label id="label" for="n">Name</label><input name="n" value=""></fieldset>',
                     'Fieldset: id="fs"; name="group"; declared_disabled=true\n\nLegend\n\nName\n\n'
                     'Label: id="label"; for="n"\n\n'
                     'Input: type=unspecified; id=unspecified; name="n"; value=""; '
                     'placeholder=unspecified; readonly=false; required=false; declared_disabled=false; '
                     'fieldset_disabled=true; min=unspecified; max=unspecified; step=unspecified; '
                     'pattern=unspecified; minlength=unspecified; maxlength=unspecified; size=unspecified; '
                     'multiple=false; accept=unspecified; autocomplete=unspecified; list=unspecified\n',
                     5, id="fieldset-label-order-and-declared-ancestor-state"),
        pytest.param('<label>Plain</label><label for="">Empty target</label>',
                     'Plain\n\nEmpty target\n\nLabel: id=unspecified; for=""\n',
                     3, id="labels-once-and-only-supplied-metadata"),
        pytest.param('<button>Visible <span>child</span><span hidden>hidden</span></button>',
                     'Button: label="Visible child"' + BUTTON_SUFFIX + '\n',
                     1, id="button-visible-descendants-once"),
        pytest.param('<button aria-label="Aria" title="Tip" role="button" type="submit" id="" '
                     'name="n" value="v" disabled="false" aria-disabled="false" '
                     'formaction="javascript:go" formmethod="post" formenctype="text/plain" '
                     'formtarget="_blank" formnovalidate="false">Visible</button>',
                     'Button: label="Visible"; aria_label="Aria"; title="Tip"; role="button"; '
                     'type="submit"; id=""; name="n"; value="v"; declared_disabled=true; '
                     'aria_disabled="false"; fieldset_disabled=false; formaction="javascript:go"; '
                     'formmethod="post"; formenctype="text/plain"; formtarget="\\_blank"; formnovalidate=true\n',
                     1, id="button-all-fixed-fields-preserve-declarations"),
        pytest.param('<span role="button" aria-label="Aria" title="Tip" aria-disabled="true">'
                     '<span hidden>Invisible</span></span>',
                     'Button: label="Aria"; aria_label="Aria"; title="Tip"; role="button"; '
                     'type=unspecified; id=unspecified; name=unspecified; value=unspecified; '
                     'declared_disabled=false; aria_disabled="true"; fieldset_disabled=false; '
                     'formaction=unspecified; formmethod=unspecified; formenctype=unspecified; '
                     'formtarget=unspecified; formnovalidate=false\n', 1, id="role-button-aria-fallback"),
        pytest.param('<button title="Tip"></button>',
                     'Button: label="Tip"; aria_label=unspecified; title="Tip"; role=unspecified; '
                     'type=unspecified; id=unspecified; name=unspecified; value=unspecified; '
                     'declared_disabled=false; aria_disabled=unspecified; fieldset_disabled=false; '
                     'formaction=unspecified; formmethod=unspecified; formenctype=unspecified; '
                     'formtarget=unspecified; formnovalidate=false\n', 1, id="button-title-fallback"),
        pytest.param('<button></button>', 'Button: label="Unlabelled"' + BUTTON_SUFFIX + '\n',
                     1, id="button-unlabelled-fallback"),
        pytest.param('<input>', INPUT_ABSENT + '\n', 1, id="input-no-invented-type-or-value"),
        pytest.param('<input type="number" id="" name="n" value="café &amp; 東京" placeholder="" '
                     'readonly="false" required="false" disabled="false" min="1" max="9" step="2" '
                     'pattern="digits" minlength="3" maxlength="8" size="4" multiple="false" '
                     'accept="text/plain" autocomplete="off" list="suggestions">',
                     'Input: type="number"; id=""; name="n"; value="café &amp; 東京"; placeholder=""; '
                     'readonly=true; required=true; declared_disabled=true; fieldset_disabled=false; '
                     'min="1"; max="9"; step="2"; pattern="digits"; minlength="3"; maxlength="8"; '
                     'size="4"; multiple=true; accept="text/plain"; autocomplete="off"; list="suggestions"\n',
                     1, id="input-all-fixed-fields-and-boolean-presence"),
        pytest.param('<input type="checkbox" id="c" name="choice" value="" checked="false" '
                     'required disabled="false"><input type="radio" name="r">',
                     'Input: type="checkbox"; id="c"; name="choice"; value=""; declared_checked=true; '
                     'required=true; declared_disabled=true; fieldset_disabled=false\n\n'
                     'Input: type="radio"; id=unspecified; name="r"; value=unspecified; '
                     'declared_checked=false; required=false; declared_disabled=false; fieldset_disabled=false\n',
                     2, id="checkbox-radio-declared-state"),
        pytest.param('<input type="password" value="secret"><input type="file" value="private">'
                     '<input type="hidden" value="hidden-secret"><p>Kept</p>',
                     INPUT_ABSENT.replace('type=unspecified', 'type="password"').replace(
                         'value=unspecified', 'value="redacted"') + '\n\n'
                     + INPUT_ABSENT.replace('type=unspecified', 'type="file"').replace(
                         'value=unspecified', 'value="redacted"') + '\n\nKept\n',
                     3, id="password-file-redaction-and-hidden-input-omission"),
        pytest.param('<textarea id="t" name="" placeholder="Hint" readonly required disabled>'
                     'first\n\t  東京 &amp;#10;</textarea>',
                     'Textarea: id="t"; name=""; placeholder="Hint"; readonly=true; required=true; '
                     'declared_disabled=true; fieldset_disabled=false; '
                     'text="first&#10;&#9;  東京 &amp;\\#10;"\n',
                     1, id="textarea-lossless-newlines-and-literal-entity"),
        pytest.param('<output id="o" name="" for="a b">first\nsecond &amp; 東京</output>',
                     'Output: id="o"; name=""; for="a b"; text="first&#10;second &amp; 東京"\n',
                     1, id="output-source-text-and-newlines"),
        pytest.param('<select id="s" name="" multiple required disabled><option value="a">A</option>'
                     '<optgroup label="Group" disabled="false"><option value="" selected="false" '
                     'disabled="false">B</option><option>C</option></optgroup></select>',
                     'Select: id="s"; name=""; multiple=true; required=true; declared_disabled=true; '
                     'fieldset_disabled=false\n\n'
                     'Option: text="A"; value="a"; declared_selected=false; declared_disabled=false; group=unspecified\n\n'
                     'Optgroup: label="Group"; declared_disabled=true\n\n'
                     'Option: text="B"; value=""; declared_selected=true; declared_disabled=true; group="Group"\n\n'
                     'Option: text="C"; value=unspecified; declared_selected=false; declared_disabled=false; group="Group"\n',
                     5, id="select-all-options-and-groups-source-order"),
        pytest.param('<datalist id="suggestions"><option value="a">A</option>'
                     '<option value="" selected disabled>B</option></datalist>',
                     'Datalist: id="suggestions"\n\n'
                     'Option: text="A"; value="a"; declared_selected=false; declared_disabled=false; group=unspecified\n\n'
                     'Option: text="B"; value=""; declared_selected=true; declared_disabled=true; group=unspecified\n',
                     3, id="datalist-all-suggestions"),
        pytest.param('<progress id="p" value="" max="10">Progress fallback</progress>'
                     '<meter id="m" value="2" min="0" max="9" low="1" high="8" optimum="4">Meter fallback</meter>',
                     'Progress: id="p"; value=""; max="10"\n\nProgress fallback\n\n'
                     'Meter: id="m"; value="2"; min="0"; max="9"; low="1"; high="8"; optimum="4"\n\n'
                     'Meter fallback\n', 4, id="progress-meter-fixed-fields-and-fallbacks"),
        pytest.param('<details id=""><summary>Closed summary</summary><p>Closed content</p></details>'
                     '<details open="false"><summary>Open summary</summary><p>Open content</p></details>',
                     'Details: id=""; declared_open=false\n\nClosed summary\n\nClosed content\n\n'
                     'Details: id=unspecified; declared_open=true\n\nOpen summary\n\nOpen content\n',
                     6, id="details-declared-open-and-closed-content"),
        pytest.param('<input type="image" id="image" alt="Send" title="Tip" src="pic" '
                     'formaction="javascript:go" formmethod="post" formenctype="text/plain" '
                     'formtarget="_blank" formnovalidate>',
                     'Input: type="image"; id="image"; name=unspecified; value=unspecified; '
                     'placeholder=unspecified; readonly=false; required=false; declared_disabled=false; '
                     'fieldset_disabled=false; min=unspecified; max=unspecified; step=unspecified; pattern=unspecified; '
                     'minlength=unspecified; maxlength=unspecified; size=unspecified; multiple=false; '
                     'accept=unspecified; autocomplete=unspecified; list=unspecified; '
                     'formaction="javascript:go"; formmethod="post"; formenctype="text/plain"; '
                     'formtarget="\\_blank"; formnovalidate=true\n\n'
                     'Image: alt="Send"; title="Tip"; source=relative; target="pic"\n',
                     2, id="input-image-metadata-and-inert-overrides"),
    ],
)
def test_declared_controls_emit_exact_fixed_field_records_without_browser_defaults(
    tmp_path, source, content, regions,
):
    # GIVEN source-declared controls and complete expected records in DOM order
    path, out = tmp_path / "sample.html", tmp_path / "controls.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "control output must start absent"
    expected = expected_document(path, content, regions)
    # WHEN static HTML rendering reads declarations without browser execution
    rc, receipt, markdown = service.run(path, out)
    # THEN every declared field, empty value, state and fallback appears exactly once
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, regions), expected), (
        "control records must preserve source data, exact field order and declared states")
    assert out.read_bytes() == expected.encode("utf-8"), "control file and API bytes must match"


@supported_html
@pytest.mark.parametrize("kind", ["button", "submit", "reset"])
def test_input_button_variants_use_supplied_value_as_label(tmp_path, kind):
    # GIVEN an input button with a supplied value and an explicit source type
    path, out = tmp_path / "sample.html", tmp_path / "button.md"
    path.write_bytes(('<input type="%s" value="Go">' % kind).encode("utf-8"))
    assert out.exists() is False, "button output must start absent"
    content = ('Button: label="Go"; aria_label=unspecified; title=unspecified; role=unspecified; '
               'type="%s"; id=unspecified; name=unspecified; value="Go"; declared_disabled=false; '
               'aria_disabled=unspecified; fieldset_disabled=false; formaction=unspecified; '
               'formmethod=unspecified; formenctype=unspecified; formtarget=unspecified; '
               'formnovalidate=false\n') % kind
    expected = expected_document(path, content, 1)
    # WHEN the static renderer records the input button declaration
    rc, receipt, markdown = service.run(path, out)
    # THEN supplied type and value determine the record without duplicate visible text
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "button, submit and reset inputs must preserve their source type and visible value")
    assert out.read_bytes() == expected.encode("utf-8"), "input button bytes must match the API"


def input_limit_source(size):
    """Pad an excluded comment to an exact raw byte size without enlarging output."""
    frame = b'<p>Kept</p><!--' + b'-->'
    return b'<p>Kept</p><!--' + b'x' * (size - len(frame)) + b'-->'


def depth_limit_source(depth):
    """Count recovered body as depth one and the included paragraph as the leaf."""
    return ("<div>" * (depth - 2) + "<p>Kept</p>" + "</div>" * (depth - 2)).encode("ascii")


def element_limit_source(count):
    """Include html/head/body, a hidden container, hidden spans and one paragraph."""
    return ('<div hidden>' + '<span></span>' * (count - 5) + '</div><p>Kept</p>').encode("ascii")


def attribute_limit_source(size):
    """Use decoded UTF-8 attribute bytes in an excluded subtree."""
    value = "é" * (size // 2) + "x" * (size % 2)
    return ('<div hidden data-value="' + value + '"></div><p>Kept</p>').encode("utf-8")


def table_limit_source(cells):
    """Amplify one origin cell into a wide grid while keeping input tiny."""
    return ('<table><tr><td colspan="%d">X</td></tr></table>' % cells).encode("ascii")


def expanded_table_content(cells):
    """Specify the entire expected grid independently of adapter expansion."""
    return ('| ' + ' | '.join(['X'] + [''] * (cells - 1)) + ' |\n| '
            + ' | '.join(['---'] * cells) + ' |\n')


ACTUAL_LIMITS = [
    pytest.param(input_limit_source, 8 * 1048576, "Kept\n", 1, 0, id="raw-input-8-mib"),
    pytest.param(depth_limit_source, 256, "Kept\n", 1, 0, id="recovered-depth-256"),
    pytest.param(element_limit_source, 200000, "Kept\n", 1, 0, id="all-elements-200000"),
    pytest.param(attribute_limit_source, 1048576, "Kept\n", 1, 0, id="decoded-attribute-1-mib"),
]


@supported_html
@pytest.mark.parametrize(("build", "limit", "content", "regions", "tables"), ACTUAL_LIMITS)
@pytest.mark.parametrize("delta", [-1, 0], ids=["below", "at"])
def test_real_resource_limits_accept_values_below_and_at_the_boundary(
    tmp_path, build, limit, content, regions, tables, delta,
):
    # GIVEN a synthetic document at a practical real default resource boundary
    path, out = tmp_path / "sample.html", tmp_path / "limited.md"
    source = build(limit + delta)
    path.write_bytes(source)
    assert path.read_bytes() == source, "the boundary source must reach the parser unchanged"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN HTML enforces the declared inclusive default limit
    rc, receipt, markdown = service.run(path, out)
    # THEN the complete output remains exact at and below the limit
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "inclusive real resource boundaries must retain valid HTML and exact output"
    assert out.read_bytes() == expected.encode("utf-8"), "accepted boundary bytes must match the API"


def assert_resource_refusal(path, out, result):
    """Check a whole failure envelope and untouched destination without accepting parser errors."""
    rc, receipt, markdown = result
    assert (rc, receipt, markdown, out.read_bytes()) == (1, {
        "file_ok": False, "route": "html", "reason": receipt["reason"], "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": HTML_OMISSIONS,
    }, "", b"keep destination\n"), "resource refusal must leave zero counts, empty Markdown and intact output"
    assert re.search(r"(?i)(limit|exceed|too (?:large|deep|many))", receipt["reason"]) is not None, (
        "resource refusal must identify a limit rather than an incidental parser exception")


@supported_html
@pytest.mark.parametrize("build,limit", [
    pytest.param(input_limit_source, 8 * 1048576, id="raw-input"),
    pytest.param(depth_limit_source, 256, id="recovered-depth"),
    pytest.param(element_limit_source, 200000, id="excluded-elements-count"),
    pytest.param(attribute_limit_source, 1048576, id="excluded-attribute-count"),
    pytest.param(table_limit_source, 100000, id="span-amplification"),
])
def test_real_resource_limits_refuse_one_over_atomically(tmp_path, build, limit):
    # GIVEN one over the real resource limit and an existing destination
    path, out = tmp_path / "sample.html", tmp_path / "limited.md"
    path.write_bytes(build(limit + 1))
    out.write_bytes(b"keep destination\n")
    assert out.read_bytes() == b"keep destination\n", "the refusal sentinel must exist before rendering"
    # WHEN the resource boundary rejects conversion before output staging
    result = service.run(path, out)
    # THEN the registered HTML failure envelope is atomic and explicitly identifies the limit
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("cells", [99999, 100000], ids=["below", "at"])
def test_real_table_span_limit_accepts_exact_expanded_grid(tmp_path, cells):
    # GIVEN one origin spanning a complete near-limit rectangular grid
    path, out = tmp_path / "sample.html", tmp_path / "table.md"
    path.write_bytes(table_limit_source(cells))
    assert len(path.read_bytes()) < 100, "span amplification must start from a tiny source"
    expected = expected_document(path, expanded_table_content(cells), 0, tables=1)
    # WHEN the real 100000 expanded-cell limit is enforced inclusively
    rc, receipt, markdown = service.run(path, out)
    # THEN every continuation remains empty and the full emitted table is deterministic
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "the actual table boundary must retain the whole span grid without truncation")
    assert out.read_bytes() == expected.encode("utf-8"), "large grid file and API bytes must agree"


@supported_html
@pytest.mark.parametrize("delta", [-1, 0], ids=["below", "at"])
def test_scaled_visible_text_limit_counts_utf8_before_whitespace_reduction(tmp_path, monkeypatch, delta):
    # GIVEN 63 or 64 decoded UTF-8 bytes, including whitespace that rendering reduces
    monkeypatch.setattr(htmltext, "_VISIBLE_TEXT_BYTE_LIMIT", 64, raising=False)
    text = "é" * 16 + " " * (32 + delta)
    assert len(text.encode("utf-8")) == 64 + delta, "the scaled limit measures decoded UTF-8 bytes"
    path, out = tmp_path / "sample.html", tmp_path / "visible.md"
    path.write_bytes(("<p>" + text + "</p>").encode("utf-8"))
    expected = expected_document(path, "é" * 16 + "\n", 1)
    # WHEN included source text reaches the scaled inclusive limit before normalization
    rc, receipt, markdown = service.run(path, out)
    # THEN normalizing whitespace does not invalidate acceptable source byte totals
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "scaled visible-text boundaries must use decoded bytes before whitespace reduction")


@supported_html
def test_scaled_visible_text_limit_refuses_one_over_before_reduction(tmp_path, monkeypatch):
    # GIVEN 65 included text bytes whose reduced rendered text would fit the scaled limit
    monkeypatch.setattr(htmltext, "_VISIBLE_TEXT_BYTE_LIMIT", 64, raising=False)
    text = "é" * 16 + " " * 33
    assert len(text.encode("utf-8")) == 65, "the source must exceed 64 decoded UTF-8 bytes exactly once"
    path, out = tmp_path / "sample.html", tmp_path / "visible.md"
    path.write_bytes(("<p>" + text + "</p>").encode("utf-8"))
    out.write_bytes(b"keep destination\n")
    # WHEN accounting precedes whitespace normalization and output assembly
    result = service.run(path, out)
    # THEN the oversized source text is refused even though its reduced text is short
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("slack", [1, 0], ids=["below", "at"])
def test_scaled_markdown_limit_counts_complete_utf8_envelope(tmp_path, monkeypatch, slack):
    # GIVEN an independently calculated complete UTF-8 document byte length
    path, out = tmp_path / "sample.html", tmp_path / "markdown.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    limit = len(expected.encode("utf-8")) + slack
    monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", limit, raising=False)
    assert len(expected.encode("utf-8")) == limit - slack, "the scaled bound must include the whole envelope"
    # WHEN final Markdown is checked at or below its exact complete UTF-8 byte limit
    rc, receipt, markdown = service.run(path, out)
    # THEN output includes metadata and all framing bytes without false refusal
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "the Markdown limit must use the complete UTF-8 envelope rather than characters or body alone")


@supported_html
def test_scaled_markdown_limit_refuses_one_over_complete_envelope(tmp_path, monkeypatch):
    # GIVEN a complete UTF-8 Markdown document one byte over its independently chosen limit
    path, out = tmp_path / "sample.html", tmp_path / "markdown.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    monkeypatch.setattr(htmltext, "_MARKDOWN_BYTE_LIMIT", len(expected.encode("utf-8")) - 1, raising=False)
    out.write_bytes(b"keep destination\n")
    assert len(expected) < len(expected.encode("utf-8")), "Unicode must distinguish characters from bytes"
    # WHEN final byte accounting includes both body and schema envelope
    result = service.run(path, out)
    # THEN the oversized complete document is refused before destination publication
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("nested_cells", [2, 3], ids=["below-total", "at-total"])
def test_scaled_nested_span_budget_sums_parent_and_nested_grids(tmp_path, monkeypatch, nested_cells):
    # GIVEN three parent positions plus two or three nested span-expanded positions
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 6, raising=False)
    path, out = tmp_path / "sample.html", tmp_path / "nested.md"
    path.write_bytes(('<table><tr><td colspan="3"><table><tr><td colspan="%d">X</td>'
                      '</tr></table></td></tr></table>' % nested_cells).encode("ascii"))
    assert 3 + nested_cells in (5, 6), "the nested total must be exactly below or at six positions"
    content = ('| Nested table: row=1; column=1; rowspan=1; colspan=%d; text="X" |  |  |\n'
               '| --- | --- | --- |\n') % nested_cells
    expected = expected_document(path, content, 0, tables=1)
    # WHEN a single global expanded-position budget accounts for both grids
    rc, receipt, markdown = service.run(path, out)
    # THEN nested data stays in its cell while the aggregate inclusive limit remains valid
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "parent and nested grids must share the same expanded-cell budget")


@supported_html
def test_scaled_nested_span_budget_refuses_aggregate_one_over(tmp_path, monkeypatch):
    # GIVEN individually small grids whose combined expanded total is seven
    monkeypatch.setattr(htmltext, "_TABLE_CELL_LIMIT", 6, raising=False)
    path, out = tmp_path / "sample.html", tmp_path / "nested.md"
    path.write_bytes(b'<table><tr><td colspan="3"><table><tr><td colspan="4">X</td></tr></table></td></tr></table>')
    out.write_bytes(b"keep destination\n")
    assert 3 + 4 == 7, "the aggregate must exceed the six-position bound by one"
    # WHEN the table plan checks aggregate amplification before allocating grids
    result = service.run(path, out)
    # THEN individual per-table checks cannot admit the oversized nested total
    assert_resource_refusal(path, out, result)


@supported_html
def test_deep_malformed_html_refuses_without_recursive_parser_traceback(tmp_path):
    # GIVEN unclosed elements whose recovered depth greatly exceeds 256
    path, out = tmp_path / "sample.html", tmp_path / "deep.md"
    path.write_bytes(b"<div>" * 2048 + b"Kept")
    out.write_bytes(b"keep destination\n")
    assert path.stat().st_size < 8 * 1048576, "depth refusal must not depend on raw input size"
    # WHEN malformed recovery produces a deeply nested body
    result = service.run(path, out)
    # THEN an explicit resource refusal reaches the normal atomic failure contract
    assert_resource_refusal(path, out, result)


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><strong></strong></p><span><em></em></span><h1></h1><p>Kept</p>',
                 'Kept\n', 1, 0, id="empty-markup-does-not-invent-text"),
    pytest.param('<svg><title>Name</title> <desc>Description</desc> <text>東京</text></svg>'
                 '<canvas><p>Canvas fallback</p></canvas><noscript><p>No script</p></noscript>'
                 '<custom><p>Unknown child</p></custom>',
                 'Name Description 東京\n\nCanvas fallback\n\nNo script\n\nUnknown child\n',
                 4, 0, id="svg-canvas-noscript-and-unknown-text"),
    pytest.param('<p><a href="https://example.test/a_(b)?x=1&amp;y=2">Safe</a> '
                 '<a href="\tjava&#x0a;script:go">Unsafe</a></p>',
                 '[Safe](https://example.test/a_%28b%29?x=1&y=2) Unsafe\n',
                 1, 0, id="url-parentheses-and-obfuscated-scheme"),
    pytest.param('<img src="a (b)" alt="&lt;x&gt;" title="café &amp; 東京">',
                 'Image: alt="&lt;x&gt;"; title="café &amp; 東京"; source=relative; target="a \\(b\\)"\n',
                 1, 0, id="image-source-remains-inert-with-spaces-and-parentheses"),
    pytest.param('<table><tr><td><table></table></td><td>Peer</td></tr></table>',
                 '|  | Peer |\n| --- | --- |\n', 0, 1, id="empty-nested-table-has-no-record"),
    pytest.param('<table><tr><td><table><tr><td><code>&lt;x&gt; &amp; 東京 "quoted"\n\t  end</code>'
                 '</td></tr></table></td></tr></table>',
                 '| Nested table: row=1; column=1; rowspan=1; colspan=1; '
                 'text="Code: text=&quot;&lt;x&gt; &amp; 東京 &quot;quoted&quot;&#10;&#9;  end&quot;" |\n'
                 '| --- |\n', 0, 1, id="nested-code-label-quotes-stay-unambiguous"),
])
def test_security_and_fallback_edges_preserve_exact_inert_data(tmp_path, source, content, regions, tables):
    # GIVEN source syntax and fallback edges with independent safe output expectations
    path, out = tmp_path / "sample.html", tmp_path / "safe.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "security output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN rendering preserves source data without activating syntax or inventing empty regions
    rc, receipt, markdown = service.run(path, out)
    # THEN the complete document retains only approved syntax, data and counters
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "safety edges must preserve useful inert source data and exact accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "safe file and API output bytes must agree"


@supported_html
@pytest.mark.parametrize("kind", ["PASSWORD", "FILE"])
def test_uppercase_sensitive_input_types_still_redact_supplied_values(tmp_path, kind):
    # GIVEN an ASCII-case-insensitive sensitive input type with a supplied secret
    path, out = tmp_path / "sample.html", tmp_path / "redacted.md"
    path.write_bytes(('<input type="%s" value="secret">' % kind).encode("ascii"))
    assert out.exists() is False, "sensitive output must start absent"
    content = INPUT_ABSENT.replace('type=unspecified', 'type="%s"' % kind).replace(
        'value=unspecified', 'value="redacted"') + '\n'
    expected = expected_document(path, content, 1)
    # WHEN source type spelling is preserved while its HTML type semantics are classified
    rc, receipt, markdown = service.run(path, out)
    # THEN source spelling remains declared metadata and supplied secrets never appear in content
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "uppercase password/file types must receive the same explicit redaction policy")


@supported_html
@pytest.mark.parametrize("tail", [b'<meta http-equiv="content-type" content="text/html; charset">',
                                      b'<meta http-equiv="content-type" content="text/html; charset=">'])
def test_malformed_http_charset_refuses_atomically(tmp_path, tail):
    # GIVEN a content-type declaration with a present but malformed charset parameter
    path, out = tmp_path / "sample.html", tmp_path / "charset.md"
    path.write_bytes(tail + b'<p>Kept</p>')
    out.write_bytes(b"keep destination\n")
    assert out.read_bytes() == b"keep destination\n", "the charset sentinel must exist first"
    # WHEN charset preflight sees the malformed declaration inside its inspected window
    rc, receipt, markdown = service.run(path, out)
    # THEN the existing HTML failure envelope preserves zero counts and the destination
    assert (rc, receipt["file_ok"], receipt["route"], receipt["units"], markdown, out.read_bytes()) == (
        1, False, "html", 0, "", b"keep destination\n",
    ), "malformed HTTP charset declarations must refuse before output publication"
    assert re.search(r"(?i)(charset|encoding)", receipt["reason"]) is not None, (
        "malformed charset refusal must explain the declaration failure")


@supported_html
def test_charset_preflight_includes_the_last_byte_of_its_window(tmp_path):
    # GIVEN a non-UTF-8 declaration whose closing bracket is byte 1024
    path, out = tmp_path / "sample.html", tmp_path / "window.md"
    declaration = b'<meta charset="latin1">'
    prefix = b' ' * (1024 - len(declaration)) + declaration
    assert len(prefix) == 1024, "the declaration must end exactly at the inspected window boundary"
    path.write_bytes(prefix + b'<p>Kept</p>')
    out.write_bytes(b"keep destination\n")
    # WHEN preflight inspects the first 1024 bytes inclusively by their byte positions
    rc, receipt, markdown = service.run(path, out)
    # THEN the declaration remains inside the window and forces an atomic refusal
    assert (rc, receipt["route"], receipt["units"], markdown, out.read_bytes()) == (
        1, "html", 0, "", b"keep destination\n",
    ), "the last inspected byte must not hide a non-UTF-8 declaration"


@supported_html
def test_charset_preflight_does_not_extend_past_the_inspected_window(tmp_path):
    # GIVEN a declaration starting at byte offset 1024 and otherwise valid UTF-8 text
    path, out = tmp_path / "sample.html", tmp_path / "window.md"
    prefix = b' ' * 1024
    assert len(prefix) == 1024, "the declaration must start after the inspected window"
    path.write_bytes(prefix + '<meta charset="latin1"><p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    # WHEN the bounded preflight leaves declarations outside its window unexamined
    rc, receipt, markdown = service.run(path, out)
    # THEN strict UTF-8 decoding still preserves exact Unicode and source hashes
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "charset inspection must honor its explicit 1024-byte bound")


RUNTIME_SIMULATION = '''
import contextlib
import importlib.abc
import io
import json
from pathlib import Path
import platform
import sys
import pdfplumber
import python_calamine

mode, directory = sys.argv[1], Path(sys.argv[2])
attempts = []
class ParserBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selectolax" or fullname.startswith("selectolax."):
            attempts.append(fullname)
            if mode == "broken-parser":
                raise OSError("simulated native binding failure")
            raise ModuleNotFoundError("simulated parser import failure")
sys.meta_path.insert(0, ParserBlocker())
if mode == "unsupported-version":
    sys.version_info = (3, 15, 0, "final", 0)
if mode == "unsupported-implementation":
    platform.python_implementation = lambda: "PyPy"
import brewdoc
from brewdoc import service
from brewdoc.selfcheck import synthetic_docx
html, legacy, out = directory / "sample.html", directory / "legacy.docx", directory / "refused.md"
html.write_bytes(b"<p>Kept</p>")
legacy.write_bytes(synthetic_docx())
out.write_bytes(b"keep destination\\n")
rc, line, markdown = brewdoc.run(html, out)
old_rc, old_line, old_markdown = brewdoc.run(legacy)
with contextlib.redirect_stdout(io.StringIO()):
    self_rc = brewdoc.self_check()
print(json.dumps({"suffix_registered": ".html" in service.ROUTES,
    "api_exported": "render_html" in brewdoc.__all__, "native_attempts": len(attempts),
    "html": [rc, line["file_ok"], line["route"], line["unit_kind"], line["units"],
             line["tables"], line["text_regions"], markdown, out.read_bytes() == b"keep destination\\n"],
    "legacy": [old_rc, old_line["route"], old_line["units"], old_line["tables"],
               old_line["text_regions"], old_markdown.isascii()], "self_check": self_rc}))
'''


@supported_html
@pytest.mark.parametrize("mode,attempts,self_rc", [
    pytest.param("missing-parser", 1, 1, id="supported-import-failure-simulation"),
    pytest.param("broken-parser", 1, 1, id="supported-native-failure-simulation"),
    pytest.param("unsupported-version", 0, 0, id="cpython-315-version-simulation"),
    pytest.param("unsupported-implementation", 0, 0, id="pypy-implementation-simulation"),
])
def test_runtime_simulations_keep_static_api_legacy_success_and_expected_html_refusal(
    tmp_path, mode, attempts, self_rc,
):
    # GIVEN a fresh child interpreter with an explicitly labelled runtime/import simulation
    directory = tmp_path / mode
    directory.mkdir()
    assert (directory / "refused.md").exists() is False, "the subprocess must own its fresh output path"
    environment = {**os.environ, "TMPDIR": str(directory), "TEMP": str(directory), "TMP": str(directory)}
    # WHEN native imports or declared HTML runtime prerequisites are simulated unavailable
    completed = subprocess.run([sys.executable, '-c', RUNTIME_SIMULATION, mode, str(directory)],
                               capture_output=True, env=environment, timeout=60)
    # THEN static routes/API and every old self-check remain available with explicit HTML refusal
    assert (completed.returncode, completed.stderr) == (0, b""), "the simulation must finish without import traceback"
    assert json.loads(completed.stdout) == {
        "suffix_registered": True, "api_exported": True, "native_attempts": attempts,
        "html": [1, False, "html", "chapter", 0, 0, 0, "", True],
        "legacy": [0, "doc", 1, 1, 1, True], "self_check": self_rc,
    }, "runtime simulations must distinguish unsupported capability from a broken supported installation"


@supported_html
def test_ascii_configured_cli_stdout_matches_api_and_utf8_file_bytes(tmp_path):
    # GIVEN a Unicode HTML source and the independent complete UTF-8 Markdown expectation
    path, out = tmp_path / "sample.html", tmp_path / "cli.md"
    path.write_bytes('<p>café 東京</p>'.encode("utf-8"))
    expected = expected_document(path, "café 東京\n", 1)
    assert out.exists() is False, "the byte-equivalence file must start absent"
    api_rc, api_line, api_markdown = service.run(path, out)
    stdout_line = {**expected_receipt(path, out, 1), "out": None}
    environment = {**os.environ, "PYTHONIOENCODING": "ascii", "TMPDIR": str(tmp_path)}
    # WHEN the CLI must emit HTML through an ASCII-configured Python stream
    completed = subprocess.run([sys.executable, '-m', 'brewdoc.cli', str(path)],
                               capture_output=True, env=environment, timeout=30)
    # THEN JSON remains ASCII and Markdown bytes equal API/file UTF-8 without a traceback or BOM
    expected_stdout = json.dumps(stdout_line, ensure_ascii=True, sort_keys=True).encode("ascii")
    assert (api_rc, api_line, api_markdown, out.read_bytes(), completed.returncode,
            completed.stdout, completed.stderr) == (
        0, expected_receipt(path, out, 1), expected, expected.encode("utf-8"), 0,
        expected_stdout + os.linesep.encode("ascii") + expected.encode("utf-8"), b"",
    ), "ASCII-configured CLI output must deliberately preserve exact ordered HTML UTF-8 bytes"


@supported_html
@pytest.mark.parametrize("kind", ["PASSWORD", "FILE"])
def test_sensitive_input_with_button_role_redacts_value_before_choosing_label(tmp_path, kind):
    # GIVEN a sensitive declared input type whose role overlaps the button representation
    path, out = tmp_path / "sample.html", tmp_path / "protected.md"
    protected = "protected" + kind.lower() + "sourcevalue"
    path.write_bytes(('<input type="%s" role="button" aria-label="Choose" value="%s">'
                      % (kind, protected)).encode("ascii"))
    assert out.exists() is False, "protected output must start absent"
    content = ('Button: label="Choose"; aria_label="Choose"; title=unspecified; role="button"; '
               'type="%s"; id=unspecified; name=unspecified; value="redacted"; declared_disabled=false; '
               'aria_disabled=unspecified; fieldset_disabled=false; formaction=unspecified; '
               'formmethod=unspecified; formenctype=unspecified; formtarget=unspecified; '
               'formnovalidate=false\n') % kind
    expected = expected_document(path, content, 1)
    # WHEN role handling chooses a Button record after sensitive-value redaction
    rc, receipt, markdown = service.run(path, out)
    # THEN declared role/type survive while neither the label nor value reveals protected data
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "password/file redaction must precede role-button label and value handling")
    assert out.read_bytes() == expected.encode("utf-8"), "protected file and API bytes must match"
    assert markdown.count(protected) == 0, "protected source values must never appear in rendered content"


@supported_html
@pytest.mark.parametrize("source,content", [
    pytest.param('<table><tr><td>Before<input type="text" name="n" value="café &amp;#10;">'
                 'Between<textarea id="t">first "quoted"\n\t  東京 &amp;#10;</textarea>'
                 'Next<output name="sum">sum\n"done" &amp;</output>After</td><td>Peer</td></tr></table>',
                 '| Before Input: type="text"; id=unspecified; name="n"; value="café &amp;\\#10;"; '
                 'placeholder=unspecified; readonly=false; required=false; declared_disabled=false; '
                 'fieldset_disabled=false; min=unspecified; max=unspecified; step=unspecified; pattern=unspecified; '
                 'minlength=unspecified; maxlength=unspecified; size=unspecified; multiple=false; '
                 'accept=unspecified; autocomplete=unspecified; list=unspecified Between '
                 'Textarea: id="t"; name=unspecified; placeholder=unspecified; readonly=false; required=false; '
                 'declared_disabled=false; fieldset_disabled=false; '
                 'text="first &quot;quoted&quot;&#10;&#9;  東京 &amp;\\#10;" Next '
                 'Output: id=unspecified; name="sum"; for=unspecified; text="sum&#10;&quot;done&quot; &amp;" '
                 'After | Peer |\n| --- | --- |\n',
                 id="ordinary-cell-controls-and-lossless-multiline-records"),
    pytest.param('<table><tr><td>Before<table><tr><td><input type="PASSWORD" role="button" '
                 'aria-label="Choose" value="protectednestedvalue"></td></tr></table>After</td></tr></table>',
                 '| Before Nested table: row=1; column=1; rowspan=1; colspan=1; '
                 'text="Button: label=&quot;Choose&quot;; aria_label=&quot;Choose&quot;; title=unspecified; '
                 'role=&quot;button&quot;; type=&quot;PASSWORD&quot;; id=unspecified; name=unspecified; '
                 'value=&quot;redacted&quot;; declared_disabled=false; aria_disabled=unspecified; '
                 'fieldset_disabled=false; formaction=unspecified; formmethod=unspecified; '
                 'formenctype=unspecified; formtarget=unspecified; formnovalidate=false" After |\n| --- |\n',
                 id="nested-origin-control-quotes-and-role-redaction"),
])
def test_table_cells_preserve_control_records_without_extra_regions(tmp_path, source, content):
    # GIVEN ordinary or nested cells with literal complete control-record expectations
    path, out = tmp_path / "sample.html", tmp_path / "cell-controls.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "table-control output must start absent"
    expected = expected_document(path, content, 0, tables=1)
    # WHEN the table cell emitter preserves controls in their source positions
    rc, receipt, markdown = service.run(path, out)
    # THEN cell records retain data and redaction while only the outer table is counted
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 0, tables=1), expected), (
        "controls inside cells must preserve exact fields, literal data and zero extra text regions")
    assert out.read_bytes() == expected.encode("utf-8"), "table-control file and API bytes must match"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><em>Before<code>&lt;x&gt; &amp; café</code>After</em></p>',
                 '*Before*\n\n```\n<x> & café\n```\n\n*After*\n', 3, 0,
                 id="emphasis-balanced-around-fence"),
    pytest.param('<p><a href="/safe"><strong>Before<em>Inner<code>literal</code>'
                 'After</em>Tail</strong></a></p>',
                 '[**Before*Inner***](/safe)\n\n```\nliteral\n```\n\n[***After*Tail**](/safe)\n',
                 3, 0, id="nested-strong-emphasis-link-segments-balanced"),
    pytest.param('<a href="a_(b)"><code>café &amp; &lt;x&gt;</code></a>',
                 '```\ncafé & <x>\n```\n\n[Link target](a_%28b%29)\n',
                 2, 0, id="code-only-safe-link-target-once"),
    pytest.param('<em><code>literal</code></em>', '```\nliteral\n```\n',
                 1, 0, id="code-only-emphasis-no-phantom-delimiters"),
    pytest.param('<ul><li><p>First</p><p>Second</p></li></ul>',
                 '- First\n\n  Second\n', 2, 0, id="plain-list-paragraph-continuation-count"),
    pytest.param('<ul><li>Outer<ul><li>A</li><li>B</li></ul>Tail</li></ul>',
                 '- Outer\n    - A\n    - B\n\n  Tail\n',
                 4, 0, id="plain-parent-tail-keeps-original-marker-once"),
    pytest.param('<ul><li><code>literal</code></li></ul>',
                 'List item (level 1, marker "-"):\n\n```\nliteral\n```\n\nEnd list item (level 1).\n',
                 1, 0, id="code-only-list-item-explicit-scope"),
    pytest.param('<ul><li>Outer<ul><li><code>literal</code></li><li>Plain</li></ul>'
                 '<p>Tail</p></li></ul>',
                 'List item (level 1, marker "-"):\n\nOuter\n\n'
                 'List item (level 2, marker "-"):\n\n```\nliteral\n```\n\n'
                 'End list item (level 2).\n\n- Plain\n\nTail\n\nEnd list item (level 1).\n',
                 4, 0, id="affected-ancestor-and-relative-plain-child-indent"),
    pytest.param('<ol start="3"><li>Before<code>literal</code>After</li></ol>',
                 'List item (level 1, marker "3."):\n\nBefore\n\n```\nliteral\n```\n\n'
                 'After\n\nEnd list item (level 1).\n',
                 3, 0, id="ordered-complex-item-preserves-marker"),
    pytest.param('<ul><li>Before<table><caption>Data</caption><tr><td>A</td></tr></table>'
                 'After</li></ul>',
                 'List item (level 1, marker "-"):\n\nBefore\n\nCaption: Data\n\n'
                 '| A |\n| --- |\n\nAfter\n\nEnd list item (level 1).\n',
                 3, 1, id="table-caption-remain-in-item-scope"),
    pytest.param('<ul><li>Before<input value="x">After</li></ul>',
                 'List item (level 1, marker "-"):\n\nBefore\n\n'
                 + INPUT_ABSENT.replace('value=unspecified', 'value="x"')
                 + '\n\nAfter\n\nEnd list item (level 1).\n',
                 3, 0, id="control-record-remains-in-item-scope"),
    pytest.param('<ul><li><img src="pic" alt="Icon"></li></ul>',
                 'List item (level 1, marker "-"):\n\n'
                 'Image: alt="Icon"; title="none"; source=relative; target="pic"\n\n'
                 'End list item (level 1).\n', 1, 0, id="image-record-remains-in-item-scope"),
    pytest.param('<ul><li><blockquote><p>Quoted</p></blockquote></li></ul>',
                 'List item (level 1, marker "-"):\n\nQuote (level 1):\n\nQuoted\n\n'
                 'End quote (level 1).\n\nEnd list item (level 1).\n',
                 1, 0, id="quote-boundaries-remain-in-item-scope"),
    pytest.param('<a href="/safe"></a><p>Kept</p>', 'Kept\n',
                 1, 0, id="empty-safe-anchor-no-generated-region"),
])
def test_inline_fences_and_list_children_keep_balanced_semantic_scopes(
    tmp_path, source, content, regions, tables,
):
    # GIVEN confirmed inline/list boundary cases and independently specified complete output
    path, out = tmp_path / "sample.html", tmp_path / "inline-list.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "inline/list output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN standalone child blocks and inline-code fallbacks preserve their semantic contexts
    rc, receipt, markdown = service.run(path, out)
    # THEN generated delimiters balance, list identity survives and every semantic block counts once
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "inline/list boundaries must retain source order, containment and exact emitted-block accounting"
    assert out.read_bytes() == expected.encode("utf-8"), "inline/list file and API bytes must match"


@supported_html
@pytest.mark.parametrize("source", [
    pytest.param('<!-- <meta charset="iso-8859-1"> --><p>café</p>', id="comment-example"),
    pytest.param('<script>const example = \'<meta charset="iso-8859-1">\';</script><p>café</p>',
                 id="script-raw-text-example"),
    pytest.param('<div title=\'<meta charset="iso-8859-1">\'><p>café</p></div>',
                 id="quoted-attribute-example"),
])
def test_charset_examples_in_inert_lexical_contexts_do_not_declare_an_encoding(tmp_path, source):
    # GIVEN valid UTF-8 whose apparent meta tag is inert source data inside the preflight window
    path, out = tmp_path / "sample.html", tmp_path / "charset-context.md"
    path.write_bytes(source.encode("utf-8"))
    assert path.stat().st_size < 1024, "the apparent declaration must be inside the inspected byte window"
    expected = expected_document(path, "café\n", 1)
    # WHEN bounded preflight recognizes actual HTML tags rather than source examples
    rc, receipt, markdown = service.run(path, out)
    # THEN no false encoding refusal suppresses the included Unicode paragraph
    assert (rc, receipt, markdown) == (0, expected_receipt(path, out, 1), expected), (
        "comments, script raw text and quoted attributes must not become encoding declarations")
    assert out.read_bytes() == expected.encode("utf-8"), "valid lexical-context file and API bytes must agree"


@supported_html
def test_actual_non_utf8_charset_declaration_remains_an_atomic_refusal(tmp_path):
    # GIVEN a genuine non-UTF-8 meta start tag inside the inspected window
    path, out = tmp_path / "sample.html", tmp_path / "charset-context.md"
    path.write_bytes('<meta charset="iso-8859-1"><p>café</p>'.encode("utf-8"))
    out.write_bytes(b"keep actual declaration destination\n")
    assert path.stat().st_size < 1024, "the actual declaration must be inside the preflight window"
    # WHEN lexical preflight distinguishes a declaration from an inert source example
    rc, receipt, markdown = service.run(path, out)
    # THEN actual encoding declarations retain the full zero-counter refusal contract
    assert (rc, receipt, markdown, out.read_bytes()) == (1, {
        "file_ok": False, "route": "html", "reason": receipt["reason"], "source": "sample.html",
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0, "dropped": zero_tally()["dropped"],
        "broken_ligature_words": 0, "not_carried": HTML_OMISSIONS,
    }, "", b"keep actual declaration destination\n"), "real non-UTF-8 declarations must still refuse atomically"
    assert re.search(r"(?i)charset", receipt["reason"]) is not None, "refusal must identify the charset declaration"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<p><em> soft </em></p>', '*soft*\n', 1, 0, id="body-em-whitespace"),
    pytest.param('<p><strong> bold </strong></p>', '**bold**\n', 1, 0, id="body-strong-whitespace"),
    pytest.param('<table><tr><td><em> soft </em></td></tr></table>',
                 '| *soft* |\n| --- |\n', 0, 1, id="cell-em-whitespace"),
    pytest.param('<table><tr><td><strong> bold </strong></td></tr></table>',
                 '| **bold** |\n| --- |\n', 0, 1, id="cell-strong-whitespace"),
    pytest.param('<p><em> before <code>x</code> after </em></p>',
                 '*before*\n\n```\nx\n```\n\n*after*\n', 3, 0, id="fence-segment-whitespace"),
    pytest.param('<p>left<em> soft </em>right</p>',
                 'left *soft* right\n', 1, 0, id="body-word-separation-outside-delimiters"),
    pytest.param('<table><tr><td>left<strong> bold </strong>right</td></tr></table>',
                 '| left **bold** right |\n| --- |\n', 0, 1, id="cell-word-separation-outside-delimiters"),
])
def test_emphasis_whitespace_stays_outside_valid_body_cell_and_fence_delimiters(
    tmp_path, source, content, regions, tables,
):
    # GIVEN source emphasis padded with whitespace and exact valid Markdown expectations
    path, out = tmp_path / "sample.html", tmp_path / "emphasis.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "emphasis output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN whitespace normalization preserves word separation and generated emphasis semantics
    rc, receipt, markdown = service.run(path, out)
    # THEN markers surround non-whitespace text independently in each body/cell/fence segment
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "whitespace-padded delimiters must not turn source emphasis into literal markers or list syntax"
    assert out.read_bytes() == expected.encode("utf-8"), "valid emphasis file and API bytes must agree"


NATIVE_INITIALIZATION_SIMULATION = '''
import builtins
import contextlib
import importlib.abc
import io
import json
from pathlib import Path
import sys

error_type, directory = sys.argv[1], Path(sys.argv[2])
class NativeInitializationFailure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "selectolax.lexbor":
            raise getattr(builtins, error_type)("simulated native initialization failure")
sys.meta_path.insert(0, NativeInitializationFailure())
import brewdoc
from brewdoc import service
html = brewdoc.run(directory / "sample.html", directory / "native.md")
legacy = brewdoc.run(directory / "legacy.docx")
with contextlib.redirect_stdout(io.StringIO()):
    self_rc = brewdoc.self_check()
print(json.dumps({"suffix_registered": ".html" in service.ROUTES,
                  "api_exported": "render_html" in brewdoc.__all__,
                  "html": html, "legacy": legacy, "self_check": self_rc}))
'''


@supported_html
@pytest.mark.parametrize("error_type", ["SystemError", "OSError"])
def test_native_initialization_failures_preserve_package_legacy_and_html_refusal(tmp_path, error_type):
    # GIVEN an isolated native-import simulation and independent legacy/refusal expectations
    path, out = tmp_path / "sample.html", tmp_path / "native.md"
    path.write_bytes(b'<p>Kept</p>')
    out.write_bytes(b"keep native destination\n")
    old_path = docx(tmp_path / "legacy.docx", '<w:p><w:r><w:t>Old text</w:t></w:r></w:p>')
    old_omissions = ["images, charts and the text drawn inside them",
                    "tracked changes, comments, footnotes, headers and footers",
                    "a table's own formatting - only its cells, row by row"]
    old_body = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Body"\n\nOld text\n'
    old_markdown = schema_two(old_path, old_body, **NOT_APPLICABLE, name="legacy\\.docx",
                              suffix=".docx", route="doc", unit="chapter", count=1,
                              keys="chapter/000001", pages=0, sheets=0, chapters=1, tables=0,
                              text_regions=1, omissions="- images, charts and the text drawn inside them\n"
                              "- tracked changes, comments, footnotes, headers and footers\n"
                              "- a table's own formatting \\- only its cells, row by row",
                              contents='- [Chapter 1: "Body"](#brewdoc-chapter-000001)')
    assert out.read_bytes() == b"keep native destination\n", "the native-failure sentinel must start intact"
    environment = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    # WHEN selectolax.lexbor initialization raises a real loader exception class in the subprocess
    completed = subprocess.run([sys.executable, '-c', NATIVE_INITIALIZATION_SIMULATION, error_type, str(tmp_path)],
                               capture_output=True, env=environment, timeout=60)
    # THEN package imports, complete legacy output and supported-parser refusal remain available
    assert (completed.returncode, completed.stderr) == (0, b""), "native initialization must not break package import"
    assert (json.loads(completed.stdout), out.read_bytes()) == ({
        "suffix_registered": True, "api_exported": True, "self_check": 1,
        "html": [1, {
            "file_ok": False, "route": "html",
            "reason": "HTML parser unavailable: selectolax 0.4.13: simulated native initialization failure",
            "source": "sample.html", "out": None, "receipt_schema": "brewdoc.receipt/1",
            "unit_kind": "chapter", "units": 0, "tables": 0, "text_regions": 0,
            "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
            "not_carried": HTML_OMISSIONS,
        }, ""],
        "legacy": [0, {
            "file_ok": True, "route": "doc",
            "reason": "doc rendered: 1 chapters, 0 tables, 1 text regions, 0 column splits",
            "source": "legacy.docx", "out": None, "receipt_schema": "brewdoc.receipt/1",
            "unit_kind": "chapter", "units": 1, "tables": 0, "text_regions": 1,
            "columns_split": 0, "dropped": zero_tally()["dropped"], "broken_ligature_words": 0,
            "not_carried": old_omissions, "artifacts": [], "markdown_schema": "brewdoc.markdown/2",
            "unit_keys": ["chapter/000001"],
        }, old_markdown],
    }, b"keep native destination\n"), "native loader failures must retain exact old-route bytes and HTML atomic refusal"


@supported_html
@pytest.mark.parametrize("source,content,regions,tables", [
    pytest.param('<code><a href="../mod/core.html#traceenable">TraceEnable</a></code>',
                 '```\nTraceEnable\n```\n\n'
                 '[Link target](../mod/core.html#traceenable); text="TraceEnable"\n',
                 2, 0, id="body-code-descendant-anchor"),
    pytest.param('<pre><a href="/multi">café "quoted"\n\t  東京 &amp;#10;</a></pre>',
                 '```\ncafé "quoted"\n\t  東京 &#10;\n```\n\n'
                 '[Link target](/multi); text="café &quot;quoted&quot;&#10;&#9;  東京 &amp;\\#10;"\n',
                 2, 0, id="body-pre-lossless-descendant-label"),
    pytest.param('<code><a href="/same">First</a> / <a href="/same">Second</a></code>',
                 '```\nFirst / Second\n```\n\n[Link target](/same); text="First"\n\n'
                 '[Link target](/same); text="Second"\n',
                 3, 0, id="repeated-url-anchors-remain-distinct"),
    pytest.param('<a href="./"><img src="back" alt="Back"></a>'
                 '<a href="#page-header"><img src="top" alt="Top"></a>',
                 'Image: alt="Back"; title="none"; source=relative; target="back"\n\n'
                 '[Link target](./)\n\nImage: alt="Top"; title="none"; source=relative; target="top"\n\n'
                 '[Link target](#page-header)\n',
                 4, 0, id="image-only-apache-relative-and-fragment-targets"),
    pytest.param('<a href="/wrap"><code>literal</code></a>',
                 '```\nliteral\n```\n\n[Link target](/wrap)\n',
                 2, 0, id="existing-enclosing-code-target-once"),
    pytest.param('<code><a hidden href="/hidden">Hidden</a><span hidden>'
                 '<a href="/ancestor">Lost</a></span><a href="javascript:go">Unsafe</a>'
                 '<a>Missing</a><a href="/empty"></a></code>',
                 '```\nUnsafeMissing\n```\n', 1, 0,
                 id="excluded-unsafe-missing-empty-descendants-no-record"),
    pytest.param('<a href="/none"><img hidden src="pic"></a><p>Kept</p>',
                 'Kept\n', 1, 0, id="excluded-enclosing-image-no-fabricated-target"),
    pytest.param('<table><tr><td>Before <code><a href="/same">café</a> / '
                 '<a href="/same">東京</a></code> After</td></tr></table>',
                 '| Before Code: text="café / 東京" [Link target](/same); text="café" '
                 '[Link target](/same); text="東京" After |\n| --- |\n',
                 0, 1, id="cell-descendant-records-zero-regions"),
    pytest.param('<table><tr><td><a href="/image"><img src="pic" alt="Icon"></a> ; '
                 '<a href="/code"><code>x</code></a></td></tr></table>',
                 '| [ Image: alt="Icon"; title="none"; source=relative; target="pic" ](/image) ; '
                 '[Code: text="x"](/code) |\n| --- |\n',
                 0, 1, id="existing-cell-enclosing-image-code-no-duplicates"),
    pytest.param('<pre>Before<code><a href="/nested">Linked</a></code>After</pre>',
                 '```\nBeforeLinkedAfter\n```\n\n[Link target](/nested); text="Linked"\n',
                 2, 0, id="nested-consumed-code-pre-collect-once"),
    pytest.param('<pre><a href="/first">One<a href="/second">Two</a></pre>',
                 '```\nOneTwo\n```\n\n[Link target](/first); text="One"\n\n'
                 '[Link target](/second); text="Two"\n',
                 3, 0, id="recovered-malformed-anchors-once-in-dom-order"),
    pytest.param('<code><a href="/whitespace"> \n\t </a></code>',
                 '```\n \n\t \n```\n\n[Link target](/whitespace); text=" &#10;&#9; "\n',
                 2, 0, id="retained-literal-whitespace-is-not-an-empty-label"),
])
def test_consumed_literal_and_image_anchors_preserve_each_safe_target_once(
    tmp_path, source, content, regions, tables,
):
    # GIVEN consumed literal/image content with exact included-anchor records and control cases
    path, out = tmp_path / "sample.html", tmp_path / "targets.md"
    path.write_bytes(source.encode("utf-8"))
    assert out.exists() is False, "link-target output must start absent"
    expected = expected_document(path, content, regions, tables=tables)
    # WHEN body or cell rendering preserves targets without modifying consumed literal text
    rc, receipt, markdown = service.run(path, out)
    # THEN every included source anchor retains its own destination, label, position and count
    assert (rc, receipt, markdown) == (
        0, expected_receipt(path, out, regions, tables=tables), expected,
    ), "consumed content must preserve each safe anchor occurrence without fabricated or duplicate records"
    assert out.read_bytes() == expected.encode("utf-8"), "target-preserving file and API bytes must agree"
