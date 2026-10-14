"""Check distribution identity, installation isolation and console validation failures."""

import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sysconfig
import tarfile
import zipfile
import types

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".github/workflows/verify_install.py"
SPEC = importlib.util.spec_from_file_location("verify_install", HELPER)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


@pytest.mark.parametrize("change", [
    {"implementation": "PyPy"}, {"version": "3.13.14"}, {"system": "Darwin"},
    {"machine": "aarch64"}, {"libc": ["musl", "1.2.5"]}, {"soabi": ""},
    {"gil_disabled": True}, {"pointer_bits": 32}, {"abi_flags": "t"},
    {"os_name": "debian"}, {"os_version": "24.04"}, {"soabi": "cpython-314-x86_64-linux-gnu"},
])
def test_expected_runtime_rejects_wrong_interpreter_platform_or_abi(change):
    # GIVEN the requested CPython/glibc cell with one incompatible observation
    identity = {"implementation": "CPython", "version": "3.13.15", "system": "Linux",
                "machine": "x86_64", "libc": ["glibc", "2.35"], "soabi": "cpython-313-x86_64-linux-gnu",
                "os_name": "ubuntu", "os_version": "22.04",
                "gil_disabled": False, "pointer_bits": 64, "abi_flags": ""}
    # WHEN parent or installed child identity is admitted
    # THEN the selected compatibility cell cannot be silently substituted
    with pytest.raises(ValueError, match="^Installed verification runtime differs from the selected cell$"):
        verification.check_runtime({**identity, **change}, "3.13.15", "linux-x64")


@pytest.mark.parametrize("version,target,system,machine,libc,soabi,os_name,os_version", [
    ("3.13.15", "linux-x64", "Linux", "x86_64", ["glibc", "2.35"], "cpython-313-x86_64-linux-gnu", "ubuntu", "22.04"),
    ("3.14.7", "macos-arm64", "Darwin", "arm64", ["", ""], "cpython-314-darwin", "Darwin", "15.8.1"),
    ("3.14.7", "windows-x64", "Windows", "AMD64", ["", ""], "cp314-win_amd64", "Windows Server 2025 Datacenter", "10.0.26100"),
])
def test_expected_runtime_accepts_the_exact_selected_cells(version, target, system, machine, libc, soabi, os_name, os_version):
    # GIVEN the normal-GIL CPython identity for a selected native runner
    identity = {"implementation": "CPython", "version": version, "system": system,
                "machine": machine, "libc": libc, "soabi": soabi, "gil_disabled": False,
                "os_name": os_name, "os_version": os_version,
                "pointer_bits": 64, "abi_flags": ""}
    # WHEN the identity is checked
    actual = verification.check_runtime(identity, version, target)
    # THEN all three representative cells are admitted exactly
    assert actual is None, "the observed interpreter must match its requested cell"


@pytest.mark.parametrize("change", [
    {"reason": "unrelated failure"}, {"file_ok": True}, {"receipt_schema": "brewdoc.receipt/1"},
    {"acquisition": {"refusal_stage": "connect", "counts": {"requests": 0}}},
    {"acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 1}}},
])
def test_js_prerequisite_receipt_rejects_wrong_reason_stage_or_requests(change):
    # GIVEN a superficially refused JS call with one incorrect contract field
    receipt = {"reason": "URL acquisition refused during prerequisites", "file_ok": False,
               "receipt_schema": "brewdoc.receipt/2",
               "acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
    # WHEN a missing-extra or Windows JS result is consumed
    # THEN another error cannot be accepted as the intended prerequisite refusal
    with pytest.raises(ValueError, match="^Unavailable JS prerequisite refusal changed$"):
        verification.check_js_refusal({**receipt, **change})


def test_full_verifier_rejects_missing_source_hash(tmp_path, monkeypatch):
    # GIVEN a full producer manifest that does not bind verifier behavior
    (tmp_path / "ci-package").mkdir()
    manifest = {"verification_scope": "full", "files": {}, "source": {}}
    (tmp_path / "ci-package/manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("unsealed verifier reached installation"))
    # WHEN full verification begins
    # THEN missing verifier provenance fails before any command
    with pytest.raises(ValueError, match="^Full installed verification requires the verifier source hash$"):
        verification.verify(tmp_path, tmp_path)


def test_full_verifier_rejects_changed_executed_verifier_before_installation(tmp_path, monkeypatch):
    # GIVEN a producer hash that differs from the executing verification script
    (tmp_path / "ci-package").mkdir()
    manifest = {"verification_scope": "full", "files": {}, "source": {verification.VERIFIER_SOURCE: "0" * 64}}
    (tmp_path / "ci-package/manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("verifier drift reached installation"))
    # WHEN full verification starts
    # THEN the actual executing verifier must retain producer identity
    with pytest.raises(ValueError, match="^Installed verifier source differs from the producer$"):
        verification.verify(tmp_path, tmp_path)


@pytest.mark.parametrize("change", ["archive", "manifest"])
def test_runtime_pair_rejects_independently_sealed_different_archives_or_manifest(tmp_path, change):
    # GIVEN smoke and compatibility pairs whose separate seals remain internally valid
    runtime = tmp_path / "ci-runtime"
    for folder, scope in ((tmp_path, "smoke"), (runtime, "full")):
        (folder / "dist").mkdir(parents=True)
        (folder / "ci-package").mkdir()
        for name in ("brewdoc.whl", "brewdoc.tar.gz"):
            (folder / "dist" / name).write_bytes(b"original")
        manifest = {"verification_scope": scope, "files": {"dist/" + name: {
            "size": 8, "sha256": verification.digest(folder / "dist" / name)}
            for name in ("brewdoc.whl", "brewdoc.tar.gz")}}
        (folder / "ci-package/manifest.json").write_text(json.dumps(manifest))
    selected = runtime / "dist/brewdoc.whl"
    replacements = {"archive": b"modified", "manifest": b"original"}
    selected.write_bytes(replacements[change])
    manifest_path = runtime / "ci-package/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    hashes = {"archive": verification.digest(selected), "manifest": "0" * 64}
    manifest["files"]["dist/brewdoc.whl"]["sha256"] = hashes[change]
    manifest_path.write_text(json.dumps(manifest))
    # WHEN both archive identities are admitted together
    # THEN separately valid but different package bytes or seals cannot claim one built pair
    with pytest.raises(ValueError, match="^Compatibility distributions differ from the smoke pair$"):
        verification.check_runtime_pair(runtime)


def test_installed_article_probe_checks_api_cli_success_and_preserved_refusal(tmp_path, monkeypatch):
    # GIVEN an isolated directory and the real entry point
    import os

    monkeypatch.chdir(tmp_path)
    console = Path(sysconfig.get_path("scripts")) / ("brewdoc.exe" if os.name == "nt" else "brewdoc")
    # WHEN the article consumer probe runs
    actual = verification.probe_article(str(console))
    # THEN API and all console consumers agree, and refusal preserves existing output
    assert ([row["exit"] for row in actual["cli"]], actual["refusal"]["reason"],
            (tmp_path / "article.md").read_bytes()) == (
        [0, 0, 1], "HTML article extraction refused: no eligible article or suitable main", b"existing output\n"), \
        "installed article mode must exercise success, output placement and refusal preservation"


def test_runtime_preparation_copies_the_built_pair_without_changing_root_smoke(tmp_path, monkeypatch):
    # GIVEN an already sealed smoke pair and locked producer inputs
    checkout, bundle = tmp_path / "checkout", tmp_path / "bundle"
    checkout.mkdir()
    (bundle / "dist").mkdir(parents=True)
    (bundle / "ci-package").mkdir()
    project = "[project]\nversion='0.3.3'\n[build-system]\nrequires=['hatchling==1.32.4']\n[tool.uv]\nbuild-constraint-dependencies=[]\n"
    (checkout / "pyproject.toml").write_text(project)
    for name in ("uv.lock", verification.VERIFIER_SOURCE, "benchmarks/corpus.json", "tests/fixtures/receipts.json"):
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sealed source")
    with zipfile.ZipFile(bundle / "dist/brewdoc.whl", "w") as archive:
        archive.writestr("brewdoc/__init__.py", "__all__=['run']\n")
        archive.writestr("brewdoc/epub.py", b"")
    with tarfile.open(bundle / "dist/brewdoc.tar.gz", "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("brewdoc/src/brewdoc/epub.py"))
    manifest = {"verification_scope": "smoke", "files": {"dist/" + path.name: {
        "size": path.stat().st_size, "sha256": verification.digest(path)}
        for path in verification.distributions(bundle / "dist")}}
    root_manifest = bundle / "ci-package/manifest.json"
    root_manifest.write_text(json.dumps(manifest))
    original = root_manifest.read_bytes()

    def command(arguments, cwd):
        """Write export constraints while substituting only external tool edges."""
        outputs = {"export": lambda: Path(arguments[-1]).write_bytes(b"locked requirements\n"),
                   "--version": lambda: None, "rev-parse": lambda: None}
        outputs[arguments[1]]()
        replies = {"export": b"", "--version": b"uv 0.12.21", "rev-parse": b"a" * 40}
        return subprocess.CompletedProcess(arguments, 0, replies[arguments[1]], b"")

    monkeypatch.setattr(verification, "command", command)
    # WHEN the full runtime bundle is prepared from the existing pair
    verification.prepare_runtime(checkout, bundle)
    # THEN full provenance binds the verifier and actual pair hashes match root smoke
    runtime = bundle / "ci-runtime"
    full = json.loads((runtime / "ci-package/manifest.json").read_text())
    assert (root_manifest.read_bytes(), full["verification_scope"], full["source"][verification.VERIFIER_SOURCE],
            verification.check_runtime_pair(runtime)) == (
        original, "full", verification.digest(checkout / verification.VERIFIER_SOURCE), None), \
        "preparing runtime compatibility must preserve the root smoke and seal unchanged distribution bytes"


@pytest.mark.parametrize("enabled,error,code,stderr,expected_error", [
    (False, "JS engine failed unexpectedly", 1, b"", "Unavailable JS runtime refusal changed"),
    (True, "JS guarded stack prerequisite unavailable", 1, b"", "Unavailable JS runtime refusal changed"),
    (False, "JS runtime unavailable; install brewdoc[render-js]", 0, b"", "CLI exit, receipt, Markdown placement or stderr changed"),
    (True, "JS installed native prerequisites unsupported", 1, b"warning", "CLI exit, receipt, Markdown placement or stderr changed"),
])
def test_optional_refusal_probe_rejects_wrong_direct_reason_exit_or_stderr(monkeypatch, enabled, error, code, stderr, expected_error):
    # GIVEN a Windows optional or missing-extra result with an incorrect direct/console verdict
    import brewdoc
    from brewdoc import htmljs

    monkeypatch.setattr(verification.sys, "platform", "win32")
    packages = {False: [], True: [types.SimpleNamespace(metadata={"Name": "quickjs-ng"})]}[enabled]
    monkeypatch.setattr(verification.importlib.metadata, "distributions", lambda: packages)
    monkeypatch.setattr(verification, "installed_url_runtime", lambda value: {"added_installed_bytes": 1})
    receipt = {"reason": "URL acquisition refused during prerequisites", "file_ok": False,
               "route": "html", "receipt_schema": "brewdoc.receipt/2",
               "acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
    stdout = (json.dumps(receipt, sort_keys=True) + verification.os.linesep).encode()
    monkeypatch.setattr(verification.subprocess, "run", lambda *args, **options:
                        subprocess.CompletedProcess([], code, stdout, stderr))

    def refuse():
        """Raise the observed installed runtime prerequisite error."""
        raise brewdoc.BrewdocError(error)

    monkeypatch.setattr(htmljs, "_check_runtime", refuse)
    # WHEN the complete optional-runtime probe consumes the observations
    # THEN an unrelated native error or incorrect CLI response cannot pass
    with pytest.raises(ValueError, match="^" + expected_error + "$"):
        verification.probe_js(enabled)


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
    ("\r\n", "sheet", "one\ntwo\n", b"one\ntwo\n"),
    ("\r\n", "html", "café\n", b"caf\xc3\xa9\n"),
    ("\r\n", "epub", "café\n", b"caf\xc3\xa9\n"),
])
def test_console_validation_preserves_native_receipt_newlines_and_unicode_routes(
        monkeypatch, newline, route, body, stdout_body):
    # GIVEN a native receipt newline and the route's actual stdout encoding
    monkeypatch.setattr(verification.os, "linesep", newline)
    receipt = {"route": route, "out": None}
    line = (json.dumps(receipt, sort_keys=True) + newline).encode("ascii")
    process = subprocess.CompletedProcess([], 0, line + stdout_body, b"")
    # WHEN the entire console response is checked
    actual = verification.check_cli(process, (0, receipt, body))
    # THEN native receipt newlines and literal UTF-8 body bytes remain distinct
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


@pytest.mark.parametrize("wheel_names,sdist_names", [
    ([], ["brewdoc-0.3.0/src/brewdoc/epub.py"]),
    (["brewdoc/epub.py"], []),
    ([], []),
])
def test_preparation_rejects_distributions_missing_the_epub_adapter(tmp_path, wheel_names, sdist_names):
    # GIVEN a distribution pair with one or both EPUB modules absent
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "a.whl", "w") as archive:
        archive.writestr("brewdoc/__init__.py", "__all__ = ['run']\n")
        for name in wheel_names:
            archive.writestr(name, b"")
    with tarfile.open(dist / "a.tar.gz", "w:gz") as archive:
        for name in sdist_names:
            archive.addfile(tarfile.TarInfo(name))
    (tmp_path / "pyproject.toml").write_text("[project]\nversion = '0.3.0'\n")
    # WHEN producer metadata is prepared
    # THEN missing source refuses before dependency export or installation
    with pytest.raises(ValueError, match="^Both distributions must include the EPUB adapter$"):
        verification.prepare(tmp_path, tmp_path)


@pytest.mark.parametrize("change", [
    {"source_sha256": "0" * 64}, {"markdown_sha256": "0" * 64},
    {"markdown_utf8_bytes": 4}, {"receipt": {"route": "epub", "out": None, "units": 2}},
])
def test_reviewed_epub_evidence_checks_source_utf8_and_the_full_receipt(tmp_path, change):
    # GIVEN independent accepted bytes and one selected evidence change
    source = tmp_path / "reviewed.epub"
    source.write_bytes(b"reviewed source")
    markdown = "café\n"
    receipt = {"route": "epub", "out": "/temporary/output.md", "units": 1}
    accepted = {"source_sha256": hashlib.sha256(b"reviewed source").hexdigest(),
                "markdown_sha256": hashlib.sha256(b"caf\xc3\xa9\n").hexdigest(),
                "markdown_utf8_bytes": 6, "receipt": {**receipt, "out": None},
                "review_reference": "benchmarks/README.md#epub-acceptance",
                "review_scope": "complete source and output review",
                "normalization": "receipt.out=null; Markdown bytes unchanged"}
    assert markdown.encode("utf-8") == b"caf\xc3\xa9\n", "the independent UTF-8 denominator must be six bytes"
    # WHEN the complete installed result is checked against accepted evidence
    original = verification.check_accepted_output(source, (0, receipt, markdown), accepted)
    # THEN only out normalization is allowed and all identity fields are checked
    assert original is None, "unchanged reviewed evidence must accept exact bytes and full receipt"
    changed = {**accepted, **change}
    with pytest.raises(ValueError, match="^Installed EPUB differs from reviewed output: reviewed.epub$"):
        verification.check_accepted_output(source, (0, receipt, markdown), changed)


@pytest.mark.parametrize("change", [
    {"review_reference": ""}, {"review_scope": ""}, {"normalization": "fold Unicode"},
])
def test_epub_evidence_requires_review_and_explicit_normalization(tmp_path, change):
    # GIVEN evidence whose review or normalization contract is absent
    accepted = dict.fromkeys(("source_sha256", "markdown_sha256", "markdown_utf8_bytes", "receipt"), None)
    accepted.update({"review_reference": "review", "review_scope": "complete",
                     "normalization": "receipt.out=null; Markdown bytes unchanged", **change})
    # WHEN evidence is consumed before reading an installed result
    # THEN unreviewed observations cannot become acceptance expectations
    with pytest.raises(ValueError, match="^EPUB accepted-output evidence is incomplete$"):
        verification.check_accepted_output(tmp_path / "absent.epub", (0, {}, ""), accepted)


def test_installation_selection_keeps_both_epub_fixtures_and_private_acceptance_evidence(tmp_path, monkeypatch):
    # GIVEN the two reviewed EPUB entries with separate receipt and acceptance data
    monkeypatch.setattr(verification, "SUFFIXES", {".epub"})
    fixture_root = tmp_path / "tests/fixtures"
    fixture_root.mkdir(parents=True)
    benchmark_root = tmp_path / "benchmarks"
    benchmark_root.mkdir()
    paths = sorted(verification.EPUB_INPUTS)
    documents = [{"path": path, "id": str(index), "size_bytes": index + 1, "features": []}
                 for index, path in enumerate(paths)]
    receipts = {Path(path).relative_to("tests/fixtures").as_posix(): {
        "rc": 0, "route": "epub", "accepted_output": {"review_scope": "accepted"}} for path in paths}
    (benchmark_root / "corpus.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")
    (fixture_root / "receipts.json").write_text(json.dumps(receipts), encoding="utf-8")
    # WHEN installation smoke inputs are selected
    actual = verification.select_inputs(tmp_path)
    # THEN both source identities and the optional evidence object reach the installed probe
    expected = [(document, receipts[Path(document["path"]).relative_to("tests/fixtures").as_posix()])
                for document in documents]
    assert actual == expected, "both EPUB fixtures must retain acceptance evidence without adding it to public receipts"


@pytest.mark.corpus
def test_epub_probe_accepts_the_real_wrapper_full_tally_and_reviewed_bytes(tmp_path, monkeypatch):
    # GIVEN a reviewed minimal publication and the actual public wrapper's full tally
    import brewdoc

    checkout = HELPER.parents[2]
    source = checkout / "tests/fixtures/epub/epubcheck-minimal.epub"
    path = tmp_path / source.name
    shutil.copyfile(source, path)
    snapshot = json.loads((checkout / "tests/fixtures/receipts.json").read_text(encoding="utf-8"))
    accepted = snapshot["epub/epubcheck-minimal.epub"]["accepted_output"]
    expected_tally = {"pages": 0, "sheets": 0, "chapters": 1, "slides": 0,
                      "tables": 0, "text_regions": 2, "columns_split": 0,
                      "broken_ligature_words": 0, "dropped": {
                          "control_chars": 0, "soft_hyphens": 0, "nbsp": 0, "pua_glyphs": 0,
                          "cid_survivors": 0, "ligatures": 0, "running_heads": 0,
                          "page_numbers": 0, "non_ascii_replaced": 0}}
    markdown, tally = brewdoc.render_epub(path)
    assert tally == expected_tally, "the real EPUB wrapper must retain all four shared unit counters"
    assert hashlib.sha256(markdown.encode("utf-8")).hexdigest() == accepted["markdown_sha256"], (
        "the real wrapper must match independently reviewed minimal EPUB bytes")
    monkeypatch.chdir(tmp_path)
    console = str(Path(sysconfig.get_path("scripts")) / ("brewdoc" + sysconfig.get_config_var("EXE")))
    # WHEN the real API, wrapper and console pass through the package probe
    actual = verification.probe_epub(path, brewdoc.run(path), accepted, console)
    # THEN the verifier accepts the complete tally and all three console output modes
    assert (actual["wrapper_tally"], len(actual["cli"]), actual["markdown_utf8_bytes"]) == (
        expected_tally, 3, 1930), "strict package verification must accept the real wrapper without dropping slides"
