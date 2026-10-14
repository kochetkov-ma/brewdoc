"""Refuse release guidance whose artifacts or public consumer evidence drift."""

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

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
                  "linux-x64": "brewdoc-linux-x64-glibc", "macos-arm64": "brewdoc-darwin-arm64"}}}
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
    names = policy["npm"]["packages"]
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
    for record, target in zip(packages[:-1], names, strict=True):
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
                    "consumers": {"linux-x64": {"native": "PASS", "npm": "PASS", "npx": "PASS"},
                                  "windows-x64": {"native": "PASS"},
                                  "macos-arm64": {"native": "PASS", "npm": "PASS", "npx": "PASS"}}}
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


def test_windows_npm_policy_refuses_before_registry_commands(release):
    # GIVEN two admitted npm platforms and three native platforms
    assert len(admit(release)["npm"]["packages"]) == 3, "the selected npm packages must pass"
    path = release[0] / ".github/distribution.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["npm"]["packages"]["windows-x64"] = "brewdoc-win32-x64"
    path.write_text(json.dumps(policy), encoding="utf-8")
    # WHEN policy adds an unaccepted npm platform
    # THEN native Windows support cannot become an npm claim
    with pytest.raises(ValueError, match="npm target set"):
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
    assert len(admit(release)["npm"]["packages"]) == 3, "two platform packages and main must pass"
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
                                 "https://github.com/kochetkov-ma/brewdoc/releases/download/v1.2.3/brewdoc-1.2.3-linux-arm64.tar.gz",
                                 "https://github.com/kochetkov-ma/brewdoc/blob/v1.2.2/INSTALL.md"])
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


@pytest.mark.parametrize("target", ["linux-x64", "macos-arm64"])
@pytest.mark.parametrize("consumer", ["npm", "npx"])
def test_public_npm_commands_require_both_os_consumers(release, target, consumer):
    # GIVEN admitted public bytes and consumers for both npm platforms
    assert admit(release, "public")["phase"] == "public", "complete two-platform evidence must pass"
    release[3]["consumers"][target].pop(consumer)
    # WHEN one required npm or npx consumer is missing
    # THEN public notes refuse before advertising the channel
    with pytest.raises(ValueError, match="public"):
        admit(release, "public")


def test_renderer_advertises_npm_only_after_public_acceptance(release):
    # GIVEN accepted artifacts with three native targets and two npm targets
    staged_facts, public_facts = admit(release), admit(release, "public")
    assert tuple(public_facts["policy"]["npm"]["packages"]) == (
        "linux-x64", "macos-arm64"), "Windows must remain a native-only target"
    commands = ('python -m pip install "brewdoc==1.2.3"\n',
                'uv tool install --python 3.12 "brewdoc==1.2.3"\n',
                'pipx install --python 3.12 "brewdoc==1.2.3"\n',
                'brewdoc --self-check\n', 'brewdoc file.pdf --out file.md\n')
    npm_commands = ('npm install --global brewdoc@1.2.3\n',
                    'npx --yes --package brewdoc@1.2.3 brewdoc --self-check\n')
    condition = "Staged release. Install commands and downloads apply after publication and verification."
    # WHEN staged and accepted public notes are rendered
    staged, public = guide.render(staged_facts), guide.render(public_facts)
    # THEN each install choice uses the admitted version and npm commands require public evidence
    assert (tuple(staged.splitlines(keepends=True).count(command) for command in commands),
            tuple(public.splitlines(keepends=True).count(command) for command in commands)) == (
        (1,) * 5, (1,) * 5), "notes must retain versioned install choices and usable conversion commands"
    assert (tuple(staged.count(command) for command in npm_commands), tuple(public.count(command) for command in npm_commands)) == (
        (0, 0), (1, 1)), "staged notes cannot advertise unaccepted registry commands"
    assert (staged.count(condition), public.count(condition)) == (
        1, 0), "install/download availability must remain conditional until public acceptance"
    assert guide.render(public_facts) == public, "identical accepted inputs must render deterministic notes"


def test_renderer_links_all_admitted_native_archives_and_versioned_install_guide(release):
    # GIVEN exact native archives including Windows with native-only public acceptance
    facts = admit(release, "public")
    assert tuple(facts["targets"]) == (
        "linux-x64", "windows-x64", "macos-arm64"), "native target coverage must remain complete"
    # WHEN compact notes select downloads and the full installation guide
    body = guide.render(facts)
    downloads = re.findall(r"/releases/download/v1\.2\.3/([^\s)]+)", body)
    # THEN every admitted archive and its checksum inventory remain accessible
    assert downloads == [record["archive"]["filename"] for record in facts["targets"].values()] + [
        "SHA256SUMS"], "short notes must preserve all native downloads and checksums"
    assert f"{guide.REPOSITORY}/blob/v1.2.3/INSTALL.md" in body, "full guide must use the same immutable version"


def test_cli_checks_install_guide_version_drift(release, capsys):
    # GIVEN current README and llms commands plus a stale full installation guide
    checkout = release[0]
    assert guide.project_version(checkout) == "1.2.3", "the package metadata and API versions must agree"
    for name in ("README.md", "llms.txt"):
        (checkout / name).write_text("python -m pip install brewdoc==1.2.3\n", encoding="utf-8")
    (checkout / "INSTALL.md").write_text("python -m pip install brewdoc==1.2.2\n", encoding="utf-8")
    # WHEN the maintained-document CLI validates all consumer entry points
    with pytest.raises(SystemExit) as refusal:
        guide.main(["check", "--checkout", str(checkout)])
    # THEN stale full instructions fail even when the summaries remain current
    assert (refusal.value.code, capsys.readouterr().err) == (
        1, "Release guide refused: Maintained guide flag, version or asset differs\n"), "INSTALL.md must share the metadata version"


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


def render_cli(release, phase):
    """Render fixture manifests through the real CLI and read its output file."""
    admit(release, phase)
    checkout = release[0]
    output = checkout / "release-body.md"
    code = guide.main([
        "render", "--checkout", str(checkout), "--phase", phase,
        "--native-manifest", str(checkout / "binaries.json"),
        "--npm-manifest", str(checkout / "npm-manifest.json"),
        "--availability", str(checkout / "public-acceptance.json"), "--output", str(output),
    ])
    return code, output.read_text(encoding="utf-8")


@pytest.mark.parametrize("phase,npm_commands,conditional_count", [
    ("staged", (), 1),
    ("public", ("npm install --global brewdoc@1.2.3",
                "npx --yes --package brewdoc@1.2.3 brewdoc --self-check"), 0),
])
def test_cli_navigation_resolves_ordered_sections_and_preserves_channel_truth(
        release, capsys, phase, npm_commands, conditional_count):
    # GIVEN three admitted native targets and matching staged or public consumer evidence
    facts = admit(release, phase)
    assert (facts["version"], tuple(facts["targets"]), facts["phase"]) == (
        "1.2.3", ("linux-x64", "windows-x64", "macos-arm64"), phase), "valid release identity must precede format checks"
    # WHEN the actual rendering CLI writes its version-bound release body
    code, body = render_cli(release, phase)
    # THEN top links resolve unique sections and installation/download claims follow admission
    navigation = "[Release notes](#release-notes) | [Installation](#installation) | [Downloads](#downloads) | [Documentation](#documentation)"
    sections = re.findall(r'^<a name="([^"]+)"></a>\n## ([^\n]+)', body, re.MULTILINE)
    before_rule, _separator, after_rule = body.partition("\n---\n")
    installation = body.partition("## Installation\n")[2].partition('<a name="downloads"></a>')[0]
    downloads = body.partition("## Downloads\n")[2].partition('<a name="documentation"></a>')[0]
    documentation = body.partition("## Documentation\n")[2]
    commands = re.findall(r'^(?:python -m pip|uv tool|pipx|npm install|npx --yes|brewdoc)[^\n]*$', installation, re.MULTILINE)
    condition = "Staged release. Install commands and downloads apply after publication and verification."
    observations = (
        code, capsys.readouterr(), body.splitlines()[0], sections, body.count("\n---\n"),
        re.findall(r'^## (Release notes|Installation|Downloads|Documentation)$', before_rule, re.MULTILINE),
        re.findall(r'^## (Release notes|Installation|Downloads|Documentation)$', after_rule, re.MULTILINE),
        tuple(commands), body.count(condition),
        installation.count("Homebrew availability awaits tap publication and consumer verification."),
        re.findall(r'/releases/download/v1\.2\.3/([^\s)]+)', downloads),
        sorted(re.findall(r'/blob/v1\.2\.3/([^\s)]+)', documentation)),
    )
    assert observations == (
        0, ("", ""), navigation,
        [("release-notes", "Release notes"), ("installation", "Installation"),
         ("downloads", "Downloads"), ("documentation", "Documentation")], 1,
        ["Release notes"], ["Installation", "Downloads", "Documentation"],
        ('python -m pip install "brewdoc==1.2.3"', 'uv tool install --python 3.12 "brewdoc==1.2.3"',
         'pipx install --python 3.12 "brewdoc==1.2.3"', *npm_commands,
         "brewdoc --self-check", "brewdoc file.pdf --out file.md"), conditional_count, 1,
        [record["archive"]["filename"] for record in facts["targets"].values()] + ["SHA256SUMS"],
        ["FORMATS.md", "INSTALL.md", "README.md"],
    ), "release navigation, versioned installation and available native/registry channels must remain coherent"


def test_cli_notes_group_supplied_changes_examples_and_pr_before_installation(release, capsys):
    # GIVEN user-facing Added/Fixed sentences, relevant command examples and a supplied synthetic PR identity
    checkout = release[0]
    policy_path = checkout / ".github/distribution.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    added = ["Convert ODT files offline.", "Pass text files through without changing bytes."]
    fixed = ["Keep table captions beside their tables."]
    examples = ["brewdoc report.odt --out report.md", "brewdoc message.eml --out message.md"]
    policy.update(release_features=added, release_fixes=fixed, release_examples=examples, release_pr=123)
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    assert admit(release)["policy"]["release_pr"] == 123, "the fixture explicitly supplies the PR number without a network claim"
    # WHEN the actual CLI renders accepted local metadata
    code, body = render_cli(release, "staged")
    # THEN changes remain in their groups and examples/PR appear once inside release notes
    notes = body.partition("## Release notes\n")[2].partition("\n---\n")[0]
    added_block = notes.partition("### Added\n")[2].partition("\n### Fixed")[0]
    fixed_block = notes.partition("### Fixed\n")[2].partition("\nExamples:")[0]
    assert (code, capsys.readouterr(), re.findall(r'^- ([^\n]+)$', added_block, re.MULTILINE),
            re.findall(r'^- ([^\n]+)$', fixed_block, re.MULTILINE),
            re.findall(r'```sh\n(.*?)\n```', notes, re.DOTALL),
            re.findall(re.escape(guide.REPOSITORY) + r'/pull/\d+', notes)) == (
        0, ("", ""), added, fixed, ["\n".join(examples)], [f"{guide.REPOSITORY}/pull/123"],
    ), "supplied complete sentences must keep punctuation and examples/PR must stay within the relevant notes"


def test_cli_missing_optional_metadata_invents_no_fix_example_or_pr(release, capsys):
    # GIVEN an accepted release containing only the existing Added metadata
    policy = admit(release)["policy"]
    assert tuple(key in policy for key in ("release_fixes", "release_examples", "release_pr")) == (
        False, False, False), "the control must exercise absent optional fields rather than empty supplied values"
    # WHEN the actual CLI renders the minimal accepted policy
    code, body = render_cli(release, "staged")
    # THEN existing features gain their Added grouping with no invented optional content
    notes = body.partition("## Release notes\n")[2].partition("\n---\n")[0]
    assert (code, capsys.readouterr(), re.findall(r'^- ([^\n]+)$', notes, re.MULTILINE),
            notes.count("### Added"), notes.count("### Fixed"), notes.count("Examples:"),
            re.findall(r'```sh\n(.*?)\n```', notes, re.DOTALL),
            re.findall(re.escape(guide.REPOSITORY) + r'/pull/\d+', body)) == (
        0, ("", ""), ["Accepted conversion."], 1, 0, 0, [], [],
    ), "optional metadata absence must preserve Added while omitting empty fixes, examples and invented PR links"


@pytest.mark.parametrize("field,value", [
    ("release_fixes", "a scalar is not a list"),
    ("release_examples", ["brewdoc first.eml\nbrewdoc second.eml"]),
    ("release_pr", True), ("release_pr", 0),
], ids=["fixes-scalar", "multiline-example", "boolean-pr", "nonpositive-pr"])
def test_cli_malformed_optional_metadata_refuses_before_replacing_reviewed_output(release, capsys, field, value):
    # GIVEN valid admitted artifacts, malformed editorial metadata and a prior reviewed body
    assert admit(release)["version"] == "1.2.3", "artifact admission must pass before the metadata substitution"
    checkout = release[0]
    policy_path = checkout / ".github/distribution.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy[field] = value
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    output = checkout / "release-body.md"
    output.write_bytes(b"reviewed release body\n")
    # WHEN the actual CLI renders malformed optional metadata
    with pytest.raises(SystemExit) as refusal:
        render_cli(release, "staged")
    # THEN the field-specific refusal leaves the prior body intact
    captured = capsys.readouterr()
    assert (refusal.value.code, captured.out, captured.err, output.read_bytes()) == (
        1, "", f"Release guide refused: Guide release metadata differs: {field}\n", b"reviewed release body\n",
    ), "malformed metadata must identify its field before writing output"
