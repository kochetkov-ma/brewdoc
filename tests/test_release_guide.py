"""Refuse release guidance whose artifacts or public consumer evidence drift."""

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".github/workflows/release_guide.py"
SPEC = importlib.util.spec_from_file_location("release_guide", HELPER)
guide = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guide)


@pytest.fixture
def release(tmp_path):
    """Create complete small accepted artifact inputs without network or engines."""
    (tmp_path / ".github").mkdir()
    (tmp_path / "src/brewdoc").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "src/brewdoc/__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    targets = {"linux-x64": {"archive": "tar.gz", "render_js": True, "verification_floor": "Linux"},
               "windows-x64": {"archive": "zip", "render_js": False, "verification_floor": "Windows"},
               "macos-arm64": {"archive": "tar.gz", "render_js": True, "verification_floor": "macOS"}}
    policy = {"schema": 1, "python": "3.12.14", "targets": targets,
              "release_features": ["Accepted conversion"],
              "signing": {"linux": "Checksums", "windows": "Unsigned", "macos": "Ad hoc"},
              "npm": {"name": "brewdoc", "node": "24.21.0", "npm": "12.2.0", "packages": {
                  "linux-x64": "brewdoc-linux-x64-glibc", "windows-x64": "brewdoc-win32-x64",
                  "macos-arm64": "brewdoc-darwin-arm64"}}}
    (tmp_path / ".github/distribution.json").write_text(json.dumps(policy), encoding="utf-8")
    native = {"schema": 1, "version": "1.2.3", "source_sha": "a" * 40, "targets": {}}
    checksums = []
    for target, facts in targets.items():
        name = f"brewdoc-1.2.3-{target}.{facts['archive']}"
        payload = target.encode()
        (tmp_path / name).write_bytes(payload)
        archive = {"filename": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        native["targets"][target] = {"schema": 1, "version": "1.2.3", "source_sha": "a" * 40,
                                      "python": "3.12.14", "target": target, "archive": archive,
                                      "minimum_tested": facts["verification_floor"], "signing": policy["signing"]}
        checksums.append(f"{archive['sha256']}  {name}")
    (tmp_path / "SHA256SUMS").write_text("\n".join(sorted(checksums)) + "\n", encoding="ascii")
    names = {"linux-x64": "brewdoc-linux-x64-glibc", "windows-x64": "brewdoc-win32-x64",
             "macos-arm64": "brewdoc-darwin-arm64"}
    packages = []
    for name in (*names.values(), "brewdoc"):
        filename = f"{name}-1.2.3.tgz"
        payload = name.encode()
        (tmp_path / filename).write_bytes(payload)
        metadata = {"name": name, "version": "1.2.3"}
        record = {"version": "1.2.3", "source_sha": "a" * 40, "metadata": metadata,
                  "filename": filename, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                  "integrity": "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")}
        packages.append(record)
    for record, target in zip(packages[:-1], targets, strict=True):
        record.update(target=target, native_archive=native["targets"][target]["archive"])
    packages[-1]["metadata"].update(optionalDependencies={name: "1.2.3" for name in names.values()},
                                   brewdocTargets={target: {"package": name} for target, name in names.items()})
    npm = {"schema": 1, "version": "1.2.3", "source_sha": "a" * 40,
           "node": "24.21.0", "npm": "12.2.0", "packages": packages}
    availability = {"schema": 1, "version": "1.2.3", "source_sha": "a" * 40, "pypi": {"published": True},
                    "github": {"assets": {row["archive"]["filename"]: {
                        key: row["archive"][key] for key in ("bytes", "sha256")} for row in native["targets"].values()}},
                    "npm": {"packages": {row["metadata"]["name"]: {
                        "version": "1.2.3", "integrity": row["integrity"]} for row in packages}},
                    "consumers": {target: {"native": "PASS", "npm": "PASS", "npx": "PASS"} for target in targets}}
    return tmp_path, native, npm, availability


def admit(release, phase="staged"):
    """Feed fixture objects through real file admission."""
    checkout, native, npm, availability = release
    paths = [checkout / name for name in ("binaries.json", "npm-manifest.json", "public-acceptance.json")]
    for path, value in zip(paths, (native, npm, availability), strict=True):
        path.write_text(json.dumps(value), encoding="utf-8")
    return guide.admit(checkout, paths[0], paths[1], phase, paths[2])


def test_complete_public_release_admits_exact_identity_and_targets(release):
    # GIVEN complete native, npm and public consumer identities
    # WHEN the public body is admitted
    facts = admit(release, "public")
    # THEN one source and the exact native target set supply the guide
    assert (facts["version"], facts["source_sha"], tuple(facts["targets"]), facts["phase"]) == (
        "1.2.3", "a" * 40, ("linux-x64", "windows-x64", "macos-arm64"), "public"), "accepted identities must survive"


@pytest.mark.parametrize("component,key,value", [
    (1, "version", "1.2.4"), (1, "source_sha", "b" * 40), (2, "version", "1.2.4"),
    (0, "__version__", "1.2.4"),
])
def test_changed_release_version_or_source_refuses_before_render(release, component, key, value):
    # GIVEN accepted artifacts with one substituted identity
    assert admit(release)["version"] == "1.2.3", "the unchanged release must pass"
    actions = {0: lambda: (release[0] / "src/brewdoc/__init__.py").write_text(f'__version__ = "{value}"\n'),
               1: lambda: release[1].update({key: value}), 2: lambda: release[2].update({key: value})}
    actions[component]()
    # WHEN release facts are admitted
    # THEN substituted versions or sources cannot enter download guidance
    with pytest.raises(ValueError, match="identity"):
        admit(release)


def test_unsupported_target_refuses_before_download_links(release):
    # GIVEN an admitted aggregate with an extra unverified architecture
    assert tuple(admit(release)["targets"]) == ("linux-x64", "windows-x64", "macos-arm64"), "baseline targets must pass"
    release[1]["targets"]["linux-arm64"] = copy.deepcopy(release[1]["targets"]["linux-x64"])
    # WHEN the target inventory is admitted
    # THEN unsupported targets cannot become advertised downloads
    with pytest.raises(ValueError, match="target set"):
        admit(release)


@pytest.mark.parametrize("change", ["filename", "sha256", "bytes", "checksum", "payload"])
def test_changed_native_archive_or_checksum_refuses(release, change):
    # GIVEN an accepted release with one altered artifact binding
    assert admit(release)["version"] == "1.2.3", "baseline archive bytes must pass"
    mutations = {"filename": "../escape.tar.gz", "sha256": "f" * 64, "bytes": 1}
    archive = release[1]["targets"]["linux-x64"]["archive"]
    actions = {"filename": lambda: archive.update(filename=mutations["filename"]),
               "sha256": lambda: archive.update(sha256=mutations["sha256"]),
               "bytes": lambda: archive.update(bytes=mutations["bytes"]),
               "checksum": lambda: (release[0] / "SHA256SUMS").write_text("bad\n", encoding="ascii"),
               "payload": lambda: (release[0] / archive["filename"]).write_bytes(b"changed")}
    actions[change]()
    # WHEN the guide validates actual archive identities
    # THEN neither unsafe names nor stale checksums or replaced bytes are admitted
    with pytest.raises(ValueError, match="archive|checksum"):
        admit(release)


@pytest.mark.parametrize("key,value", [("version", "1.2.4"), ("source_sha", "b" * 40), ("target", "linux-arm64")])
def test_npm_package_substitution_refuses(release, key, value):
    # GIVEN a matching packed package set with one substituted platform record
    assert len(admit(release)["npm"]["packages"]) == 4, "baseline packages must pass"
    release[2]["packages"][0][key] = value
    # WHEN npm identities are admitted
    # THEN version, source and platform substitutions cannot become npm commands
    with pytest.raises(ValueError, match="npm"):
        admit(release)


@pytest.mark.parametrize("section", ["github", "npm", "consumers", "pypi"])
def test_public_claims_require_complete_matching_availability(release, section):
    # GIVEN a complete staged release lacking one public acceptance section
    assert admit(release)["phase"] == "staged", "local artifacts must remain usable for staged notes"
    release[3].pop(section)
    # WHEN public availability is required
    # THEN local artifact success cannot stand in for actual public consumers
    with pytest.raises(ValueError, match="public"):
        admit(release, "public")


@pytest.mark.parametrize("text", ["brewdoc --version", "pip install brewdoc==1.2.4",
                                 "https://github.com/kochetkov-ma/brewdoc/releases/download/v1.2.4/brewdoc-1.2.4-linux-x64.tar.gz",
                                 "https://github.com/kochetkov-ma/brewdoc/releases/download/v1.2.3/brewdoc-1.2.2-linux-x64.tar.gz",
                                 "https://github.com/kochetkov-ma/brewdoc/releases/download/v1.2.3/brewdoc-1.2.3-linux-arm64.tar.gz"])
def test_guide_command_and_version_drift_refuses(release, text):
    # GIVEN an invented flag or stale versioned install/download command
    policy = json.loads((release[0] / ".github/distribution.json").read_text(encoding="utf-8"))
    assert guide.check_document("pip install brewdoc==1.2.3", "1.2.3", policy) is None, "the exact current command must pass"
    # WHEN a maintained guide is checked against the metadata version
    # THEN unsupported flags and incorrect release selection refuse
    with pytest.raises(ValueError, match="guide"):
        guide.check_document(text, "1.2.3", policy)


def test_complete_release_checksum_inventory_admits_native_and_npm_assets(release):
    # GIVEN release staging with one checksum list for every published asset
    checkout, native, npm, _availability = release
    assets = {record["archive"]["filename"]: {key: record["archive"][key] for key in ("bytes", "sha256")}
              for record in native["targets"].values()}
    assets.update({record["filename"]: {key: record[key] for key in ("bytes", "sha256")} for record in npm["packages"]})
    assets["brewdoc-1.2.3-py3-none-any.whl"] = {"bytes": 10, "sha256": "f" * 64}
    manifest = {"schema": 1, "version": "1.2.3", "source_sha": "a" * 40, "tag": "v1.2.3", "assets": assets}
    (checkout / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (checkout / "SHA256SUMS").write_text("\n".join(f"{record['sha256']}  {name}" for name, record in sorted(assets.items())) + "\n")
    # WHEN native/npm guide inputs use the complete release checksum inventory
    facts = admit(release, "public")
    # THEN unrelated accepted package entries do not hide or replace native/npm bindings
    assert (facts["version"], facts["phase"]) == ("1.2.3", "public"), "complete release sums must remain usable"


def test_renderer_preserves_install_commands_windows_paths_and_publication_condition(release):
    # GIVEN the same accepted artifacts for staged and public instructions
    staged_facts, public_facts = admit(release), admit(release, "public")
    commands = ('.venv/bin/python -m pip install "brewdoc==1.2.3"\n',
                r'.\.venv\Scripts\python.exe -m pip install "brewdoc==1.2.3"' + "\n",
                'uvx --python 3.12 --from "brewdoc==1.2.3" brewdoc --self-check\n',
                'uv tool install --python 3.12 "brewdoc==1.2.3"\n',
                'pipx install --python 3.12 "brewdoc==1.2.3"\n',
                '\nnpm install --global brewdoc@1.2.3\n',
                'npx --yes --package brewdoc@1.2.3 brewdoc --self-check\n',
                'tar -xzf brewdoc-1.2.3-linux-x64.tar.gz -C brewdoc-1.2.3-linux-x64\n',
                'tar -xzf brewdoc-1.2.3-macos-arm64.tar.gz -C brewdoc-1.2.3-macos-arm64\n',
                'Expand-Archive "brewdoc-1.2.3-windows-x64.zip" -DestinationPath "brewdoc-1.2.3-windows-x64"\n',
                r'Get-Content .\brewdoc\version.json' + "\n",
                r'.\brewdoc\brewdoc.exe "C:\path\file.pdf" --out file.md' + "\n")
    condition = "This release is staged. The commands and download links below apply only after this exact version and channel are published and verified."
    # WHEN complete installation bodies are rendered
    staged, public = guide.render(staged_facts), guide.render(public_facts)
    # THEN copy/paste command lines retain exact versions and single Windows backslashes
    assert (tuple(staged.count(command) for command in commands), tuple(public.count(command) for command in commands)) == (
        (1,) * 12, (1,) * 12), "both bodies must contain every literal install/native command exactly once"
    assert (staged.count(condition), public.count(condition), guide.render(public_facts)) == (
        1, 0, public), "only staged output carries the publication condition and rendering is deterministic"


def test_cli_refusal_preserves_an_existing_reviewed_body(release, capsys):
    # GIVEN accepted inputs and an existing body before a version substitution
    assert admit(release)["version"] == "1.2.3", "the unchanged CLI inputs must pass admission"
    checkout = release[0]
    output = checkout / "reviewed-body.md"
    output.write_bytes(b"already reviewed\n")
    release[1]["version"] = "1.2.4"
    (checkout / "binaries.json").write_text(json.dumps(release[1]), encoding="utf-8")
    # WHEN actual CLI rendering admits substituted native metadata
    with pytest.raises(SystemExit) as refusal:
        guide.main(["render", "--checkout", str(checkout), "--native-manifest", str(checkout / "binaries.json"),
                    "--npm-manifest", str(checkout / "npm-manifest.json"), "--output", str(output)])
    # THEN refusal exits one with a specific diagnostic and leaves prior output bytes intact
    assert (refusal.value.code, capsys.readouterr().err, output.read_bytes()) == (
        1, "Release guide refused: Guide native release identity differs\n", b"already reviewed\n"), "failed admission must not overwrite reviewed notes"
