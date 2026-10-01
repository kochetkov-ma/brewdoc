"""Check distribution identity, installation isolation and console validation failures."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".github/workflows/verify_install.py"
SPEC = importlib.util.spec_from_file_location("verify_install", HELPER)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


def test_subprocess_environment_keeps_cache_but_removes_source_and_interpreter_overrides(monkeypatch):
    # GIVEN source and virtual-environment overrides alongside an intended uv cache
    monkeypatch.setattr(verification.os, "environ", {
        "PATH": "/bin", "UV_CACHE_DIR": "/cache", "PYTHONPATH": "/source",
        "PYTHONHOME": "/source", "VIRTUAL_ENV": "/source", "UV_PYTHON": "/source",
        "UV_PROJECT_ENVIRONMENT": "/source",
    })
    # WHEN installation subprocess environment is derived
    actual = verification.clean_environment()
    # THEN source selection is removed without disabling the package cache
    assert actual == {"PATH": "/bin", "UV_CACHE_DIR": "/cache"}, (
        "independent installations must preserve caching but remove source environment selection")


@pytest.mark.parametrize("names", [
    ("a.whl",), ("a.tar.gz",), ("a.whl", "b.whl", "a.tar.gz"),
    ("a.whl", "a.tar.gz", "credentials.txt"),
])
def test_distribution_pair_refuses_missing_duplicate_or_extra_inputs(tmp_path, names):
    # GIVEN an incomplete or ambiguous publishing directory
    for name in names:
        (tmp_path / name).write_bytes(b"test")
    # WHEN the distribution inputs are selected
    # THEN ambiguity fails before any installation or publication
    with pytest.raises(ValueError, match="^dist must contain exactly one wheel and one .tar.gz sdist$"):
        verification.distributions(tmp_path)


def test_distribution_pair_returns_wheel_then_sdist(tmp_path):
    # GIVEN one wheel and one source archive
    wheel, sdist = tmp_path / "a.whl", tmp_path / "a.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    # WHEN the pair is selected
    actual = verification.distributions(tmp_path)
    # THEN each independent installer receives its intended format
    assert actual == (wheel, sdist), "the two installation consumers must keep their formats"


def test_manifest_accepts_only_the_original_distribution_bytes(tmp_path):
    # GIVEN a recorded artifact and its exact size and hash
    archive = tmp_path / "package.whl"
    archive.write_bytes(b"original")
    manifest = {"files": {archive.name: {"size": 8, "sha256": verification.digest(archive)}}}
    # WHEN the unchanged input is verified
    actual = verification.verify_manifest(tmp_path, manifest)
    # THEN identity succeeds without modifying the archive
    assert (actual, archive.read_bytes()) == (None, b"original"), "verification must be read-only"


def test_manifest_rejects_same_size_artifact_tampering(tmp_path):
    # GIVEN an artifact replaced after its manifest was recorded
    archive = tmp_path / "package.whl"
    archive.write_bytes(b"original")
    manifest = {"files": {archive.name: {"size": 8, "sha256": verification.digest(archive)}}}
    archive.write_bytes(b"modified")
    # WHEN the replacement is verified
    # THEN matching byte size cannot hide different content
    with pytest.raises(ValueError, match="^Artifact bytes changed: package.whl$"):
        verification.verify_manifest(tmp_path, manifest)


def test_manifest_rejects_a_path_outside_the_downloaded_bundle(tmp_path):
    # GIVEN a valid file referenced through a parent traversal
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"original")
    manifest = {"files": {"../outside.whl": {"size": 8, "sha256": verification.digest(outside)}}}
    # WHEN the manifest is consumed
    # THEN only files inside the downloaded artifact can be installed
    with pytest.raises(ValueError, match="^Artifact file escapes or is missing: ../outside.whl$"):
        verification.verify_manifest(bundle, manifest)


@pytest.mark.parametrize("module,search,editable", [
    ("checkout/src/brewdoc/__init__.py", [], False),
    ("environment/lib/brewdoc/__init__.py", ["checkout/src"], False),
    ("environment/lib/brewdoc/__init__.py", [], True),
])
def test_installed_origin_rejects_source_paths_and_editable_metadata(tmp_path, module, search, editable):
    # GIVEN an installation contaminated by source imports or editable state
    paths = [str(tmp_path / item) for item in search]
    # WHEN its origin is checked
    # THEN the source checkout cannot substitute for installation proof
    with pytest.raises(ValueError, match="^Installed package leaked checkout or editable state$"):
        verification.check_origin(tmp_path / module, tmp_path / "environment", tmp_path / "checkout",
                                  paths, {"dir_info": {"editable": editable}})


def test_installed_origin_accepts_a_separate_noneditable_environment(tmp_path):
    # GIVEN a module inside an independent environment with no source search path
    prefix, checkout = tmp_path / "environment", tmp_path / "checkout"
    # WHEN its noneditable metadata and origin are checked
    actual = verification.check_origin(prefix / "lib/brewdoc/__init__.py", prefix, checkout, [], {})
    # THEN origin proof succeeds without importing the checkout
    assert actual is None, "a valid independent installed origin must be accepted"


@pytest.mark.parametrize("newline,route,body,stdout_body", [
    ("\n", "sheet", "one\ntwo\n", b"one\ntwo\n"),
    ("\r\n", "sheet", "one\ntwo\n", b"one\r\ntwo\r\n"),
    ("\r\n", "html", "café\n", b"caf\xc3\xa9\n"),
])
def test_console_validation_preserves_native_receipt_newlines_and_html_utf8(
        monkeypatch, newline, route, body, stdout_body):
    # GIVEN a native receipt newline and the route's actual stdout encoding
    monkeypatch.setattr(verification.os, "linesep", newline)
    receipt = {"route": route, "out": None}
    line = (json.dumps(receipt, sort_keys=True) + newline).encode("ascii")
    process = subprocess.CompletedProcess([], 0, line + stdout_body, b"")
    # WHEN the entire console response is checked
    actual = verification.check_cli(process, (0, receipt, body))
    # THEN Windows ASCII translation and direct HTML bytes remain distinct
    assert actual == {"exit": 0, "stdout": (line + stdout_body).decode("utf-8"), "stderr": ""}, (
        "console validation must retain the platform and route byte contract")


@pytest.mark.parametrize("code,stdout,stderr", [
    (1, b'{"out": null, "route": "sheet"}\nbody\n', b""),
    (0, b'{"out": null, "route": "sheet"}\n', b""),
    (0, b'{"out": null, "route": "sheet"}\nbody\n', b"warning"),
])
def test_console_validation_rejects_exit_body_or_stderr_drift(monkeypatch, code, stdout, stderr):
    # GIVEN a response with one changed console contract
    monkeypatch.setattr(verification.os, "linesep", "\n")
    process = subprocess.CompletedProcess([], code, stdout, stderr)
    # WHEN it is compared against the complete successful response
    # THEN a passing API result cannot mask a broken console consumer
    with pytest.raises(ValueError, match="^CLI exit, receipt, Markdown placement or stderr changed$"):
        verification.check_cli(process, (0, {"route": "sheet", "out": None}, "body\n"))


def test_console_output_mode_rejects_markdown_on_stdout(tmp_path, monkeypatch):
    # GIVEN correct output bytes with an unintended duplicate stdout body
    monkeypatch.setattr(verification.os, "linesep", "\n")
    output = tmp_path / "written.md"
    output.write_bytes(b"body\n")
    receipt = {"route": "sheet", "out": str(output)}
    stdout = (json.dumps(receipt, sort_keys=True) + "\nbody\n").encode("ascii")
    process = subprocess.CompletedProcess([], 0, stdout, b"")
    # WHEN --out placement is validated
    # THEN stdout must contain only its receipt line
    with pytest.raises(ValueError, match="^CLI exit, receipt, Markdown placement or stderr changed$"):
        verification.check_cli(process, (0, receipt, "body\n"), output)


def test_console_output_mode_rejects_altered_written_bytes(tmp_path, monkeypatch):
    # GIVEN a successful receipt and a wrong Markdown file
    monkeypatch.setattr(verification.os, "linesep", "\n")
    output = tmp_path / "written.md"
    output.write_bytes(b"wrong\n")
    receipt = {"route": "sheet", "out": str(output)}
    process = subprocess.CompletedProcess([], 0, (json.dumps(receipt, sort_keys=True) + "\n").encode(), b"")
    # WHEN the output file is checked
    # THEN file corruption cannot pass solely because stdout is correct
    with pytest.raises(ValueError, match="^CLI output bytes changed$"):
        verification.check_cli(process, (0, receipt, "body\n"), output)


def test_private_archive_content_is_rejected_before_export(tmp_path):
    # GIVEN a wheel containing private task data beside its package
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "a.whl", "w") as archive:
        archive.writestr("brewdoc/__init__.py", "__all__ = ['run']\n")
        archive.writestr(".codex/private.txt", "synthetic sentinel")
    with tarfile.open(dist / "a.tar.gz", "w:gz"):
        pass
    # WHEN producer metadata is prepared
    (tmp_path / "pyproject.toml").write_text("[project]\nversion = '0.2.0'\n")
    # THEN private content fails before any dependency export or installation
    with pytest.raises(ValueError, match="^Distribution includes private or local project files$"):
        verification.prepare(tmp_path, tmp_path)
