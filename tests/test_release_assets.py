"""Complete release admission refuses partial assets and unavailable preflight."""

import copy
from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import zipfile
from types import SimpleNamespace

import pytest

CHECKOUT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_assets", CHECKOUT / ".github/workflows/release_assets.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
SOURCE = "a" * 40
VERSION = "0.3.0"


def producer_run():
    """Describe one successful canonical main producer without network access."""
    return {"id": 17, "run_attempt": 2, "event": "push", "head_branch": "main", "head_sha": SOURCE,
            "status": "completed", "conclusion": "success", "path": ".github/workflows/ci.yml",
            "repository": {"full_name": RELEASE.REPOSITORY}, "head_repository": {"full_name": RELEASE.REPOSITORY}}


def test_ci_admission_accepts_native_bundle_and_only_two_npm_consumer_artifacts():
    # GIVEN one sealed producer and reports for both supported npm hosts
    names = ("ci-dist", "ci-native-assets", "ci-npm-release", "ci-npm-consumer-linux-x64", "ci-npm-consumer-macos-arm64")
    rows = [{"id": index, "name": name, "expired": False, "size_in_bytes": 12,
             "expires_at": "2999-01-01T00:00:00Z", "digest": "sha256:" + "c" * 64,
             "workflow_run": {"id": 17, "head_sha": SOURCE}} for index, name in enumerate(names, 1)]
    assert len(rows) == 5, "native Windows is bundled without a Windows npm consumer artifact"
    # WHEN the complete producer set is admitted without a Windows npm artifact
    actual = RELEASE.admit_ci_artifacts(rows, producer_run())
    # THEN each original artifact is retained under its exact canonical name
    assert actual == dict(zip(names, rows)), "two supported npm hosts complete the artifact set"


def test_retired_v031_cli_refuses_before_reading_release_bytes(tmp_path, monkeypatch, capsys):
    # GIVEN a retired entrypoint and paths that must never reach release admission
    calls = []
    monkeypatch.setattr(RELEASE, "identity", lambda path: calls.append(path) or {"bytes": 0, "sha256": "0" * 64})
    assert calls == [], "the retired operation starts without any manifest read"
    # WHEN the controller receives the old fixed-version command
    with pytest.raises(SystemExit) as error:
        RELEASE.main(["admit-v031", "--input", str(tmp_path / "release"), "--output", str(tmp_path / "receipt.json")])
    # THEN usage refusal precedes every manifest read and receipt write
    captured = capsys.readouterr()
    assert (error.value.code, calls, captured.out, (tmp_path / "receipt.json").exists()) == (
        2, [], "", False), "the retired CLI cannot admit or resume original v0.3.1 publication"


def test_npm_report_refuses_windows_before_accessing_consumer_metadata():
    # GIVEN a native-only Windows target without any npm evidence
    report = {}
    assert report == {}, "native Windows provides no synthetic npm consumer metadata"
    # WHEN npm admission receives the unsupported target
    with pytest.raises(ValueError, match="npm target is unsupported"):
        RELEASE.npm_report(report, "windows-x64", {}, {}, {}, "public")
    # THEN unsupported-target refusal leaves the report untouched
    assert report == {}, "npm admission cannot fabricate Windows publication evidence"


@pytest.mark.parametrize("delta", [{"event": "pull_request"}, {"head_branch": "feature"},
    {"head_sha": "b" * 40}, {"conclusion": "cancelled"}, {"path": ".github/workflows/other.yml"},
    {"repository": {"full_name": "fork/brewdoc"}}])
def test_ci_producer_refuses_untrusted_source_before_artifact_access(delta):
    # GIVEN a completed producer whose trust identity differs
    run = {**producer_run(), **delta}
    assert producer_run()["head_sha"] == SOURCE, "the control producer matches the immutable tag source"
    # WHEN release admission checks the producer
    with pytest.raises(ValueError, match="producer identity"):
        RELEASE.admit_producer(run, SOURCE)
    # THEN admission never relabels its source identity
    assert run == {**producer_run(), **delta}, "refusal preserves the original producer metadata"


@pytest.mark.parametrize("change", ["missing", "duplicate", "expired", "digest", "run"])
def test_ci_artifact_admission_refuses_incomplete_or_unverifiable_sets(change):
    # GIVEN every required artifact has a bounded digest and matching run identity
    rows = [{"id": index + 1, "name": name, "expired": False, "size_in_bytes": 12,
             "expires_at": "2999-01-01T00:00:00Z",
             "digest": "sha256:" + "c" * 64, "workflow_run": {"id": 17, "head_sha": SOURCE}}
            for index, name in enumerate(RELEASE.CI_ARTIFACTS)]
    assert len(rows) == 5, "package, native, npm and two consumer artifacts are required"
    mutations = {"missing": lambda: rows.pop(), "duplicate": lambda: rows.append(copy.deepcopy(rows[0])),
                 "expired": lambda: rows[0].update(expired=True), "digest": lambda: rows[0].update(digest=""),
                 "run": lambda: rows[0]["workflow_run"].update(head_sha="d" * 40)}
    mutations[change]()
    # WHEN artifact identities are admitted before any download
    with pytest.raises(ValueError, match="CI artifact"):
        RELEASE.admit_ci_artifacts(rows, producer_run())
    # THEN no missing or foreign artifact receives a fabricated digest
    assert rows[0]["id"] == 1, "admission does not replace producer artifacts"


@pytest.mark.parametrize("paths,expected", [(["docs/architecture.md"], False), (["README.md"], True),
    (["RELEASING.md"], False), (["FORMATS.md"], True), (["unknown.md"], True), ([], True),
    (["docs/architecture.md", "src/brewdoc/cli.py"], True), (["docs/../README.md"], True)])
def test_ci_path_gate_skips_only_proven_nonpackage_documentation(paths, expected):
    # GIVEN a complete list of changed paths
    snapshot = list(paths)
    assert paths == snapshot, "path selection starts without normalization or deletion"
    # WHEN the always-running gate selects package smoke
    actual = RELEASE.needs_smoke(paths)
    # THEN package inputs and unknown or incomplete changes retain all checks
    assert actual is expected, "only ordinary docs and exact release controls can skip the artifact graph"


@pytest.mark.parametrize("paths,expected", [
    ([".github/workflows/release.yml"], False),
    ([".github/workflows/release_assets.py"], False),
    (["tests/test_release_assets.py", "docs/architecture.md"], False),
    ([".github/workflows/ci.yml"], True),
    ([".github/workflows/release_assets.py", ".github/workflows/build_npm.py"], True),
], ids=["release-workflow", "release-controller", "controller-tests-and-docs", "package-ci", "mixed-package-input"])
def test_ci_path_gate_skips_exact_release_controls_and_keeps_package_changes(paths, expected):
    # GIVEN controller-only changes or a nearby package-producing input
    snapshot = list(paths)
    assert paths == snapshot, "the complete changed-path observation is preserved"
    # WHEN package smoke admission separates fixed release control from package inputs
    actual = RELEASE.needs_smoke(paths)
    # THEN only the accepted exact controller files may skip package construction
    assert actual is expected, "neighboring workflow and npm build inputs retain package smoke"


def push_scope_fixture(tmp_path, monkeypatch):
    """Provide an exact main push event and fake only read-only Git command output."""
    before, after = "a" * 40, "b" * 40
    event = tmp_path / "push.json"
    event.write_text(json.dumps({"before": before, "after": after, "ref": "refs/heads/main"}), encoding="utf-8")
    for name, value in {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
                        "GITHUB_SHA": after, "GITHUB_EVENT_PATH": str(event)}.items():
        monkeypatch.setenv(name, value)
    calls = []

    def git(arguments, payload=None):
        """Return HEAD and the controller-only diff without mutating Git."""
        calls.append(arguments)
        return {"rev-parse": (after + "\n").encode(), "diff": b".github/workflows/release.yml\0"}[arguments[3]]

    monkeypatch.setattr(RELEASE, "command", git)
    return event, before, after, calls


def test_ci_scope_reads_exact_main_push_event_when_both_explicit_commits_are_absent(tmp_path, monkeypatch):
    # GIVEN a bounded main push event whose after commit equals checkout HEAD and GITHUB_SHA
    _event, before, after, calls = push_scope_fixture(tmp_path, monkeypatch)
    output = tmp_path / "github-output"
    assert calls == [], "no Git observation precedes scope admission"
    # WHEN the existing CLI receives no explicit before or after commit
    actual = RELEASE.main(["ci-scope", "--checkout", str(tmp_path), "--base", "", "--head", "", "--github-output", str(output)])
    # THEN the exact controller diff can skip rebuilding unchanged release packages
    assert (actual, output.read_text(encoding="utf-8")) == (0, "smoke=false\n"), "valid push evidence supplies the missing commit pair"
    assert [call for call in calls if call[3] == "diff"] == [["git", "-C", str(tmp_path), "diff", "--name-only", "-z",
        "--no-renames", before, after]], "the scope gate compares only the admitted immutable push commits"


@pytest.mark.parametrize("change,base,head", [
    ("foreign-ref", "", ""), ("foreign-event", "", ""), ("foreign-after", "", ""),
    ("zero-before", "", ""), ("malformed", "", ""), ("array", "", ""), ("oversized", "", ""),
    ("valid", "a" * 40, ""),
], ids=["foreign-ref", "foreign-event", "foreign-after", "zero-before", "malformed", "array", "oversized", "partial-explicit-pair"])
def test_ci_scope_keeps_smoke_for_untrusted_or_partial_push_evidence(tmp_path, monkeypatch, change, base, head):
    # GIVEN a controller-only diff with incomplete or untrusted event evidence
    event, before, after, calls = push_scope_fixture(tmp_path, monkeypatch)
    assert calls == [], "the event guard starts without any Git diff observation"
    mutations = {"foreign-ref": lambda: monkeypatch.setenv("GITHUB_REF", "refs/heads/feature"),
                 "foreign-event": lambda: monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch"),
                 "foreign-after": lambda: event.write_text(json.dumps({"before": before, "after": "c" * 40})),
                 "zero-before": lambda: event.write_text(json.dumps({"before": "0" * 40, "after": after})),
                 "malformed": lambda: event.write_bytes(b"not JSON"),
                 "array": lambda: event.write_bytes(b"[]"),
                 "oversized": lambda: event.write_bytes(b" " * (64 * 1024 + 1)), "valid": lambda: None}
    mutations[change]()
    # WHEN the scope gate considers absent or partially supplied explicit IDs
    actual = RELEASE.ci_scope(tmp_path, base, head)
    # THEN unavailable exact commit evidence retains package checks
    assert actual is True, "untrusted or incomplete scope evidence must run smoke"
    assert [call for call in calls if call[3] == "diff"] == [], "invalid event evidence never reaches Git diff"


def npm_archives(output, targets, *, version=VERSION, source_sha=SOURCE):
    """Produce small real tarballs through the npm owner's exact metadata contract."""
    builder = RELEASE.helper("build_npm")
    output.mkdir()
    records = []
    for target in [*builder.PACKAGES, None]:
        metadata = builder.package_metadata(version, target)
        payloads = {"package.json": json.dumps(metadata).encode()}
        record = {"version": version, "source_sha": source_sha, "metadata": metadata}
        if target is not None:
            native = targets[target]
            verified = {"verdict": "PASS", "target": target, "version": version, "source_sha": source_sha,
                        "archive_sha256": native["archive"]["sha256"]}
            scope = native.get("verification_scope", "full")
            verified.update(verification_scope=scope, schema=1, self_check=38, fixtures=[],
                            probes=[row["probe"] for row in native["probes"]])
            if "inspection" in native:
                verified["inspection"] = RELEASE.helper("verify_install").expected_inspection_result(native["inspection"])
            payloads.update({"native-manifest.json": json.dumps(native).encode(),
                             "native-verification.json": json.dumps(verified).encode(),
                             "binary/" + native["executable"].removeprefix("brewdoc/"): b"synthetic native executable"})
            record.update({"target": target, "native_archive": native["archive"], "links": {}})
        filename = metadata["name"] + "-" + version + ".tgz"
        inventory = {}
        with tarfile.open(output / filename, "w:gz") as archive:
            for name, payload in payloads.items():
                item = tarfile.TarInfo("package/" + name)
                item.size, item.mode = len(payload), 0o644
                archive.addfile(item, io.BytesIO(payload))
                inventory[name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "mode": item.mode}
        payload = (output / filename).read_bytes()
        record.update({"filename": filename, **RELEASE.identity(output / filename),
                       "integrity": builder.integrity(payload), "files": inventory,
                       "verification_scope": next(iter(targets.values())).get("verification_scope", "full")})
        records.append(record)
    RELEASE.write_json(output / "npm-manifest.json", {"schema": 1, "version": version, "source_sha": source_sha,
                       "node": builder.NODE_VERSION, "npm": builder.NPM_VERSION, "packages": records,
                       "verification_scope": next(iter(targets.values())).get("verification_scope", "full")})


def sealed_release(tmp_path, monkeypatch):
    """Prepare real synthetic archive identities through the existing admission helpers."""
    checkout, bundle, native = (tmp_path / name for name in ("checkout", "bundle", "native"))
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n', encoding="utf-8")
    (checkout / "uv.lock").write_bytes(b"locked")
    (checkout / "src/brewdoc").mkdir(parents=True)
    (checkout / "src/brewdoc/__init__.py").write_text(f'__version__ = "{VERSION}"\n', encoding="utf-8")
    (checkout / ".github").mkdir()
    (checkout / ".github/distribution.json").write_bytes((CHECKOUT / ".github/distribution.json").read_bytes())
    original_helper = RELEASE.helper

    def fixture_helper(name):
        """Bind npm's source version to the synthetic checkout while retaining its validators."""
        module = original_helper(name)
        if name == "build_npm":
            module.ROOT = checkout
        return module

    monkeypatch.setattr(RELEASE, "helper", fixture_helper)
    monkeypatch.setattr(RELEASE, "CHECKOUT", checkout)
    (bundle / "dist").mkdir(parents=True)
    (bundle / "ci-package").mkdir()
    wheel = bundle / f"dist/brewdoc-{VERSION}-py3-none-any.whl"
    sdist = bundle / f"dist/brewdoc-{VERSION}.tar.gz"
    wheel.write_bytes(b"accepted wheel bytes")
    sdist.write_bytes(b"accepted source bytes")
    producer = {"version": VERSION, "checkout_sha": SOURCE,
                "source": {"uv.lock": hashlib.sha256(b"locked").hexdigest()},
                "files": {path.relative_to(bundle).as_posix(): {
                    "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in (wheel, sdist)}}
    RELEASE.write_json(bundle / "ci-package/manifest.json", producer)
    native.mkdir()
    builder = RELEASE.helper("build_standalone")
    policy = RELEASE.read_json(CHECKOUT / ".github/distribution.json")
    targets = {}
    for target in RELEASE.TARGETS:
        root = tmp_path / target / "brewdoc"
        root.mkdir(parents=True)
        executable = {"windows-x64": "brewdoc.exe", "linux-x64": "brewdoc", "macos-arm64": "brewdoc"}[target]
        (root / executable).write_bytes(b"synthetic native executable")
        extension = {"windows-x64": "zip", "linux-x64": "tar.gz", "macos-arm64": "tar.gz"}[target]
        archive = native / f"brewdoc-{VERSION}-{target}.{extension}"
        inventory = builder.archive_bundle(root, archive, extension)
        targets[target] = {"schema": 1, "source_sha": SOURCE, "version": VERSION, "python": "3.12.14",
                           "target": target, "wheel_sha256": builder.digest(wheel),
                           "lock_sha256": builder.digest(checkout / "uv.lock"), "inventory": inventory,
                           "executable": "brewdoc/" + executable,
                           "archive": {"filename": archive.name, **RELEASE.identity(archive)},
                           "signing": policy["signing"], "minimum_tested": policy["targets"][target]["verification_floor"],
                           "probes": [{"probe": name, "path": f"tests/fixtures/{name}.xlsx"}
                                      for name in ("sheet-order", "vba", "epub2", "formula")],
                           "oracle": [{"path": f"tests/fixtures/input-{index}.pdf", "code": 0,
                                       "source_sha256": "b" * 64, "receipt": {"out": None},
                                       "markdown_bytes": 1, "markdown_sha256": "c" * 64} for index in range(11)]}
    RELEASE.write_json(native / "binaries.json", {"schema": 1, "version": VERSION,
                                                "source_sha": SOURCE, "targets": targets})
    (native / "SHA256SUMS").write_bytes(b"aggregate-owned checksum input")
    body = tmp_path / "body.md"
    body.write_bytes(b"Staged release instructions.\n")
    monkeypatch.setattr(RELEASE.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, (SOURCE + "\n").encode(), b""))
    output = tmp_path / "release"
    npm = tmp_path / "npm"
    npm_archives(npm, targets)
    manifest = RELEASE.prepare(checkout, bundle, native, output, body, npm)
    return output, manifest


def ci_smoke_artifacts(tmp_path, monkeypatch):
    """Seal real ZIP transports and synthetic smoke records while faking only GitHub I/O."""
    import brewdoc

    folder, manifest = sealed_release(tmp_path, monkeypatch)
    checkout = tmp_path / "checkout"
    native, npm = tmp_path / "ci-native", tmp_path / "ci-npm"
    native.mkdir()
    (native / "verification").mkdir()
    builder = RELEASE.helper("build_standalone")
    generated = tmp_path / "synthetic"
    generated.mkdir()
    probes = []
    for case in builder.synthetic_cases(CHECKOUT, generated):
        source = Path(case["source"])
        code, receipt, markdown = brewdoc.run(source)
        row = {"probe": case["probe"], "path": source.name, "bytes_base64": case["bytes_base64"],
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "code": code,
               "receipt": receipt, "markdown_bytes": len(markdown.encode("utf-8")),
               "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest()}
        if case["probe"] == "formula":
            payload = brewdoc.read_book_artifact(source, "formula/sheet/000002")
            row["artifact"] = {"key": "formula/sheet/000002", "bytes": len(payload),
                               "sha256": hashlib.sha256(payload).hexdigest()}
        probes.append(row)
    binaries = RELEASE.read_json(folder / "binaries.json")
    binaries["verification_scope"] = "smoke"
    consumer_folders = {}
    for target, record in binaries["targets"].items():
        record.update(verification_scope="smoke", oracle=[], probes=copy.deepcopy(probes))
        shutil.copyfile(folder / record["archive"]["filename"], native / record["archive"]["filename"])
        _directory, report = public_reports(tmp_path / target, manifest, record, target)
        report["verification_scope"] = "smoke"
        windows = target == "windows-x64"
        javascript = {False: {"capture_status": "settled"}, True: {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
        report["javascript"]["acquisition"] = javascript[windows]
        report["children"]["exited_owner"] = {"code": 1, "stdout_bytes": 0, "stderr_bytes": 0}
        RELEASE.write_json(native / f"verification/{target}.json", report)
    for target in ("linux-x64", "macos-arm64"):
        directory = tmp_path / f"ci-npm-consumer-{target}"
        directory.mkdir()
        consumer_folders[directory.name] = directory
    RELEASE.write_json(native / "binaries.json", binaries)
    shutil.copyfile(tmp_path / "native/SHA256SUMS", native / "SHA256SUMS")
    npm_archives(npm, binaries["targets"])
    npm_manifest = RELEASE.read_json(npm / "npm-manifest.json")
    packages = {row["metadata"]["name"]: row["integrity"] for row in npm_manifest["packages"]}
    epub = next(row for row in probes if row["probe"] == "epub2")
    for target in ("linux-x64", "macos-arm64"):
        package = next(row["metadata"]["name"] for row in npm_manifest["packages"] if row.get("target") == target)
        report = {"schema": 1, "verdict": "PASS", "verification_scope": "smoke", "mode": "local",
                  "target": target, "version": VERSION, "source_sha": SOURCE,
                  "node": npm_manifest["node"], "npm": npm_manifest["npm"], "packages": packages,
                  "consumers": {name: {"self_check": 38, "selected_package": package, "fixtures": [],
                      "probes": {"global": [{"probe": "epub2", "path": epub["path"],
                                              "markdown_sha256": epub["markdown_sha256"]}], "npx": []}[name]}
                      for name in ("global", "npx")},
                  "cancellation": {"native_observed": True, "native_terminated": True, "dns_observed": False,
                      "owned_processes": 2, "javascript": "observed-and-terminated",
                      "trigger": "offline-html-output", "launcher_exit": "nonzero"}}
        RELEASE.write_json(consumer_folders[f"ci-npm-consumer-{target}"] / f"ci-npm-probe-{target}.json", report)
    names = ["pyproject.toml", "uv.lock", "src/brewdoc/__init__.py", ".github/distribution.json"]
    monkeypatch.setattr(RELEASE, "command", lambda arguments, payload=None:
                        ("\0".join(names) + "\0").encode() if arguments[-2:] == ["ls-files", "-z"] else SOURCE.encode())
    bundle = tmp_path / "bundle"
    RELEASE.write_json(bundle / "ci-package/producer-inputs.json", RELEASE.source_inputs(checkout, SOURCE))
    inputs = {"ci-dist": bundle, "ci-native-assets": native, "ci-npm-release": npm, **consumer_folders}
    transports, rows = {}, []
    for index, (name, directory) in enumerate(inputs.items(), 1):
        path = tmp_path / (name + ".zip")
        zip_directory(directory, path)
        transports[index] = path
        rows.append({"id": index, "name": name, "expired": False, "expires_at": "2999-01-01T00:00:00Z",
                     "size_in_bytes": path.stat().st_size, "digest": "sha256:" + RELEASE.identity(path)["sha256"],
                     "workflow_run": {"id": 17, "head_sha": SOURCE}})
    run = producer_run()
    responses = {f"git/ref/tags/v{VERSION}": {"ref": f"refs/tags/v{VERSION}", "object": {"type": "commit", "sha": SOURCE}},
                 f"actions/workflows/ci.yml/runs?event=push&branch=main&status=success&head_sha={SOURCE}&per_page=100":
                    {"total_count": 1, "workflow_runs": [run]}, "actions/runs/17": run,
                 "actions/runs/17/artifacts?per_page=100": {"total_count": len(rows), "artifacts": rows}}
    monkeypatch.setattr(RELEASE, "api", lambda endpoint, **kwargs: responses[endpoint])
    monkeypatch.setattr(RELEASE, "authenticated_download", lambda endpoint, destination, size, *, accept:
                        shutil.copyfile(transports[int(endpoint.split("/")[2])], destination))
    return checkout, rows, transports, responses


def zip_directory(directory, destination):
    """Create a real artifact transport from ordinary fixture files."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(path for path in directory.rglob("*") if path.is_file()):
            archive.write(item, item.relative_to(directory).as_posix())


def native_inspection_control(tmp_path, monkeypatch, version):
    """Retain valid smoke evidence while binding its real archive to the test version."""
    ci_smoke_artifacts(tmp_path, monkeypatch)
    directory = tmp_path / "ci-native"
    native = RELEASE.read_json(directory / "binaries.json")["targets"]["linux-x64"]
    report = RELEASE.read_json(directory / "verification/linux-x64.json")
    builder = RELEASE.helper("build_standalone")
    builder.validate_manifest(native)
    builder.validate_verification(native, report)
    archive = directory / native["archive"]["filename"]
    filename = f"brewdoc-{version}-linux-x64.tar.gz"
    archive.rename(directory / filename)
    native["version"], native["archive"]["filename"], report["version"] = version, filename, version
    assert RELEASE.identity(directory / filename) == {
        key: native["archive"][key] for key in ("bytes", "sha256")
    }, "versioned control retains the real archive bytes and digest"
    assert (native["schema"], report["schema"], native["oracle"], report["fixtures"],
            [row["probe"] for row in native["probes"]], report["probes"]) == (
        1, 1, [], [], ["epub2", "formula"], ["epub2", "formula"]
    ), "schema-one smoke controls preserve the empty oracle and exact two original probes"
    return native, report


def test_native_publisher_accepts_historical_smoke_without_inspection(tmp_path, monkeypatch):
    # GIVEN a complete historical schema-one smoke record without additive evidence
    native, report = native_inspection_control(tmp_path, monkeypatch, "0.7.1")
    assert ("inspection" in native, "inspection" in report) == (False, False), \
        "the historical control has no inspection extension"
    # WHEN the actual publisher checks the immutable historical profile
    actual = RELEASE.native_report(report, native, SOURCE, "0.7.1", "linux-x64")
    # THEN the old profile remains valid without new mandatory fields
    assert actual is None, "the unchanged historical smoke profile passes real publisher admission"


def test_native_publisher_refuses_missing_inspection_at_external_new_version(tmp_path, monkeypatch):
    # GIVEN a valid schema-one smoke profile at the externally requested new version
    native, report = native_inspection_control(tmp_path, monkeypatch, "0.8.0")
    assert (native["version"], report["version"], "inspection" in native, "inspection" in report) == (
        "0.8.0", "0.8.0", False, False), "only the mandatory inspection extension is absent"
    # WHEN the actual publisher admits the externally requested 0.8.0 release
    # THEN missing inspection evidence is refused before it can establish release acceptance
    with pytest.raises(ValueError, match="(?i)inspection"):
        RELEASE.native_report(report, native, SOURCE, "0.8.0", "linux-x64")


@pytest.mark.parametrize("version", ["0.7.1", "0.8.0"])
def test_native_publisher_refuses_unknown_inspection_schema_at_every_version(tmp_path, monkeypatch, version):
    # GIVEN otherwise valid smoke records with an explicitly unknown inspection extension
    native, report = native_inspection_control(tmp_path, monkeypatch, version)
    native["inspection"] = {"schema": "brewdoc.delivery-inspection/999"}
    report["inspection"] = {"schema": "brewdoc.delivery-inspection/999"}
    assert (native["inspection"], report["inspection"]) == (
        {"schema": "brewdoc.delivery-inspection/999"}, {"schema": "brewdoc.delivery-inspection/999"}
    ), "matching unknown schemas cannot manufacture a supported inspection profile"
    # WHEN the actual publisher checks either a historical or a new external version
    # THEN a present unknown extension is refused independently of the version gate
    with pytest.raises(ValueError, match="(?i)inspection"):
        RELEASE.native_report(report, native, SOURCE, version, "linux-x64")


@pytest.mark.parametrize("delta", [
    {"probes": ["epub2", "formula", "inspection"]},
    {"probes": ["epub2"]},
    {"fixtures": [{"path": "tests/fixtures/foreign.pdf", "markdown_sha256": "c" * 64}]},
    {"source_sha": "b" * 40},
    {"archive_sha256": "d" * 64},
])
def test_native_publisher_retains_exact_historical_smoke_identities(tmp_path, monkeypatch, delta):
    # GIVEN a valid old profile admitted by the real publisher before one identity changes
    native, report = native_inspection_control(tmp_path, monkeypatch, "0.7.1")
    assert RELEASE.native_report(report, native, SOURCE, "0.7.1", "linux-x64") is None, \
        "the complete historical control passes before the isolated mutation"
    report.update(delta)
    # WHEN the publisher checks a changed old probe, oracle, source or archive identity
    # THEN additive inspection support must retain the existing exact identity refusal
    with pytest.raises(ValueError, match="Native public consumer evidence differs"):
        RELEASE.native_report(report, native, SOURCE, "0.7.1", "linux-x64")


@pytest.fixture
def inspection_delivery():
    """Reuse one independently sealed complete packet and package descriptor."""
    from test_install_verification import complete_inspection_packet

    packet = complete_inspection_packet()
    packages = RELEASE.helper("verify_install")
    descriptor = packages.inspection_descriptor(packet, version=packet["version"], source_sha=packet["source_sha"],
                                                module_sources_sha256=packet["module_sources_sha256"])
    return packet, descriptor


def current_native_inspection_control(tmp_path, monkeypatch, delivery):
    """Attach admitted fifteen-format evidence to the exact old smoke control."""
    packet, descriptor = delivery
    native, report = native_inspection_control(tmp_path, monkeypatch, packet["version"])
    native.update(source_sha=packet["source_sha"], inspection=copy.deepcopy(packet))
    report.update(source_sha=packet["source_sha"], inspection=copy.deepcopy(descriptor["result"]))
    return native, report


def current_release_inputs(tmp_path, monkeypatch, delivery):
    """Reuse sealed sources while creating complete real publisher archive controls."""
    packet, descriptor = delivery
    checkout, rows, transports, responses = ci_smoke_artifacts(tmp_path, monkeypatch)
    packages = RELEASE.helper("verify_install")
    modules = packages.module_sources(CHECKOUT)
    assert hashlib.sha256(packages.canonical_json(modules)).hexdigest() == packet["module_sources_sha256"], \
        "the complete fixture packet must retain the current independently checked source module map"
    version, source_sha = packet["version"], packet["source_sha"]
    (checkout / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    for name in modules:
        destination = checkout / "src" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CHECKOUT / "src" / name, destination)
    bundle = tmp_path / "bundle"
    old_wheel, old_sdist = packages.distributions(bundle / "dist")
    old_wheel.unlink()
    old_sdist.unlink()
    wheel = bundle / f"dist/brewdoc-{version}-py3-none-any.whl"
    sdist = bundle / f"dist/brewdoc-{version}.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in modules:
            archive.write(checkout / "src" / name, name)
    with tarfile.open(sdist, "w:gz") as archive:
        for name in modules:
            archive.add(checkout / "src" / name, arcname=f"brewdoc-{version}/src/{name}")
    producer = {"version": version, "checkout_sha": source_sha, "verification_scope": "smoke",
                "module_sources": modules, "module_sources_sha256": packet["module_sources_sha256"],
                "source": {"uv.lock": packages.digest(checkout / "uv.lock")}}
    for name in packages.INSPECTION_SOURCE_FILES:
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CHECKOUT / name, destination)
        producer["source"][name] = packages.digest(destination)
    packet_path = bundle / "ci-package/inspection.json"
    packet_path.write_bytes(packages.canonical_json(packet))
    producer["files"] = {path.relative_to(bundle).as_posix(): {"size": path.stat().st_size, "sha256": packages.digest(path)}
                         for path in (wheel, sdist, packet_path)}
    producer["inspection"] = {"path": "ci-package/inspection.json", **producer["files"]["ci-package/inspection.json"]}
    RELEASE.write_json(bundle / "ci-package/manifest.json", producer)
    native = tmp_path / "ci-native"
    binaries = RELEASE.read_json(native / "binaries.json")
    binaries.update(version=version, source_sha=source_sha)
    for target, record in binaries["targets"].items():
        archive = native / record["archive"]["filename"]
        filename = record["archive"]["filename"].replace(VERSION, version)
        archive.rename(native / filename)
        record["archive"]["filename"] = filename
        record.update(version=version, source_sha=source_sha, wheel_sha256=packages.digest(wheel), inspection=packet)
        path = native / f"verification/{target}.json"
        report = RELEASE.read_json(path)
        report.update(version=version, source_sha=source_sha, inspection=descriptor["result"])
        RELEASE.write_json(path, report)
    RELEASE.write_json(native / "binaries.json", binaries)
    npm = tmp_path / "current-npm"
    npm_archives(npm, binaries["targets"], version=version, source_sha=source_sha)
    npm_manifest = RELEASE.read_json(npm / "npm-manifest.json")
    integrities = {row["metadata"]["name"]: row["integrity"] for row in npm_manifest["packages"]}
    consumers = {}
    for target in RELEASE.NPM_TARGETS:
        directory = tmp_path / f"ci-npm-consumer-{target}"
        consumers[directory.name] = directory
        path = directory / f"ci-npm-probe-{target}.json"
        report = RELEASE.read_json(path)
        report.update(version=version, source_sha=source_sha, packages=integrities)
        for consumer in report["consumers"].values():
            consumer["inspection"] = descriptor["result"]
        RELEASE.write_json(path, report)
    original_command = RELEASE.command
    monkeypatch.setattr(RELEASE, "command", lambda arguments, payload=None:
                        original_command(arguments, payload) if arguments[-2:] == ["ls-files", "-z"] else source_sha.encode())
    RELEASE.write_json(bundle / "ci-package/producer-inputs.json", RELEASE.source_inputs(checkout, source_sha))
    inputs = {"ci-dist": bundle, "ci-native-assets": native, "ci-npm-release": npm, **consumers}
    for row in rows:
        transport = transports[row["id"]]
        zip_directory(inputs[row["name"]], transport)
        row.update(size_in_bytes=transport.stat().st_size, digest="sha256:" + RELEASE.identity(transport)["sha256"])
        row["workflow_run"]["head_sha"] = source_sha
    run = responses["actions/runs/17"]
    run["head_sha"] = source_sha
    responses[f"git/ref/tags/v{version}"] = {"ref": f"refs/tags/v{version}", "object": {"type": "commit", "sha": source_sha}}
    responses[f"actions/workflows/ci.yml/runs?event=push&branch=main&status=success&head_sha={source_sha}&per_page=100"] = {
        "total_count": 1, "workflow_runs": [run]}
    return checkout, bundle, native, npm


def test_current_publisher_reuse_prepare_and_admit_bind_the_independent_package(tmp_path, monkeypatch, inspection_delivery):
    # GIVEN complete real package/native/npm archive controls and independent source expectations
    packet, descriptor = inspection_delivery
    checkout, bundle, native, npm = current_release_inputs(tmp_path, monkeypatch, inspection_delivery)
    assert RELEASE.helper("verify_install").load_inspection_packet(
        bundle, RELEASE.read_json(bundle / "ci-package/manifest.json")) == packet, "the package loader admits the entire sealed packet"
    # WHEN actual CI reuse, preparation and subsequent admission consume the same packet
    reused = RELEASE.reuse_ci(checkout, tmp_path / "reused", "v" + packet["version"], packet["source_sha"])
    folder = tmp_path / "current-release"
    manifest = RELEASE.prepare(checkout, bundle, native, folder, tmp_path / "body.md", npm)
    admitted = RELEASE.admit(folder, manifest["tag"], packet["source_sha"], inspection=descriptor)
    # THEN each real call path retains the independently derived descriptor exactly
    assert (reused["inspection"], manifest["inspection"], admitted["inspection"]) == (descriptor, descriptor, descriptor), \
        "reuse, prepare and re-admission all bind to package expectations"


def test_current_release_readmission_refuses_removed_package_descriptor(tmp_path, monkeypatch, inspection_delivery):
    # GIVEN a complete prepared release before its independent package descriptor is removed
    packet, descriptor = inspection_delivery
    checkout, bundle, native, npm = current_release_inputs(tmp_path, monkeypatch, inspection_delivery)
    folder = tmp_path / "current-release"
    manifest = RELEASE.prepare(checkout, bundle, native, folder, tmp_path / "body.md", npm)
    assert RELEASE.admit(folder, manifest["tag"], packet["source_sha"], inspection=descriptor) == manifest, \
        "the unchanged complete release passes before descriptor removal"
    manifest.pop("inspection")
    RELEASE.write_json(folder / "release-manifest.json", manifest)
    # WHEN re-admission still uses the externally requested current source and version
    # THEN complete embedded native/npm packets cannot replace the removed package descriptor
    with pytest.raises(ValueError, match="release inspection descriptor"):
        RELEASE.admit(folder, manifest["tag"], packet["source_sha"])


@pytest.mark.parametrize("operation", ["reuse", "prepare"])
def test_current_publisher_refuses_foreign_consistent_native_before_release_admission(
        tmp_path, monkeypatch, inspection_delivery, operation):
    # GIVEN complete publisher inputs before native and verification substitute a matching foreign module map
    packet, descriptor = inspection_delivery
    checkout, bundle, native, npm = current_release_inputs(tmp_path, monkeypatch, inspection_delivery)
    assert RELEASE.package_inspection(checkout, bundle, RELEASE.read_json(bundle / "ci-package/manifest.json"),
                                      version=packet["version"], source_sha=packet["source_sha"]) == descriptor, \
        "the independently admitted package remains valid before native substitution"
    binaries = RELEASE.read_json(native / "binaries.json")
    foreign = copy.deepcopy(packet)
    foreign["module_sources_sha256"] = "f" * 64
    binaries["targets"]["linux-x64"]["inspection"] = foreign
    RELEASE.write_json(native / "binaries.json", binaries)
    report_path = native / "verification/linux-x64.json"
    report = RELEASE.read_json(report_path)
    report["inspection"] = RELEASE.helper("verify_install").expected_inspection_result(foreign)
    RELEASE.write_json(report_path, report)
    archive = tmp_path / "ci-native-assets.zip"
    zip_directory(native, archive)
    rows = RELEASE.api("actions/runs/17/artifacts?per_page=100")["artifacts"]
    native_row = next(row for row in rows if row["name"] == "ci-native-assets")
    native_row.update(size_in_bytes=archive.stat().st_size, digest="sha256:" + RELEASE.identity(archive)["sha256"])
    calls = {"reuse": lambda: RELEASE.reuse_ci(checkout, tmp_path / "reused", "v" + packet["version"], packet["source_sha"]),
             "prepare": lambda: RELEASE.prepare(checkout, bundle, native, tmp_path / "current-release", tmp_path / "body.md", npm)}
    # WHEN either actual publisher path receives the internally matching foreign records
    # THEN no native/npm agreement replaces the independent package descriptor
    with pytest.raises(ValueError, match="independent package descriptor"):
        calls[operation]()


def test_current_ci_reuse_refuses_foreign_embedded_npm_packet_before_producer_receipt(
        tmp_path, monkeypatch, inspection_delivery):
    # GIVEN complete real CI inputs accepted against the genuine package and aggregate native descriptor
    packet, descriptor = inspection_delivery
    checkout, _bundle, native, _npm = current_release_inputs(tmp_path, monkeypatch, inspection_delivery)
    control = RELEASE.reuse_ci(checkout, tmp_path / "valid-reuse", "v" + packet["version"], packet["source_sha"])
    assert control["inspection"] == descriptor, "the unchanged complete modern reuse control passes before npm substitution"
    binaries = RELEASE.read_json(native / "binaries.json")
    foreign_targets = copy.deepcopy(binaries["targets"])
    for target in foreign_targets.values():
        target["inspection"]["module_sources_sha256"] = "f" * 64
    foreign_npm = tmp_path / "foreign-npm"
    npm_archives(foreign_npm, foreign_targets, version=packet["version"], source_sha=packet["source_sha"])
    admitted_npm = RELEASE.helper("build_npm").verify_release(foreign_npm)
    assert (admitted_npm["version"], RELEASE.read_json(native / "binaries.json")) == (packet["version"], binaries), \
        "the foreign npm tarballs remain internally valid while the genuine native aggregate is unchanged"
    integrities = {row["metadata"]["name"]: row["integrity"] for row in admitted_npm["packages"]}
    inputs = {"ci-npm-release": foreign_npm}
    for target in RELEASE.NPM_TARGETS:
        directory = tmp_path / f"ci-npm-consumer-{target}"
        path = directory / f"ci-npm-probe-{target}.json"
        report = RELEASE.read_json(path)
        report["packages"] = integrities
        RELEASE.write_json(path, report)
        inputs[directory.name] = directory
    rows = RELEASE.api("actions/runs/17/artifacts?per_page=100")["artifacts"]
    selected = [row for row in rows if row["name"] in inputs]
    assert len(selected) == 3, "only the npm archive and its two matching consumer transports are replaced"
    for row in selected:
        archive = tmp_path / (row["name"] + ".zip")
        zip_directory(inputs[row["name"]], archive)
        row.update(size_in_bytes=archive.stat().st_size, digest="sha256:" + RELEASE.identity(archive)["sha256"])
    refused = tmp_path / "foreign-reuse"
    # WHEN actual reuse sees valid transports and consumers paired with foreign embedded npm inspection
    with pytest.raises(ValueError, match="independent package descriptor"):
        RELEASE.reuse_ci(checkout, refused, "v" + packet["version"], packet["source_sha"])
    # THEN independent package admission fails before release-ready provenance is written
    assert (refused / "producer.json").exists() is False, "foreign embedded npm evidence cannot write a producer receipt"


@pytest.mark.parametrize("consumer", ["global", "npx"])
def test_npm_publisher_requires_complete_inspection_beside_original_smoke_probes(
        tmp_path, monkeypatch, inspection_delivery, consumer):
    # GIVEN complete current native/npm evidence, including the previously conversion-empty npx route
    packet, descriptor = inspection_delivery
    _checkout, _bundle, native_folder, npm_folder = current_release_inputs(tmp_path, monkeypatch, inspection_delivery)
    native = RELEASE.read_json(native_folder / "binaries.json")["targets"]["linux-x64"]
    npm = RELEASE.read_json(npm_folder / "npm-manifest.json")
    report = RELEASE.read_json(tmp_path / "ci-npm-consumer-linux-x64/ci-npm-probe-linux-x64.json")
    manifest = {"version": packet["version"], "source_sha": packet["source_sha"]}
    assert RELEASE.npm_report(report, "linux-x64", manifest, native, npm, "local", inspection=descriptor) is None, \
        "the complete actual npm publisher path admits both independent inspection results"
    report["consumers"][consumer].pop("inspection")
    # WHEN either required consumer loses its complete additive result
    # THEN original conversion probes and self-check cannot replace inspection evidence
    with pytest.raises(ValueError, match="inspection evidence differs"):
        RELEASE.npm_report(report, "linux-x64", manifest, native, npm, "local", inspection=descriptor)


def current_public_inputs(tmp_path, monkeypatch, delivery):
    """Keep byte/network validators real while supplying complete public consumer records."""
    packet, descriptor = delivery
    real_command = RELEASE.command
    real_api = RELEASE.api
    checkout, bundle, native, npm = current_release_inputs(tmp_path, monkeypatch, delivery)
    folder = tmp_path / "current-release"
    manifest = RELEASE.prepare(checkout, bundle, native, folder, tmp_path / "body.md", npm)
    monkeypatch.setattr(RELEASE, "command", real_command)
    monkeypatch.setattr(RELEASE, "api", real_api)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False, changes={"target_commitish": packet["source_sha"]})
    responses[f"git/ref/tags/{manifest['tag']}"]["object"]["sha"] = packet["source_sha"]
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = tmp_path / "public-evidence"
    assets = responses["releases/42/assets?per_page=100"]
    binaries = RELEASE.read_json(folder / "binaries.json")
    for target in RELEASE.TARGETS:
        directory = evidence / target
        directory.mkdir(parents=True)
        archive = binaries["targets"][target]["archive"]
        RELEASE.write_json(directory / "archive.download.json", {
            "schema": 1, "tag": manifest["tag"], "source_sha": packet["source_sha"], "target": target,
            "release_id": 42, "asset_id": next(row["id"] for row in assets if row["name"] == archive["filename"]),
            "asset_filename": archive["filename"], "url": RELEASE.public_url(manifest["tag"], archive["filename"]),
            "download_sha256": archive["sha256"], "bytes": archive["bytes"], "verified_at": "2026-01-01T00:00:00+00:00"})
        shutil.copyfile(native / f"verification/{target}.json", directory / "verification.json")
    for target in RELEASE.NPM_TARGETS:
        report = RELEASE.read_json(tmp_path / f"ci-npm-consumer-{target}/ci-npm-probe-{target}.json")
        report.update(mode="public", signatures={"verified_packages": 2, "verified_attestations": 2, "stdout_sha256": "f" * 64})
        RELEASE.write_json(evidence / target / "npm-public.json", report)
    npm_manifest = RELEASE.read_json(folder / "npm-manifest.json")
    RELEASE.write_json(evidence / "npm-public-registry.json", {
        "schema": 1, "verdict": "PASS", "version": packet["version"], "source_sha": packet["source_sha"],
        "verification_scope": "smoke", "packages": {row["metadata"]["name"]: row["integrity"] for row in npm_manifest["packages"]}})
    public_python_reports(evidence, packet, descriptor)
    return folder, manifest, evidence, calls


def test_current_public_publisher_and_finalization_readmit_installed_routes(tmp_path, monkeypatch, inspection_delivery):
    # GIVEN actual public byte admission controls and both complete installed Python route reports
    _packet, descriptor = inspection_delivery
    folder, manifest, evidence, calls = current_public_inputs(tmp_path, monkeypatch, inspection_delivery)
    accepted = RELEASE.admit_public(folder, manifest, evidence, inspection=descriptor)
    assert (accepted["pypi"], accepted["pypi_consumers"]) == (
        {"published": True}, {"wheel": "PASS", "sdist": "PASS"}), "both installed routes accompany the unchanged public guide contract"
    availability = tmp_path / "public-acceptance.json"
    RELEASE.write_json(availability, RELEASE.admit_public(folder, manifest, evidence, inspection=descriptor))
    guide = RELEASE.helper("release_guide")
    body = tmp_path / "public-body.md"
    body.write_text(guide.render(guide.admit(RELEASE.CHECKOUT, folder / "binaries.json", folder / "npm-manifest.json",
                                           "public", availability)), encoding="utf-8")
    # WHEN finalization repeats the entire independent admission before the exact notes edit
    RELEASE.finalize(folder, manifest, evidence, body, inspection=descriptor)
    # THEN only the admitted notes are edited after both installed routes pass again
    assert [call for call in calls if call[1] == "release"] == [
        ["gh", "release", "edit", manifest["tag"], "--notes-file", str(body), "--repo", RELEASE.REPOSITORY]
    ], "finalization retains the exact existing public edit contract"


@pytest.mark.parametrize("operation", ["admit", "finalize"])
def test_current_public_paths_refuse_missing_installed_route_before_notes_edit(
        tmp_path, monkeypatch, inspection_delivery, operation):
    # GIVEN otherwise complete native/npm/registry evidence with the installed sdist result removed
    _packet, descriptor = inspection_delivery
    folder, manifest, evidence, calls = current_public_inputs(tmp_path, monkeypatch, inspection_delivery)
    assert RELEASE.admit_public(folder, manifest, evidence, inspection=descriptor)["inspection"] == descriptor, \
        "the complete public control passes before removing one installed route"
    (evidence / "pypi-sdist.json").unlink()
    operations = {"admit": lambda: RELEASE.admit_public(folder, manifest, evidence, inspection=descriptor),
                  "finalize": lambda: RELEASE.finalize(folder, manifest, evidence, tmp_path / "absent-body.md", inspection=descriptor)}
    # WHEN either public admission or finalization repeats installed-route admission
    with pytest.raises(ValueError, match="exactly wheel and sdist"):
        operations[operation]()
    # THEN no final notes edit occurs even though all public package bytes are correct
    assert [call for call in calls if call[1] == "release"] == [], "missing installed evidence refuses before notes publication"


def test_native_publisher_accepts_complete_independently_bound_inspection(tmp_path, monkeypatch, inspection_delivery):
    # GIVEN real old smoke fields plus an independently sealed complete fifteen-case packet
    packet, descriptor = inspection_delivery
    native, report = current_native_inspection_control(tmp_path, monkeypatch, inspection_delivery)
    assert [row["id"] for row in report["inspection"]["cases"]] == sorted(RELEASE.helper("verify_install").SUFFIXES), \
        "the positive control contains every supported format in exact order"
    # WHEN the publisher compares the actual consumer result with package expectations
    actual = RELEASE.native_report(report, native, packet["source_sha"], packet["version"], "linux-x64",
                                   inspection=descriptor)
    # THEN the additive result passes without changing the exact two old probes
    assert (actual, report["probes"], native["oracle"]) == (None, ["epub2", "formula"], []), \
        "independent inspection evidence is admitted beside the unchanged smoke profile"


@pytest.mark.parametrize("version", ["0.8.0", "0.10.0", "1.0.0"])
def test_native_publisher_requires_independent_descriptor_at_external_numeric_version(
        tmp_path, monkeypatch, inspection_delivery, version):
    # GIVEN a complete mutually matching packet and result without independent publisher authority
    native, report = current_native_inspection_control(tmp_path, monkeypatch, inspection_delivery)
    assert report["inspection"]["suite_sha256"] == inspection_delivery[1]["suite_sha256"], \
        "native and consumer agree before independent descriptor admission"
    # WHEN the externally requested current release supplies no independent descriptor
    # THEN embedded agreement cannot bypass the numeric version gate
    with pytest.raises(ValueError, match="independent package inspection descriptor"):
        RELEASE.native_report(report, native, native["source_sha"], version, "linux-x64")


@pytest.mark.parametrize("field,value", [("source_sha", "e" * 40), ("module_sources_sha256", "f" * 64)])
def test_native_publisher_refuses_mutually_consistent_foreign_inspection(
        tmp_path, monkeypatch, inspection_delivery, field, value):
    # GIVEN a complete foreign packet whose own native and observed results agree
    packet, descriptor = inspection_delivery
    native, report = current_native_inspection_control(tmp_path, monkeypatch, inspection_delivery)
    foreign = native["inspection"]
    foreign[field] = value
    packages = RELEASE.helper("verify_install")
    report["inspection"] = packages.expected_inspection_result(foreign)
    assert packages.validate_inspection_packet(foreign, version=foreign["version"], source_sha=foreign["source_sha"]) == foreign, \
        "the adversary remains a complete internally valid packet"
    # WHEN publisher admission uses independent package source and descriptor controls
    # THEN matching foreign native/consumer declarations cannot establish this release
    with pytest.raises(ValueError, match="(?i)inspection"):
        RELEASE.native_report(report, native, packet["source_sha"], packet["version"], "linux-x64",
                              inspection=descriptor)


def test_native_publisher_refuses_inner_historical_version_downgrade(tmp_path, monkeypatch, inspection_delivery):
    # GIVEN a new external release whose native and consumer records claim an old profile
    packet, descriptor = inspection_delivery
    native, report = native_inspection_control(tmp_path, monkeypatch, "0.7.1")
    assert (native["version"], report["version"], "inspection" in native) == ("0.7.1", "0.7.1", False), \
        "the downgrade has unchanged historical fields without an inspection packet"
    # WHEN the publisher retains the externally requested current release controls
    # THEN inner relabelling cannot remove mandatory inspection evidence
    with pytest.raises(ValueError, match="(?i)inspection"):
        RELEASE.native_report(report, native, packet["source_sha"], packet["version"], "linux-x64",
                              inspection=descriptor)


@pytest.mark.parametrize("record", ["native", "report"])
@pytest.mark.parametrize("block", [None, {}, {"schema": "brewdoc.delivery-inspection/999"}])
def test_historical_publisher_refuses_present_malformed_inspection_on_either_record(
        tmp_path, monkeypatch, record, block):
    # GIVEN a historical valid profile before one side presents a malformed extension
    native, report = native_inspection_control(tmp_path, monkeypatch, "0.7.1")
    assert RELEASE.native_report(report, native, SOURCE, "0.7.1", "linux-x64") is None, \
        "a truly absent historical extension remains valid"
    {"native": native, "report": report}[record]["inspection"] = block
    # WHEN a null, empty or unknown extension appears on either old record
    # THEN presence remains distinct from the supported historical absence
    with pytest.raises(ValueError, match="(?i)inspection"):
        RELEASE.native_report(report, native, SOURCE, "0.7.1", "linux-x64")


def public_python_reports(directory, packet, descriptor):
    """Write distinct route-bound installed reports with the whole observed result."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"version": packet["version"], "source_sha": packet["source_sha"], "inspection": descriptor}
    for route in ("wheel", "sdist"):
        RELEASE.write_json(directory / f"pypi-{route}.json", {
            "schema": 1, "verdict": "PASS", "route": route, "version": packet["version"],
            "source_sha": packet["source_sha"], "distribution": {"name": "brewdoc", "version": packet["version"]},
            "inspection": descriptor["result"]})
    return manifest


def test_public_pypi_inspection_accepts_distinct_complete_installed_routes(tmp_path, inspection_delivery):
    # GIVEN separate complete reports from wheel and forced-sdist installed consumers
    packet, descriptor = inspection_delivery
    manifest = public_python_reports(tmp_path, packet, descriptor)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["pypi-sdist.json", "pypi-wheel.json"], \
        "both registry installation routes have distinct report files"
    # WHEN final public admission compares both complete results to package expectations
    actual = RELEASE.pypi_inspection(tmp_path, manifest, inspection=descriptor)
    # THEN both installed routes pass together
    assert actual is None, "the exact independently bound installed reports satisfy public admission"


@pytest.mark.parametrize("route", ["wheel", "sdist"])
def test_public_pypi_inspection_refuses_missing_installed_route(tmp_path, inspection_delivery, route):
    # GIVEN complete installed evidence before one required route disappears
    packet, descriptor = inspection_delivery
    manifest = public_python_reports(tmp_path, packet, descriptor)
    assert RELEASE.pypi_inspection(tmp_path, manifest, inspection=descriptor) is None, \
        "both original route reports pass before removal"
    (tmp_path / f"pypi-{route}.json").unlink()
    # WHEN public admission attempts to use only one installation route
    # THEN registry bytes and one successful install cannot replace the missing route
    with pytest.raises(ValueError, match="exactly wheel and sdist"):
        RELEASE.pypi_inspection(tmp_path, manifest, inspection=descriptor)


@pytest.mark.parametrize("delta", [
    {"route": "wheel"}, {"source_sha": "e" * 40}, {"inspection": {"verdict": "PASS"}},
    {"distribution": {"name": "brewdoc", "version": "0.7.1"}},
])
def test_public_pypi_inspection_refuses_duplicate_route_or_foreign_installed_result(tmp_path, inspection_delivery, delta):
    # GIVEN two valid route-bound reports before one sdist report is substituted
    packet, descriptor = inspection_delivery
    manifest = public_python_reports(tmp_path, packet, descriptor)
    assert RELEASE.pypi_inspection(tmp_path, manifest, inspection=descriptor) is None, \
        "the complete original wheel/sdist result pair passes admission"
    path = tmp_path / "pypi-sdist.json"
    report = RELEASE.read_json(path)
    report.update(delta)
    RELEASE.write_json(path, report)
    # WHEN a duplicate route or foreign installed identity reaches public admission
    # THEN the entire route-bound record must still equal package expectations
    with pytest.raises(ValueError, match="installed report differs"):
        RELEASE.pypi_inspection(tmp_path, manifest, inspection=descriptor)


def download_process(monkeypatch, payload, accept, *, status=0):
    """Fake gh transport only, refusing unsupported media headers before byte admission."""
    calls = []

    class Process:
        """Expose the lifecycle used by the existing bounded authenticated byte reader."""

        def __init__(self, arguments, **options):
            calls.append((arguments, options))
            admitted = "Accept: " + accept in arguments
            self.stdout = io.BytesIO(payload if admitted else b'{"status":"415"}')
            self.returncode = status if admitted else 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stdout.close()

        def wait(self, **kwargs):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = 1

    monkeypatch.setattr(RELEASE.subprocess, "Popen", Process)
    return calls


def test_actions_zip_download_uses_json_accept_and_retains_exact_transport_digest(tmp_path, monkeypatch):
    # GIVEN an actual ZIP transport whose API refuses binary-media request headers
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_bytes(b'{"exact":"artifact bytes"}\n')
    archive = tmp_path / "transport.zip"
    zip_directory(source, archive)
    payload = archive.read_bytes()
    calls = download_process(monkeypatch, payload, "application/json")
    row = {"id": 17, "size_in_bytes": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
    assert len(calls) == 0, "the server receives no request before artifact admission"
    # WHEN existing transport and safe ZIP extraction admit the original download
    RELEASE.download_ci_artifact(row, tmp_path / "downloaded")
    # THEN the artifact endpoint receives its required media header and exact verified file bytes
    assert calls == [(["gh", "api", f"repos/{RELEASE.REPOSITORY}/actions/artifacts/17/zip", "--header", "Accept: application/json"],
                     {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL})], "Actions archive requests require JSON Accept"
    assert (tmp_path / "downloaded/manifest.json").read_bytes() == b'{"exact":"artifact bytes"}\n', (
        "changing the media header must preserve original ZIP digest and extracted payload")


def test_release_asset_byte_download_keeps_octet_stream_accept(tmp_path, monkeypatch):
    # GIVEN an admitted release asset whose byte API requires binary media
    payload = b"exact release bytes"
    calls = download_process(monkeypatch, payload, "application/octet-stream")
    assert len(payload) == 19, "the accepted release file has an exact byte ceiling"
    # WHEN draft readback uses its original authenticated byte boundary
    RELEASE.draft_download(23, tmp_path / "release.bin", {"bytes": len(payload)})
    # THEN release-asset downloads preserve the binary header and original bounded file bytes
    assert calls == [(["gh", "api", f"repos/{RELEASE.REPOSITORY}/releases/assets/23", "--header", "Accept: application/octet-stream"],
                     {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL})], "release byte requests retain octet-stream Accept"
    assert (tmp_path / "release.bin").read_bytes() == payload, "release-asset bytes are not interpreted as JSON metadata"


@pytest.mark.parametrize("status,payload", [(1, b"accepted"), (0, b"oversized")])
def test_endpoint_media_selection_preserves_failure_and_byte_ceiling_guards(tmp_path, monkeypatch, status, payload):
    # GIVEN an authenticated release stream with a failed process or one excess byte
    calls = download_process(monkeypatch, payload, "application/octet-stream", status=status)
    assert len(calls) == 0, "guard refusal starts without any transport call"
    # WHEN the original bounded byte reader receives the unacceptable response
    with pytest.raises(ValueError, match="exceeds|unavailable"):
        RELEASE.draft_download(23, tmp_path / "refused.bin", {"bytes": 8})
    # THEN refusal never issues another request or switches the endpoint's media contract
    assert len(calls) == 1, "transport failures have no fallback header or retry path"


def test_ci_reuse_preserves_exact_synthetic_transport_bytes_and_original_attempt(tmp_path, monkeypatch):
    # GIVEN complete real ZIP artifacts from a retained successful main attempt
    checkout, rows, _transports, _responses = ci_smoke_artifacts(tmp_path, monkeypatch)
    assert len(rows) == 5, "the producer has the complete five-artifact admission set"
    # WHEN release reuses the trusted producer through real transport and report validators
    record = RELEASE.reuse_ci(checkout, tmp_path / "reused", "v" + VERSION, SOURCE)
    # THEN successful prior-job bytes retain their source and run attempt without rebuilding
    assert (record["run_id"], record["run_attempt"], record["source_sha"], record["version"]) == (
        17, 2, SOURCE, VERSION), "rerun provenance binds the actual producer attempt"
    assert (tmp_path / "reused/ci-dist/dist/brewdoc-0.3.0-py3-none-any.whl").read_bytes() == (
        b"accepted wheel bytes"), "release reuses the original admitted wheel bytes unchanged"


@pytest.mark.parametrize("change", ["digest", "inputs", "missing", "expired", "event", "version", "profile", "probe", "report_scope", "native_probe"])
def test_ci_reuse_refuses_untrusted_or_incomplete_real_artifact_transports(tmp_path, monkeypatch, change):
    # GIVEN a complete exact-source producer and one meaningful mutation
    checkout, rows, transports, responses = ci_smoke_artifacts(tmp_path, monkeypatch)
    assert rows[0]["name"] == "ci-dist", "the package artifact starts at its canonical identity"
    operations = {"digest": lambda: transports[1].write_bytes(b"wrong transport"),
                  "inputs": lambda: (checkout / "uv.lock").write_bytes(b"changed lock"),
                  "missing": lambda: rows.pop(), "expired": lambda: rows[0].update(expires_at="2000-01-01T00:00:00Z"),
                  "event": lambda: responses["actions/runs/17"].update(event="pull_request"),
                  "version": lambda: (checkout / "pyproject.toml").write_text('[project]\nversion="0.3.1"\n'),
                  "profile": lambda: RELEASE.write_json(tmp_path / "ci-native/binaries.json", {
                      **RELEASE.read_json(tmp_path / "ci-native/binaries.json"), "verification_scope": "full"}),
                  "probe": lambda: RELEASE.write_json(tmp_path / "ci-npm-consumer-linux-x64/ci-npm-probe-linux-x64.json", {
                      **RELEASE.read_json(tmp_path / "ci-npm-consumer-linux-x64/ci-npm-probe-linux-x64.json"), "consumers": {}}),
                  "report_scope": lambda: RELEASE.write_json(tmp_path / "ci-native/verification/linux-x64.json", {
                      **RELEASE.read_json(tmp_path / "ci-native/verification/linux-x64.json"), "verification_scope": "full"}),
                  "native_probe": lambda: RELEASE.write_json(tmp_path / "ci-native/verification/linux-x64.json", {
                      **RELEASE.read_json(tmp_path / "ci-native/verification/linux-x64.json"), "probes": ["epub2"]})}
    operations[change]()
    for row in rows:
        directory = {"ci-dist": tmp_path / "bundle", "ci-native-assets": tmp_path / "ci-native",
                     "ci-npm-release": tmp_path / "ci-npm"}.get(row["name"], tmp_path / row["name"])
        zip_directory(directory, transports[row["id"]])
        row.update(size_in_bytes=transports[row["id"]].stat().st_size,
                   digest="sha256:" + RELEASE.identity(transports[row["id"]])["sha256"])
    rows[0]["digest"] = {"digest": "sha256:" + "f" * 64}.get(change, rows[0]["digest"])
    # WHEN actual ZIP/profile/input admission reaches the altered boundary
    with pytest.raises(ValueError, match="CI |tag |npm public consumer|Native "):
        RELEASE.reuse_ci(checkout, tmp_path / "refused", "v" + VERSION, SOURCE)
    # THEN no failure writes a release-ready producer receipt
    assert (tmp_path / "refused/producer.json").exists() is False, "refused artifacts cannot reach publication admission"


def fake_api(monkeypatch, folder, manifest, *, draft=True, changes=None):
    """Fake only the GitHub CLI process boundary with exact source and asset metadata."""
    assets = [{"id": index + 1, "name": name, "size": row["bytes"]}
              for index, (name, row) in enumerate(RELEASE.upload_identities(folder, manifest).items())]
    release = {"id": 42, "tag_name": manifest["tag"], "target_commitish": SOURCE, "prerelease": False,
               "draft": draft, "body": (folder / "staged-body.md").read_text(encoding="utf-8")}
    release.update(changes or {})
    responses = {f"git/ref/tags/{manifest['tag']}": {"ref": f"refs/tags/{manifest['tag']}",
                 "object": {"type": "commit", "sha": SOURCE}}, f"releases/tags/{manifest['tag']}": release,
                 "releases/42": release,
                 "releases/42/assets?per_page=100": assets}
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[1:3] == ["release", "upload"]:
            name = Path(arguments[4]).name
            assets.append({"id": 100 + len(assets), "name": name, "size": (folder / name).stat().st_size})
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        if arguments[1:3] == ["release", "edit"]:
            release["draft"] = False
            if "--notes-file" in arguments:
                release["body"] = Path(arguments[arguments.index("--notes-file") + 1]).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(arguments, 0, b"", b"")
        endpoint = arguments[2].removeprefix(f"repos/{RELEASE.REPOSITORY}/")
        return subprocess.CompletedProcess(arguments, 0, json.dumps(responses[endpoint]).encode(), b"")

    monkeypatch.setattr(RELEASE.subprocess, "run", run)
    return responses, calls


def hidden_draft_api(monkeypatch, folder, manifest):
    """Model a listed draft hidden by the tag endpoint and reject duplicate creation."""
    responses, calls = fake_api(monkeypatch, folder, manifest)
    release = responses[f"releases/tags/{manifest['tag']}"]
    responses.update({"releases?per_page=100": [release], "releases/42": release})
    original_run = RELEASE.subprocess.run

    def run(arguments, **kwargs):
        """Keep ordinary repository reads real while returning bounded HTTP failures."""
        endpoint = arguments[2].removeprefix(f"repos/{RELEASE.REPOSITORY}/")
        failures = {f"releases/tags/{manifest['tag']}": 404, "releases": 409}
        if endpoint in failures:
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 1, json.dumps({"status": str(failures[endpoint])}).encode(), b"")
        return original_run(arguments, **kwargs)

    monkeypatch.setattr(RELEASE.subprocess, "run", run)
    return responses, calls


def created_draft_api(monkeypatch, folder, manifest, *, response=None, listing=None):
    """Keep a new draft absent from tag and list lookups throughout publication."""
    responses, calls = fake_api(monkeypatch, folder, manifest)
    release = responses['releases/42']
    assets = responses['releases/42/assets?per_page=100']
    assets.clear()
    created = []
    original_run = RELEASE.subprocess.run

    def run(arguments, **kwargs):
        """Return absent discovery while retaining direct-ID reads and real byte checks."""
        endpoint = arguments[2].removeprefix(f'repos/{RELEASE.REPOSITORY}/')
        if endpoint == f"releases/tags/{manifest['tag']}":
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 1, b'{"status":"404"}', b'')
        if endpoint == 'releases?per_page=100':
            calls.append(arguments)
            rows = [] if not created or listing is None else listing
            return subprocess.CompletedProcess(arguments, 0, json.dumps(rows).encode(), b'')
        if endpoint == 'releases':
            calls.append(arguments)
            created.append(True)
            return subprocess.CompletedProcess(arguments, 0, json.dumps(release if response is None else response).encode(), b'')
        return original_run(arguments, **kwargs)

    monkeypatch.setattr(RELEASE.subprocess, 'run', run)
    return responses, calls


def test_created_draft_id_survives_absent_list_through_stage_and_publish(tmp_path, monkeypatch):
    # GIVEN a newly created draft that remains absent from both discovery endpoints
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = created_draft_api(monkeypatch, folder, manifest)
    draft_io(monkeypatch, folder, responses['releases/42/assets?per_page=100'])
    public_io(monkeypatch, folder)
    assert responses['releases/42/assets?per_page=100'] == [], 'the draft begins with no uploaded assets'
    # WHEN the controller completes staging and publishing with its admitted create response
    code = RELEASE.main(['stage-publish', '--input', str(folder), '--tag', manifest['tag'], '--source-sha', SOURCE])
    # THEN all exact bytes pass readback without waiting for the list to expose the draft
    assert (code, responses['releases/42']['draft'], len(responses['releases/42/assets?per_page=100'])) == (
        0, False, 12), 'the created identity reaches complete public state through the workflow entrypoint'
    assert sum(call[2].endswith('/releases') for call in calls) == 1, 'one absent-list creation is followed only by exact-ID reads'


def test_stage_publish_readmits_local_bytes_after_staging_before_publication(tmp_path, monkeypatch):
    # GIVEN a staged release whose local wheel changes after the first admission
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    wheel = folder / f'brewdoc-{VERSION}-py3-none-any.whl'
    publications = []

    def stage(input_folder, admitted):
        """Change local bytes at the old boundary between the two CLI invocations."""
        wheel.write_bytes(b'changed after staging')
        return 42

    monkeypatch.setattr(RELEASE, 'stage', stage)
    monkeypatch.setattr(RELEASE, 'publish', lambda *args, **kwargs: publications.append((args, kwargs)))
    assert RELEASE.identity(wheel) == manifest['assets'][wheel.name], 'the first CLI admission starts with exact accepted bytes'
    # WHEN the combined controller crosses from staging to publication
    with pytest.raises(ValueError, match='^Release asset bytes changed$'):
        RELEASE.main(['stage-publish', '--input', str(folder), '--tag', manifest['tag'], '--source-sha', SOURCE])
    # THEN the second original admission refuses before any publication call
    assert publications == [], 'in-memory release identity cannot bypass the local byte admission'


@pytest.mark.parametrize('response', [{}, {'id': True}, {'id': 0}, []])
def test_created_draft_refuses_invalid_create_response_before_upload(tmp_path, monkeypatch, response):
    # GIVEN a create response that cannot identify a release unambiguously
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    _responses, calls = created_draft_api(monkeypatch, folder, manifest, response=response)
    assert calls == [], 'creation starts after the existing list and identity admission'
    # WHEN staging receives the malformed creation identity
    with pytest.raises(ValueError, match='^Existing release identifier is invalid$'):
        RELEASE.stage(folder, manifest)
    # THEN malformed IDs cannot authorize uploads or release edits
    assert [call for call in calls if call[1] == 'release'] == [], 'creation-response refusal precedes any asset mutation'


@pytest.mark.parametrize('listing,reason', [
    ([{'tag_name': 'v0.3.0', 'id': 42}, {'tag_name': 'v0.3.0', 'id': 43}], 'Release tag is ambiguous'),
    ({'releases': []}, 'Release list is malformed'),
    ([{}] * 100, 'Release list is truncated'),
    ([{'tag_name': 'v0.3.0', 'id': 43}], 'Existing release identity changed during lookup'),
])
def test_created_draft_direct_lookup_preserves_list_refusals(tmp_path, monkeypatch, listing, reason):
    # GIVEN a list conflict that appears after the controller creates a draft
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    _responses, calls = created_draft_api(monkeypatch, folder, manifest, listing=listing)
    assert calls == [], 'preflight has not performed a remote mutation yet'
    # WHEN the created ID is re-read alongside the original list guards
    with pytest.raises(ValueError, match='^' + reason + '$'):
        RELEASE.stage(folder, manifest)
    # THEN every original list refusal remains immediate before upload
    assert [call for call in calls if call[1] == 'release'] == [], 'a known create ID cannot override ambiguous or malformed discovery'


@pytest.mark.parametrize('change', [{'target_commitish': 'b' * 40}, {'tag_name': 'v0.2.0'}, {'prerelease': True}])
def test_created_draft_direct_lookup_refuses_foreign_identity_before_upload(tmp_path, monkeypatch, change):
    # GIVEN a created release whose direct metadata differs from the immutable manifest
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = created_draft_api(monkeypatch, folder, manifest)
    responses['releases/42'].update(change)
    assert responses['releases/42']['id'] == 42, 'the returned numeric ID alone does not establish release ownership'
    # WHEN staging re-reads the exact created release
    with pytest.raises(ValueError, match='^Existing release identity differs$'):
        RELEASE.stage(folder, manifest)
    # THEN all foreign identity conflicts retain their message and refuse before upload
    assert [call for call in calls if call[1] == 'release'] == [], 'foreign created metadata cannot authorize writes'


@pytest.mark.parametrize('change', [{'target_commitish': 'b' * 40}, {'tag_name': 'v0.2.0'}, {'prerelease': True}])
def test_known_id_cannot_replace_foreign_tag_metadata_with_valid_direct_metadata(tmp_path, monkeypatch, change):
    # GIVEN a foreign tag response sharing a valid direct release's numeric ID
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    responses[f"releases/tags/{manifest['tag']}"] = {**responses['releases/42'], **change}
    assert responses['releases/42']['target_commitish'] == SOURCE, 'the direct response alone would pass identity admission'
    # WHEN a known numeric ID is supplied alongside the conflicting tag read
    with pytest.raises(ValueError, match='^Existing release identity differs$'):
        RELEASE.release_state(folder, manifest, release_id=42)
    # THEN the first foreign identity remains an immediate refusal with the old message
    assert [call[2] for call in calls] == [f"repos/{RELEASE.REPOSITORY}/git/ref/tags/{manifest['tag']}",
        f"repos/{RELEASE.REPOSITORY}/releases/tags/{manifest['tag']}"], 'foreign tag identity refuses before any direct or asset read'


def draft_io(monkeypatch, folder, assets, substitutions=None):
    """Fake the authenticated download process while retaining actual byte validation."""
    substitutions = substitutions or {}

    class Process:
        """Expose only the subprocess lifecycle used by bounded asset readback."""

        def __init__(self, arguments, **kwargs):
            asset_id = int(arguments[2].rsplit("/", 1)[1])
            name = next(row["name"] for row in assets if row["id"] == asset_id)
            self.stdout = io.BytesIO(substitutions.get(name, (folder / name).read_bytes()))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stdout.close()

        def wait(self, **kwargs):
            return 0

        def poll(self):
            return 0

        def kill(self):
            self.stdout.close()

    monkeypatch.setattr(RELEASE.subprocess, "Popen", Process)


def public_io(monkeypatch, folder, substitutions=None):
    """Fake urllib's transport without replacing release byte or destination checks."""
    substitutions = substitutions or {}
    urls = []

    def open_url(url, **kwargs):
        urls.append(url)
        name = url.rsplit("/", 1)[1]
        return io.BytesIO(substitutions.get(name, (folder / name).read_bytes()))

    monkeypatch.setattr(RELEASE.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_url))
    return urls


def public_reports(tmp_path, manifest, native, target="linux-x64"):
    """Write exact synthetic public acquisition and native consumer report identities."""
    directory = tmp_path / "reports" / target
    directory.mkdir(parents=True)
    archive = native["archive"]
    RELEASE.write_json(directory / "archive.download.json", {
        "schema": 1, "tag": manifest["tag"], "source_sha": SOURCE, "target": target,
        "release_id": 42, "asset_id": 7, "asset_filename": archive["filename"],
        "url": RELEASE.public_url(manifest["tag"], archive["filename"]),
        "download_sha256": archive["sha256"], "bytes": archive["bytes"], "verified_at": "2026-01-01T00:00:00+00:00"})
    report = {"schema": 1, "verdict": "PASS", "source_sha": SOURCE, "version": VERSION,
              "target": target, "archive_sha256": archive["sha256"], "self_check": 38,
              "probes": [row["probe"] for row in native["probes"]],
              "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]],
              "https": {"file_ok": True, "acquisition": {"capture_status": "static"}},
              "invalid_tls": {"acquisition": {"refusal_stage": "tls"}},
              "children": {"dns": ["127.0.0.1"], "worker": {}},
              "javascript": {"acquisition": {"capture_status": "settled"}}}
    RELEASE.write_json(directory / "verification.json", report)
    return directory, report


def public_registry(monkeypatch, folder, manifest):
    """Return actual accepted bytes and version-specific metadata at fake HTTP edges."""
    packages = RELEASE.read_json(folder / "npm-manifest.json")["packages"]
    pypi = {"urls": [{"filename": name, "size": row["bytes"], "digests": {"sha256": row["sha256"]},
                     "yanked": False, "url": "https://files.pythonhosted.org/" + name}
                    for name, row in manifest["assets"].items() if name.endswith(".whl") or name == f"brewdoc-{manifest['version']}.tar.gz"]}

    def open_url(url, **kwargs):
        if url.startswith("https://pypi.org/"):
            return io.BytesIO(json.dumps(pypi).encode())
        name = url.rsplit("/", 1)[1]
        if name.endswith(".tgz"):
            return io.BytesIO((folder / name).read_bytes())
        package = next(row for row in packages if row["metadata"]["name"] == url.split("/")[-2])
        metadata = {**package["metadata"], "dist": {"integrity": package["integrity"],
                    "tarball": "https://registry.npmjs.org/" + package["metadata"]["name"] + "/-/" + package["filename"]}}
        return io.BytesIO(json.dumps(metadata).encode())

    monkeypatch.setattr(RELEASE.urllib.request, "urlopen", open_url)


def complete_public_evidence(tmp_path, folder, manifest, assets):
    """Write the three platform reports using the concrete owner-produced schemas."""
    binaries = RELEASE.read_json(folder / "binaries.json")
    npm = RELEASE.read_json(folder / "npm-manifest.json")
    for target in RELEASE.TARGETS:
        native = binaries["targets"][target]
        directory, report = public_reports(tmp_path, manifest, native, target)
        downloaded = RELEASE.read_json(directory / "archive.download.json")
        downloaded["asset_id"] = next(row["id"] for row in assets if row["name"] == native["archive"]["filename"])
        RELEASE.write_json(directory / "archive.download.json", downloaded)
        if target == "windows-x64":
            report["javascript"] = {"acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
            report["children"]["exited_owner"] = {"code": 1, "stdout_bytes": 0, "stderr_bytes": 0}
            RELEASE.write_json(directory / "verification.json", report)
    for target in ("linux-x64", "macos-arm64"):
        native = binaries["targets"][target]
        directory = tmp_path / "reports" / target
        package = next(row["metadata"]["name"] for row in npm["packages"] if row.get("target") == target)
        consumer = {"self_check": 38, "selected_package": package, "atomic_refusal": True, "help_sha256": "e" * 64,
                    "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]]}
        RELEASE.write_json(directory / "npm-public.json", {"schema": 1, "verdict": "PASS", "mode": "public", "target": target,
            "version": VERSION, "source_sha": SOURCE, "node": npm["node"], "npm": npm["npm"],
            "packages": {row["metadata"]["name"]: row["integrity"] for row in npm["packages"]},
            "consumers": {name: copy.deepcopy(consumer) for name in ("global", "local", "npx")},
            "signatures": {"verified_packages": 2, "verified_attestations": 2, "stdout_sha256": "f" * 64},
            "cancellation": {"native_observed": True, "native_terminated": True,
                "javascript": "observed-and-terminated", "dns_observed": False, "owned_processes": 2}})
    return tmp_path / "reports"


def test_prepare_seals_one_complete_native_and_python_set(tmp_path, monkeypatch):
    # GIVEN three real synthetic native archives and one accepted package pair
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    assert manifest["version"] == "0.3.0", "the source metadata selects the release version"
    # WHEN the sealed release is independently admitted
    actual = RELEASE.admit(folder, "v0.3.0", SOURCE)
    # THEN the complete release identity and ordinary upload set remain fixed
    assert actual == manifest, "admission preserves the full prepared identity"
    assert sorted(path.name for path in folder.iterdir()) == [
        "SHA256SUMS", "binaries.json", "brewdoc-0.3.0-linux-x64.tar.gz", "brewdoc-0.3.0-macos-arm64.tar.gz",
        "brewdoc-0.3.0-py3-none-any.whl", "brewdoc-0.3.0-windows-x64.zip", "brewdoc-0.3.0.tar.gz",
        "brewdoc-0.3.0.tgz", "brewdoc-darwin-arm64-0.3.0.tgz", "brewdoc-linux-x64-glibc-0.3.0.tgz",
        "npm-manifest.json", "release-manifest.json", "staged-body.md"
    ], "the asset directory contains only admitted files"


@pytest.mark.parametrize("tag,sha", [("v0.3.1", SOURCE), ("v0.3.0", "d" * 40)])
def test_admission_refuses_workflow_tag_or_source_substitution(tmp_path, monkeypatch, tag, sha):
    # GIVEN a release whose sealed source and version are known
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    assert manifest["source_sha"] == SOURCE, "the package is bound to the original source"
    # WHEN a workflow supplies a different immutable identity
    with pytest.raises(ValueError, match="tag, repository or source"):
        RELEASE.admit(folder, tag, sha)
    # THEN no staged body was changed
    assert (folder / "staged-body.md").read_bytes() == b"Staged release instructions.\n", "refusal leaves the draft intact"


@pytest.mark.parametrize("filename", ["extra.zip", "brewdoc-0.3.0-py3-none-any.whl", "staged-body.md"])
def test_admission_refuses_unexpected_assets_or_mutated_bytes(tmp_path, monkeypatch, filename):
    # GIVEN a sealed release inventory
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    expected = copy.deepcopy(manifest)
    assert manifest == expected, "the sealed inventory starts unchanged"
    (folder / filename).write_bytes(b"substituted bytes")
    # WHEN admission observes the extra or modified file
    with pytest.raises(ValueError, match="unexpected|bytes changed|body changed"):
        RELEASE.admit(folder, manifest["tag"], SOURCE)
    # THEN the original inventory remains immutable
    assert RELEASE.read_json(folder / "release-manifest.json") == expected, "refusal never rewrites inventory"


def test_admission_refuses_removed_asset_even_with_rewritten_inventory(tmp_path, monkeypatch):
    # GIVEN an attacker removes a target and rewrites checksums consistently
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    name = "brewdoc-0.3.0-windows-x64.zip"
    assert name in manifest["assets"], "the complete release originally includes Windows"
    (folder / name).unlink()
    del manifest["assets"][name]
    RELEASE.write_json(folder / "release-manifest.json", manifest)
    (folder / "SHA256SUMS").write_text(RELEASE.checksums(manifest["assets"]), encoding="ascii")
    # WHEN admission compares the inventory against native owner records
    with pytest.raises(ValueError, match="Sealed native archive differs"):
        RELEASE.admit(folder, manifest["tag"], SOURCE)
    # THEN the native target record has not been silently removed
    assert sorted(RELEASE.read_json(folder / "binaries.json")["targets"]) == [
        "linux-x64", "macos-arm64", "windows-x64"], "all native target identities remain required"


@pytest.mark.parametrize("stdout,code", [(b'{"status":"403","message":"secret"}', 1), (b"not JSON", 1)])
def test_unavailable_preflight_is_never_treated_as_release_absence(monkeypatch, stdout, code):
    # GIVEN a GitHub error whose response must not reach logs
    monkeypatch.setattr(RELEASE.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], code, stdout, b"private diagnostic"))
    assert code == 1, "the preflight process failed"
    # WHEN an operation explicitly permits release absence
    with pytest.raises(ValueError, match="preflight") as error:
        RELEASE.api("releases/tags/v0.3.0", missing=True)
    # THEN failure details contain no untrusted response or stderr
    assert ("secret" in str(error.value)) is False, "remote error details cannot expose credentials"


def test_explicit_http_404_is_the_only_absent_release(monkeypatch):
    # GIVEN a valid GitHub 404 JSON error
    monkeypatch.setattr(RELEASE.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, b'{"status":"404"}', b""))
    assert RELEASE.REPOSITORY == "kochetkov-ma/brewdoc", "publication targets the fixed official repository"
    # WHEN missing releases are explicitly admitted
    result = RELEASE.api("releases/tags/v0.3.0", missing=True)
    # THEN only this absence is reusable for draft creation
    assert result is None, "an explicit 404 admits a new draft"


def test_release_state_finds_unique_existing_draft_when_tag_endpoint_returns_404(tmp_path, monkeypatch):
    # GIVEN an exact existing draft listed by GitHub but hidden by its tag endpoint
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = hidden_draft_api(monkeypatch, folder, manifest)
    release = responses["releases/42"]
    rows = responses["releases/42/assets?per_page=100"]
    assert (release["draft"], len(rows)) == (True, 12), "the existing draft already holds the complete exact upload set"
    # WHEN release lookup admits absence only after checking authenticated drafts
    actual = RELEASE.release_state(folder, manifest, missing=True)
    # THEN the existing draft is returned instead of admitting duplicate creation
    assert actual == (release, {row["name"]: row for row in rows}), "a tag lookup 404 cannot conceal an existing matching draft"
    assert [call for call in calls if "--method" in call] == [], "draft discovery remains read-only"


@pytest.mark.parametrize("change,reason", [("ambiguous", "ambiguous|multiple|duplicated"),
                                         ("truncated", "bounded|truncated"),
                                         ("malformed", "malformed|invalid|list")])
def test_hidden_draft_lookup_refuses_ambiguous_truncated_or_malformed_listing(tmp_path, monkeypatch, change, reason):
    # GIVEN a tag lookup 404 and a listing that cannot establish unique release identity
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = hidden_draft_api(monkeypatch, folder, manifest)
    release = responses["releases/42"]
    assert release["tag_name"] == manifest["tag"], "the initial draft matches the admitted tag"
    listings = {"ambiguous": [release, {**release, "id": 43}],
                "truncated": [{**release, "id": index + 1} for index in range(100)],
                "malformed": {"releases": [release]}}
    responses["releases?per_page=100"] = listings[change]
    # WHEN bounded authenticated fallback tries to resolve the existing draft
    with pytest.raises(ValueError, match=reason):
        RELEASE.release_state(folder, manifest, missing=True)
    # THEN uncertain absence cannot authorize duplicate release creation
    assert [call for call in calls if "--method" in call] == [], "invalid listing refusal performs no remote write"


def test_stage_reuses_complete_hidden_draft_without_creation_or_upload(tmp_path, monkeypatch):
    # GIVEN one existing draft whose exact twelve assets already passed staging
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = hidden_draft_api(monkeypatch, folder, manifest)
    draft_io(monkeypatch, folder, responses["releases/42/assets?per_page=100"])
    assert len(responses["releases/42/assets?per_page=100"]) == 12, "the hidden draft already contains all required bytes"
    # WHEN staging resumes through the authenticated draft fallback
    actual = RELEASE.stage(folder, manifest)
    # THEN complete byte readback succeeds without duplicate creation or replacement
    assert actual == 42, "idempotent staging retains the admitted identity for publication"
    assert [call for call in calls if "--method" in call or call[1] == "release"] == [], "existing exact assets require no public mutation"


def test_stage_accepts_complete_public_release_with_exact_staged_body_without_mutation(tmp_path, monkeypatch):
    # GIVEN an already public release retaining the exact staged body and complete bytes
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    downloads = public_io(monkeypatch, folder)
    assert (responses["releases/tags/v0.3.0"]["draft"], len(responses["releases/42/assets?per_page=100"])) == (
        False, 12), "the public release already contains the complete accepted set"
    # WHEN staging resumes after the previous publication transition
    actual = RELEASE.stage(folder, manifest)
    # THEN exact byte verification completes without any release or asset mutation
    assert actual == 42, "the verified public release retains its admitted identity"
    assert sorted(url.rsplit("/", 1)[1] for url in downloads) == sorted(RELEASE.upload_identities(folder, manifest)), "every existing public asset is read back"
    assert [call for call in calls if "--method" in call or call[1] == "release"] == [], "a complete public release requires no publication mutation"


@pytest.mark.parametrize("delta,asset_count,replace_bytes,reason", [
    ({"body": "Changed release instructions.\n"}, 12, False, "staged body differs"),
    ({}, 11, False, "asset set is incomplete"),
    ({}, 12, True, "asset bytes differ"),
], ids=["changed-body", "partial-assets", "changed-bytes"])
def test_public_release_staging_refuses_changed_body_partial_assets_or_bytes(tmp_path, monkeypatch, delta, asset_count, replace_bytes, reason):
    # GIVEN a public release with one conflicting staged-body or asset identity
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    release, rows = responses["releases/tags/v0.3.0"], responses["releases/42/assets?per_page=100"]
    assert (release["draft"], len(rows)) == (False, 12), "the release starts public with all twelve assets"
    release.update(delta)
    rows[:] = rows[:asset_count]
    name = "brewdoc-0.3.0-py3-none-any.whl"
    substitutions = {False: {}, True: {name: b"x" * manifest["assets"][name]["bytes"]}}[replace_bytes]
    public_io(monkeypatch, folder, substitutions)
    # WHEN repeated staging verifies the existing publication
    with pytest.raises(ValueError, match=reason):
        RELEASE.stage(folder, manifest)
    # THEN refusal cannot upload, replace or republish any public asset
    assert [call for call in calls if "--method" in call or call[1] == "release"] == [], "public identity conflicts refuse before mutation"


@pytest.mark.parametrize("delta,replace_bytes,reason", [
    ({"target_commitish": "b" * 40}, False, "Existing release identity differs"),
    ({"body": "Changed release instructions.\n"}, False, "staged body differs"),
    ({}, True, "asset bytes differ"),
], ids=["foreign-source", "changed-body", "changed-bytes"])
def test_hidden_draft_staging_refuses_foreign_source_body_or_actual_bytes(tmp_path, monkeypatch, delta, replace_bytes, reason):
    # GIVEN an existing hidden draft with one conflicting publication identity
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = hidden_draft_api(monkeypatch, folder, manifest)
    release = responses["releases/42"]
    assert release["target_commitish"] == SOURCE, "the draft starts at the exact admitted source"
    release.update(delta)
    name = "brewdoc-0.3.0-py3-none-any.whl"
    substitutions = {False: {}, True: {name: b"x" * manifest["assets"][name]["bytes"]}}[replace_bytes]
    draft_io(monkeypatch, folder, responses["releases/42/assets?per_page=100"], substitutions)
    # WHEN staging validates the discovered draft and every existing asset
    with pytest.raises(ValueError, match=reason):
        RELEASE.stage(folder, manifest)
    # THEN foreign or altered state refuses before creation, upload or publication
    assert [call for call in calls if "--method" in call or call[1] == "release"] == [], "conflicting draft evidence never permits mutation"


@pytest.mark.parametrize("change", [{"target_commitish": "main"}, {"tag_name": "v0.2.0"}, {"prerelease": True}])
def test_release_preflight_refuses_existing_identity_mismatch(tmp_path, monkeypatch, change):
    # GIVEN remote release metadata that differs from the admitted immutable source
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    _responses, calls = fake_api(monkeypatch, folder, manifest, changes=change)
    assert calls == [], "preflight starts without public mutations"
    # WHEN the helper checks the existing release
    with pytest.raises(ValueError, match="Existing release identity differs"):
        RELEASE.release_state(folder, manifest)
    # THEN only tag and release reads have been attempted
    assert len(calls) == 2, "identity refusal precedes asset uploads or release edits"


def test_release_preflight_refuses_unexpected_remote_assets(tmp_path, monkeypatch):
    # GIVEN an otherwise matching release containing one foreign asset
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    responses["releases/42/assets?per_page=100"].append({"id": 999, "name": "foreign.zip", "size": 1})
    assert len(calls) == 0, "the mutation-free preflight starts idle"
    # WHEN the helper admits remote inventory
    with pytest.raises(ValueError, match="unexpected"):
        RELEASE.release_state(folder, manifest)
    # THEN every invocation remains an official repository read
    assert [call[1] for call in calls] == ["api", "api", "api"], "no blanket overwrite occurs"


def test_public_download_refuses_existing_destination_before_network(tmp_path):
    # GIVEN a destination belonging to another owner
    output = tmp_path / "owned.tar.gz"
    output.write_bytes(b"preserve")
    assert output.read_bytes() == b"preserve", "the destination contains prior work"
    # WHEN the helper attempts a public acquisition
    with pytest.raises(ValueError, match="destination exists"):
        RELEASE.download("https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.0/archive", output,
                         {"bytes": 1, "sha256": "a" * 64})
    # THEN the original file remains intact
    assert output.read_bytes() == b"preserve", "downloads never overwrite existing files"


@pytest.mark.parametrize("url", ["http://github.com/file", "https://evil.example/file", "https://user:secret@github.com/file"])
def test_public_redirect_refuses_external_or_credentialled_destinations(url):
    # GIVEN a public redirect outside the fixed artifact boundary
    assert isinstance(url, str), "the proposed redirect is a URL string"
    # WHEN urllib asks the handler to follow it
    with pytest.raises(ValueError, match="outside artifact hosts"):
        RELEASE.PublicRedirect().redirect_request(None, None, 302, "Found", {}, url)
    # THEN the helper's ordinary version-specific URL remains exact
    assert RELEASE.public_url("v0.3.0", "asset.zip") == (
        "https://github.com/kochetkov-ma/brewdoc/releases/download/v0.3.0/asset.zip"), "public URLs use the fixed repository"


def test_stage_uploads_only_missing_assets_after_exact_existing_readback(tmp_path, monkeypatch):
    # GIVEN a matching draft with exactly two missing upload assets
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    assets = responses["releases/42/assets?per_page=100"]
    del assets[-2:]
    draft_io(monkeypatch, folder, assets)
    assert len(assets) == 10, "the draft already holds all ten payload assets"
    # WHEN staging resumes after byte verification of every present asset
    RELEASE.stage(folder, manifest)
    # THEN only the manifest and complete checksums are uploaded without clobber
    assert [call for call in calls if call[1] == "release"] == [
        ["gh", "release", "upload", "v0.3.0", str(folder / "SHA256SUMS"), "--repo", "kochetkov-ma/brewdoc"],
        ["gh", "release", "upload", "v0.3.0", str(folder / "release-manifest.json"), "--repo", "kochetkov-ma/brewdoc"]
    ], "draft reuse never replaces existing assets"


def test_stage_refuses_same_size_different_remote_bytes_before_upload(tmp_path, monkeypatch):
    # GIVEN a matching draft whose wheel metadata hides changed remote bytes
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    name = "brewdoc-0.3.0-py3-none-any.whl"
    replacement = b"x" * (folder / name).stat().st_size
    draft_io(monkeypatch, folder, responses["releases/42/assets?per_page=100"], {name: replacement})
    assert len(replacement) == manifest["assets"][name]["bytes"], "remote sizes alone cannot reveal substitution"
    # WHEN staging verifies existing draft bytes
    with pytest.raises(ValueError, match="asset bytes differ"):
        RELEASE.stage(folder, manifest)
    # THEN no upload or release edit is attempted
    assert [call for call in calls if call[1] == "release"] == [], "unknown existing bytes refuse before mutation"


def test_publish_verifies_complete_draft_and_every_public_asset(tmp_path, monkeypatch):
    # GIVEN one complete matching draft
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    draft_io(monkeypatch, folder, responses["releases/42/assets?per_page=100"])
    urls = public_io(monkeypatch, folder)
    assert responses["releases/tags/v0.3.0"]["draft"] is True, "the candidate starts unpublished"
    # WHEN publication verifies its draft bytes and staged body
    RELEASE.publish(folder, manifest)
    # THEN only draft visibility changes and all twelve public assets are downloaded
    assert [call for call in calls if call[1] == "release"] == [
        ["gh", "release", "edit", "v0.3.0", "--draft=false", "--repo", "kochetkov-ma/brewdoc"]
    ], "the helper publishes the admitted draft without replacing assets"
    assert sorted(url.rsplit("/", 1)[1] for url in urls) == sorted(
        RELEASE.upload_identities(folder, manifest)), "public readback covers every admitted asset"


@pytest.mark.parametrize("payload", [b"too long", b"x", b"bad"])
def test_public_download_refuses_truncated_oversized_or_changed_bytes(tmp_path, monkeypatch, payload):
    # GIVEN an admitted three-byte payload and a substituted HTTP response
    folder = tmp_path / "remote"
    folder.mkdir()
    (folder / "asset.zip").write_bytes(b"yes")
    public_io(monkeypatch, folder, {"asset.zip": payload})
    expected = RELEASE.identity(folder / "asset.zip")
    assert expected["bytes"] == 3, "download containment uses the sealed byte budget"
    # WHEN the public consumer reads the substituted bytes
    with pytest.raises(ValueError, match="incomplete|exceeds|bytes differ"):
        RELEASE.download(RELEASE.public_url("v0.3.0", "asset.zip"), tmp_path / "downloaded", expected)
    # THEN the accepted producer payload is unchanged
    assert (folder / "asset.zip").read_bytes() == b"yes", "readback failures cannot alter accepted producer bytes"


def test_native_evidence_requires_exact_public_asset_and_all_fixture_outputs(tmp_path, monkeypatch):
    # GIVEN a complete native report tied to one actual public release asset
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["linux-x64"]
    directory, report = public_reports(tmp_path, manifest, native)
    assets = {native["archive"]["filename"]: {"id": 7}}
    assert len(report["fixtures"]) == 11, "all sealed licensed fixture outputs are represented"
    # WHEN the helper binds public acquisition to the actual native consumer report
    result = RELEASE.native_evidence(directory, "linux-x64", manifest, native, {"id": 42}, assets)
    # THEN the complete report is admitted without rewriting its evidence
    assert result is None, "all exact public source and consumer identities pass admission"
    assert RELEASE.read_json(directory / "verification.json") == report, "admission does not manufacture consumer evidence"


@pytest.mark.parametrize("owner_record", [{}, {"exited_owner": {"code": 0, "stdout_bytes": 0, "stderr_bytes": 0}},
                                       {"exited_owner": {"code": 1, "stdout_bytes": 1, "stderr_bytes": 0}},
                                       {"exited_owner": {"code": 1, "stdout_bytes": 0, "stderr_bytes": 1}}])
def test_windows_native_evidence_refuses_missing_or_altered_exited_owner_result(tmp_path, monkeypatch, owner_record):
    # GIVEN a complete Windows report with the exact frozen exited-owner refusal
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["windows-x64"]
    directory, report = public_reports(tmp_path, manifest, native, "windows-x64")
    report["javascript"] = {"acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
    report["children"]["exited_owner"] = {"code": 1, "stdout_bytes": 0, "stderr_bytes": 0}
    RELEASE.write_json(directory / "verification.json", report)
    assets = {native["archive"]["filename"]: {"id": 7}}
    assert RELEASE.native_evidence(directory, "windows-x64", manifest, native, {"id": 42}, assets) is None, \
        "the complete Windows refusal remains admissible"
    # WHEN the public report loses or alters the frozen owner refusal
    report["children"] = {"dns": ["127.0.0.1"], **owner_record}
    RELEASE.write_json(directory / "verification.json", report)
    # THEN live-owner DNS success cannot replace the missing exact owner admission proof
    with pytest.raises(ValueError, match="Native public Windows exited-owner evidence differs"):
        RELEASE.native_evidence(directory, "windows-x64", manifest, native, {"id": 42}, assets)


@pytest.mark.parametrize("delta", [{"self_check": 37}, {"fixtures": []}, {"archive_sha256": "d" * 64}, {"verdict": "FAIL"}])
def test_native_evidence_refuses_incomplete_or_foreign_runtime_results(tmp_path, monkeypatch, delta):
    # GIVEN a public acquisition paired with altered native runtime evidence
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["linux-x64"]
    directory, report = public_reports(tmp_path, manifest, native)
    report.update(delta)
    RELEASE.write_json(directory / "verification.json", report)
    assert len(list(directory.glob("*.download.json"))) == 1, "one precise public acquisition report exists"
    # WHEN availability admission checks consumer behavior against the sealed oracle
    with pytest.raises(ValueError, match="consumer evidence differs"):
        RELEASE.native_evidence(directory, "linux-x64", manifest, native, {"id": 42},
                                {native["archive"]["filename"]: {"id": 7}})
    # THEN the rejected report is preserved for review
    assert RELEASE.read_json(directory / "verification.json") == report, "refusal leaves failed evidence intact"


def test_native_evidence_refuses_download_from_another_release_id(tmp_path, monkeypatch):
    # GIVEN valid runtime bytes downloaded from a different public release identity
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["linux-x64"]
    directory, report = public_reports(tmp_path, manifest, native)
    assert report["verdict"] == "PASS", "runtime success alone cannot establish current publication"
    # WHEN the current immutable release id differs from acquisition evidence
    with pytest.raises(ValueError, match="download identity differs"):
        RELEASE.native_evidence(directory, "linux-x64", manifest, native, {"id": 43},
                                {native["archive"]["filename"]: {"id": 7}})
    # THEN no availability summary has been synthesized
    assert sorted(path.name for path in directory.iterdir()) == [
        "archive.download.json", "verification.json"], "unmatched public reports remain unapproved"


def test_npm_evidence_refuses_a_free_form_pass_without_registry_consumers(tmp_path, monkeypatch):
    # GIVEN a report claiming success without npm package or npx identity
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["linux-x64"]
    directory = tmp_path / "npm-reports"
    directory.mkdir()
    RELEASE.write_json(directory / "npm-public.json", {"verdict": "PASS"})
    assert RELEASE.read_json(directory / "npm-public.json") == {"verdict": "PASS"}, "the report contains no actual consumer proof"
    # WHEN the helper binds normal registry consumers to an exact release
    with pytest.raises(ValueError, match="npm public consumer identity differs"):
        RELEASE.npm_evidence(directory, "linux-x64", manifest, native,
                             {"node": "24.21.0", "npm": "12.2.0", "packages": []})
    # THEN the unsupported success claim remains unchanged
    assert RELEASE.read_json(directory / "npm-public.json") == {"verdict": "PASS"}, "the helper never promotes arbitrary PASS text"


@pytest.mark.parametrize("cancellation,accepted", [
    ({"native_observed": True, "native_terminated": True, "javascript": "observed-and-terminated", "dns_observed": False, "owned_processes": 2}, True),
    ({"native_observed": True, "native_terminated": False, "javascript": "observed-and-terminated", "dns_observed": False, "owned_processes": 2}, False),
])
def test_npm_evidence_binds_install_npx_provenance_and_cancellation(tmp_path, monkeypatch, cancellation, accepted):
    # GIVEN exact public registry package identities and all three normal consumers
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    native = RELEASE.read_json(folder / "binaries.json")["targets"]["linux-x64"]
    directory = tmp_path / "npm-consumers"
    directory.mkdir()
    names = {"linux-x64": "brewdoc-linux-x64-glibc", "macos-arm64": "brewdoc-darwin-arm64"}
    packages = [{"target": target, "metadata": {"name": name}, "integrity": "sha512-synthetic"} for target, name in names.items()]
    packages.append({"metadata": {"name": "brewdoc"}, "integrity": "sha512-main"})
    npm = {"node": "24.21.0", "npm": "12.2.0", "packages": packages}
    consumer = {"self_check": 38, "selected_package": names["linux-x64"], "atomic_refusal": True, "help_sha256": "e" * 64,
                "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]]}
    report = {"schema": 1, "verdict": "PASS", "mode": "public", "target": "linux-x64", "version": VERSION,
              "source_sha": SOURCE, "node": npm["node"], "npm": npm["npm"],
              "packages": {row["metadata"]["name"]: row["integrity"] for row in packages},
              "consumers": {name: copy.deepcopy(consumer) for name in ("global", "local", "npx")},
              "signatures": {"verified_packages": 2, "verified_attestations": 2, "stdout_sha256": "f" * 64},
              "cancellation": cancellation}
    RELEASE.write_json(directory / "npm-public.json", report)
    assert sorted(report["consumers"]) == ["global", "local", "npx"], "each independent consumer provides sealed output identities"
    # WHEN admission considers whether owned native children actually terminated
    contexts = {True: nullcontext(), False: pytest.raises(ValueError, match="cancellation evidence differs")}
    with contexts[accepted]:
        RELEASE.npm_evidence(directory, "linux-x64", manifest, native, npm)
    # THEN all report bindings pass only when cancellation succeeded
    assert RELEASE.read_json(directory / "npm-public.json") == report, "admission preserves success and refusal evidence"


def test_tag_preflight_peels_annotated_tag_before_matching_source(tmp_path, monkeypatch):
    # GIVEN an annotated tag object whose SHA differs from its source commit
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest)
    responses["git/ref/tags/v0.3.0"]["object"] = {"type": "tag", "sha": "d" * 40}
    responses["git/tags/" + "d" * 40] = {"object": {"type": "commit", "sha": SOURCE}}
    assert calls == [], "tag admission starts without repository writes"
    # WHEN the helper checks the annotated release reference
    result = RELEASE.tag_preflight(manifest)
    # THEN source identity uses the peeled commit and both operations are reads
    assert result is None, "a correctly peeled annotated tag matches the reviewed source"
    assert [call[2] for call in calls] == [
        "repos/kochetkov-ma/brewdoc/git/ref/tags/v0.3.0", "repos/kochetkov-ma/brewdoc/git/tags/" + "d" * 40
    ], "tag object SHA is never confused with the release source SHA"


def test_prepare_refuses_omitted_npm_before_any_input_or_remote_access(tmp_path, monkeypatch):
    # GIVEN omitted npm input and unavailable paths that must never be inspected
    calls = []
    monkeypatch.setattr(RELEASE.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert calls == [], "there is no repository or network access before admission"
    # WHEN the new release is prepared without its mandatory npm package set
    with pytest.raises(ValueError, match="requires all three npm packages"):
        RELEASE.prepare(tmp_path / "missing-source", tmp_path / "missing-package", tmp_path / "missing-native",
                        tmp_path / "release", tmp_path / "missing-body")
    # THEN preparation refuses before reading or writing anything
    assert (calls, (tmp_path / "release").exists()) == ([], False), "omission never admits a partial new release"


@pytest.mark.parametrize("operation", ["stage", "publish"])
def test_remote_mutations_refuse_manifest_without_mandatory_npm(tmp_path, monkeypatch, operation):
    # GIVEN a complete new release whose npm requirement is tampered with
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    manifest["npm"] = False
    calls = []
    monkeypatch.setattr(RELEASE.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    assert manifest["npm"] is False, "the adversarial manifest attempts the removed non-npm lane"
    # WHEN either public mutation entry is called directly
    with pytest.raises(ValueError, match="requires all three npm packages"):
        getattr(RELEASE, operation)(folder, manifest)
    # THEN no preflight, upload or visibility change is attempted
    assert calls == [], "mandatory npm omission refuses before every remote operation"


def test_prepare_cli_requires_explicit_npm_input_before_source_access(tmp_path, capsys):
    # GIVEN all other required CLI inputs refer to unavailable paths
    arguments = ["prepare", "--checkout", str(tmp_path / "source"), "--bundle", str(tmp_path / "packages"),
                 "--native", str(tmp_path / "native"), "--output", str(tmp_path / "release"),
                 "--body", str(tmp_path / "body")]
    assert (tmp_path / "release").exists() is False, "the CLI begins without an output directory"
    # WHEN the new release CLI omits its mandatory npm argument
    with pytest.raises(SystemExit) as error:
        RELEASE.main(arguments)
    # THEN argparse refuses omission before preparing a partial release
    captured = capsys.readouterr()
    assert (error.value.code, "--npm" in captured.err, captured.out) == (2, True, ""), "CLI omission is a usage failure with no asset output"


def test_admit_public_binds_three_native_hosts_and_two_npm_hosts(tmp_path, monkeypatch):
    # GIVEN exact public bytes and complete reports from all three native hosts
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = complete_public_evidence(tmp_path, folder, manifest, responses["releases/42/assets?per_page=100"])
    assert sorted(path.name for path in evidence.iterdir()) == ["linux-x64", "macos-arm64", "windows-x64"], "every native host has concrete reports"
    assert sorted(path.name for path in (evidence / "windows-x64").iterdir()) == [
        "archive.download.json", "verification.json"], "Windows supplies native proof without npm evidence"
    # WHEN the helper independently admits actual public channel bytes and runtime identities
    accepted = RELEASE.admit_public(folder, manifest, evidence)
    # THEN availability is produced only from complete matching evidence without public writes
    assert accepted["consumers"] == {"linux-x64": {"native": "PASS", "npm": "PASS", "npx": "PASS"},
        "windows-x64": {"native": "PASS"},
        "macos-arm64": {"native": "PASS", "npm": "PASS", "npx": "PASS"}}, "npm and npx consumers gate Linux and macOS while Windows retains native admission"
    assert [call for call in calls if call[1] == "release"] == [], "availability admission performs only public reads"


@pytest.mark.parametrize("missing", ["linux-x64/npm-public.json", "macos-arm64/npm-public.json",
                                   "windows-x64/verification.json"], ids=["linux-npm", "macos-npm", "windows-native"])
def test_public_admission_still_requires_supported_npm_hosts_and_windows_native(tmp_path, monkeypatch, missing):
    # GIVEN complete public evidence with one required runtime report removed
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = complete_public_evidence(tmp_path, folder, manifest, responses["releases/42/assets?per_page=100"])
    report = evidence / missing
    assert report.is_file() is True, "the report exists before the intended evidence omission"
    report.unlink()
    # WHEN public acceptance observes the missing supported runtime proof
    with pytest.raises(ValueError, match="Missing or linked release evidence"):
        RELEASE.admit_public(folder, manifest, evidence)
    # THEN incomplete consumers cannot update a release or fabricate the missing proof
    assert ([call for call in calls if call[1] == "release"], report.exists()) == (
        [], False), "native Windows and both supported npm hosts remain mandatory"


def test_finalize_refuses_arbitrary_public_body_before_edit(tmp_path, monkeypatch):
    # GIVEN complete public consumer evidence and a body unrelated to deterministic guides
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = complete_public_evidence(tmp_path, folder, manifest, responses["releases/42/assets?per_page=100"])
    body = tmp_path / "public-body.md"
    body.write_text("Unverified public claims.\n", encoding="utf-8")
    assert responses["releases/tags/v0.3.0"]["body"] == "Staged release instructions.\n", "the public release still carries bounded staged instructions"
    # WHEN finalization regenerates the public guide using freshly admitted evidence
    with pytest.raises(ValueError, match="deterministic guide"):
        RELEASE.finalize(folder, manifest, evidence, body)
    # THEN no public body write has happened
    assert [call for call in calls if call[1] == "release"] == [], "arbitrary final body cannot publish after runtime PASS"


def test_finalize_publishes_only_exact_regenerated_public_guide(tmp_path, monkeypatch):
    # GIVEN all public consumer reports and a deterministic version-specific guide
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = complete_public_evidence(tmp_path, folder, manifest, responses["releases/42/assets?per_page=100"])
    availability = tmp_path / "public-acceptance.json"
    RELEASE.write_json(availability, RELEASE.admit_public(folder, manifest, evidence))
    guide = RELEASE.helper("release_guide")
    expected = guide.render(guide.admit(RELEASE.CHECKOUT, folder / "binaries.json", folder / "npm-manifest.json", "public", availability))
    body = tmp_path / "public-body.md"
    body.write_text(expected, encoding="utf-8")
    assert responses["releases/tags/v0.3.0"]["body"] == "Staged release instructions.\n", "the existing public body awaits finalization"
    # WHEN finalization independently repeats the gate and compares the entire body
    RELEASE.finalize(folder, manifest, evidence, body)
    # THEN only the exact notes file is published and read back
    assert [call for call in calls if call[1] == "release"] == [
        ["gh", "release", "edit", "v0.3.0", "--notes-file", str(body), "--repo", "kochetkov-ma/brewdoc"]
    ], "the final public edit cannot change assets or tag identity"
    assert responses["releases/tags/v0.3.0"]["body"] == expected, "published instructions match every regenerated byte"
