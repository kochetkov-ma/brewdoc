"""Exercise original-release admission without network or publisher access."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import urllib.error
import zipfile

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".github/workflows/recover_v020.py"
SPEC = importlib.util.spec_from_file_location("recover_v020", HELPER)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
SHA = "a" * 40


def publish_inputs():
    """Declare one fixed operation with two explicit package approvals."""
    return {"operation": "publish-v020", "prepared_run_id": "123",
            "approved_wheel_sha256": "b" * 64, "approved_sdist_sha256": "c" * 64}


def prepared_run():
    """Provide successful same-repository preparation provenance."""
    return {"id": 123, "repository": {"full_name": recovery.REPOSITORY},
            "head_repository": {"full_name": recovery.REPOSITORY},
            "path": ".github/workflows/release.yml", "event": "workflow_dispatch",
            "head_branch": "main", "head_sha": SHA, "status": "completed",
            "conclusion": "success", "run_attempt": 1}


@pytest.mark.parametrize("key,value,reason", [
    ("operation", "publish-anything", "Publication requires a positive decimal preparation run"),
    ("prepared_run_id", "0", "Publication requires a positive decimal preparation run"),
    ("prepared_run_id", "123; echo unsafe", "Publication requires a positive decimal preparation run"),
    ("approved_wheel_sha256", "B" * 64, "Publication requires two lowercase SHA-256 approvals"),
    ("approved_sdist_sha256", "c" * 63, "Publication requires two lowercase SHA-256 approvals"),
])
def test_invalid_dispatch_inputs_refuse_before_acquisition(key, value, reason):
    # GIVEN one malformed field in an otherwise admitted fixed dispatch
    inputs = publish_inputs()
    assert recovery.validate_inputs(inputs) == ("publish-v020", 123, ("b" * 64, "c" * 64)), (
        "the unchanged dispatch must be admissible")
    inputs[key] = value
    # WHEN the dispatch is validated
    # THEN malformed operations, run IDs and approvals cannot reach acquisition
    with pytest.raises(ValueError, match=f"^{reason}$"):
        recovery.validate_inputs(inputs)


def test_preparation_refuses_publication_inputs():
    # GIVEN approved publication values attached to the preparation operation
    inputs = publish_inputs()
    inputs["operation"] = "prepare-v020"
    # WHEN preparation inputs are validated
    # THEN preparation cannot consume publication approvals or a supplied run
    with pytest.raises(ValueError, match="^Preparation inputs must leave run and approved hashes empty$"):
        recovery.validate_inputs(inputs)


def test_dispatch_refuses_an_arbitrary_source_input():
    # GIVEN a publication with an undeclared source override
    inputs = {**publish_inputs(), "source_sha": "d" * 40}
    # WHEN its complete input shape is checked
    # THEN recovery cannot be generalized to arbitrary source identities
    with pytest.raises(ValueError, match="^Recovery inputs must contain exactly the four declared strings$"):
        recovery.validate_inputs(inputs)


@pytest.mark.parametrize("key,value", [
    ("repository", {"full_name": "foreign/project"}),
    ("head_repository", {"full_name": "foreign/project"}),
    ("head_sha", "d" * 40), ("head_branch", "feature"), ("event", "push"),
    ("path", ".github/workflows/ci.yml"), ("conclusion", "failure"),
])
def test_preparation_provenance_refuses_foreign_or_stale_runs(key, value):
    # GIVEN an admitted preparation run with one changed provenance field
    run = prepared_run()
    jobs = {"jobs": [{"name": "recovery-prepare", "conclusion": "success"}]}
    assert recovery.validate_run(run, jobs, SHA, 123) is None, "the baseline preparation must pass"
    run[key] = value
    # WHEN publication admission validates the run
    # THEN no foreign, stale or unsuccessful preparation can be published
    with pytest.raises(ValueError, match="^Preparation run is foreign, stale or unsuccessful$"):
        recovery.validate_run(run, jobs, SHA, 123)


def test_successful_run_without_preparation_job_is_refused():
    # GIVEN a successful workflow containing no successful preparation job
    run = prepared_run()
    # WHEN its jobs are validated
    # THEN event identity alone cannot substitute for preparation success
    with pytest.raises(ValueError, match="^Preparation job did not succeed$"):
        recovery.validate_run(run, {"jobs": [{"name": "recovery-admit", "conclusion": "success"}]}, SHA, 123)


def test_moved_original_tag_is_refused():
    # GIVEN the original tag name pointing to a later commit
    reference = {"ref": "refs/tags/v0.2.0", "object": {"sha": "d" * 40, "type": "commit"}}
    # WHEN the recovery source is checked
    # THEN the tag cannot silently substitute a later source identity
    with pytest.raises(ValueError, match="^Original v0.2.0 tag changed$"):
        recovery.validate_tag(reference)


def archive_bytes(names, linked=False):
    """Construct a compact acquisition archive without extracting files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            member = zipfile.ZipInfo(name)
            member.external_attr = {False: stat.S_IFREG, True: stat.S_IFLNK}[linked] << 16
            archive.writestr(member, b"package")
    return buffer.getvalue()


@pytest.mark.parametrize("names,linked,reason", [
    (("../outside.whl", "brewdoc-0.2.0.tar.gz"), False,
     "Recovery artifact contains unexpected or duplicate files"),
    (("brewdoc-0.2.0-py3-none-any.whl", "brewdoc-0.2.0-py3-none-any.whl"), False,
     "Recovery artifact contains unexpected or duplicate files"),
    (recovery.FILENAMES, True, "Recovery artifact contains unsafe members"),
])
def test_artifact_archive_refuses_escaping_duplicate_or_linked_members(tmp_path, names, linked, reason):
    # GIVEN a digest-correct archive with unsafe members
    payload = archive_bytes(names, linked)
    target = tmp_path / "bundle"
    assert target.exists() is False, "the target must be absent before acquisition"
    # WHEN only the two fixed package filenames are admitted
    # THEN unsafe content is refused before any bundle is written
    with pytest.raises(ValueError, match=f"^{reason}$"):
        recovery.unpack_artifact(payload, hashlib.sha256(payload).hexdigest(), recovery.FILENAMES, target)
    assert target.exists() is False, "archive refusal must not create a partial bundle"


def test_artifact_digest_mismatch_refuses_before_archive_read(tmp_path):
    # GIVEN valid ZIP bytes whose approved archive digest differs
    payload = archive_bytes(recovery.FILENAMES)
    # WHEN the archive is admitted
    # THEN changed bytes cannot be inspected or staged as accepted packages
    with pytest.raises(ValueError, match="^Recovery artifact archive digest changed$"):
        recovery.unpack_artifact(payload, "0" * 64, recovery.FILENAMES, tmp_path / "bundle")


def bundle_manifest(tmp_path):
    """Provide approved package bytes and matching original-source verification evidence."""
    for name in recovery.FILENAMES:
        (tmp_path / name).write_bytes(name.encode())
    files = recovery.file_identities(tmp_path)
    manifest = {"schema": 1, "operation": "prepare-v020", "repository": recovery.REPOSITORY,
                "tag": recovery.TAG, "source_sha": recovery.SOURCE_SHA, "version": "0.2.0",
                "control_sha": SHA, "prepared_run_id": 123, "prepared_run_attempt": 1,
                "method": "retained", "files": files,
                "verification": [{"filename": name, "version": "0.2.0", "origin_verified": True,
                                  "contracts_verified": True} for name in recovery.FILENAMES]}
    return manifest, tuple(files[name]["sha256"] for name in recovery.FILENAMES)


@pytest.mark.parametrize("change,reason", [
    ({"operation": "publish-v020"}, "Recovery manifest identity differs from the preparation run"),
    ({"prepared_run_attempt": 2}, "Recovery manifest identity differs from the preparation run"),
    ({"verification": []}, "Original package installation verification is incomplete"),
])
def test_manifest_refuses_wrong_operation_attempt_or_missing_installation(tmp_path, change, reason):
    # GIVEN accepted bytes and a manifest with one changed admission fact
    manifest, approvals = bundle_manifest(tmp_path)
    assert recovery.validate_manifest(manifest, tmp_path, prepared_run(), approvals) is None, (
        "the unchanged package manifest must be admissible")
    manifest.update(change)
    # WHEN publication binds provenance and installation evidence
    # THEN altered preparation evidence is refused
    with pytest.raises(ValueError, match=f"^{reason}$"):
        recovery.validate_manifest(manifest, tmp_path, prepared_run(), approvals)


def test_manifest_refuses_altered_packages_even_with_unchanged_approval(tmp_path):
    # GIVEN accepted packages followed by a same-name byte replacement
    manifest, approvals = bundle_manifest(tmp_path)
    (tmp_path / recovery.FILENAMES[0]).write_bytes(b"altered")
    # WHEN manifest and approval hashes are enforced
    # THEN filenames cannot substitute for exact accepted bytes
    with pytest.raises(ValueError, match="^Recovery package bytes differ from manifest or approved hashes$"):
        recovery.validate_manifest(manifest, tmp_path, prepared_run(), approvals)


@pytest.mark.parametrize("existing,missing", [
    ((), list(recovery.FILENAMES)),
    ((recovery.FILENAMES[0],), [recovery.FILENAMES[1]]),
    (recovery.FILENAMES, []),
])
def test_partial_publication_stages_only_missing_accepted_files(tmp_path, existing, missing):
    # GIVEN zero, one or both byte-identical packages already public
    manifest, _ = bundle_manifest(tmp_path)
    entries = [{"filename": name, "size": manifest["files"][name]["size"], "url": name} for name in existing]
    # WHEN public bytes are checked against accepted identities
    actual = recovery.missing_public_files(manifest["files"], entries,
                                          lambda name: (tmp_path / name).read_bytes())
    # THEN recovery requests exactly the missing distribution filenames
    assert actual == missing, "matching existing files must never be uploaded again"


def test_partial_publication_refuses_an_existing_same_name_with_different_bytes(tmp_path):
    # GIVEN one expected filename whose public bytes were replaced
    manifest, _ = bundle_manifest(tmp_path)
    name = recovery.FILENAMES[0]
    entries = [{"filename": name, "size": 7, "url": name}]
    # WHEN public identity is checked
    # THEN blanket skip-existing cannot mask the different distribution
    with pytest.raises(ValueError, match="^Public release bytes differ from approved packages$"):
        recovery.missing_public_files(manifest["files"], entries, lambda _: b"changed")


def test_public_preflight_failure_cannot_be_treated_as_absent_release(tmp_path, monkeypatch):
    # GIVEN an unavailable registry metadata preflight
    bundle_manifest(tmp_path)
    def unavailable(_):
        """Model a failed public preflight without network access."""
        raise urllib.error.HTTPError("https://pypi.org/", 503, "unavailable", {}, None)
    monkeypatch.setattr(recovery, "public_bytes", unavailable)
    # WHEN public state is inspected
    # THEN network or server failure cannot authorize upload
    with pytest.raises(ValueError, match="^PyPI preflight is unavailable$"):
        recovery.pypi_missing(tmp_path)


def test_retained_artifact_mismatch_never_falls_back_to_a_rebuild(tmp_path, monkeypatch):
    # GIVEN an available original artifact reporting a different archive digest
    artifact = {"name": "dist", "expired": False, "expires_at": "2099-01-01T00:00:00Z",
                "workflow_run": {"id": 36746089723, "head_sha": recovery.SOURCE_SHA},
                "digest": "sha256:" + "d" * 64}
    monkeypatch.setattr(recovery, "api", lambda *args, **kwargs: artifact)
    commands = []
    monkeypatch.setattr(recovery, "command", lambda *args, **kwargs: commands.append(args))
    # WHEN preparation tries to reuse the retained artifact
    # THEN an identity mismatch refuses without issuing download, build or install commands
    with pytest.raises(ValueError, match="^Original artifact digest changed$"):
        recovery.prepare(tmp_path / "source", tmp_path / "bundle")
    assert commands == [], "a retained artifact mismatch must not trigger a tag-source rebuild"


@pytest.mark.parametrize("change", [
    {"expired": True}, {"workflow_run": {"id": 999, "head_sha": SHA}},
    {"workflow_run": {"id": 123, "head_sha": "d" * 40}},
    {"expires_at": "2000-01-01T00:00:00Z"}, {"digest": None},
])
def test_prepared_artifact_refuses_expiry_or_changed_provenance(change):
    # GIVEN a valid prepared artifact whose provenance or retention changed
    artifact = {"name": recovery.ARTIFACT_NAME, "expired": False,
                "expires_at": "2099-01-01T00:00:00Z", "workflow_run": {"id": 123, "head_sha": SHA},
                "digest": "sha256:" + "b" * 64}
    assert recovery.validate_artifact(artifact, 123, SHA, recovery.ARTIFACT_NAME) == "b" * 64, (
        "the unchanged artifact must be admissible")
    artifact.update(change)
    # WHEN publication admission checks its current metadata
    # THEN stale or unidentified bytes cannot enter the publisher
    with pytest.raises(ValueError, match="^Recovery artifact provenance, expiry or digest is invalid$"):
        recovery.validate_artifact(artifact, 123, SHA, recovery.ARTIFACT_NAME)


def test_public_preflight_stages_exactly_one_missing_distribution(tmp_path, monkeypatch):
    # GIVEN approved packages with only the wheel missing from PyPI
    folder = tmp_path / "bundle"
    folder.mkdir()
    bundle_manifest(folder)
    output = tmp_path / "job-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(recovery, "pypi_missing", lambda _: [recovery.FILENAMES[0]])
    # WHEN the publisher's input directory is staged
    recovery.stage_upload(folder)
    upload = tmp_path / "recovery-upload"
    # THEN the existing sdist is excluded and the approved wheel bytes remain exact
    assert ({path.name: path.read_bytes() for path in upload.iterdir()}, output.read_text()) == (
        {recovery.FILENAMES[0]: recovery.FILENAMES[0].encode()}, "upload_needed=true\n"), (
        "partial publication must stage only the missing accepted wheel")


def test_github_asset_mismatch_refuses_before_any_write(tmp_path, monkeypatch):
    # GIVEN a complete PyPI release and a same-name GitHub asset with different bytes
    bundle_manifest(tmp_path)
    name = recovery.FILENAMES[0]
    release = {"tag_name": recovery.TAG, "draft": False, "prerelease": False,
               "assets": [{"name": name, "size": 7,
                           "browser_download_url": f"https://github.com/{recovery.REPOSITORY}/releases/download/{recovery.TAG}/{name}"}]}
    monkeypatch.setattr(recovery, "pypi_missing", lambda _: [])
    monkeypatch.setattr(recovery, "api", lambda *args, **kwargs: release)
    monkeypatch.setattr(recovery, "public_bytes", lambda _: b"changed")
    writes = []
    monkeypatch.setattr(recovery, "command", lambda *args, **kwargs: writes.append(args))
    # WHEN original GitHub assets are reconciled
    # THEN an existing mismatch prevents release creation or any upload
    with pytest.raises(ValueError, match="^Public release bytes differ from approved packages$"):
        recovery.complete_github(tmp_path)
    assert writes == [], "mismatching GitHub assets must never be replaced or supplemented"


@pytest.mark.parametrize("variable,value", [
    ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/feature"),
    ("GITHUB_REPOSITORY", "foreign/project"),
    ("GITHUB_WORKFLOW_REF", f"{recovery.REPOSITORY}/.github/workflows/ci.yml@refs/heads/main"),
])
def test_recovery_context_refuses_foreign_event_ref_repository_or_workflow(tmp_path, monkeypatch, variable, value):
    # GIVEN one altered runtime identity in an otherwise fixed main dispatch
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"repository": {"full_name": recovery.REPOSITORY},
                                "inputs": publish_inputs()}))
    environment = {"GITHUB_EVENT_PATH": str(event), "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": recovery.REPOSITORY,
                   "GITHUB_WORKFLOW_REF": f"{recovery.REPOSITORY}/.github/workflows/release.yml@refs/heads/main",
                   "GITHUB_SHA": SHA, "GITHUB_WORKSPACE": str(tmp_path)}
    monkeypatch.setattr(recovery.os, "environ", {**environment, variable: value})
    # WHEN fixed main execution is checked
    # THEN unsafe dispatch identity is rejected before repository or public requests
    with pytest.raises(ValueError, match="^Recovery must run from release.yml on repository main$"):
        recovery.context()


@pytest.mark.parametrize("response", [
    {"info": {"name": "brewdoc", "version": "0.2.0"}},
    {"info": {"name": "brewdoc", "version": "0.2.0"}, "urls": None},
    {"info": {"name": "brewdoc", "version": "0.2.0"}, "urls": {}},
    {"info": {"name": "brewdoc", "version": "0.2.0"}, "urls": [None]},
    {"info": {"name": "brewdoc", "version": "0.2.0"}, "urls": [{}]},
])
def test_malformed_successful_pypi_preflight_cannot_stage_publication(tmp_path, monkeypatch, response):
    # GIVEN HTTP-200 metadata with unknown or incomplete public-file state
    folder = tmp_path / "bundle"
    folder.mkdir()
    bundle_manifest(folder)
    output = tmp_path / "job-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(recovery, "public_bytes", lambda _: json.dumps(response).encode())
    assert ((tmp_path / "recovery-upload").exists(), output.exists()) == (False, False), (
        "unknown public state must start without publisher inputs")
    # WHEN publication attempts its public-file preflight
    # THEN malformed successful responses refuse before staging or authorizing upload
    with pytest.raises(ValueError, match="^PyPI public-file metadata is malformed$"):
        recovery.stage_upload(folder)
    assert ((tmp_path / "recovery-upload").exists(), output.exists()) == (False, False), (
        "malformed metadata must leave publisher staging and authorization absent")


@pytest.mark.parametrize("change", [
    {"size": True}, {"size": -1}, {"url": None},
    {"url": "https://pypi.org/not-a-distribution"},
    {"url": "https://reader:password@files.pythonhosted.org/package.whl"},
    {"digests": None}, {"digests": {}}, {"digests": {"sha256": "A" * 64}},
])
def test_incomplete_pypi_file_entries_refuse_before_any_download(tmp_path, monkeypatch, change):
    # GIVEN one malformed file entry within an otherwise identified release
    bundle_manifest(tmp_path)
    name = recovery.FILENAMES[0]
    entry = {"filename": name, "size": len(name), "url": "https://files.pythonhosted.org/package.whl",
             "digests": {"sha256": hashlib.sha256(name.encode()).hexdigest()}}
    entry.update(change)
    response = {"info": {"name": "brewdoc", "version": "0.2.0"}, "urls": [entry]}
    requests = []
    def metadata(url):
        """Record acquisition without providing package bytes."""
        requests.append(url)
        return json.dumps(response).encode()
    monkeypatch.setattr(recovery, "public_bytes", metadata)
    # WHEN public-file metadata is validated
    # THEN malformed entries stop before a distribution download
    with pytest.raises(ValueError, match="^PyPI public-file metadata is malformed$"):
        recovery.pypi_missing(tmp_path)
    assert requests == ["https://pypi.org/pypi/brewdoc/0.2.0/json"], (
        "malformed file metadata must not initiate a package request")


def test_command_failure_retains_probe_reason_and_redacts_credentials(monkeypatch):
    # GIVEN a failed acceptance probe with credential-like diagnostic text
    stderr = (b"AssertionError: original public exports differ\nAuthorization: Bearer ghp_abc\n"
              b"https://reader:password@pypi.org/simple\nGITHUB_TOKEN=github_pat_sensitive\n")
    process = subprocess.CompletedProcess([], 1, b"", stderr)
    monkeypatch.setattr(recovery.subprocess, "run", lambda *args, **kwargs: process)
    # WHEN the command failure is reported
    with pytest.raises(ValueError) as failure:
        recovery.command(["/private/bin/python", "--token", "argument-secret"])
    # THEN the bounded reason is useful without exposing arguments, credentials or environment values
    assert str(failure.value) == (
        "Recovery command failed: python (exit 1)\nAssertionError: original public exports differ\n"
        "Authorization=[redacted]\nhttps://[redacted]@pypi.org/simple\nGITHUB_TOKEN=[redacted]"
    ), "failure reporting must preserve assertion reasons and redact credential forms"
