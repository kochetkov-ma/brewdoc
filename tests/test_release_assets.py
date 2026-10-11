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
    assert len(rows) == 6, "one package, native, npm and three consumer artifacts are required"
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
    (["RELEASING.md"], True), (["FORMATS.md"], True), (["unknown.md"], True), ([], True),
    (["docs/architecture.md", "src/brewdoc/cli.py"], True), (["docs/../README.md"], True)])
def test_ci_path_gate_skips_only_proven_nonpackage_documentation(paths, expected):
    # GIVEN a complete list of changed paths
    snapshot = list(paths)
    assert paths == snapshot, "path selection starts without normalization or deletion"
    # WHEN the always-running gate selects package smoke
    actual = RELEASE.needs_smoke(paths)
    # THEN package inputs and unknown or incomplete changes retain all checks
    assert actual is expected, "only ordinary docs paths can skip the artifact graph"


def npm_archives(output, targets):
    """Produce four small real tarballs through the npm owner's exact metadata contract."""
    builder = RELEASE.helper("build_npm")
    output.mkdir()
    records = []
    for target in [*RELEASE.TARGETS, None]:
        metadata = builder.package_metadata(VERSION, target)
        payloads = {"package.json": json.dumps(metadata).encode()}
        record = {"version": VERSION, "source_sha": SOURCE, "metadata": metadata}
        if target is not None:
            native = targets[target]
            verified = {"verdict": "PASS", "target": target, "version": VERSION, "source_sha": SOURCE,
                        "archive_sha256": native["archive"]["sha256"]}
            scope = native.get("verification_scope", "full")
            verified.update(verification_scope=scope, schema=1, self_check=38, fixtures=[],
                            probes=[row["probe"] for row in native["probes"]])
            payloads.update({"native-manifest.json": json.dumps(native).encode(),
                             "native-verification.json": json.dumps(verified).encode(),
                             "binary/" + native["executable"].removeprefix("brewdoc/"): b"synthetic native executable"})
            record.update({"target": target, "native_archive": native["archive"], "links": {}})
        filename = metadata["name"] + "-" + VERSION + ".tgz"
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
    RELEASE.write_json(output / "npm-manifest.json", {"schema": 1, "version": VERSION, "source_sha": SOURCE,
                       "node": builder.NODE_VERSION, "npm": builder.NPM_VERSION, "packages": records,
                       "verification_scope": next(iter(targets.values())).get("verification_scope", "full")})


def sealed_release(tmp_path, monkeypatch):
    """Prepare real synthetic archive identities through the existing admission helpers."""
    checkout, bundle, native = (tmp_path / name for name in ("checkout", "bundle", "native"))
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n', encoding="utf-8")
    (checkout / "uv.lock").write_bytes(b"locked")
    (checkout / "src/brewdoc").mkdir(parents=True)
    (checkout / "src/brewdoc/__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
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
    wheel = bundle / "dist/brewdoc-0.3.0-py3-none-any.whl"
    sdist = bundle / "dist/brewdoc-0.3.0.tar.gz"
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
        directory = tmp_path / f"ci-npm-consumer-{target}"
        directory.mkdir()
        consumer_folders[directory.name] = directory
    RELEASE.write_json(native / "binaries.json", binaries)
    shutil.copyfile(tmp_path / "native/SHA256SUMS", native / "SHA256SUMS")
    npm_archives(npm, binaries["targets"])
    npm_manifest = RELEASE.read_json(npm / "npm-manifest.json")
    packages = {row["metadata"]["name"]: row["integrity"] for row in npm_manifest["packages"]}
    epub = next(row for row in probes if row["probe"] == "epub2")
    for target, record in binaries["targets"].items():
        package = next(row["metadata"]["name"] for row in npm_manifest["packages"] if row.get("target") == target)
        report = {"schema": 1, "verdict": "PASS", "verification_scope": "smoke", "mode": "local",
                  "target": target, "version": VERSION, "source_sha": SOURCE,
                  "node": npm_manifest["node"], "npm": npm_manifest["npm"], "packages": packages,
                  "consumers": {name: {"self_check": 38, "selected_package": package, "fixtures": [],
                      "probes": {"global": [{"probe": "epub2", "path": epub["path"],
                                              "markdown_sha256": epub["markdown_sha256"]}], "npx": []}[name]}
                      for name in ("global", "npx")},
                  "cancellation": {"native_observed": True, "native_terminated": True, "dns_observed": False,
                      "owned_processes": 2, "javascript": {True: "unsupported", False: "observed-and-terminated"}[target == "windows-x64"],
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
    monkeypatch.setattr(RELEASE, "authenticated_download", lambda endpoint, destination, size:
                        shutil.copyfile(transports[int(endpoint.split("/")[2])], destination))
    return checkout, rows, transports, responses


def zip_directory(directory, destination):
    """Create a real artifact transport from ordinary fixture files."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(path for path in directory.rglob("*") if path.is_file()):
            archive.write(item, item.relative_to(directory).as_posix())


def test_ci_reuse_preserves_exact_synthetic_transport_bytes_and_original_attempt(tmp_path, monkeypatch):
    # GIVEN complete real ZIP artifacts from a retained successful main attempt
    checkout, rows, _transports, _responses = ci_smoke_artifacts(tmp_path, monkeypatch)
    assert len(rows) == 6, "the producer has the complete six-artifact admission set"
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
                    for name, row in manifest["assets"].items() if name.endswith(".whl") or name == f"brewdoc-{VERSION}.tar.gz"]}

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
        package = next(row["metadata"]["name"] for row in npm["packages"] if row.get("target") == target)
        consumer = {"self_check": 38, "selected_package": package, "atomic_refusal": True, "help_sha256": "e" * 64,
                    "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]]}
        RELEASE.write_json(directory / "npm-public.json", {"schema": 1, "verdict": "PASS", "mode": "public", "target": target,
            "version": VERSION, "source_sha": SOURCE, "node": npm["node"], "npm": npm["npm"],
            "packages": {row["metadata"]["name"]: row["integrity"] for row in npm["packages"]},
            "consumers": {name: copy.deepcopy(consumer) for name in ("global", "local", "npx")},
            "signatures": {"verified_packages": 2, "verified_attestations": 2, "stdout_sha256": "f" * 64},
            "cancellation": {"native_observed": True, "native_terminated": True,
                "javascript": {"windows-x64": "unsupported", "linux-x64": "observed-and-terminated",
                               "macos-arm64": "observed-and-terminated"}[target], "dns_observed": False, "owned_processes": 2}})
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
        "brewdoc-win32-x64-0.3.0.tgz", "npm-manifest.json", "release-manifest.json", "staged-body.md"
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
    assert len(assets) == 11, "the draft already holds all eleven payload assets"
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
    # THEN only draft visibility changes and all thirteen public assets are downloaded
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
    names = {"linux-x64": "brewdoc-linux-x64-glibc", "windows-x64": "brewdoc-win32-x64", "macos-arm64": "brewdoc-darwin-arm64"}
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
    with pytest.raises(ValueError, match="requires all four npm packages"):
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
    with pytest.raises(ValueError, match="requires all four npm packages"):
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


def test_admit_public_binds_all_three_native_and_npm_consumers(tmp_path, monkeypatch):
    # GIVEN exact public bytes and complete reports from all three native hosts
    folder, manifest = sealed_release(tmp_path, monkeypatch)
    responses, calls = fake_api(monkeypatch, folder, manifest, draft=False)
    public_io(monkeypatch, folder)
    public_registry(monkeypatch, folder, manifest)
    evidence = complete_public_evidence(tmp_path, folder, manifest, responses["releases/42/assets?per_page=100"])
    assert sorted(path.name for path in evidence.iterdir()) == ["linux-x64", "macos-arm64", "windows-x64"], "every native host has concrete reports"
    # WHEN the helper independently admits actual public channel bytes and runtime identities
    accepted = RELEASE.admit_public(folder, manifest, evidence)
    # THEN availability is produced only from complete matching evidence without public writes
    assert accepted["consumers"] == {"linux-x64": {"native": "PASS", "npm": "PASS", "npx": "PASS"},
        "windows-x64": {"native": "PASS", "npm": "PASS", "npx": "PASS"},
        "macos-arm64": {"native": "PASS", "npm": "PASS", "npx": "PASS"}}, "normal npm and npx consumers gate every advertised platform"
    assert [call for call in calls if call[1] == "release"] == [], "availability admission performs only public reads"


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
    assert "This release is staged." not in expected, "public guidance follows completed exact channel and consumer admission"
    # WHEN finalization independently repeats the gate and compares the entire body
    RELEASE.finalize(folder, manifest, evidence, body)
    # THEN only the exact notes file is published and read back
    assert [call for call in calls if call[1] == "release"] == [
        ["gh", "release", "edit", "v0.3.0", "--notes-file", str(body), "--repo", "kochetkov-ma/brewdoc"]
    ], "the final public edit cannot change assets or tag identity"
    assert responses["releases/tags/v0.3.0"]["body"] == expected, "published instructions match every regenerated byte"
