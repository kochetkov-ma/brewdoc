"""Offline EML bodies, source metadata and safe public output."""

import hashlib
import io
import json
import os
import re
import socket
import sys
import urllib.request
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pytest

from brewdoc import run
from brewdoc.cli import main
from brewdoc import htmltext
from brewdoc import eml


OMISSIONS = [
    "unlisted headers, including Bcc, routing and signature fields",
    "attachment payloads and nested message bodies",
    "unselected body alternatives and non-body inline resources",
]
ZERO_DROPS = {
    "control_chars": 0, "soft_hyphens": 0, "nbsp": 0, "pua_glyphs": 0,
    "cid_survivors": 0, "ligatures": 0, "running_heads": 0,
    "page_numbers": 0, "non_ascii_replaced": 0,
}


def _receipt(path, regions, out=None):
    """Pin the complete successful EML receipt, without retrievable attachments."""
    return {
        "file_ok": True, "route": "eml", "source": path.name, "out": out,
        "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter", "units": 1,
        "tables": 0, "text_regions": regions, "columns_split": 0,
        "broken_ligature_words": 0, "dropped": ZERO_DROPS.copy(),
        "not_carried": OMISSIONS.copy(), "artifacts": [],
        "unit_keys": ["chapter/000001"], "markdown_schema": "brewdoc.markdown/2",
        "reason": f"eml rendered: 1 chapters, 0 tables, {regions} text regions, 0 column splits",
    }


def _refusal(path, reason):
    """Pin failure fields independently of the selected MIME defect."""
    return {
        "file_ok": False, "route": "eml", "reason": reason, "source": path.name,
        "out": None, "receipt_schema": "brewdoc.receipt/1", "unit_kind": "chapter",
        "units": 0, "tables": 0, "text_regions": 0, "columns_split": 0,
        "broken_ligature_words": 0, "dropped": ZERO_DROPS.copy(),
        "not_carried": OMISSIONS.copy(),
    }


def _markdown(path, content, regions, label="Untitled"):
    """Build the public envelope from literal expected content, never adapter helpers."""
    name = path.name
    body = '<a id="brewdoc-chapter-000001"></a>\n' + f'## Chapter 1: "{label}"\n'
    body += content
    rows = [
        ("Markdown schema", "brewdoc.markdown/2"), ("Source name", f'"{name}"'),
        ("Source suffix", ".eml"), ("Source bytes", str(len(path.read_bytes()))),
        ("Source SHA-256", hashlib.sha256(path.read_bytes()).hexdigest()),
        ("Route", "eml"), ("Unit kind", "chapter"), ("Source unit count", "1"),
        ("Rendered unit count", "1"), ("Ordered selection keys", "chapter/000001"),
        ("Rendered body bytes", str(len(body.encode("utf-8")))),
        ("Rendered body SHA-256", hashlib.sha256(body.encode("utf-8")).hexdigest()),
        ("Pages", "0"), ("Sheets", "0"), ("Chapters", "1"), ("Tables", "0"),
        ("Text regions", str(regions)), ("Column splits", "0"), ("Broken ligature words", "0"),
        *(("Dropped " + key.replace("_", " "), "0") for key in ZERO_DROPS),
        ("Formula capability", "not applicable"), ("Selected formula count", "not applicable"),
        ("VBA project capability", "not applicable"), ("VBA project count", "not applicable"),
        ("VBA source module capability", "not applicable"),
        ("VBA source module count", "not applicable"),
    ]
    metadata = "\n".join(f"| {key} | {value} |" for key, value in rows)
    omissions = "\n".join("- " + item for item in OMISSIONS)
    return (
        f'# "{name}"\n\n<a id="brewdoc-metadata"></a>\n## Metadata\n\n'
        '| Field | Value |\n| --- | --- |\n' + metadata
        + '\n\n## Artifacts\n\nNone.\n\n## Known omissions\n\n' + omissions
        + '\n\n<a id="brewdoc-contents"></a>\n## Contents\n\n'
        + f'- [Chapter 1: "{label}"](#brewdoc-chapter-000001)\n\n' + body
    )


def _multipart(subtype, parts, parameters=b""):
    """Make explicit source-order MIME parts with a deterministic boundary."""
    return (b'MIME-Version: 1.0\nContent-Type: multipart/' + subtype
            + b'; boundary="outer"' + parameters + b'\n\n--outer\n'
            + b'\n--outer\n'.join(parts) + b'\n--outer--\n')


PLAIN = b"Content-Type: text/plain; charset=utf-8\n\nplain first"
HTML = b"Content-Type: text/html; charset=utf-8\n\n<p>HTML alternative</p>"
BODY_CASES = [
    pytest.param(_multipart(b"alternative", [HTML, PLAIN]), b"plain first", "text/plain",
                 "\n```\nplain first\n```\n", 1, id="html-first-plain-wins"),
    pytest.param(_multipart(b"alternative", [PLAIN, b"Content-Type: text/plain\n\nsecond"]),
                 b"plain first", "text/plain", "\n```\nplain first\n```\n", 1,
                 id="first-plain-wins"),
    pytest.param(_multipart(b"alternative", [HTML, b"Content-Type: text/plain\n\n"]),
                 b"", "text/plain", "", 0, id="empty-plain-stays-empty"),
    pytest.param(_multipart(b"related", [
        b'Content-Type: text/plain\nContent-ID: <resource>\n\nwrong resource',
        b'Content-Type: text/html\nContent-ID: <root>\n\n<p>root body</p>',
    ], b'; start="<root>"'), b"<p>root body</p>", "text/html", "\nroot body\n", 1,
                 id="related-declared-root"),
    pytest.param(b"Content-Type: text/plain; charset=utf-8\n\n# literal\r\n````\r<a>*text*</a>",
                 b"# literal\r\n````\r<a>*text*</a>", "text/plain",
                 "\n`````\n# literal\n````\n<a>*text*</a>\n`````\n", 1,
                 id="literal-fences-and-newlines"),
    pytest.param(b"Content-Type: text/plain\nContent-Transfer-Encoding: quoted-printable\n\nbad=ZZ literal",
                 b"bad=ZZ literal", "text/plain", "\n```\nbad=ZZ literal\n```\n", 1,
                 id="invalid-quoted-printable-remains-literal"),
    pytest.param(b"Content-Type: text/html; charset=iso-8859-1\n\n"
                 b'<html><head><meta charset="iso-8859-1"><title>Ignored</title></head>'
                 b'<body><p>caf\xe9</p><script>bad()</script></body></html>',
                 b'<html><head><meta charset="iso-8859-1"><title>Ignored</title></head>'
                 b'<body><p>caf\xe9</p><script>bad()</script></body></html>', "text/html",
                 "\ncafé\n", 1, id="mime-latin1-decoded-once"),
    pytest.param(b"Content-Type: text/html; charset=utf-8\n\n" + "<p>東京</p>".encode(),
                 "<p>東京</p>".encode(), "text/html", "\n東京\n", 1, id="html-only-unicode"),
]


@pytest.mark.parametrize("raw,selected,media,content,regions", BODY_CASES)
def test_eml_selects_one_native_body_and_returns_exact_markdown(
        tmp_path, raw, selected, media, content, regions):
    # GIVEN a selected MIME body verified independently of the converter
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    body = BytesParser(policy=policy.default).parsebytes(raw).get_body(preferencelist=("plain", "html"))
    assert (body.get_content_type(), body.get_payload(decode=True)) == (media, selected), (
        "source MIME must select the exact intended candidate"
    )

    # WHEN the public route converts the local message
    conversion = run(source)

    # THEN one schema 2 chapter carries only the selected body
    assert conversion == (0, _receipt(source, regions), _markdown(source, content, regions)), (
        "body selection, literal content, Unicode and all receipt counters must match"
    )


def test_eml_repeated_headers_keep_fixed_name_order_and_source_spelling(tmp_path):
    # GIVEN reordered, repeated and empty headers plus an encoded Subject
    raw = (b"Subject: =?utf-8?b?Y2Fmw6k=?=\nTo: first@example.test\n"
           b"From: first@example.test\nCc: copy@example.test\nFrom: second@example.test\n"
           b"To:\nDate: Fri, 11 Jul 2003 21:00:37 -0700\n"
           b"Message-ID: <id@example.test>\nBcc: omitted@example.test\n"
           b"Content-Type: text/plain\n\nhello")
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert ([str(value) for value in message.get_all("From")],
            [str(value) for value in message.get_all("To")], str(message["Subject"])) == (
        ["first@example.test", "second@example.test"], ["first@example.test", ""], "café",
    ), "source must retain repeated identities and an empty To"
    content = ("\nFrom: first@example.test\nFrom: second@example.test\n"
               "To: first@example.test\nTo: \nCc: copy@example.test\n"
               "Date: Fri, 11 Jul 2003 21:00:37 -0700\nSubject: café\n"
               "Message-ID: &lt;id@example.test>\n\n```\nhello\n```\n")

    # WHEN only the supplied public header subset is rendered
    conversion = run(source)

    # THEN spelling, decoded words, empty values and repeated order remain source driven
    assert conversion == (0, _receipt(source, 2), _markdown(source, content, 2, "café")), (
        "headers must be literal source values in From/To/Cc/Date/Subject/Message-ID order"
    )


def test_eml_attachment_inventory_uses_names_without_decoding_payloads(tmp_path, monkeypatch):
    # GIVEN named inline, unnamed attached and opaque attached-message parts
    raw = _multipart(b"mixed", [PLAIN,
        b"Content-Type: image/png\nContent-Disposition: inline; filename*=utf-8''..%2Fcaf%C3%A9.png\n"
        b"Content-Transfer-Encoding: base64\n\n%%%",
        b"Content-Type: application/octet-stream\nContent-Disposition: attachment\n\nopaque",
        b"Content-Type: message/rfc822\nContent-Disposition: attachment; filename=mail.eml\n\n"
        b"Content-Type: text/plain\n\nattached secret",
    ])
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert (len(list(message.walk())), message.get_payload()[1].get_filename()) == (
        6, "../café.png",
    ), "source must include an opaque attached child and decoded path-like filename"
    original = EmailMessage.get_payload
    decoded = []

    def selected_payload_only(part, i=None, decode=False):
        """Reject any decoding request outside the selected plain MIME part."""
        decoded.extend([part.get_content_type()] * decode)
        assert not decode or part.get_content_type() == "text/plain", "attachment payload must never decode"
        assert not decode or original(part) == "plain first", "attached-message text must stay opaque"
        return original(part, i, decode)

    monkeypatch.setattr(EmailMessage, "get_payload", selected_payload_only)
    content = ("\n```\nplain first\n```\n\nAttachments:\n"
               "- ../café.png\n- \\[unnamed attachment\\]\n- mail.eml\n")

    # WHEN conversion inventories declared attachment metadata
    conversion = run(source)

    # THEN names follow MIME source order and cannot create paths or artifacts
    assert (conversion, decoded, sorted(path.name for path in tmp_path.iterdir())) == (
        (0, _receipt(source, 2), _markdown(source, content, 2)), ["text/plain"], ["source.eml"],
    ), "inventory must omit all attached content and never join filenames to output paths"


@pytest.mark.parametrize("raw,media,charset,transfer,reason_pattern", [
    pytest.param(b"Content-Type: text/plain; charset=unknown-brewdoc\n\nhello",
                 "text/plain", "unknown-brewdoc", "7bit",
                 r"EML .*charset.*unknown-brewdoc.*", id="unknown-charset"),
    pytest.param(b"Content-Type: text/plain; charset=utf-8\n\nbad\xff",
                 "text/plain", "utf-8", "7bit",
                 r"EML .*utf-8.*", id="invalid-declared-bytes"),
    pytest.param(b"Content-Type: text/plain\n\nbad\xff",
                 "text/plain", None, "7bit",
                 r"EML .*ascii.*", id="absent-charset-means-ascii"),
    pytest.param(b"Content-Type: text/plain\nContent-Transfer-Encoding: base64\n\n%%%",
                 "text/plain", None, "base64",
                 r"EML .*base64.*", id="invalid-base64"),
    pytest.param(b"Content-Type: text/plain\nContent-Transfer-Encoding: x-unknown\n\nhello",
                 "text/plain", None, "x-unknown",
                 r"EML .*transfer encoding.*x-unknown.*", id="unknown-transfer-encoding"),
    pytest.param(b'Content-Type: multipart/mixed; boundary="missing"\n\nnot a boundary',
                 "multipart/mixed", None, "7bit",
                 r"EML .*boundary.*", id="broken-outer-boundary"),
])
def test_eml_selected_body_or_outer_defect_refuses_without_output_writes(
        tmp_path, raw, media, charset, transfer, reason_pattern):
    # GIVEN a malformed selected payload or outer container and a protected destination
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep destination\r\n")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert (message.get_content_type(), message.get_content_charset(),
            str(message.get("Content-Transfer-Encoding", "7bit"))) == (
        media, charset, transfer,
    ), "the malformed source must target the intended MIME decoder or outer container"

    # WHEN the public route validates data it would consume
    code, receipt, markdown = run(source, out)

    # THEN the named EML refusal leaves existing bytes and no staged files
    assert (code, receipt, markdown, out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        1, _refusal(source, receipt["reason"]), "", b"keep destination\r\n",
        ["output.md", "source.eml"],
    ), "failure must belong to EML and preserve every output before staging"
    assert re.fullmatch(reason_pattern, receipt["reason"], re.DOTALL) is not None, (
        "refusal must identify the selected decoding or outer structure error"
    )


def test_eml_plain_body_does_not_require_html_parser(tmp_path, monkeypatch):
    # GIVEN plain mail and an unavailable optional HTML parser
    source = tmp_path / "source.eml"
    source.write_bytes(PLAIN)
    monkeypatch.setattr(htmltext, "LexborHTMLParser", None)
    assert htmltext.LexborHTMLParser is None, "HTML backend must be unavailable during conversion"

    # WHEN the public route selects plain text
    conversion = run(source)

    # THEN no HTML prerequisite prevents exact literal output
    assert conversion == (
        0, _receipt(source, 1), _markdown(source, "\n```\nplain first\n```\n", 1),
    ), "plain EML must work without the HTML backend"


def test_eml_html_scripts_are_omitted_and_external_image_stays_inert(tmp_path, monkeypatch):
    # GIVEN HTML-only mail with Unicode, a script and an external image
    payload = ('<p>東京</p><script>bad()</script>'
               '<img src="https://example.test/image.png" alt="photo">').encode("utf-8")
    raw = b"Content-Type: text/html; charset=utf-8\n\n" + payload
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    selected = BytesParser(policy=policy.default).parsebytes(raw).get_body()
    assert (selected.get_content_type(), selected.get_payload(decode=True)) == (
        "text/html", payload,
    ), "the selected body must contain the script and external image reference"

    def refuse_network(*args, **kwargs):
        """Fail immediately if local conversion tries to acquire a remote resource."""
        raise AssertionError("local EML must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse_network)
    monkeypatch.setattr(urllib.request, "urlopen", refuse_network)
    content = '\n東京\n\n![photo](https://example.test/image.png)\n'

    # WHEN the existing static HTML body renderer handles the message
    conversion = run(source)

    # THEN script content disappears and the image becomes an unfetched Markdown image
    assert conversion == (0, _receipt(source, 2), _markdown(source, content, 2)), (
        "HTML EML must retain static rendering semantics without fetching its image"
    )


@pytest.mark.parametrize("payload,content,regions", [
    pytest.param('<nav><p>Nav</p></nav><header><p>Head</p></header><main><p>Main</p></main>'
                 '<aside><p>Aside</p></aside><footer><p>Footer</p></footer>',
                 "\nMain\n", 1, id="landmark-tags-dropped"),
    pytest.param('<div role="navigation"><p>N</p></div><div role="Banner extra"><p>B</p></div>'
                 '<div role="contentinfo"><p>C</p></div><div role="complementary"><p>A</p></div>'
                 '<p>Body</p>', "\nBody\n", 1, id="landmark-roles-dropped"),
    pytest.param('<article><header><p>By</p></header><nav><p>TOC</p></nav><p>Text</p></article>',
                 "\nBy\n\nTOC\n\nText\n", 3, id="landmarks-inside-article-kept"),
    pytest.param('<form><label for="e">Email</label><input id="e" value="x"><button>Send</button>'
                 '<select><option>A</option></select><textarea>t</textarea></form>',
                 "\nEmail\n", 1, id="form-controls-dropped"),
    pytest.param('<img src="x.png"><img alt=" " src="y.png"><img src="pic" alt="A &amp; B" title="t">'
                 '<p>T</p>', "\n![A & B](pic)\n\nT\n", 2, id="images-without-alt-dropped"),
    pytest.param('<ul><li>a</li><li>b<ul><li>c</li></ul></li><li>d</li></ul>',
                 "\n- a\n- b\n  - c\n- d\n", 4, id="plain-nested-list"),
    pytest.param('<blockquote><p>q</p><blockquote><p>inner</p></blockquote></blockquote>',
                 "\n> q\n>\n> > inner\n", 2, id="quote-prefixes"),
    pytest.param('<p>AT&amp;T &amp;copy; snake_case *x* [y] &lt;z&gt; "q" a &gt; b # 1. C:\\d</p>'
                 '<p>- dash</p>',
                 '\nAT&T &amp;copy; snake_case \\*x\\* \\[y\\] &lt;z> "q" a > b # 1. C:\\\\d\n\n'
                 '\\- dash\n', 2, id="minimal-escaping"),
    pytest.param('<blockquote><p>Spring, <br> Or the bees \t<br>end</p></blockquote>'
                 '<p>One <br>Two</p>',
                 "\n> Spring,\n> Or the bees\n> end\n\nOne\nTwo\n", 2,
                 id="line-breaks-leave-no-trailing-blanks"),
    pytest.param('<p>Q&amp;</p><p>a&amp;<span>amp;</span></p>',
                 "\nQ&\n\na&amp;amp;\n", 2, id="node-final-ampersand-escaped-only-when-joining"),
    pytest.param('<p>Hi!<a href="https://x.test/t">x</a></p>', "\nHi\\![x](https://x.test/t)\n", 1,
                 id="bang-before-link-escaped"),
])
def test_eml_html_body_uses_the_clean_html_profile(tmp_path, payload, content, regions):
    # GIVEN HTML-only mail whose body carries one clean-profile feature
    raw = b"Content-Type: text/html; charset=utf-8\n\n" + payload.encode("utf-8")
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    selected = BytesParser(policy=policy.default).parsebytes(raw).get_body()
    assert (selected.get_content_type(), selected.get_payload(decode=True)) == (
        "text/html", payload.encode("utf-8"),
    ), "the selected body must be the exact HTML payload"

    # WHEN the EML route renders the body through the shared HTML content renderer
    conversion = run(source)

    # THEN landmarks, controls, images, lists, quotes and escaping follow the HTML clean rules
    assert conversion == (0, _receipt(source, regions), _markdown(source, content, regions)), (
        "EML HTML bodies must render exactly as the clean static HTML profile"
    )


def test_eml_html_body_refuses_when_html_parser_is_unavailable(tmp_path, monkeypatch):
    # GIVEN HTML-only mail and an unavailable HTML parser
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(HTML)
    out.write_bytes(b"keep")
    monkeypatch.setattr(htmltext, "LexborHTMLParser", None)
    monkeypatch.setattr(htmltext, "_PARSER_ERROR", "test missing backend")
    assert BytesParser(policy=policy.default).parsebytes(HTML).get_body().get_content_type() == (
        "text/html"
    ), "source must require the existing HTML content renderer"

    # WHEN conversion reaches the HTML prerequisite check
    conversion = run(source, out)

    # THEN the existing named parser refusal preserves the destination
    assert (conversion, out.read_bytes()) == (
        (1, _refusal(source, "HTML parser unavailable: selectolax 0.4.13: test missing backend"), ""),
        b"keep",
    ), "HTML-only EML must reuse the existing HTML prerequisite refusal"


def test_eml_api_file_and_ascii_cli_stdout_publish_exact_utf8(tmp_path, monkeypatch, capsys):
    # GIVEN Unicode mail and an ASCII-configured stdout
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(b"Content-Type: text/plain; charset=utf-8\n\n" + "café 東京".encode())
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    markdown = _markdown(source, "\n```\ncafé 東京\n```\n", 1)
    assert stream.encoding == "ascii", "CLI must emit Unicode through its byte-buffer path"

    # WHEN the API writes a file and the CLI publishes stdout
    written = run(source, out)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        code = main([str(source)])
        stream.flush()
        stdout = stream.buffer.getvalue()
    captured = capsys.readouterr()

    # THEN both output destinations carry exact UTF-8 with complete receipt framing
    assert written == (0, _receipt(source, 1, str(out)), markdown), "API must return the full result"
    assert (out.read_bytes(), code, stdout, captured.out, captured.err) == (
        markdown.encode("utf-8"), 0,
        (json.dumps(_receipt(source, 1), ensure_ascii=True, sort_keys=True)
         + os.linesep).encode("ascii") + markdown.encode("utf-8"), "", "",
    ), "file and ASCII stdout must retain Unicode without extra body framing"


@pytest.mark.corpus
def test_eml_groupwise_attached_message_defect_keeps_valid_primary_body(tmp_path):
    # GIVEN the licensed GroupWise source with a defect only inside its attached message
    source = Path(__file__).parent / "fixtures/eml/tika-testGroupWiseEml.eml"
    raw = source.read_bytes()
    message = BytesParser(policy=policy.default).parsebytes(raw)
    selected = message.get_body(preferencelist=("plain", "html"))
    assert (hashlib.sha256(raw).hexdigest(), len(raw), len(list(message.walk())),
            selected.get_payload(decode=True),
            [type(defect).__name__ for defect in message.get_payload()[1].get_payload()[0].defects],
            dict(message.raw_items())["From"]) == (
        "9e313395e35efe1e0d0eab9410b2820bbc354965144d017c42e91740815f2233", 2411, 7,
        b"test", ["MissingHeaderBodySeparatorDefect"], '"Novell GroupWise" <$$$.$$$.$$$>',
    ), "fixture identity and attached-child defect must match the documented source constraint"
    out = tmp_path / "output.md"
    content = ('\nFrom: "Novell GroupWise" &lt;$$$.$$$.$$$>\n'
               "Date: Thu, 27 Jun 2013 13:29:57 +0200\nSubject: test333\n"
               "Message-ID: &lt;51CC3E55020000000000000F@$$$>\n"
               "\n```\ntest\n```\n\nAttachments:\n- test.eml\n- Neues Textdokument.txt\n")

    # WHEN only primary content and attachment metadata are converted
    conversion = run(source, out)

    # THEN an omitted nested-message defect cannot refuse the valid primary body
    assert conversion == (0, _receipt(source, 3, str(out)),
                          _markdown(source, content, 3, "test333")), (
        "primary body and both declared attachment names must survive without nested content"
    )
    assert out.read_bytes() == conversion[2].encode("utf-8"), "corpus output must use UTF-8"


def test_eml_named_inline_text_remains_a_native_body_candidate(tmp_path):
    # GIVEN a named inline text part before an unnamed alternative
    raw = _multipart(b"mixed", [
        b'Content-Type: text/plain\nContent-Disposition: inline; filename="=?utf-8?b?Y2Fmw6kudHh0?="\n\nnamed body',
        PLAIN,
    ])
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    selected = message.get_body(preferencelist=("plain", "html"))
    assert (selected.get_payload(decode=True), selected.get_filename()) == (
        b"named body", "café.txt",
    ), "filename alone must not remove an inline text body candidate"
    content = "\n```\nnamed body\n```\n\nAttachments:\n- café.txt\n"

    # WHEN native body selection and metadata inventory share that part
    conversion = run(source)

    # THEN its selected text and decoded filename are both carried once
    assert conversion == (0, _receipt(source, 2), _markdown(source, content, 2)), (
        "named inline text must retain native selection and source-driven inventory"
    )


def test_eml_missing_supported_body_has_explicit_omission(tmp_path):
    # GIVEN an opaque encrypted MIME payload without supported text
    raw = b"Content-Type: application/pkcs7-mime\n\nopaque encrypted payload"
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    assert BytesParser(policy=policy.default).parsebytes(raw).get_body() is None, (
        "opaque encrypted MIME must have no supported primary body"
    )

    # WHEN local conversion carries only available source metadata
    conversion = run(source)

    # THEN unavailable text is disclosed without decryption or invented regions
    assert conversion == (0, _receipt(source, 0),
                          _markdown(source, "\nNo supported message body.\n", 0)), (
        "missing body must be explicit and cannot be replaced with encrypted payload bytes"
    )


def _nested_mime(depth):
    """Wrap one leaf with unique MIME boundaries; root counts as depth one."""
    raw = PLAIN
    for index in range(depth - 1):
        boundary = f"level-{index}".encode()
        raw = (b'Content-Type: multipart/mixed; boundary="' + boundary + b'"\n\n--'
               + boundary + b'\n' + raw + b'\n--' + boundary + b'--\n')
    return raw


@pytest.mark.parametrize("nodes", [1024])
def test_eml_actual_message_node_boundary_counts_root_without_factory_probe(tmp_path, nodes):
    # GIVEN exactly 1024 actual nodes, counting root and opaque parts
    raw = _multipart(b"mixed", [PLAIN] + [b"Content-Type: application/octet-stream\n\nopaque"] * (nodes - 2))
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert len(list(message.walk())) == nodes, "source must contain the actual admitted node count"

    # WHEN the route parses the boundary message with a policy factory
    conversion = run(source)

    # THEN no disposable parser probe consumes a retained-node allowance
    assert conversion == (0, _receipt(source, 1),
                          _markdown(source, "\n```\nplain first\n```\n", 1)), (
        "1024 retained nodes must succeed, including root, body and opaque resources"
    )


def test_eml_actual_message_node_limit_plus_one_refuses_without_writes(tmp_path):
    # GIVEN one root, one body and 1023 opaque parts with a protected destination
    raw = _multipart(b"mixed", [PLAIN] + [b"Content-Type: application/octet-stream\n\nopaque"] * 1023)
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep")
    assert len(list(BytesParser(policy=policy.default).parsebytes(raw).walk())) == 1025, (
        "the refusal boundary must count actual retained nodes"
    )

    # WHEN parsing would construct a 1025th message node
    conversion = run(source, out)

    # THEN the stable node-limit refusal occurs before output staging
    assert (conversion, out.read_bytes(), sorted(path.name for path in tmp_path.iterdir())) == (
        (1, _refusal(source, "EML message nodes limit exceeded: 1024"), ""),
        b"keep", ["output.md", "source.eml"],
    ), "excess MIME nodes must preserve the destination and avoid staged output"


def test_eml_mime_depth_boundary_counts_root_as_one(tmp_path):
    # GIVEN one text leaf at MIME depth 64
    raw = _nested_mime(64)
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert (len(list(message.walk())), message.get_body().get_payload(decode=True)) == (
        64, b"plain first",
    ), "the singly nested source must have root depth one and a depth-64 leaf"

    # WHEN the iterative depth check admits this message
    conversion = run(source)

    # THEN native body selection proceeds at the exact supported depth
    assert conversion == (0, _receipt(source, 1),
                          _markdown(source, "\n```\nplain first\n```\n", 1)), (
        "depth 64 must succeed and preserve the selected plain body"
    )


def test_eml_mime_depth_limit_plus_one_refuses_before_output(tmp_path):
    # GIVEN a depth-65 text leaf and a protected output
    raw = _nested_mime(65)
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep")
    assert len(list(BytesParser(policy=policy.default).parsebytes(raw).walk())) == 65, (
        "singly nested source must exceed the root-inclusive depth by exactly one"
    )

    # WHEN the route checks MIME depth before recursive body selection
    conversion = run(source, out)

    # THEN the stable depth refusal preserves output and no stage exists
    assert (conversion, out.read_bytes(), sorted(path.name for path in tmp_path.iterdir())) == (
        (1, _refusal(source, "EML MIME depth limit exceeded: 64"), ""),
        b"keep", ["output.md", "source.eml"],
    ), "excess depth must fail before output staging or body traversal"


def test_eml_policy_factory_constructs_exact_retained_node_allowance(tmp_path, monkeypatch):
    # GIVEN three actual MIME nodes and an allocation allowance of three
    source = tmp_path / "source.eml"
    source.write_bytes(_multipart(b"mixed", [PLAIN, b"Content-Type: application/octet-stream\n\nopaque"]))
    monkeypatch.setattr(eml, "MAX_MESSAGE_NODES", 3)
    original = eml.EmailMessage
    constructed = []

    def count_constructor(*, policy):
        """Record actual message allocations without a disposable parser-class probe."""
        part = original(policy=policy)
        constructed.append(part)
        return part

    monkeypatch.setattr(eml, "EmailMessage", count_constructor)
    assert eml.MAX_MESSAGE_NODES == 3, "the small node budget must include root and both parts"

    # WHEN the parser admits exactly the allowed retained nodes
    message = eml._parse_message(source)

    # THEN the policy factory allocates only the three nodes retained in the parsed tree
    assert (constructed, [part.get_content_type() for part in message.walk()]) == (
        list(message.walk()), ["multipart/mixed", "text/plain", "application/octet-stream"],
    ), "the parser must not spend node budget or allocate an extra message for a class probe"


@pytest.mark.parametrize("raw", [
    pytest.param(_multipart(b"mixed", [PLAIN] + [b"Content-Type: application/octet-stream\n\nopaque"] * 2),
                 id="opaque-binary-parts"),
    pytest.param(_multipart(b"mixed", [PLAIN,
        b"Content-Type: message/rfc822\nContent-Disposition: attachment\n\n"
        b"Content-Type: text/plain\n\nopaque attached text",
    ]), id="opaque-attached-descendant"),
])
def test_eml_policy_factory_refuses_before_over_budget_construction(tmp_path, monkeypatch, raw):
    # GIVEN four actual nodes and a budget that permits only three allocations
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep")
    assert len(list(BytesParser(policy=policy.default).parsebytes(raw).walk())) == 4, (
        "the source must request one actual retained node beyond the injected budget"
    )
    monkeypatch.setattr(eml, "MAX_MESSAGE_NODES", 3)
    original = eml.EmailMessage
    constructed = []

    def count_constructor(*, policy):
        """Record actual allocations so refusal after constructing the excess node fails."""
        part = original(policy=policy)
        constructed.append(part)
        return part

    monkeypatch.setattr(eml, "EmailMessage", count_constructor)

    # WHEN the public route would need its fourth retained message
    conversion = run(source, out)

    # THEN the fourth constructor is never called and output remains untouched
    assert (conversion, len(constructed), out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        (1, _refusal(source, "EML message nodes limit exceeded: 3"), ""),
        3, b"keep", ["output.md", "source.eml"],
    ), "the policy factory must refuse before allocation, not after retaining an excess node"


def test_eml_depth_check_refuses_before_recursive_native_body_selection(tmp_path, monkeypatch):
    # GIVEN a depth-three leaf and a depth-two allowance
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    raw = _nested_mime(3)
    source.write_bytes(raw)
    out.write_bytes(b"keep")
    assert len(list(BytesParser(policy=policy.default).parsebytes(raw).walk())) == 3, (
        "the singly nested source must exceed the injected root-inclusive depth"
    )
    monkeypatch.setattr(eml, "MAX_MIME_DEPTH", 2)
    selections = []

    def forbidden_body_selection(message, preferencelist=("related", "html", "plain")):
        """Make any recursive native body selection before the depth refusal visible."""
        selections.append(message)
        raise AssertionError("body selection must follow the iterative MIME depth check")

    monkeypatch.setattr(EmailMessage, "get_body", forbidden_body_selection)

    # WHEN local conversion checks the parsed MIME tree
    conversion = run(source, out)

    # THEN depth refusal precedes every get_body call and output publication
    assert (conversion, selections, out.read_bytes()) == (
        (1, _refusal(source, "EML MIME depth limit exceeded: 2"), ""), [], b"keep",
    ), "over-depth messages must never reach native recursive body selection"


def test_eml_small_depth_boundary_reaches_native_body_selection_once(tmp_path, monkeypatch):
    # GIVEN a depth-two leaf and the same root-inclusive allowance
    source = tmp_path / "source.eml"
    raw = _nested_mime(2)
    source.write_bytes(raw)
    assert len(list(BytesParser(policy=policy.default).parsebytes(raw).walk())) == 2, (
        "the single container and leaf must be exactly at the injected depth boundary"
    )
    monkeypatch.setattr(eml, "MAX_MIME_DEPTH", 2)
    original = EmailMessage.get_body
    selections = []

    def record_body_selection(message, preferencelist=("related", "html", "plain")):
        """Record the native selection entry after the iterative depth check admits the tree."""
        selections.append(message.get_content_type())
        return original(message, preferencelist)

    monkeypatch.setattr(EmailMessage, "get_body", record_body_selection)

    # WHEN local conversion admits the exact depth boundary
    conversion = run(source)

    # THEN native body selection runs once on the root after depth admission
    assert (conversion, selections) == (
        (0, _receipt(source, 1), _markdown(source, "\n```\nplain first\n```\n", 1)),
        ["multipart/mixed"],
    ), "the exact depth allowance must reach normal body selection and preserve its output"


@pytest.mark.parametrize("raw,selected,media,content", [
    pytest.param(_multipart(b"mixed", [PLAIN,
        b'Content-Type: multipart/mixed; boundary="missing"\n\nopaque malformed resource',
    ]), b"plain first", "text/plain", "\n```\nplain first\n```\n", id="unselected-inline-container"),
    pytest.param(_multipart(b"related", [
        b'Content-Type: multipart/mixed; boundary="missing"\nContent-ID: <resource>\n\nopaque malformed resource',
        b'Content-Type: text/html\nContent-ID: <root>\n\n<p>root body</p>',
    ], b'; start="<root>"'), b"<p>root body</p>", "text/html", "\nroot body\n",
                 id="non-root-related-container"),
])
def test_eml_defect_in_unselected_inline_container_keeps_valid_selected_body(
        tmp_path, raw, selected, media, content):
    # GIVEN a valid selected body and a malformed container in an omitted MIME subtree
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=("plain", "html"))
    assert (message.defects, body.get_content_type(), body.get_payload(decode=True),
            sorted(type(defect).__name__ for part in message.walk() for defect in part.defects)) == (
        [], media, selected, ["StartBoundaryNotFoundDefect"],
    ), "malformation must belong only to an omitted container, not the selected body or outer message"

    # WHEN conversion consumes primary body and available metadata
    conversion = run(source)

    # THEN omitted resource defects cannot invalidate the selected primary body
    assert conversion == (0, _receipt(source, 1), _markdown(source, content, 1)), (
        "structural refusal must be limited to consumed outer structure and selected-body ancestors"
    )


@pytest.mark.parametrize("subject,reason_pattern", [
    pytest.param("=?unknown-brewdoc?b?aGVsbG8=?=", r"EML header charset unknown-brewdoc unreadable: .*",
                 id="header-unknown-charset"),
    pytest.param("=?utf-8?b?/w==?=", r"EML header charset utf-8 unreadable: .*",
                 id="header-invalid-declared-bytes"),
    pytest.param("=?utf-8?b?%%%?=", r"EML header base64 unreadable: .*",
                 id="header-invalid-base64"),
])
def test_eml_rendered_encoded_header_defect_refuses_without_writes(tmp_path, subject, reason_pattern):
    # GIVEN an invalid rendered Subject and an independently valid selected plain body
    raw = ("Subject: " + subject + "\nContent-Type: text/plain\n\nhello").encode("ascii")
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert (dict(message.raw_items())["Subject"], message.get_body().get_payload(decode=True)) == (
        subject, b"hello",
    ), "only a carried encoded header, not the selected body, must be malformed"

    # WHEN the EML route decodes the header it would carry
    code, receipt, markdown = run(source, out)

    # THEN strict header refusal preserves the destination before publication
    assert (code, receipt, markdown, out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        1, _refusal(source, receipt["reason"]), "", b"keep", ["output.md", "source.eml"],
    ), "malformed rendered metadata must not be replaced or written as valid source text"
    assert re.fullmatch(reason_pattern, receipt["reason"], re.DOTALL) is not None, (
        "the refusal must name the consumed header decoding error"
    )


def test_eml_parser_recursion_failure_returns_named_refusal_without_writes(tmp_path, monkeypatch):
    # GIVEN valid local bytes and a parser that exhausts recursion during MIME parsing
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(PLAIN)
    out.write_bytes(b"keep")
    assert source.read_bytes() == PLAIN, "recursion refusal must be caused by the parser boundary"

    def exhausted_parser(parser, source_stream, headersonly=False):
        """Simulate the stdlib capacity failure without growing an unbounded MIME input."""
        raise RecursionError("simulated MIME parser capacity")

    monkeypatch.setattr(BytesParser, "parse", exhausted_parser)

    # WHEN the existing public route invokes the failing stdlib parser
    conversion = run(source, out)

    # THEN the stable capacity refusal prevents traceback leakage and output writes
    assert (conversion, out.read_bytes()) == (
        (1, _refusal(source, "EML MIME nesting exceeds parser capacity"), ""), b"keep",
    ), "parser recursion failure must use the named EML refusal and preserve output"


def test_eml_selected_content_type_header_defect_refuses_without_writes(tmp_path):
    # GIVEN a selected UTF-8 body whose charset parameter lacks its closing quote
    raw = b'Content-Type: text/plain; charset="utf-8\n\ncaf\xc3\xa9'
    source, out = tmp_path / "source.eml", tmp_path / "output.md"
    source.write_bytes(raw)
    out.write_bytes(b"keep destination\r\n")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    selected = message.get_body(preferencelist=("plain", "html"))
    assert (message.defects, [type(defect).__name__ for defect in selected["Content-Type"].defects],
            selected.get_content_charset(), selected.get_payload(decode=True)) == (
        [], ["InvalidHeaderDefect"], "utf-8", b"caf\xc3\xa9",
    ), "the defect must live on the consumed MIME header while native parsing recovers valid text"

    # WHEN the route validates the selected body's MIME decoding declaration
    code, receipt, markdown = run(source, out)

    # THEN malformed consumed MIME metadata cannot publish recovered content
    assert (code, receipt, markdown, out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        1, _refusal(source, receipt["reason"]), "", b"keep destination\r\n",
        ["output.md", "source.eml"],
    ), "a consumed Content-Type header defect must refuse before any output replacement"
    assert re.fullmatch(r"EML .*Content-Type.*InvalidHeaderDefect.*", receipt["reason"], re.DOTALL) is not None, (
        "the refusal must identify the consumed MIME header and its defect"
    )


def test_eml_ignored_attachment_content_type_defect_keeps_valid_primary_body(tmp_path):
    # GIVEN a valid primary body and the same malformed charset syntax on an opaque attachment
    raw = _multipart(b"mixed", [PLAIN,
        b'Content-Type: application/octet-stream; charset="utf-8\n'
        b'Content-Disposition: attachment; filename="opaque.bin"\n\nopaque\xff',
    ])
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    selected = message.get_body(preferencelist=("plain", "html"))
    attached = message.get_payload()[1]
    assert (message.defects, selected.get_payload(decode=True), attached.get_content_disposition(),
            attached.get_filename(), [type(defect).__name__ for defect in attached["Content-Type"].defects]) == (
        [], b"plain first", "attachment", "opaque.bin", ["InvalidHeaderDefect"],
    ), "the malformed MIME declaration must belong only to intentionally omitted attachment data"
    content = "\n```\nplain first\n```\n\nAttachments:\n- opaque.bin\n"

    # WHEN conversion carries the primary body and declared attachment name
    conversion = run(source)

    # THEN attachment header recovery cannot invalidate the valid selected body
    assert conversion == (0, _receipt(source, 2), _markdown(source, content, 2)), (
        "MIME header validation must stop at consumed structure and preserve opaque attachments"
    )
