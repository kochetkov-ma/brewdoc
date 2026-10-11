"""Check npm staging identities and rejected native link layouts."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


@pytest.fixture
def npm_builder():
    """Load the release helper without importing product modules."""
    path = Path(__file__).parents[1] / ".github/workflows/build_npm.py"
    spec = importlib.util.spec_from_file_location("build_npm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


@pytest.mark.parametrize("mode,executable,expected", [(0o666, False, 0o644), (0o666, True, 0o755), (0o755, False, 0o755)])
def test_portable_tar_modes_preserve_executable_bits(npm_builder, mode, executable, expected):
    # GIVEN Windows writable-file or native executable source modes.
    assert mode & 0o600 == 0o600, "Source owner permissions precondition changed"

    # WHEN pinned npm creates its portable tar headers.
    observed = npm_builder.packed_mode(mode, executable)

    # THEN group writes are removed and bin executables remain executable.
    assert observed == expected, "npm portable mode mapping changed"


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
