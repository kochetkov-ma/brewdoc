import copy
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from test_release_guide import HELPER, admit, release


FORMULA_TEMPLATE = '''# Source: __SOURCE_SHA__
require "json"

class Brewdoc < Formula
  desc "Deterministic document-to-Markdown conversion"
  homepage "https://github.com/kochetkov-ma/brewdoc"
  version "__VERSION__"
  license "MIT"

  on_macos do
    depends_on arch: :arm64
    depends_on macos: :sequoia
    preserve_rpath
    url "https://github.com/kochetkov-ma/brewdoc/releases/download/v__VERSION__/brewdoc-__VERSION__-macos-arm64.tar.gz"
    sha256 "__MACOS_SHA__"
  end

  on_linux do
    depends_on arch: :x86_64
    url "https://github.com/kochetkov-ma/brewdoc/releases/download/v__VERSION__/brewdoc-__VERSION__-linux-x64.tar.gz"
    sha256 "__LINUX_SHA__"
  end

  skip_clean "libexec"

  def install
    supported = (OS.mac? && Hardware::CPU.arm? && Hardware::CPU.is_64_bit?) ||
                (OS.linux? && Hardware::CPU.intel? && Hardware::CPU.is_64_bit?)
    odie "Brewdoc supports macOS ARM64 and Linux x64" unless supported
    odie "Brewdoc requires macOS 15 or newer" if OS.mac? && MacOS.version < :sequoia
    if OS.linux?
      glibc = Utils.safe_popen_read("/usr/bin/getconf", "GNU_LIBC_VERSION").strip
      parsed = /\\Aglibc (\\d+)\\.(\\d+)\\z/.match(glibc)
      odie "Brewdoc requires system glibc 2.35 or newer" unless parsed && (parsed.captures.map(&:to_i) <=> [2, 35]) >= 0
    end
    unless File.executable?("brewdoc") && File.directory?("_internal") &&
           File.directory?("licenses") && File.file?("version.json")
      odie "Unexpected Brewdoc archive layout"
    end
    libexec.install Dir.children(".") - [".brew_home"]
    bin.install_symlink libexec/"brewdoc"
  end

  test do
    assert_match "self-check: ok", shell_output("#{bin}/brewdoc --self-check")
    (testpath/"probe.html").write("<html><body><p>Homebrew probe</p></body></html>")
    output = shell_output("#{bin}/brewdoc #{testpath}/probe.html")
    receipt, markdown = output.split("\\n", 2)
    status = JSON.parse(receipt)
    assert_equal true, status.fetch("file_ok"), "The offline HTML probe must render"
    assert_equal "html", status.fetch("route"), "The local input must use the HTML route"
    assert_match "\\nHomebrew probe\\n", markdown
  end
end
'''


def expected_formula(native):
    """Declare complete Ruby using the independent admitted archive identities."""
    return (FORMULA_TEMPLATE.replace("__SOURCE_SHA__", native["source_sha"])
            .replace("__VERSION__", native["version"])
            .replace("__MACOS_SHA__", hashlib.sha256(b"macos-arm64").hexdigest())
            .replace("__LINUX_SHA__", hashlib.sha256(b"linux-x64").hexdigest()))


def formula_command(release, output, phase="public"):
    """Call the existing guide executable with the accepted new operation contract."""
    checkout, native, npm, availability = release
    paths = [checkout / name for name in ("binaries.json", "npm-manifest.json", "public-acceptance.json")]
    for path, value in zip(paths, (native, npm, availability), strict=True):
        path.write_text(json.dumps(value), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(HELPER), "homebrew", "--checkout", str(checkout), "--phase", phase,
         "--native-manifest", str(paths[0]), "--npm-manifest", str(paths[1]),
         "--availability", str(paths[2]), "--output", str(output)],
        capture_output=True, timeout=5, check=False)


def test_homebrew_command_renders_exact_public_unix_bundle_formula(release):
    # GIVEN complete public facts admitted by the existing release guard
    checkout, native, _npm, _availability = release
    facts = admit(release, "public")
    assert (facts["version"], facts["source_sha"], tuple(facts["targets"]), facts["phase"]) == (
        "1.2.3", "a" * 40, ("linux-x64", "windows-x64", "macos-arm64"), "public",
    ), "the synthetic release must be valid before testing the missing formula operation"
    output = checkout / "brewdoc.rb"
    assert output.exists() is False, "formula generation must begin without an output file"
    # WHEN the existing release guide is asked for a Homebrew formula
    process = formula_command(release, output)
    # THEN the operation succeeds and produces the complete immutable native-bundle formula
    assert (process.returncode, process.stdout, process.stderr) == (0, b"", b""), (
        "the current CLI must implement formula generation for already admitted public facts")
    assert output.read_bytes() == expected_formula(native).encode("utf-8"), (
        "the formula must retain whole bundles, precise platform floors, preservation flags and offline probes")


@pytest.mark.skipif(shutil.which("ruby") is None, reason="Executing the emitted Homebrew install selection requires Ruby.")
def test_emitted_homebrew_install_excludes_staging_home_and_retains_hidden_payload(tmp_path, release, homebrew):
    # GIVEN a real staging tree containing bundle payload and Homebrew's unrelated staging home
    stage = tmp_path / "stage"
    stage.mkdir()
    for name in ("_internal", "licenses", ".payload-data", ".brew_home"):
        (stage / name).mkdir()
    for name in ("brewdoc", "version.json", ".payload-identity", "_internal/engine.so",
                 "licenses/MIT.txt", ".payload-data/seal", ".brew_home/.bazelrc", ".brew_home/.gitignore"):
        (stage / name).write_bytes(b"small synthetic payload\n")
    payload = [".payload-data", ".payload-identity", "_internal", "brewdoc", "licenses", "version.json"]
    assert sorted(path.name for path in stage.iterdir()) == [".brew_home", *payload], (
        "the staging precondition must include both legitimate hidden payload and unrelated Homebrew state")
    formula = homebrew.guide.render_homebrew(admit(release, "public"))
    statements = [line.strip() for line in formula.splitlines() if line.strip().startswith("libexec.install ")]
    assert len(statements) == 1, "the test must execute exactly the emitted bundle-copy selection"
    capture = ('require "json"\n'
               'capture = Object.new\n'
               'def capture.install(paths); puts JSON.generate(paths.sort); end\n'
               'define_singleton_method(:libexec) { capture }\n')
    # WHEN actual Ruby executes the generated install selection against this physical staging tree
    process = subprocess.run([shutil.which("ruby"), "-e", capture + statements[0]], cwd=stage,
                             capture_output=True, timeout=5, check=False)
    # THEN only Homebrew's staging home is excluded and every hidden or ordinary payload entry remains
    assert (process.returncode, process.stderr) == (0, b""), "the actual emitted Ruby statement must execute successfully"
    assert json.loads(process.stdout) == payload, "bundle copy must exclude only .brew_home while preserving complete payload"


def test_staged_homebrew_request_refuses_without_replacing_reviewed_formula(release):
    # GIVEN valid staged artifacts and a previously reviewed formula
    checkout = release[0]
    facts = admit(release, "staged")
    assert facts["phase"] == "staged", "staged admission must succeed before the Homebrew public-only guard"
    output = checkout / "brewdoc.rb"
    previous = b"reviewed public formula\n"
    output.write_bytes(previous)
    # WHEN Homebrew generation receives staged rather than public facts
    process = formula_command(release, output, "staged")
    # THEN semantic refusal preserves the earlier complete formula
    assert (process.returncode, process.stdout, output.read_bytes()) == (1, b"", previous), (
        "staged facts must be refused before replacing a reviewed formula")


@pytest.mark.parametrize(("field", "value"), [("version", "1.2.4"), ("source_sha", "b" * 40)],
                         ids=["version", "source"])
def test_homebrew_release_identity_drift_refuses_before_formula_write(release, field, value):
    # GIVEN valid public facts followed by one native release identity substitution
    checkout, native, _npm, _availability = release
    assert admit(release, "public")["version"] == "1.2.3", "the unchanged release must pass public admission"
    native[field] = value
    output = checkout / "brewdoc.rb"
    previous = b"reviewed public formula\n"
    output.write_bytes(previous)
    # WHEN formula generation sees the mismatched version or source
    process = formula_command(release, output)
    # THEN admission refuses without creating a formula from foreign bytes
    assert (process.returncode, process.stdout, output.read_bytes()) == (1, b"", previous), (
        "release identity drift must not replace the accepted formula")


def test_homebrew_changed_native_archive_hash_refuses_before_formula_write(release):
    # GIVEN admitted public bytes whose native manifest hash is then replaced
    checkout, native, _npm, _availability = release
    assert admit(release, "public")["phase"] == "public", "valid public bytes must be admitted first"
    native["targets"]["linux-x64"]["archive"]["sha256"] = "0" * 64
    output = checkout / "brewdoc.rb"
    previous = b"reviewed public formula\n"
    output.write_bytes(previous)
    # WHEN formula generation sees the altered digest
    process = formula_command(release, output)
    # THEN the manifest cannot advertise unadmitted archive bytes
    assert (process.returncode, process.stdout, output.read_bytes()) == (1, b"", previous), (
        "the Homebrew URL digest must match admitted native bytes before output changes")


def test_homebrew_unknown_native_target_refuses_before_formula_write(release):
    # GIVEN public release facts followed by an unverified architecture target
    checkout, native, _npm, _availability = release
    assert tuple(admit(release, "public")["targets"]) == ("linux-x64", "windows-x64", "macos-arm64"), (
        "the source release must have the accepted native target set")
    native["targets"]["linux-arm64"] = copy.deepcopy(native["targets"]["linux-x64"])
    output = checkout / "brewdoc.rb"
    previous = b"reviewed public formula\n"
    output.write_bytes(previous)
    # WHEN the operation receives a target without policy or public acceptance
    process = formula_command(release, output)
    # THEN unverified architectures cannot enter a generated formula
    assert (process.returncode, process.stdout, output.read_bytes()) == (1, b"", previous), (
        "an unknown target must refuse without changing the reviewed formula")


def test_homebrew_failed_public_native_consumer_refuses_before_formula_write(release):
    # GIVEN public bytes whose native consumer proof is then changed to failure
    checkout, _native, _npm, availability = release
    assert admit(release, "public")["phase"] == "public", "the unchanged public consumers must pass admission"
    availability["consumers"]["linux-x64"]["native"] = "FAIL"
    output = checkout / "brewdoc.rb"
    previous = b"reviewed public formula\n"
    output.write_bytes(previous)
    # WHEN formula generation reads failed public runtime acceptance
    process = formula_command(release, output)
    # THEN bytes alone cannot authorize a new installation channel
    assert (process.returncode, process.stdout, output.read_bytes()) == (1, b"", previous), (
        "public native consumer acceptance is required before formula output changes")


@pytest.fixture
def homebrew():
    """Load the concrete consumer without invoking Homebrew or public services."""
    spec = importlib.util.spec_from_file_location("homebrew_consumer", HELPER.with_name("verify_homebrew.py"))
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)
    return consumer


def public_formula_record(native):
    """Describe controlled GitHub bytes for the canonical synthetic formula."""
    payload = expected_formula(native).encode("utf-8")
    return {"type": "file", "path": "Formula/brewdoc.rb", "encoding": "base64", "size": len(payload),
            "content": base64.b64encode(payload).decode("ascii"), "sha": "e" * 40}


def installed_tree(directory):
    """Build a complete small Unix bundle with explicit modes and one internal link."""
    root = directory / "libexec"
    root.mkdir(mode=0o755)
    (root / "_internal").mkdir(mode=0o755)
    (root / "licenses").mkdir(mode=0o755)
    for directory in (root, root / "_internal", root / "licenses"):
        directory.chmod(0o755)
    files = {"brewdoc": (b"executable bytes\n", 0o755), "version.json": (b"{}\n", 0o644),
             "_internal/engine.so": (b"engine bytes\n", 0o644), "licenses/MIT.txt": (b"MIT notice\n", 0o644)}
    for name, (payload, mode) in files.items():
        path = root / name
        path.write_bytes(payload)
        path.chmod(mode)
    link = root / "_internal/engine-link.so"
    link.symlink_to("engine.so")
    expected = {"brewdoc": {"kind": "directory", "mode": 0o755},
                "brewdoc/_internal": {"kind": "directory", "mode": 0o755},
                "brewdoc/licenses": {"kind": "directory", "mode": 0o755},
                "brewdoc/_internal/engine-link.so": {"kind": "symlink", "target": "engine.so",
                                                    "mode": stat.S_IMODE(link.lstat().st_mode)}}
    expected.update({"brewdoc/" + name: {"kind": "file", "bytes": len(payload),
                                       "sha256": hashlib.sha256(payload).hexdigest(), "mode": mode}
                     for name, (payload, mode) in files.items()})
    return root, expected


@pytest.mark.skipif(os.name != "posix", reason="Homebrew bundle modes and links require Unix")
def test_installed_inventory_normalizes_only_the_outer_bundle_name(tmp_path, homebrew):
    # GIVEN an admitted full file, directory, mode and link inventory
    root, expected = installed_tree(tmp_path)
    output = tmp_path / "inventory.json"
    assert root.name == "libexec", "the installed root must differ from the sealed archive root"
    # WHEN the consumer compares the real installed tree
    actual = homebrew.verify_inventory(root, {"inventory": expected}, output)
    # THEN every complete inventory row remains exact after the one allowed root relocation
    assert (actual, json.loads(output.read_text())) == (expected, expected), (
        "libexec relocation must preserve every installed byte, size, mode, directory and internal link")


@pytest.mark.skipif(os.name != "posix", reason="Homebrew bundle modes and links require Unix")
@pytest.mark.parametrize(("alter", "name", "changed"), [
    (lambda root: (root / "brewdoc").write_bytes(b"altered executable\n"), "brewdoc/brewdoc",
     {"kind": "file", "bytes": 19, "sha256": hashlib.sha256(b"altered executable\n").hexdigest(), "mode": 0o755}),
    (lambda root: (root / "_internal/engine.so").chmod(0o600), "brewdoc/_internal/engine.so",
     {"kind": "file", "bytes": 13, "sha256": hashlib.sha256(b"engine bytes\n").hexdigest(), "mode": 0o600}),
    (lambda root: ((root / "_internal/engine-link.so").unlink(),
                  (root / "_internal/engine-link.so").symlink_to("../licenses/MIT.txt")),
     "brewdoc/_internal/engine-link.so", {"kind": "symlink", "target": "../licenses/MIT.txt"}),
], ids=["bytes", "mode", "internal-link"])
def test_installed_inventory_refuses_drift_and_retains_actual_evidence(tmp_path, homebrew, alter, name, changed):
    # GIVEN a complete immutable expected inventory followed by one installed change
    root, expected = installed_tree(tmp_path)
    output = tmp_path / "inventory.json"
    assert homebrew.verify_inventory(root, {"inventory": expected}, output) == expected, (
        "the unmodified installed bundle must satisfy the exact admission precondition")
    alter(root)
    observed = {**expected, name: {**expected[name], **changed}}
    # WHEN bytes, modes or an internal target differ after install
    with pytest.raises(ValueError, match="^Installed Homebrew inventory differs$"):
        homebrew.verify_inventory(root, {"inventory": expected}, output)
    # THEN the first mismatch is retained without refreshing accepted expectations
    assert json.loads(output.read_text()) == observed, "refusal evidence must contain the actual changed full inventory"


@pytest.mark.skipif(os.name != "posix", reason="Homebrew bundle modes and links require Unix")
def test_installed_inventory_refuses_an_escaping_link_before_writing_evidence(tmp_path, homebrew):
    # GIVEN an admitted bundle whose internal link is retargeted outside the bundle
    root, expected = installed_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside sentinel\n")
    output = tmp_path / "inventory.json"
    assert output.exists() is False, "unsafe inventory inspection must begin without a destination"
    link = root / "_internal/engine-link.so"
    link.unlink()
    link.symlink_to(outside)
    # WHEN the installed inventory inspects the unsafe target
    with pytest.raises(ValueError, match="^Standalone link escapes its bundle:"):
        homebrew.verify_inventory(root, {"inventory": expected}, output)
    # THEN the outside file is untouched and no misleading accepted snapshot exists
    assert (output.exists(), outside.read_bytes()) == (False, b"outside sentinel\n"), (
        "an escaping link must refuse without following or modifying outside content")


def consumer_arguments(release):
    """Create task-local dispatch paths with complete valid initial identities."""
    checkout = release[0]
    brew = checkout / "brew"
    brew.write_bytes(b"unit Homebrew path\n")
    return argparse.Namespace(checkout=checkout, brew=brew, output=checkout / "evidence",
                              tag="v1.2.3", source_sha="a" * 40, bootstrap_tap_sha="b" * 40,
                              tap_sha="c" * 40, target="linux-x64")


def reject_external_activity(*args, **kwargs):
    """Fail an identity test if it reaches an external operation."""
    raise AssertionError("identity refusal must precede external activity")


@pytest.mark.parametrize(("field", "value"), [("tag", "v1.2.4"), ("source_sha", "G" * 40),
                                           ("tap_sha", "short"), ("bootstrap_tap_sha", "c" * 40)],
                         ids=["tag-version", "source-format", "tap-format", "same-tap-revisions"])
def test_dispatch_identity_refusal_precedes_external_activity_and_output_creation(
        release, homebrew, monkeypatch, field, value):
    # GIVEN valid local project metadata and one unsafe or mismatched dispatch identity
    arguments = consumer_arguments(release)
    assert homebrew.guide.project_version(arguments.checkout) == "1.2.3", "project identity must be valid first"
    setattr(arguments, field, value)
    monkeypatch.setattr(homebrew.assets, "command", reject_external_activity)
    monkeypatch.setattr(homebrew, "public_native", reject_external_activity)
    monkeypatch.setattr(homebrew.subprocess, "run", reject_external_activity)
    # WHEN the consumer validates dispatch inputs
    with pytest.raises(ValueError, match="^Homebrew dispatch identities differ$"):
        homebrew.verify(arguments)
    # THEN no installer, network call or evidence parent can exist
    assert arguments.output.exists() is False, "invalid dispatch identity must refuse before external or output work"


def test_checkout_source_mismatch_stops_before_native_or_public_work(release, homebrew, monkeypatch):
    # GIVEN valid dispatch syntax naming a source different from the actual checkout
    arguments = consumer_arguments(release)
    requests = []
    assert arguments.output.exists() is False, "checkout refusal must start before evidence creation"

    def checkout_head(command):
        """Return a controlled different HEAD at the Git read boundary."""
        requests.append(command)
        return ("d" * 40 + "\n").encode()

    monkeypatch.setattr(homebrew.assets, "command", checkout_head)
    monkeypatch.setattr(homebrew.native, "facts", reject_external_activity)
    monkeypatch.setattr(homebrew, "public_native", reject_external_activity)
    # WHEN source identity is compared with the actual checkout
    with pytest.raises(ValueError, match="^Homebrew checkout source differs$"):
        homebrew.verify(arguments)
    # THEN only the necessary Git read happened and no public/install work began
    assert (requests, arguments.output.exists()) == (
        [["git", "-C", str(arguments.checkout), "rev-parse", "HEAD"]], False,
    ), "foreign source must stop before native validation, publication reads or output changes"


def test_published_formula_admits_exact_bytes_at_the_selected_immutable_commit(release, homebrew, monkeypatch):
    # GIVEN independently declared canonical Ruby in a controlled public file response
    _checkout, native, _npm, _availability = release
    record = public_formula_record(native)
    assert base64.b64decode(record["content"]) == expected_formula(native).encode(), (
        "the public response must carry the complete canonical formula bytes")
    requests = []

    def public_read(command):
        """Return one controlled public response without contacting GitHub."""
        requests.append(command)
        return json.dumps(record).encode()

    monkeypatch.setattr(homebrew.assets, "command", public_read)
    # WHEN an exact published commit is checked against admitted native facts
    actual = homebrew.published_formula("c" * 40, native)
    # THEN version/source provenance and the immutable request are exact
    assert (actual, requests) == (
        {"tap_sha": "c" * 40, "version": "1.2.3", "source_sha": "a" * 40},
        [["gh", "api", "repos/kochetkov-ma/homebrew-brew/contents/Formula/brewdoc.rb?ref=" + "c" * 40]],
    ), "formula identity must come from exact canonical bytes at the selected tap commit"


@pytest.mark.parametrize("alter", [
    lambda record: record.update(type="dir"), lambda record: record.update(path="Formula/foreign.rb"),
    lambda record: record.update(encoding="utf-8"), lambda record: record.update(size=0),
    lambda record: record.update(content=base64.b64encode(b"foreign formula\n").decode(), size=16),
], ids=["type", "path", "encoding", "size", "formula-bytes"])
def test_published_formula_refuses_wrong_file_or_foreign_bytes(release, homebrew, monkeypatch, alter):
    # GIVEN exact canonical public bytes followed by one file identity or content substitution
    _checkout, native, _npm, _availability = release
    record = public_formula_record(native)
    assert record["size"] == len(expected_formula(native).encode()), "the original response must have exact declared size"
    alter(record)
    requests = []

    def public_read(command):
        """Return changed public input at the existing I/O boundary."""
        requests.append(command)
        return json.dumps(record).encode()

    monkeypatch.setattr(homebrew.assets, "command", public_read)
    # WHEN the consumer compares the selected public file with immutable native facts
    with pytest.raises(ValueError):
        homebrew.published_formula("c" * 40, native)
    # THEN substituted files or formula bytes cannot become accepted provenance
    assert requests == [["gh", "api", "repos/kochetkov-ma/homebrew-brew/contents/Formula/brewdoc.rb?ref=" + "c" * 40]], (
        "public formula refusal must concern the exact requested commit and path")


@pytest.mark.skipif(os.name != "posix", reason="Homebrew executable entrypoints require Unix")
def test_installed_probe_uses_the_passed_executable_for_sealed_stdout_and_file_outputs(
        tmp_path, homebrew, monkeypatch):
    # GIVEN a task-local installed entrypoint and a sealed source-engine HTML probe
    import brewdoc

    source = tmp_path / "probe.html"
    payload = b"<html><body><p>Installed consumer</p></body></html>"
    source.write_bytes(payload)
    code, receipt, markdown = brewdoc.run(source)
    assert (code, receipt["file_ok"]) == (0, True), "the independent source engine must admit the sealed local probe"
    binary = tmp_path / "installed brewdoc"
    binary.write_text("#!" + sys.executable + "\nfrom brewdoc.cli import main\nraise SystemExit(main())\n")
    binary.chmod(0o755)
    row = {"probe": "html", "path": "probe.html", "bytes_base64": base64.b64encode(payload).decode(),
           "source_sha256": hashlib.sha256(payload).hexdigest(), "code": code, "receipt": receipt,
           "markdown_bytes": len(markdown.encode()), "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest()}
    commands, boundaries = [], []
    original_command = homebrew.native.command

    def invoke(command, **keywords):
        """Observe real converter commands without replacing their output."""
        commands.append(command)
        return original_command(command, **keywords)

    def refusal(command, work, environment):
        """Record the existing refusal boundary without claiming native proof."""
        boundaries.append(("refusal", command))
        return {"unit_boundary": "refusal"}

    def children(executable, work, environment, javascript):
        """Record child-check routing without simulating a hosted PASS."""
        boundaries.append(("children", executable, javascript))
        return {"unit_boundary": "children"}

    monkeypatch.setattr(homebrew.native, "command", invoke)
    monkeypatch.setattr(homebrew.native, "extract_archive", reject_external_activity)
    monkeypatch.setattr(homebrew.native, "zip_xml_refusal_probe", refusal)
    monkeypatch.setattr(homebrew.native, "child_checks", children)
    work = tmp_path / "consumer-work"
    # WHEN stdout and --out probes run through the consumer
    actual = homebrew.installed_probe(binary, {"verification_scope": "smoke", "probes": [row]}, work)
    # THEN every real command and subordinate check uses the passed installed executable
    assert (actual, commands, boundaries, (work / "html.md").read_bytes()) == (
        {"self_check": 38, "probes": ["html"], "zip_xml_refusal": {"unit_boundary": "refusal"},
         "children": {"unit_boundary": "children"}},
        [[str(binary), "--self-check"], [str(binary), str(work / "probe.html")],
         [str(binary), str(work / "probe.html"), "--out", str(work / "html.md")]],
        [("refusal", [str(binary)]), ("children", binary, True)], markdown.encode(),
    ), "installed probes must reuse the installed path and exact sealed output without extracting another archive"


def test_failed_install_stops_upgrade_retains_command_evidence_and_cleans_temporary_work(
        release, homebrew, monkeypatch):
    # GIVEN valid local identities and controlled external preflight responses
    arguments = consumer_arguments(release)
    checkout, native, _npm, _availability = release
    old = {**native, "version": "0.4.1", "source_sha": "d" * 40,
           "targets": {target: {**record, "version": "0.4.1", "source_sha": "d" * 40,
                                "archive": {**record["archive"], "filename": record["archive"]["filename"].replace("1.2.3", "0.4.1")}}
                       for target, record in native["targets"].items()}}
    repository, tap = checkout / "brew-repository", checkout / "tap"
    repository.mkdir()
    (tap / "Formula").mkdir(parents=True)
    preflight = {"v0.4.1": ({"source_sha": "d" * 40}, old), "v1.2.3": ({"source_sha": "a" * 40}, native)}
    monkeypatch.setattr(homebrew, "public_native", lambda tag, output: preflight[tag])
    monkeypatch.setattr(homebrew, "published_formula", lambda sha, binaries: {"tap_sha": sha})
    monkeypatch.setattr(homebrew.native, "facts", lambda *args: {})
    old_formula = homebrew.guide.render_homebrew({"phase": "public", **old})
    callbacks = {
        ("git", "-C", str(checkout), "rev-parse", "HEAD"): lambda: ("a" * 40 + "\n").encode(),
        ("gh", "api", "repos/kochetkov-ma/homebrew-brew/git/ref/heads/main"): lambda: json.dumps({"object": {"sha": "c" * 40}}).encode(),
        ("git", "-C", str(repository), "rev-parse", "HEAD"): lambda: ("e" * 40 + "\n").encode(),
        ("git", "-C", str(tap), "merge-base", "--is-ancestor", "b" * 40, "c" * 40): lambda: b"",
        ("git", "-C", str(tap), "checkout", "--detach", "b" * 40): lambda: (
            (tap / "Formula/brewdoc.rb").write_text(old_formula), b"")[1],
    }
    monkeypatch.setattr(homebrew.assets, "command", lambda command: callbacks[tuple(command)]())
    replies = {("--version",): (0, b"Homebrew unit protocol\n", b""),
               ("--repository",): (0, (str(repository) + "\n").encode(), b""),
               ("list", "--formula"): (0, b"", b""), ("tap",): (0, b"", b""),
               ("tap", "kochetkov-ma/brew"): (0, b"", b""),
               ("--repository", "kochetkov-ma/brew"): (0, (str(tap) + "\n").encode(), b""),
               ("install", "--build-from-source", "kochetkov-ma/brew/brewdoc"): (1, b"install details\n", b"unit install failure\n")}
    commands, temporary_work = [], []
    original_temporary = homebrew.tempfile.TemporaryDirectory

    def brew_command(command, **keywords):
        """Return a failed install at the process boundary without executing Homebrew."""
        commands.append(command)
        assert (keywords["env"]["HOMEBREW_NO_AUTO_UPDATE"], "GH_TOKEN" in keywords["env"]) == ("1", False), (
            "brew must keep immutable tap state and receive no GitHub token")
        return subprocess.CompletedProcess(command, *replies[tuple(command[1:])])

    def task_temporary(**keywords):
        """Keep actual temporary directory ownership inside this test's tmp_path."""
        context = original_temporary(dir=checkout, **keywords)
        temporary_work.append(Path(context.name))
        return context

    monkeypatch.setenv("GH_TOKEN", "synthetic unit token")
    monkeypatch.setattr(homebrew.subprocess, "run", brew_command)
    monkeypatch.setattr(homebrew.tempfile, "TemporaryDirectory", task_temporary)
    assert arguments.output.exists() is False, "the failure test must begin without acceptance evidence"
    # WHEN the first real lifecycle operation reports an install failure
    with pytest.raises(ValueError, match="^Homebrew lifecycle command failed: install$"):
        homebrew.verify(arguments)
    # THEN no upgrade or acceptance follows, diagnostics remain and temporary sentinels are cleaned
    assert (commands, [path.exists() for path in temporary_work],
            (arguments.output / "verification.json").exists(),
            (arguments.output / "command-6.stdout").read_bytes(),
            (arguments.output / "command-6.stderr").read_bytes()) == (
        [[str(arguments.brew), *command] for command in replies], [False], False,
        b"install details\n", b"unit install failure\n",
    ), "first install failure must stop upgrade and retain truthful failure evidence without leaking temporary work"
