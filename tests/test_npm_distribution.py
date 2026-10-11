"""Check npm staging identities and rejected native link layouts."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def npm_builder():
    """Load the release helper without importing product modules."""
    path = Path(__file__).parents[1] / ".github/workflows/build_npm.py"
    spec = importlib.util.spec_from_file_location("build_npm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("suffix,expected", [(["--self-check"],
    'C:\\Windows\\System32\\cmd.exe /d /s /c ""C:\\brewdoc npm consumer Unicode-é\\global\\brewdoc.cmd" --self-check"'),
    (["C:\\Unicode café\\sample.epub"],
     'C:\\Windows\\System32\\cmd.exe /d /s /c ""C:\\brewdoc npm consumer Unicode-é\\global\\brewdoc.cmd" "C:\\Unicode café\\sample.epub""')])
def test_windows_installed_cmd_uses_one_raw_command_line_without_second_crt_quote_serialization(tmp_path, monkeypatch, npm_builder, suffix, expected):
    # GIVEN an installed npm shim under a real smoke path shape with Unicode and spaces
    calls = []
    monkeypatch.setattr(npm_builder, "os", SimpleNamespace(name="nt", environ={"COMSPEC": "C:\\Windows\\System32\\cmd.exe"}))

    def launch(command, **options):
        """Retain the actual CreateProcess argument form at the subprocess I/O edge."""
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0, b"exact stdout", b"")

    monkeypatch.setattr(npm_builder.subprocess, "run", launch)
    assert len(calls) == 0, "no command interpreter is started before the shim request"
    # WHEN the normal installed shim receives self-check or one Unicode document path
    result = npm_builder.cli_result(["C:\\brewdoc npm consumer Unicode-é\\global\\brewdoc.cmd"], suffix, tmp_path, {}, 45)
    # THEN cmd receives literal outer quotes once and exact streams without shell fallback
    assert calls == [(expected, {"cwd": tmp_path, "env": {}, "capture_output": True, "timeout": 45})], (
        "a prequoted CMD payload must never be serialized again as a CRT argv element")
    assert (result.returncode, result.stdout, result.stderr) == (0, b"exact stdout", b""), "shim result streams remain untouched"


@pytest.mark.parametrize("platform_name,arguments", [("posix", ["/tmp/brewdoc/bin/brewdoc"]),
                                                     ("nt", ["C:\\node\\node.exe", "C:\\npm\\npx-cli.js"])])
def test_direct_node_and_posix_cli_preserve_argument_lists_without_command_shell(tmp_path, monkeypatch, npm_builder, platform_name, arguments):
    # GIVEN a direct executable invocation with no command shim
    calls = []
    monkeypatch.setattr(npm_builder, "os", SimpleNamespace(name=platform_name, environ={}))
    monkeypatch.setattr(npm_builder.subprocess, "run", lambda command, **options:
                        calls.append((command, options)) or subprocess.CompletedProcess(command, 0, b"direct bytes", b""))
    assert calls == [], "direct invocation starts without a command shell"
    # WHEN the CLI is exercised through its original executable argument list
    npm_builder.cli_result(arguments, ["--self-check"], tmp_path, {}, 30)
    # THEN original argv and subprocess boundaries remain exact
    assert calls == [([*arguments, "--self-check"], {"cwd": tmp_path, "env": {}, "capture_output": True, "timeout": 30})], (
        "only Windows .cmd requires command-interpreter parsing")


def test_public_self_check_failure_reports_exit_and_only_bounded_actual_streams(tmp_path, monkeypatch, npm_builder):
    # GIVEN isolated public self-check output with a failing exit and oversized streams
    monkeypatch.setattr(npm_builder, "cli_result", lambda *args: subprocess.CompletedProcess([], 2, b"o" * 600, b"e" * 600))
    assert len(b"o" * 600) == 600, "the probe fixture exceeds the diagnostic byte limit"
    # WHEN strict self-check admission refuses the actual response
    with pytest.raises(ValueError) as failure:
        npm_builder.consumer_probe(["brewdoc.cmd"], tmp_path, {}, {}, "brewdoc-win32-x64")
    # THEN the refusal exposes useful bounded streams without argv or environment values
    assert str(failure.value) == ("npm consumer self-check changed (exit 2; stdout=" + repr(b"o" * 512)
                                  + "; stderr=" + repr(b"e" * 512) + ")"), "diagnostics contain exactly the first 512 bytes per stream"


def npm_publish_response(tmp_path, monkeypatch, npm_builder, stdout, *, returncode=1, stderr=b"CANARY_STDERR_SECRET"):
    """Replace the npm process boundary with bounded synthetic publication output."""
    environment = {"NODE_AUTH_TOKEN": "CANARY_ENV_SECRET"}
    calls = []

    def process(arguments, **options):
        """Retain subprocess arguments while returning the authored failure streams."""
        calls.append((arguments, options))
        return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)

    monkeypatch.setattr(npm_builder, "npm_command", lambda arguments, npx=False: ["node", "npm", *arguments])
    monkeypatch.setattr(npm_builder.subprocess, "run", process)
    return environment, calls


@pytest.mark.parametrize("code,cause", [("E403", "registry-forbidden"), ("EOTP", "one-time-password-required"),
                                      ("EPUBLISHCONFLICT", "version-conflict")], ids=["forbidden", "otp", "version-conflict"])
def test_publish_failure_exposes_only_allowlisted_code_and_static_cause(tmp_path, monkeypatch, capsys, npm_builder, code, cause):
    # GIVEN a complete npm error object with a known code and secret-bearing nested fields
    payload = json.dumps({"error": {"code": code, "summary": "CANARY_SUMMARY_SECRET", "detail": "CANARY_DETAIL_SECRET",
        "authUrl": "https://npm.invalid/auth?token=CANARY_AUTH_SECRET", "doneUrl": "CANARY_DONE_SECRET",
        "extras": {"token": "CANARY_NESTED_SECRET"}}}).encode()
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, payload)
    assert json.loads(payload)["error"]["code"] == code, "the diagnostic premise is a complete known-code error"
    # WHEN the existing publisher receives the failed authenticated npm process
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN only the fixed code/category and actual exit status are observable
    assert str(failure.value) == f"npm publish failed (1); code={code}; cause={cause}; authenticated output was suppressed", "authenticated fields must never enter diagnostic text"
    assert len(calls) == 1, "a publication failure cannot trigger an automatic retry"
    assert capsys.readouterr() == ("", ""), "publication diagnostics never print authenticated process streams"


@pytest.mark.parametrize("stdout,stderr", [
    (b'{"error":{"code":"CANARY_CODE_SECRET","summary":"CANARY_SUMMARY_SECRET"}}', b"CANARY_STDERR_SECRET"),
    (b'{"error":{"code":"E403","detail":"CANARY_TRUNCATED_SECRET"}', b"CANARY_STDERR_SECRET"),
    (json.dumps({"error": {"code": "E403", "detail": "CANARY_OVERSIZE_SECRET" + "x" * 65536}}).encode(), b"CANARY_STDERR_SECRET"),
    (b'{"error":{"code":"E403","code":"EOTP","detail":"CANARY_DUPLICATE_SECRET"}}', b"CANARY_STDERR_SECRET"),
    (b'[{"error":{"code":"E403","detail":"CANARY_ARRAY_SECRET"}}]', b"CANARY_STDERR_SECRET"),
    (b"", b'{"error":{"code":"E403","detail":"CANARY_STDERR_SECRET"}}'),
    (b'{"error":{"code":"E403","summary":"provenance generation failed; one-time password required CANARY_SUMMARY_SECRET","detail":"CANARY_DETAIL_SECRET"}}', b"CANARY_STDERR_SECRET"),
    (b'{"error":{"code":["CANARY_CODE_SECRET"],"summary":"CANARY_SUMMARY_SECRET"}}', b"CANARY_STDERR_SECRET"),
], ids=["unknown-code", "malformed", "oversized", "conflicting-keys", "wrong-shape", "stderr-only", "conflicting-causes", "wrong-code-type"])
def test_publish_failure_uses_generic_diagnostics_for_untrusted_error_output(tmp_path, monkeypatch, capsys, npm_builder, stdout, stderr):
    # GIVEN untrusted or unavailable JSON error evidence and a concrete failing exit
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, stdout, returncode=7, stderr=stderr)
    assert calls == [], "no npm process has run before the failure observation"
    # WHEN the publisher observes unknown, malformed, oversized or ambiguous evidence
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN static fallback preserves the actual exit without exposing untrusted fields
    assert str(failure.value) == "npm publish failed (7); code=UNKNOWN; cause=unclassified; authenticated output was suppressed", "invalid diagnostic evidence must fail closed"
    assert len(calls) == 1, "invalid diagnostic evidence never retries publication"
    assert capsys.readouterr() == ("", ""), "untrusted publication output must remain unprinted"


@pytest.mark.parametrize("code,expected_code", [("E403", "E403"), ("CANARY_CODE_SECRET", "UNKNOWN")], ids=["known-code", "unknown-code"])
def test_publish_failure_classifies_fixed_provenance_phrase_without_exposing_authenticated_detail(tmp_path, monkeypatch, capsys, npm_builder, code, expected_code):
    # GIVEN a complete error with one approved provenance phrase and unrelated secrets
    payload = json.dumps({"error": {"code": code, "summary": "generate provenance CANARY_SUMMARY_SECRET",
                                  "detail": "CANARY_DETAIL_SECRET", "authUrl": "CANARY_AUTH_SECRET"}}).encode()
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, payload)
    assert calls == [], "classification starts before any publication response"
    # WHEN failed npm publication returns this single finite phrase category
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN classification emits only a finite code and category without quoting diagnostic prose
    assert str(failure.value) == f"npm publish failed (1); code={expected_code}; cause=provenance-error; authenticated output was suppressed", "known phrases never authorize echoing unknown codes or raw detail"
    assert capsys.readouterr() == ("", ""), "classified provenance errors remain unprinted"


@pytest.mark.parametrize("detail", ["provenance generation failed CANARY_DETAIL_SECRET",
                                  "Package name too similar to existing package CANARY_DETAIL_SECRET"], ids=["provenance", "name-similarity"])
def test_e403_generic_summary_never_classifies_specific_reason_from_detail(tmp_path, monkeypatch, capsys, npm_builder, detail):
    # GIVEN an E403 generic summary whose detail contains a specific reason and secret
    payload = json.dumps({"error": {"code": "E403", "summary": "Forbidden", "detail": detail,
                                  "extras": {"authUrl": "CANARY_AUTH_SECRET"}}}).encode()
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, payload)
    assert json.loads(payload)["error"]["summary"] == "Forbidden", "the server summary states no specific refusal reason"
    # WHEN failed publication classifies the explicit server summary only
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN detail cannot replace the generic refusal or enter its observable message
    assert str(failure.value) == "npm publish failed (1); code=E403; cause=registry-forbidden; authenticated output was suppressed", "generic summary must not inherit diagnostic boilerplate or detail claims"
    assert len(calls) == 1, "summary-only refusal classification does not retry publication"
    assert capsys.readouterr() == ("", ""), "authenticated detail remains unprinted"


@pytest.mark.parametrize("code,summary,expected_code,cause", [
    ("E403", "Package name too similar to existing package", "E403", "package-name-refused"),
    ("E403", "Please verify your email before publishing", "E403", "email-verification-required"),
    ("E403", "You do not have permission to publish this package", "E403", "publish-permission-refused"),
    ("E403", "Publication blocked by a security policy", "E403", "security-policy-refused"),
    ("E403", "Publication not allowed from your IP address", "E403", "ip-restriction-mentioned"),
    ("E401", "Package name too similar to existing package", "E401", "authentication-rejected"),
    ("E403", "Package name too similar; verify your email", "UNKNOWN", "unclassified"),
], ids=["name-similarity", "email", "publish-permission", "explicit-security-policy", "explicit-ip", "non-e403", "conflicting-summary"])
def test_explicit_registry_summary_emits_only_fixed_e403_refusal_categories(tmp_path, monkeypatch, capsys, npm_builder, code, summary, expected_code, cause):
    # GIVEN one explicit server summary and secret-bearing detail and extras
    payload = json.dumps({"error": {"code": code, "summary": summary,
        "detail": "Forbidden by security policy CANARY_DETAIL_SECRET",
        "extras": {"token": "CANARY_TOKEN_SECRET", "authUrl": "CANARY_AUTH_SECRET"},
        "doneUrl": "CANARY_DONE_SECRET"}}).encode()
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, payload)
    assert json.loads(payload)["error"]["summary"] == summary, "only the explicit summary supplies the refusal premise"
    # WHEN publication classifies the bounded complete JSON error
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN finite E403 refinement cannot disclose prose, change other codes or resolve ambiguity
    assert str(failure.value) == f"npm publish failed (1); code={expected_code}; cause={cause}; authenticated output was suppressed", "only unambiguous explicit E403 summary categories may refine the failure"
    assert len(calls) == 1, "registry refusal diagnostics do not retry publication"
    assert capsys.readouterr() == ("", ""), "authenticated registry strings remain unprinted"


def test_publish_failure_preserves_generic_refusal_when_json_decoder_exceeds_recursion(tmp_path, monkeypatch, capsys, npm_builder):
    # GIVEN an isolated parser-depth failure whose diagnostic contains a synthetic secret
    payload = b'{"error":{"code":"E403"}}'
    environment, calls = npm_publish_response(tmp_path, monkeypatch, npm_builder, payload)

    def parser_failure(*arguments, **options):
        """Model the standard JSON decoder's nesting failure without shared recursion state."""
        raise RecursionError("CANARY_PARSER_SECRET")

    monkeypatch.setattr(npm_builder.json, "loads", parser_failure)
    assert calls == [], "the decoder fault is installed before npm executes"
    # WHEN failed publication reaches the JSON decoding boundary
    with pytest.raises(RuntimeError) as failure:
        npm_builder.run_npm(["publish", "fixture.tgz", "--json"], tmp_path, environment)
    # THEN decoder failure retains the publication exit and exposes no parser text
    assert str(failure.value) == "npm publish failed (1); code=UNKNOWN; cause=unclassified; authenticated output was suppressed", "JSON nesting failure must not leak or replace the publication error"
    assert capsys.readouterr() == ("", ""), "parser diagnostics remain unprinted"


def test_publish_requests_json_only_for_missing_packages_and_preserves_platform_first_order(tmp_path, monkeypatch, npm_builder):
    # GIVEN exact package admission and an already public first platform package
    names = ["linux.tgz", "windows.tgz", "macos.tgz", "main.tgz"]
    records = [{"filename": name} for name in names]
    manifest = {"packages": records}
    answers = iter([True, False, False, False, True, True, True, True])
    reads, calls = [], []
    monkeypatch.setattr(npm_builder, "verify_release", lambda unused: manifest)
    monkeypatch.setattr(npm_builder, "check_tools", lambda *unused: None)
    monkeypatch.setattr(npm_builder, "os", SimpleNamespace(environ={"NODE_AUTH_TOKEN": "CANARY_ENV_SECRET"}))
    monkeypatch.setattr(npm_builder, "public_record", lambda record: reads.append(record["filename"]) or next(answers))
    monkeypatch.setattr(npm_builder, "run_npm", lambda arguments, cwd, environment: calls.append((arguments, cwd, environment)))
    assert calls == [], "existing public byte admission precedes every publication attempt"
    # WHEN publication skips exact existing bytes and continues the missing packages
    actual = npm_builder.publish(tmp_path)
    # THEN the JSON flag preserves scripts, access, provenance and platform-first ordering
    expected = [(["publish", str((tmp_path / name).resolve()), "--ignore-scripts", "--access", "public", "--provenance", "--json"],
                 tmp_path, {"NODE_AUTH_TOKEN": "CANARY_ENV_SECRET"}) for name in names[1:]]
    assert (actual, calls) == (None, expected), "only missing packages publish, with the main package last and no changed permissions"
    assert reads == names + names, "every package is checked before writes and again after its publication slot"


@pytest.mark.parametrize("native_mode,tar_mode", [
    pytest.param(0o755, 0o755, marks=pytest.mark.skipif(os.name == "nt", reason="POSIX executable modes"), id="posix"),
    pytest.param(0o666, 0o644, marks=pytest.mark.skipif(os.name != "nt", reason="Windows file modes"), id="windows"),
])
def test_internal_link_materializes_exact_terminal_bytes_and_mode(tmp_path, npm_builder, native_mode, tar_mode):
    # GIVEN an admitted native library and two relative aliases.
    native = tmp_path / "native"
    native.mkdir()
    library = native / "lib.so.1"
    library.write_bytes(b"native bytes")
    library.chmod(native_mode)
    (native / "lib.so").symlink_to("lib.so.1")
    (native / "alias.so").symlink_to("lib.so")
    sha256 = hashlib.sha256(b"native bytes").hexdigest()
    inventory = {
        "brewdoc/lib.so.1": {"kind": "file", "bytes": 12, "sha256": sha256, "mode": native_mode},
        "brewdoc/lib.so": {"kind": "symlink", "target": "lib.so.1", "mode": 0o777},
        "brewdoc/alias.so": {"kind": "symlink", "target": "lib.so", "mode": 0o777},
    }
    assert library.read_bytes() == b"native bytes", "Native precondition changed"
    assert library.stat().st_mode & 0o777 == native_mode, "Host file-mode precondition changed"

    # WHEN npm staging materializes the admitted aliases.
    output = tmp_path / "binary"
    mappings = npm_builder.stage_native(native, inventory, output)

    # THEN aliases contain the exact terminal bytes and executable mode.
    assert mappings == {
        "binary/alias.so": {"link": "lib.so", "terminal": "brewdoc/lib.so.1", "sha256": sha256, "mode": native_mode, "packed_mode": tar_mode},
        "binary/lib.so": {"link": "lib.so.1", "terminal": "brewdoc/lib.so.1", "sha256": sha256, "mode": native_mode, "packed_mode": tar_mode},
    }, "Link provenance must bind terminal identities"
    assert (output / "alias.so").read_bytes() == b"native bytes", "Alias bytes changed"
    assert (output / "alias.so").stat().st_mode & 0o777 == native_mode, "Alias mode changed"
    assert (output / "alias.so").is_symlink() is False, "npm staging retained a dropped symlink"


@pytest.mark.parametrize("target", ["../outside", "/tmp/outside", "loop"], ids=["escape", "absolute", "cycle"])
def test_link_escape_or_cycle_refuses_before_staging(tmp_path, npm_builder, target):
    # GIVEN a native link with an unsafe target.
    native = tmp_path / "native"
    native.mkdir()
    (native / "loop").symlink_to(target)
    inventory = {"brewdoc/loop": {"kind": "symlink", "target": target, "mode": 0o777}}
    assert (native / "loop").is_symlink() is True, "Unsafe-link precondition changed"

    # WHEN the native layout is admitted for npm staging.
    output = tmp_path / "binary"
    with pytest.raises(ValueError, match="link|path|cycle"):
        npm_builder.stage_native(native, inventory, output)

    # THEN no staging directory exists.
    assert output.exists() is False, "Unsafe layout wrote staging files"


def test_unexpected_native_file_refuses_before_staging(tmp_path, npm_builder):
    # GIVEN a file absent from the admitted native inventory.
    native = tmp_path / "native"
    native.mkdir()
    (native / "extra").write_bytes(b"unlisted")
    assert sorted(path.name for path in native.iterdir()) == ["extra"], "Unexpected file precondition changed"

    # WHEN staging validates the full source inventory.
    output = tmp_path / "binary"
    with pytest.raises(ValueError, match="inventory"):
        npm_builder.stage_native(native, {}, output)

    # THEN the unexplained file is never copied.
    assert output.exists() is False, "Unlisted native file reached npm staging"


def test_package_versions_and_platform_filters_are_exact(npm_builder):
    # GIVEN one release version shared by all generated packages.
    version = "0.3.1"
    assert npm_builder.PACKAGES["linux-x64"] == "brewdoc-linux-x64-glibc", "Selected Linux package name changed"

    # WHEN main and Linux metadata are generated.
    main = npm_builder.package_metadata(version)
    linux = npm_builder.package_metadata(version, "linux-x64")

    # THEN optional dependencies and platform admission remain exact.
    assert main["optionalDependencies"] == {
        "brewdoc-linux-x64-glibc": version,
        "brewdoc-win32-x64": version,
        "brewdoc-darwin-arm64": version,
    }, "Platform dependencies must share one exact version"
    assert {key: linux[key] for key in ("name", "version", "os", "cpu", "libc")} == {
        "name": "brewdoc-linux-x64-glibc", "version": version,
        "os": ["linux"], "cpu": ["x64"], "libc": ["glibc"],
    }, "Linux platform filters changed"


def test_pinned_npm_pack_object_result_is_admitted(tmp_path, monkeypatch, npm_builder):
    # GIVEN npm 12's package-name-keyed JSON and its exact archive bytes.
    folder, output = tmp_path / "package", tmp_path / "output"
    folder.mkdir()
    output.mkdir()
    (folder / "package.json").write_text('{"name":"brewdoc","version":"0.3.1"}\n')
    archive = output / "brewdoc-0.3.1.tgz"
    body = (folder / "package.json").read_bytes()
    with tarfile.open(archive, "w:gz") as stream:
        member = tarfile.TarInfo("package/package.json")
        member.size, member.mode = len(body), 0o644
        stream.addfile(member, io.BytesIO(body))
    packed = {"brewdoc": {"filename": archive.name, "integrity": npm_builder.integrity(archive.read_bytes())}}
    monkeypatch.setattr(npm_builder, "check_tools", lambda *arguments: None)
    monkeypatch.setattr(npm_builder, "run_npm", lambda *arguments: subprocess.CompletedProcess([], 0, json.dumps(packed).encode(), b""))
    assert list(packed) == ["brewdoc"], "Pinned npm JSON precondition changed"

    # WHEN packing admits the returned archive rather than assuming a legacy list.
    record = npm_builder.pack_directory(folder, output, {})

    # THEN the exact package-named result binds the actual tarball.
    assert {key: record[key] for key in ("filename", "sha256", "integrity")} == {
        "filename": "brewdoc-0.3.1.tgz", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "integrity": packed["brewdoc"]["integrity"],
    }, "npm 12 pack result was not bound to its archive"


@pytest.mark.parametrize("mode,executable,target,expected", [(0o666, False, None, 0o644), (0o666, True, None, 0o755),
    (0o755, False, None, 0o755), (0o777, False, "windows-x64", 0o644), (0o777, True, "windows-x64", 0o755),
    (0o755, False, "linux-x64", 0o755), (0o755, False, "macos-arm64", 0o755)])
def test_portable_tar_modes_preserve_executable_bits(npm_builder, mode, executable, target, expected):
    # GIVEN Windows writable-file or native executable source modes.
    assert mode & 0o600 == 0o600, "Source owner permissions precondition changed"

    # WHEN pinned npm creates its portable tar headers.
    observed = npm_builder.packed_mode(mode, executable, target=target)

    # THEN group writes are removed and bin executables remain executable.
    assert observed == expected, "npm portable mode mapping changed"


def windows_platform_tarball(tmp_path, builder, *, payload=b"Windows native executable", tar_mode=0o644):
    """Build a real package carrying Windows' observed 0777 native and 0644 tar modes."""
    original = b"Windows native executable"
    native = {"schema": 1, "target": "windows-x64", "version": "0.3.1", "source_sha": "a" * 40,
              "python": "3.12.14", "wheel_sha256": "b" * 64, "lock_sha256": "c" * 64,
              "executable": "brewdoc/brewdoc.exe", "archive": {
                  "filename": "brewdoc-0.3.1-windows-x64.zip", "bytes": 1, "sha256": "d" * 64},
              "inventory": {"brewdoc": {"kind": "directory", "mode": 0o777}, "brewdoc/brewdoc.exe": {
                  "kind": "file", "mode": 0o777, "bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}},
              "oracle": [{"path": f"tests/fixtures/input-{index}.pdf", "source_sha256": "e" * 64,
                  "code": 0, "receipt": {"out": None}, "markdown_bytes": 1, "markdown_sha256": "f" * 64} for index in range(11)],
              "probes": [{"probe": name, "path": "tests/fixtures/input.xlsx"} for name in ("sheet-order", "vba", "epub2", "formula")]}
    verification = {"verdict": "PASS", "target": "windows-x64", "version": "0.3.1", "source_sha": "a" * 40,
                    "archive_sha256": "d" * 64}
    metadata = builder.package_metadata("0.3.1", "windows-x64")
    payloads = {"package.json": json.dumps(metadata).encode(), "native-manifest.json": json.dumps(native).encode(),
                "native-verification.json": json.dumps(verification).encode(), "binary/brewdoc.exe": payload}
    path, files = tmp_path / "brewdoc-win32-x64-0.3.1.tgz", {}
    with tarfile.open(path, "w:gz") as archive:
        for name, body in payloads.items():
            member = tarfile.TarInfo("package/" + name)
            member.size, member.mode = len(body), tar_mode if name == "binary/brewdoc.exe" else 0o644
            archive.addfile(member, io.BytesIO(body))
            files[name] = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "mode": member.mode}
    body = path.read_bytes()
    record = {"target": "windows-x64", "version": "0.3.1", "source_sha": "a" * 40,
              "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "integrity": builder.integrity(body),
              "metadata": metadata, "files": files, "native_archive": native["archive"], "links": {}}
    return path, record


def test_windows_native_executable_admits_exact_non_bin_tar_mode_without_changing_its_bytes(tmp_path, npm_builder):
    # GIVEN Windows Python reports 0777 for the exact native executable while pinned npm emits 0644
    path, record = windows_platform_tarball(tmp_path, npm_builder)
    assert record["files"]["binary/brewdoc.exe"]["mode"] == 0o644, "the real tar carries the observed Windows non-bin mode"
    # WHEN complete native/archive admission reopens and verifies actual tar members
    verdict = npm_builder.verify_tarball(path, record)
    # THEN the unchanged native executable is admitted under exact target-specific packing semantics
    assert verdict is None, "Windows portable mode conversion must preserve strict native file identity"


def test_windows_npm_producer_seals_the_same_target_specific_modes_as_cross_host_admission(tmp_path, monkeypatch, npm_builder):
    # GIVEN a real Windows-shaped tarball and staged executable with Python's 0777 source mode
    output = tmp_path / "output"
    output.mkdir()
    path, original = windows_platform_tarball(output, npm_builder)
    stage = tmp_path / "stage"
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            destination = stage / member.name.removeprefix("package/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
    (stage / "binary/brewdoc.exe").chmod(0o777)
    packed = {"brewdoc-win32-x64": {"filename": path.name, "integrity": original["integrity"]}}
    monkeypatch.setattr(npm_builder, "check_tools", lambda *arguments: None)
    monkeypatch.setattr(npm_builder, "run_npm", lambda *arguments: subprocess.CompletedProcess([], 0, json.dumps(packed).encode(), b""))
    assert (stage / "binary/brewdoc.exe").stat().st_mode & 0o777 == 0o777, "the source stage retains the raw native executable mode"
    # WHEN packing creates the platform record and immediately reopens the actual tarball
    record = npm_builder.pack_directory(stage, output, {key: original[key] for key in ("target", "version", "source_sha", "native_archive", "links")})
    # THEN producer and remote admission share exact modes while source staging remains unchanged
    assert record["files"] == original["files"], "the producer seals every actual file byte/hash/mode under the admitted target"
    assert (stage / "binary/brewdoc.exe").stat().st_mode & 0o777 == 0o777, "portable tar expectations never alter raw staged mode checks"


@pytest.mark.parametrize("payload,mode", [(b"substituted executable", 0o644), (b"Windows native executable", 0o755)])
def test_windows_npm_native_admission_still_refuses_changed_executable_bytes_or_wrong_tar_mode(tmp_path, npm_builder, payload, mode):
    # GIVEN self-consistent package transport and outer inventory hide an altered native file or mode
    path, record = windows_platform_tarball(tmp_path, npm_builder, payload=payload, tar_mode=mode)
    assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), "the transport itself starts internally consistent"
    # WHEN admission binds the actual tar members to the original admitted native inventory
    with pytest.raises(ValueError, match="native files"):
        npm_builder.verify_tarball(path, record)
    # THEN refusing the substitution cannot rewrite original native source evidence
    assert record["source_sha"] == "a" * 40, "the refused source identity remains unchanged"


def test_registry_omitted_files_field_accepts_exact_tarball(npm_builder, monkeypatch):
    # GIVEN registry metadata omitting files while retaining the exact tarball.
    body = b"admitted tarball"
    metadata = {"name": "brewdoc", "version": "0.3.1", "files": ["bin/"],
                "optionalDependencies": {"brewdoc-darwin-arm64": "0.3.1"}}
    record = {"metadata": metadata, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
              "integrity": npm_builder.integrity(body)}
    public = {"name": "brewdoc", "version": "0.3.1",
              "optionalDependencies": {"brewdoc-darwin-arm64": "0.3.1"}}
    public["dist"] = {"tarball": "https://registry.npmjs.org/brewdoc/-/brewdoc-0.3.1.tgz", "integrity": record["integrity"]}
    responses = iter([io.BytesIO(json.dumps(public).encode()), io.BytesIO(body)])
    monkeypatch.setattr(npm_builder.urllib.request, "urlopen", lambda *arguments, **keywords: next(responses))
    assert "files" not in public, "Registry normalization precondition changed"

    # WHEN exact-version retry admission checks metadata and downloaded bytes.
    admitted = npm_builder.public_record(record)

    # THEN npm's omitted metadata field does not invalidate matching package bytes.
    assert admitted is True, "Exact tarball retry was rejected by registry normalization"


@pytest.mark.parametrize("dependency_version,tar_bytes,error", [
    ("0.3.2", b"admitted tarball", "metadata"), ("0.3.1", b"altered tarball", "integrity"),
], ids=["dependency-mismatch", "changed-public-bytes"])
def test_existing_registry_version_refuses_semantic_or_byte_mismatch(npm_builder, monkeypatch, dependency_version, tar_bytes, error):
    # GIVEN one admitted version and a conflicting public dependency or tarball.
    admitted_bytes = b"admitted tarball"
    metadata = {"name": "brewdoc", "version": "0.3.1",
                "optionalDependencies": {"brewdoc-darwin-arm64": "0.3.1"}}
    record = {"metadata": metadata, "bytes": len(admitted_bytes), "sha256": hashlib.sha256(admitted_bytes).hexdigest(),
              "integrity": npm_builder.integrity(admitted_bytes)}
    public = {"name": "brewdoc", "version": "0.3.1",
              "optionalDependencies": {"brewdoc-darwin-arm64": dependency_version},
              "dist": {"tarball": "https://registry.npmjs.org/brewdoc/-/brewdoc-0.3.1.tgz", "integrity": record["integrity"]}}
    responses = iter([io.BytesIO(json.dumps(public).encode()), io.BytesIO(tar_bytes)])
    monkeypatch.setattr(npm_builder.urllib.request, "urlopen", lambda *arguments, **keywords: next(responses))
    assert record["bytes"] == 16, "Admitted package precondition changed"

    # WHEN retry admission checks the existing public version.
    with pytest.raises(ValueError, match=error):
        npm_builder.public_record(record)

    # THEN the admitted identity stays intact for a reviewed publication attempt.
    assert record["metadata"]["optionalDependencies"] == {"brewdoc-darwin-arm64": "0.3.1"}, "Public conflict changed the admitted dependency"


def test_smoke_consumer_uses_sealed_synthetic_epub_without_corpus_or_full_replay(tmp_path, monkeypatch, npm_builder):
    # GIVEN the real native synthetic EPUB with source-sensitive receipt and Markdown.
    import brewdoc
    sealed = tmp_path / "seal"
    sealed.mkdir()
    case = npm_builder.native_builder().synthetic_cases(npm_builder.ROOT, sealed)[0]
    source = Path(case["source"])
    code, receipt, text = brewdoc.run(source)
    payload, markdown = source.read_bytes(), text.encode("utf-8")
    row = {"probe": "epub2", "path": "sample.epub", "bytes_base64": npm_builder.base64.b64encode(payload).decode(),
           "source_sha256": npm_builder.digest(payload), "code": 0, "receipt": receipt,
           "markdown_bytes": len(markdown), "markdown_sha256": npm_builder.digest(markdown)}
    native = {"verification_scope": "smoke", "oracle": [], "probes": [row, {"probe": "formula"}]}
    calls = []
    arguments = [sys.executable, "-I", "-c", "from brewdoc.cli import main; raise SystemExit(main())"]
    original_cli = npm_builder.cli_result
    def run(arguments, suffix, *parameters):
        """Run the real CLI and retain its source-sensitive stdout for admission."""
        calls.append(suffix)
        return original_cli(arguments, suffix, *parameters)
    monkeypatch.setattr(npm_builder, "cli_result", run)
    assert native["oracle"] == [], "Smoke must have no licensed inputs before execution"
    assert (code, {"receipt": receipt, "markdown_sha256": npm_builder.digest(markdown)}) == (
        0, case["expected"]), "Native EPUB literals must match the real source-sensitive seal"
    assert receipt["source"] == "sample.epub", "The source basename belongs to the sealed receipt"

    # WHEN the installed global consumer verifies its smoke contract.
    result = npm_builder.consumer_probe(arguments, tmp_path, {}, native, "brewdoc-darwin-arm64")

    # THEN only self-check and one Unicode synthetic conversion are executed.
    assert calls == [["--self-check"], [str(tmp_path / "Unicode caf\u00e9" / "sample.epub")]], "Smoke changed the sealed basename or replayed full checks"
    assert result == {"self_check": 38, "selected_package": "brewdoc-darwin-arm64", "fixtures": [],
                      "probes": [{"probe": "epub2", "path": "sample.epub", "markdown_sha256": row["markdown_sha256"]}]}, "Smoke evidence must bind the synthetic seal"


@pytest.mark.parametrize("fields,expected", [({}, "full"), ({"verification_scope": "full"}, "full"),
                                          ({"verification_scope": "smoke"}, "smoke")])
def test_explicit_smoke_and_legacy_full_scopes_remain_distinct(npm_builder, fields, expected):
    # GIVEN an explicit profile or an older full record.
    assert set(fields) <= {"verification_scope"}, "Scope fixture contains unrelated fields"
    # WHEN profile admission reads the record.
    scope = npm_builder.verification_scope(fields)
    # THEN a missing profile never claims smoke coverage.
    assert scope == expected, "Legacy full coverage was relabeled"


def test_mixed_native_scopes_refuse_before_aggregate_writes(tmp_path, monkeypatch, npm_builder):
    # GIVEN three platform records with one full record among smoke records.
    for target, scope in zip(npm_builder.PACKAGES, ("smoke", "full", "smoke"), strict=True):
        folder = tmp_path / target
        folder.mkdir()
        npm_builder.write_json(folder / "platform-record.json", {
            "target": target, "version": "0.3.1", "source_sha": "a" * 40, "verification_scope": scope})
    monkeypatch.setattr(npm_builder, "release_version", lambda: "0.3.1")
    output = tmp_path / "aggregate"
    assert output.exists() is False, "Aggregation must start outside an existing output"
    # WHEN aggregation checks the complete target set.
    with pytest.raises(ValueError, match="verification scopes differ"):
        npm_builder.aggregate(tmp_path, output)
    # THEN mixed coverage cannot create a release directory.
    assert output.exists() is False, "Mixed profiles wrote an aggregate"


def test_manifest_scope_mismatch_refuses_before_npm_or_network(tmp_path, monkeypatch, npm_builder):
    # GIVEN smoke-only release evidence requested as a full consumer run.
    manifest = {"verification_scope": "smoke"}
    monkeypatch.setattr(npm_builder, "verify_release", lambda unused: manifest)
    calls = []
    monkeypatch.setattr(npm_builder, "run_npm", lambda *args, **kwargs: calls.append(args))
    assert calls == [], "No install should precede scope admission"
    # WHEN a caller omits the explicit smoke option.
    with pytest.raises(ValueError, match="verification scope differs"):
        npm_builder.probe(tmp_path, "macos-arm64", "public", tmp_path / "report.json")
    # THEN no npm process or report is created.
    assert (calls, (tmp_path / "report.json").exists()) == ([], False), "Mismatched mode reached installation"


def test_central_public_check_binds_all_four_tarball_integrities(tmp_path, monkeypatch, npm_builder):
    # GIVEN a complete smoke package set with fixed integrity identities.
    records = [{"metadata": {"name": name}, "integrity": "sha512-" + name} for name in [*npm_builder.PACKAGES.values(), "brewdoc"]]
    manifest = {"verification_scope": "smoke", "version": "0.3.1", "source_sha": "a" * 40, "packages": records}
    monkeypatch.setattr(npm_builder, "verify_release", lambda unused: manifest)
    checked = []
    def public(record):
        """Record central registry admissions without network access."""
        checked.append(record["metadata"]["name"])
        return True
    monkeypatch.setattr(npm_builder, "public_record", public)
    assert len(records) == 4, "Central admission needs every public package"
    # WHEN one central public byte gate writes reusable evidence.
    output = tmp_path / "public.json"
    npm_builder.check_public(tmp_path, output)
    # THEN all four identities are checked exactly once and bound to source/profile.
    assert checked == [*npm_builder.PACKAGES.values(), "brewdoc"], "Central gate omitted or duplicated a package"
    assert npm_builder.read_json(output) == {"schema": 1, "verdict": "PASS", "version": "0.3.1", "source_sha": "a" * 40,
                                            "verification_scope": "smoke", "packages": {row["metadata"]["name"]: row["integrity"] for row in records}}, "Central public evidence lost its binding"


def test_smoke_public_probe_installs_global_and_npx_without_local_replay(tmp_path, monkeypatch, npm_builder):
    # GIVEN centrally admitted smoke bytes and isolated target identity resolution.
    target, version, source_sha = "macos-arm64", "0.3.1", "a" * 40
    records = [{"target": platform_target, "metadata": {"name": name}, "integrity": "sha512-" + name,
                "native_archive": {"sha256": "b" * 64}} for platform_target, name in npm_builder.PACKAGES.items()]
    records.append({"metadata": {"name": "brewdoc"}, "integrity": "sha512-main"})
    manifest = {"version": version, "source_sha": source_sha, "verification_scope": "smoke", "packages": records}
    evidence = tmp_path / "public.json"
    npm_builder.write_json(evidence, {"schema": 1, "verdict": "PASS", "version": version, "source_sha": source_sha,
                                     "verification_scope": "smoke", "packages": {row["metadata"]["name"]: row["integrity"] for row in records}})
    native = {"source_sha": source_sha, "version": version, "target": target,
              "archive": {"sha256": "b" * 64}, "verification_scope": "smoke"}
    monkeypatch.setattr(npm_builder, "verify_release", lambda unused: manifest)
    monkeypatch.setattr(npm_builder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(npm_builder.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(npm_builder, "check_tools", lambda *unused: None)
    monkeypatch.setattr(npm_builder, "installed_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(npm_builder, "npm_command", lambda *args, **kwargs: ["/bin/node", "npm"])
    public_calls, npm_calls, consumer_calls, cancellation_calls = [], [], [], []
    npm_responses = iter([("self-check: ok (38 checks)" + os.linesep).encode(),
                          ("self-check: ok (38 checks)" + os.linesep).encode(),
                          b"2 packages have verified registry signatures\n2 packages have verified attestations\n"])
    def public(record):
        """Record target registry admissions without network access."""
        public_calls.append(record["metadata"]["name"])
        return True
    def npm(arguments, work, environment, **keywords):
        """Materialize only the npx discovery path and return fixed tool results."""
        npm_calls.append(arguments)
        folder = work / "config/cache/_npx/identity/node_modules/brewdoc"
        folder.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess([], 0, next(npm_responses), b"")
    def resolve(arguments, **keywords):
        """Resolve an admitted native package inside the consumer workspace."""
        folder = Path(keywords["env"]["HOME"]).parent / "native"
        folder.mkdir(exist_ok=True)
        npm_builder.write_json(folder / "native-manifest.json", native)
        return subprocess.CompletedProcess([], 0, str(folder / "package.json").encode(), b"")
    def consumer(arguments, *unused):
        """Record the one global conversion entry point."""
        consumer_calls.append(arguments)
        return {"self_check": 38, "selected_package": npm_builder.PACKAGES[target], "fixtures": [], "probes": [{"probe": "epub2"}]}
    def cancellation(launcher, *unused, **unused_keywords):
        """Record the installed launcher used for cancellation."""
        cancellation_calls.append(launcher.parts[-3:])
        return {"native_terminated": True}
    monkeypatch.setattr(npm_builder, "public_record", public)
    monkeypatch.setattr(npm_builder, "run_npm", npm)
    monkeypatch.setattr(npm_builder.subprocess, "run", resolve)
    monkeypatch.setattr(npm_builder, "consumer_probe", consumer)
    monkeypatch.setattr(npm_builder, "cancellation_probe", cancellation)
    assert npm_builder.read_json(evidence)["verification_scope"] == "smoke", "Public evidence must match smoke"
    # WHEN public target smoke consumes central evidence and advertised commands.
    output = tmp_path / "report.json"
    npm_builder.probe(tmp_path, target, "public", output, smoke=True, public_evidence=evidence)
    # THEN only the native pair is fetched and only global+npx consumers are recorded.
    report = npm_builder.read_json(output)
    assert public_calls == [npm_builder.PACKAGES[target], "brewdoc"], "Target duplicated central all-platform downloads"
    assert [call[0] for call in npm_calls] == ["install", "--yes", "audit"], "Smoke repeated a local installation"
    assert len(consumer_calls) == 1, "Synthetic conversion must use one installed global bin"
    assert cancellation_calls == [("brewdoc", "bin", "brewdoc.cjs")], "Cancellation missed the installed launcher"
    assert (report["verification_scope"], list(report["consumers"]), report["consumers"]["npx"]) == (
        "smoke", ["global", "npx"], {"self_check": 38, "selected_package": npm_builder.PACKAGES[target], "fixtures": [], "probes": []}), "Smoke report claimed full consumers"


@pytest.mark.parametrize("scope", ["full", None, "partial"])
def test_smoke_release_refuses_mismatched_package_scope_before_tar_admission(tmp_path, monkeypatch, npm_builder, scope):
    # GIVEN a smoke release with one mismatched or unspecified package record.
    version = "0.3.1"
    records = [{"metadata": npm_builder.package_metadata(version, target), "version": version,
                "source_sha": "a" * 40, "verification_scope": "smoke"} for target in npm_builder.PACKAGES]
    records.append({"metadata": npm_builder.package_metadata(version), "version": version,
                    "source_sha": "a" * 40, "verification_scope": "smoke"})
    records[0]["verification_scope"] = scope
    npm_builder.write_json(tmp_path / "npm-manifest.json", {"schema": 1, "version": version, "source_sha": "a" * 40,
        "node": npm_builder.NODE_VERSION, "npm": npm_builder.NPM_VERSION, "verification_scope": "smoke", "packages": records})
    monkeypatch.setattr(npm_builder, "release_version", lambda: version)
    calls = []
    monkeypatch.setattr(npm_builder, "verify_tarball", lambda *args: calls.append(args))
    assert calls == [], "Tar admission must follow profile admission"
    # WHEN the npm aggregate binds every package to its verification profile.
    with pytest.raises(ValueError, match="verification scope"):
        npm_builder.verify_release(tmp_path)
    # THEN no differently verified tarball is admitted.
    assert calls == [], "Mismatched package scope reached tar admission"


@pytest.mark.parametrize("change", [{"source_sha": "b" * 40}, {"verification_scope": "full"}, {"packages": {}}],
                         ids=["different-source", "full-instead-of-smoke", "missing-tar-identities"])
def test_public_smoke_refuses_unbound_central_evidence_before_registry_calls(tmp_path, monkeypatch, npm_builder, change):
    # GIVEN smoke target bytes and central evidence from a conflicting identity.
    records = [{"target": "macos-arm64", "metadata": {"name": "brewdoc-darwin-arm64"}, "integrity": "sha512-native"},
               {"metadata": {"name": "brewdoc"}, "integrity": "sha512-main"}]
    manifest = {"verification_scope": "smoke", "version": "0.3.1", "source_sha": "a" * 40, "packages": records}
    evidence = {"schema": 1, "verdict": "PASS", "version": "0.3.1", "source_sha": "a" * 40,
                "verification_scope": "smoke", "packages": {row["metadata"]["name"]: row["integrity"] for row in records}}
    evidence.update(change)
    npm_builder.write_json(tmp_path / "public.json", evidence)
    monkeypatch.setattr(npm_builder, "verify_release", lambda unused: manifest)
    monkeypatch.setattr(npm_builder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(npm_builder.platform, "machine", lambda: "arm64")
    calls = []
    monkeypatch.setattr(npm_builder, "public_record", lambda record: calls.append(record))
    assert calls == [], "Registry proof must follow central evidence admission"
    # WHEN target smoke checks source, scope and all admitted package identities.
    with pytest.raises(ValueError, match="central public byte evidence"):
        npm_builder.probe(tmp_path, "macos-arm64", "public", tmp_path / "report.json", True, tmp_path / "public.json")
    # THEN conflicting evidence starts no registry request or installation.
    assert (calls, (tmp_path / "report.json").exists()) == ([], False), "Unbound central evidence was consumed"


def test_smoke_epub_payload_drift_refuses_before_conversion_write(tmp_path, monkeypatch, npm_builder):
    # GIVEN synthetic bytes differing from their sealed native source hash.
    native = {"verification_scope": "smoke", "oracle": [], "probes": [
        {"probe": "epub2", "bytes_base64": npm_builder.base64.b64encode(b"altered").decode(), "source_sha256": "a" * 64},
        {"probe": "formula"}]}
    calls = []
    def self_check(arguments, suffix, *unused):
        """Return a successful self-check while recording every CLI request."""
        calls.append(suffix)
        return subprocess.CompletedProcess([], 0, ("self-check: ok (38 checks)" + os.linesep).encode(), b"")
    monkeypatch.setattr(npm_builder, "cli_result", self_check)
    assert list(tmp_path.iterdir()) == [], "Synthetic conversion must begin with no files"
    # WHEN the npm consumer checks the source seal before passing a document.
    with pytest.raises(ValueError, match="synthetic EPUB source identity"):
        npm_builder.consumer_probe(["brewdoc"], tmp_path, {}, native, "brewdoc-darwin-arm64")
    # THEN only self-check ran and no unsealed document was written.
    assert (calls, list(tmp_path.iterdir())) == ([["--self-check"]], []), "Unsealed synthetic source reached conversion"


def test_legacy_full_consumer_retains_help_fixture_and_atomic_refusal(tmp_path, monkeypatch, npm_builder):
    # GIVEN a legacy full seal and exact stdout for one isolated synthetic fixture.
    source = tmp_path / "source"
    source.mkdir()
    (source / "fixture.epub").write_bytes(b"full fixture")
    markdown = b"# Full fixture\n"
    receipt = {"file_ok": True, "units": 1, "out": None, "route": "epub"}
    row = {"path": "fixture.epub", "source_sha256": npm_builder.digest(b"full fixture"), "receipt": receipt,
           "code": 0, "markdown_bytes": len(markdown), "markdown_sha256": npm_builder.digest(markdown)}
    responses = iter([("self-check: ok (38 checks)" + os.linesep).encode(), b"usage: brewdoc fixture\n",
                      (json.dumps(receipt, sort_keys=True) + os.linesep).encode() + markdown,
                      b'{"file_ok":false,"units":0}\n'])
    codes, calls = iter([0, 0, 0, 1]), []
    def cli(arguments, suffix, *unused):
        """Return full-mode CLI results without running a native binary."""
        calls.append(suffix)
        return subprocess.CompletedProcess([], next(codes), next(responses), b"")
    monkeypatch.setattr(npm_builder, "ROOT", source)
    monkeypatch.setattr(npm_builder, "cli_result", cli)
    native = {"oracle": [row]}
    assert npm_builder.verification_scope(native) == "full", "Legacy seal must remain full coverage"
    # WHEN the original full consumer route runs outside its source folder.
    result = npm_builder.consumer_probe(["brewdoc"], tmp_path, {}, native, "brewdoc-darwin-arm64")
    # THEN all previous checks and the sealed fixture identity are retained.
    assert calls == [["--self-check"], ["--help"], [str(tmp_path / "fixture.epub")],
                     [str(tmp_path / "malformed.epub"), "--out", str(tmp_path / "atomic refusal.md")]], "Full checks were reduced by smoke support"
    assert result == {"self_check": 38, "selected_package": "brewdoc-darwin-arm64",
                      "fixtures": [{"path": "fixture.epub", "markdown_sha256": row["markdown_sha256"]}],
                      "atomic_refusal": True, "help_sha256": npm_builder.digest(b"usage: brewdoc fixture\n")}, "Full evidence shape changed"


def test_windows_cancellation_refuses_natural_zero_exit(tmp_path, monkeypatch, npm_builder):
    # GIVEN an observed native child followed by an ordinary successful launcher exit.
    receipt = {"file_ok": True, "route": "html", "source": "cancellation.html", "out": None}
    child = SimpleNamespace(pid=100, returncode=None, stdout=io.BytesIO(json.dumps(receipt).encode() + b"\n"))
    events = []
    child.poll = lambda: child.returncode
    child.send_signal = lambda unused: events.append("signal")
    def communicate(**unused):
        """Simulate normal launcher completion rather than cancellation."""
        child.returncode = 0
        events.append("communicate")
        return b"", b""
    def wait(**unused):
        """Return the ordinary zero exit even after a signal request."""
        child.returncode = 0
        events.append("wait")
        return 0
    child.communicate, child.wait = communicate, wait
    child.terminate = lambda: setattr(child, "returncode", 0)
    snapshots = iter([{101: {"parent": 100, "start": "owned", "command": "native", "executable": str(tmp_path / "brewdoc.exe")}}, {}, {}])
    monkeypatch.setattr(npm_builder, "os", SimpleNamespace(name="nt", kill=lambda *unused: None))
    monkeypatch.setattr(npm_builder.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(npm_builder.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(npm_builder.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(npm_builder, "npm_command", lambda unused: ["node"])
    monkeypatch.setattr(npm_builder, "process_snapshot", lambda: next(snapshots, {}))
    assert child.returncode is None, "Cancellation needs a live launcher precondition"
    # WHEN a Windows cancellation claim observes a zero exit.
    with pytest.raises(ValueError, match="nonzero|natural"):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False,
                                       native_binary=tmp_path / "brewdoc.exe")
    # THEN zero completion cannot become a PASS cancellation receipt.
    assert child.returncode == 0, "The natural exit fixture must remain distinguishable"


@pytest.fixture
def windows_cancel_process(tmp_path, monkeypatch, npm_builder):
    """Provide isolated process identities and observable bounded cleanup calls."""
    events, launched = [], []
    binary = tmp_path / "brewdoc.exe"
    receipt = {"file_ok": True, "route": "html", "source": "cancellation.html", "out": None}
    child = SimpleNamespace(pid=100, returncode=None, stdout=io.BytesIO(json.dumps(receipt).encode() + b"\n"))
    child.poll = lambda: child.returncode
    child.send_signal = lambda unused: events.append(("signal", None))
    def wait(timeout):
        """Record the exit wait before any output drain."""
        events.append(("wait", timeout))
        child.returncode = 1
        return 1
    def communicate(timeout):
        """Record bounded draining without changing the exit status."""
        events.append(("communicate", timeout))
        return b"", b""
    def kill():
        """Terminate the owned launcher on a readiness failure."""
        events.append(("kill-launcher", None))
        child.returncode = 1
    def launch(arguments, **keywords):
        """Retain the exact offline input and pipe settings."""
        launched.append((arguments, keywords["bufsize"], keywords["creationflags"]))
        return child
    def taskkill(arguments, **keywords):
        """Record the bounded owned-tree fallback without running Windows tools."""
        events.append(("kill-tree", keywords["timeout"]))
        return subprocess.CompletedProcess(arguments, 0, b"", b"")
    child.wait, child.communicate, child.kill = wait, communicate, kill
    snapshots = iter([{101: {"parent": 100, "start": "owned", "command": "native", "executable": str(binary)}}, {}, {}, {}])
    monkeypatch.setattr(npm_builder, "os", SimpleNamespace(name="nt", kill=lambda pid, unused: events.append(("kill-native", pid))))
    monkeypatch.setattr(npm_builder.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(npm_builder.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(npm_builder.subprocess, "Popen", launch)
    monkeypatch.setattr(npm_builder.subprocess, "run", taskkill)
    monkeypatch.setattr(npm_builder, "npm_command", lambda unused: ["node"])
    monkeypatch.setattr(npm_builder, "process_snapshot", lambda: next(snapshots, {}))
    return child, binary, events, launched


def test_windows_offline_cancellation_waits_before_drain_and_reports_only_observed_scope(tmp_path, npm_builder, windows_cancel_process):
    # GIVEN an undrained successful receipt and a live admitted native descendant.
    child, binary, events, launched = windows_cancel_process
    assert (child.returncode, events) == (None, []), "The cancellation fixture must start live and idle"
    # WHEN Windows signals the output-blocked native launcher.
    report = npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN waits precede one drain and the evidence makes no JS or DNS claim.
    assert events == [("signal", None), ("wait", 10), ("wait", 5), ("communicate", 5)], "Draining before exit can permit natural completion"
    assert launched == [(["node", str(tmp_path / "launcher.cjs"), str(tmp_path / "cancellation.html")], 0, 512)], "Windows cancellation must be offline with unbuffered output"
    assert (tmp_path / "cancellation.html").read_bytes() == b"<html><body><p>" + b"x" * (1024 * 1024) + b"</p></body></html>", "Offline cancellation payload changed"
    assert report == {"native_observed": True, "native_terminated": True, "javascript": "unsupported", "dns_observed": False,
                      "owned_processes": 1, "trigger": "offline-html-output", "launcher_exit": "nonzero"}, "Cancellation scope exceeds observed native output"


def test_windows_receipt_failure_discovers_and_reaps_native_before_launcher_drain(tmp_path, npm_builder, windows_cancel_process):
    # GIVEN an unsuccessful receipt before native readiness has been recorded.
    child, binary, events, unused = windows_cancel_process
    child.stdout = io.BytesIO(b"{}\n")
    assert events == [], "No native identity should be recorded before receipt admission"
    # WHEN the bounded receipt fails its HTML success contract.
    with pytest.raises(ValueError, match="HTML capture did not succeed"):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN cleanup discovers descendants before killing and draining the launcher.
    assert events == [("kill-native", 101), ("kill-launcher", None), ("wait", 5), ("communicate", 5)], "Receipt failure leaked an undiscovered native writer"


def test_windows_receipt_deadline_is_bounded_and_preserves_descendant_cleanup(tmp_path, monkeypatch, npm_builder, windows_cancel_process):
    # GIVEN a receipt queue that reaches its deadline before any usable line.
    child, binary, events, unused = windows_cancel_process
    deadlines = []
    def expire(timeout):
        """Record the readiness bound and simulate expiration."""
        deadlines.append(timeout)
        raise npm_builder.queue.Empty
    monkeypatch.setattr(npm_builder.queue, "Queue", lambda: SimpleNamespace(put=lambda unused: None, get=expire))
    assert child.returncode is None, "A blocked receipt still has an owned live launcher"
    # WHEN readiness expires instead of returning a successful receipt.
    with pytest.raises(ValueError, match="receipt was not ready"):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN the ten-second bound and native-first cleanup remain unconditional.
    assert (deadlines, events) == ([10], [("kill-native", 101), ("kill-launcher", None), ("wait", 5), ("communicate", 5)]), "Readiness expiration left an owned process or unbounded wait"


def test_windows_snapshot_failure_still_kills_and_reaps_owned_launcher(tmp_path, monkeypatch, npm_builder, windows_cancel_process):
    # GIVEN failed receipt readiness and a bounded CIM query failure.
    child, binary, events, unused = windows_cancel_process
    child.stdout = io.BytesIO(b"{}\n")
    def unavailable():
        """Simulate failure before any native identity can be recorded."""
        raise subprocess.TimeoutExpired("CIM", 10)
    monkeypatch.setattr(npm_builder, "process_snapshot", unavailable)
    assert child.returncode is None, "A snapshot failure must retain an owned live launcher"
    # WHEN cleanup cannot enumerate descendants through CIM.
    with pytest.raises(subprocess.TimeoutExpired):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN the owned tree fallback and known launcher reap still complete.
    assert (events, child.stdout.closed) == ([("kill-tree", 5), ("kill-launcher", None), ("wait", 5), ("communicate", 5)], True), "Snapshot failure aborted launcher and pipe cleanup"


def test_windows_vanished_native_pid_does_not_abort_launcher_cleanup(tmp_path, monkeypatch, npm_builder, windows_cancel_process):
    # GIVEN a failed receipt and a native PID disappearing just before termination.
    child, binary, events, unused = windows_cancel_process
    child.stdout = io.BytesIO(b"{}\n")
    def vanished(*unused):
        """Simulate the benign native exit between observation and kill."""
        raise ProcessLookupError
    monkeypatch.setattr(npm_builder.os, "kill", vanished)
    assert child.returncode is None, "The launcher must remain owned when the child vanishes"
    # WHEN failure cleanup encounters the vanished process race.
    with pytest.raises(ValueError, match="HTML capture did not succeed"):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN cleanup still reaps the live launcher and closes its pipe.
    assert (events, child.stdout.closed) == ([("kill-launcher", None), ("wait", 5), ("communicate", 5)], True), "Vanished native PID aborted known launcher cleanup"


def test_windows_tree_fallback_timeout_still_reaps_known_launcher(tmp_path, monkeypatch, npm_builder, windows_cancel_process):
    # GIVEN failed receipt readiness and a timeout in the owned-tree fallback.
    child, binary, events, unused = windows_cancel_process
    child.stdout = io.BytesIO(b"{}\n")
    def timeout(arguments, **keywords):
        """Keep fallback failure visible while testing guaranteed Popen cleanup."""
        raise subprocess.TimeoutExpired(arguments, keywords["timeout"])
    def unavailable():
        """Require tree fallback because native discovery failed."""
        raise subprocess.TimeoutExpired("CIM", 10)
    monkeypatch.setattr(npm_builder.subprocess, "run", timeout)
    monkeypatch.setattr(npm_builder, "process_snapshot", unavailable)
    assert child.returncode is None, "Fallback timeout starts with an owned live launcher"
    # WHEN the native-first cleanup cannot complete its tree command.
    with pytest.raises(subprocess.TimeoutExpired):
        npm_builder.cancellation_probe(tmp_path / "launcher.cjs", tmp_path, {}, False, binary)
    # THEN known launcher kill, reap and pipe cleanup run despite the error.
    assert (events, child.stdout.closed) == ([("kill-launcher", None), ("wait", 5), ("communicate", 5)], True), "Tree timeout prevented guaranteed Popen cleanup"
