"""Exact UTF-8 pass-through through the existing public service and CLI."""

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from brewdoc import run
from brewdoc.cli import main


TEXT_CASES = [
    pytest.param(b"", id="empty"),
    pytest.param(b"one\ntwo\n", id="lf-final-newline"),
    pytest.param(b"one\rtwo\r", id="bare-cr"),
    pytest.param(b"  \tfirst\r\n\r\nlast  ", id="crlf-whitespace-no-final-newline"),
    pytest.param("\ufeff# café 東京\r\n\r\n```\r\n*literal  ".encode("utf-8"),
                 id="bom-unicode-fence"),
    pytest.param("<a> & | * _ \\ `\n\x00\u00ad\u00a0\ue000ﬀ\u2028".encode("utf-8"),
                 id="syntax-controls-and-unicode"),
]


def _receipt_base(path):
    """Describe opaque text without claiming parsed regions or generated units."""
    return {
        "route": "text", "source": path.name, "out": None,
        "receipt_schema": "brewdoc.receipt/1", "unit_kind": "none", "units": 0,
        "tables": 0, "text_regions": 0, "columns_split": 0,
        "broken_ligature_words": 0, "not_carried": [],
        "dropped": {"control_chars": 0, "soft_hyphens": 0, "nbsp": 0,
                    "pua_glyphs": 0, "cid_survivors": 0, "ligatures": 0,
                    "running_heads": 0, "page_numbers": 0, "non_ascii_replaced": 0},
    }


def _success(path, out=None):
    """Pin the complete schema 1 receipt for the raw Markdown contract."""
    return _receipt_base(path) | {
        "file_ok": True, "reason": "text passed through without normalization",
        "out": out, "artifacts": [], "unit_keys": [],
        "markdown_schema": "brewdoc.markdown/passthrough/1",
    }


def _refusal(path, reason):
    """Pin the existing refusal envelope, including absent success-only fields."""
    return _receipt_base(path) | {"file_ok": False, "reason": reason}


@pytest.mark.parametrize("suffix", [".md", ".TXT"])
@pytest.mark.parametrize("payload", TEXT_CASES)
def test_text_api_and_output_preserve_every_utf8_byte(tmp_path, suffix, payload):
    # GIVEN opaque UTF-8 text and an existing output to replace
    source, out = tmp_path / ("source" + suffix), tmp_path / "output.md"
    source.write_bytes(payload)
    out.write_bytes(b"old output\r\n")
    assert payload.decode("utf-8").encode("utf-8") == payload, "input must be strict UTF-8"

    # WHEN the public API returns text and writes a destination
    direct = run(source)
    written = run(source, out)

    # THEN both receipts and body bytes follow the pass-through contract
    assert (direct, written, out.read_bytes(), source.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        (0, _success(source), payload.decode("utf-8")),
        (0, _success(source, str(out)), payload.decode("utf-8")),
        payload, payload, sorted([source.name, out.name]),
    ), "API output must preserve BOM, syntax, whitespace, controls and newline presence"


@pytest.mark.parametrize("suffix", [".MD", ".txt"])
@pytest.mark.parametrize("payload", TEXT_CASES)
def test_text_cli_ascii_stdout_emits_exact_receipt_and_utf8_body(
        tmp_path, monkeypatch, capsys, suffix, payload):
    # GIVEN strict UTF-8 source and stdout that rejects non-ASCII text writes
    source = tmp_path / ("source" + suffix)
    source.write_bytes(payload)
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    assert stream.encoding == "ascii", "the test must exercise ASCII-configured stdout"

    # WHEN the CLI emits its receipt followed by the unframed source body
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        exit_code = main([str(source)])
        stream.flush()
        stdout = stream.buffer.getvalue()
    captured = capsys.readouterr()

    # THEN ASCII JSON framing precedes the unchanged body, including an empty body
    expected = (json.dumps(_success(source), ensure_ascii=True, sort_keys=True)
                + os.linesep).encode("ascii") + payload
    assert (exit_code, stdout, captured.out, captured.err) == (
        0, expected, "", "",
    ), "CLI must emit Unicode through its byte buffer with exactly one receipt line"


@pytest.mark.parametrize("payload", TEXT_CASES)
def test_text_cli_out_writes_only_raw_bytes_and_prints_only_receipt(
        tmp_path, capsys, payload):
    # GIVEN opaque source text and an absent nested output directory
    source, out = tmp_path / "source.txt", tmp_path / "nested" / "output.md"
    source.write_bytes(payload)
    assert out.parent.exists() is False, "output transaction must create the absent parent"

    # WHEN the CLI publishes pass-through bytes to a file
    exit_code = main([str(source), "--out", str(out)])
    captured = capsys.readouterr()

    # THEN stdout has only the full receipt and the file has no generated envelope
    assert (exit_code, captured.out, captured.err) == (
        0, json.dumps(_success(source, str(out)), ensure_ascii=True, sort_keys=True) + "\n",
        "",
    ), "--out must succeed with one receipt before the destination is inspected"
    assert out.read_bytes() == payload, "--out must preserve source bytes without an envelope"


@pytest.mark.parametrize("suffix", [".md", ".txt"])
@pytest.mark.parametrize("payload", [
    pytest.param(b"bad\xff", id="invalid-utf8"),
    pytest.param(b"truncated\xe2\x82", id="truncated-utf8"),
    pytest.param("hello".encode("utf-16"), id="utf16-bom"),
    pytest.param("hello".encode("utf-32"), id="utf32-bom"),
])
def test_text_invalid_encoding_refuses_before_output_staging(tmp_path, suffix, payload):
    # GIVEN undecodable text and a destination whose bytes must survive
    source, out = tmp_path / ("source" + suffix), tmp_path / "output.md"
    source.write_bytes(payload)
    out.write_bytes(b"keep destination\r\n")
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")

    # WHEN the public API is asked to publish this invalid source
    exit_code, receipt, markdown = run(source, out)

    # THEN encoding refusal retains the complete envelope and creates no stage
    assert (exit_code, receipt, markdown, out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        1, _refusal(source, receipt["reason"]), "", b"keep destination\r\n",
        sorted([source.name, out.name]),
    ), "invalid encodings must be refused by the text route before touching output"


@pytest.mark.parametrize("options,cli_options,reason", [
    pytest.param({"sheets": ["Sheet1"]}, ["--sheet", "Sheet1"],
                 "--sheet and --artifact are workbook-only options", id="sheet"),
    pytest.param({"artifact_outputs": {"formula/sheet/000001": "artifact.json"}},
                 ["--artifact", "formula/sheet/000001=artifact.json"],
                 "--sheet and --artifact are workbook-only options", id="artifact"),
    pytest.param({"article": True}, ["--article"],
                 "--article is an HTML-only option", id="article"),
])
def test_text_rejects_format_specific_api_and_cli_options(
        tmp_path, monkeypatch, capsys, options, cli_options, reason):
    # GIVEN valid text and a guarded existing destination
    monkeypatch.chdir(tmp_path)
    source, out = tmp_path / "source.md", tmp_path / "output.md"
    source.write_bytes(b"# literal\r\n")
    out.write_bytes(b"keep destination\r\n")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["output.md", "source.md"], (
        "no artifact or stage must exist before the request"
    )

    # WHEN workbook or HTML options reach both public entry points
    api = run(source, out, **options)
    exit_code = main([str(source), "--out", str(out), *cli_options])
    captured = capsys.readouterr()

    # THEN both entry points refuse without writing any destination or artifact
    assert (api, exit_code, captured.out, captured.err, out.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        (1, _refusal(source, reason), ""), 1,
        json.dumps(_refusal(source, reason), ensure_ascii=True, sort_keys=True) + "\n",
        "", b"keep destination\r\n", ["output.md", "source.md"],
    ), "text must retain workbook-only and HTML-only option refusals"


def test_text_output_cannot_replace_its_source(tmp_path):
    # GIVEN source bytes whose pathname is also requested as output
    source = tmp_path / "source.md"
    payload = b"# source\r\n"
    source.write_bytes(payload)
    assert source.is_file() is True, "alias validation needs an existing source file"

    # WHEN output targets that exact source
    conversion = run(source, source)

    # THEN the source is preserved and the normal alias refusal is returned
    assert (conversion, source.read_bytes(), list(tmp_path.iterdir())) == (
        (1, _refusal(source, "Markdown output aliases the source: %s" % source), ""),
        payload, [source],
    ), "pass-through must use the existing source-alias guard"


@pytest.mark.parametrize("link", ["hardlink_to", "symlink_to"], ids=["hardlink", "symlink"])
def test_text_linked_output_alias_preserves_source(tmp_path, link):
    # GIVEN an output alias sharing the source file's identity
    source, alias = tmp_path / "source.txt", tmp_path / "alias.md"
    source.write_bytes(b"raw source\r\n")
    getattr(alias, link)(source)
    assert source.samefile(alias) is True, "the output must alias the source inode"

    # WHEN the public service validates the linked destination
    conversion = run(source, alias)

    # THEN alias refusal preserves both names and creates no stage
    assert (conversion, source.read_bytes(), alias.read_bytes(),
            sorted(path.name for path in tmp_path.iterdir())) == (
        (1, _refusal(source, "Markdown output aliases the source: %s" % alias), ""),
        b"raw source\r\n", b"raw source\r\n", ["alias.md", "source.txt"],
    ), "source identity protection must include hardlinks and symlinks"


@pytest.mark.corpus
@pytest.mark.parametrize("name,sha256,size", [
    ("md/calamine-README.md", "97340d41949b5f9f813cae99027b5fdfb47cb9d8c918c2e455f54f7d31e0cbf0", 13296),
    ("md/pdfplumber-README.md", "14dfb8d559bc533be876402984e04ec3f5ac7f0a05e150103f2becdc967e6a2e", 42610),
    ("txt/nasa-giss-global-temperature-index.txt", "f2cda52e1dce11e0c94915ac67159f4f02ec5de0d754a548145d2854f8383418", 17039),
    ("txt/noaa-ghcn-daily-readme.txt", "3a71ae355252a6693a56a473ed7182b500ae7816c2cb2b2ce708c1e9ea9dcfde", 28567),
])
def test_licensed_text_fixture_is_passed_through_exactly(tmp_path, name, sha256, size):
    # GIVEN a licensed corpus source with the recorded identity from SOURCES.md
    source = Path(__file__).parent / "fixtures" / name
    payload = source.read_bytes()
    out = tmp_path / "output.md"
    assert (hashlib.sha256(payload).hexdigest(), len(payload)) == (sha256, size), (
        "licensed source identity must match its recorded provenance"
    )

    # WHEN the public API publishes the original corpus bytes
    conversion = run(source, out)

    # THEN syntax, aligned tables and full receipt are preserved without parsing
    assert conversion == (0, _success(source, str(out)), payload.decode("utf-8")), (
        "licensed text must return the complete source and pass-through receipt"
    )
    assert out.read_bytes() == payload, "licensed output bytes must equal the complete source"
