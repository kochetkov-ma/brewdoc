"""Prepare and admit only immutable v0.2.0 release recovery."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "kochetkov-ma/brewdoc"
SOURCE_SHA = "dc1f0c7fdedeec024840127ed989e1f58416762a"
TAG = "v0.2.0"
FILENAMES = ("brewdoc-0.2.0-py3-none-any.whl", "brewdoc-0.2.0.tar.gz")
ORIGINAL_HASHES = (
    "234a5c83497ac9f70e1530c0e36920657df93220539d3935fa632e3c7fe189de",
    "7085c35212568b92e4a2cea465f723ee3722abb70f4885e12c6f0d8231d9dcb2",
)
ORIGINAL_ARTIFACT = 11112736558
ORIGINAL_DIGEST = "2e83b3ae22ffb59e7ecdd73eacc10b6770965f98f091a6a84249ccc8caba2bea"
ARTIFACT_NAME = "recovery-v020"
MANIFEST_NAME = "recovery-manifest.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_DOWNLOAD = 64 * 1024 * 1024


def command(argv: list[str], cwd: Path | None = None) -> bytes:
    """Run a bounded command without inheriting source-import overrides."""
    environment = {key: value for key, value in os.environ.items() if key not in {
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON"}}
    completed = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, timeout=600)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace")
        detail = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", detail)
        detail = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [redacted]", detail)
        detail = re.sub(r"(?i)\b((?:[a-z_]+_)?(?:token|password|secret|api_key)|authorization)\s*[:=]\s*[^\r\n]+",
                        r"\1=[redacted]", detail)
        detail = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b",
                        "[redacted]", detail)
        raise ValueError(f"Recovery command failed: {Path(argv[0]).name} (exit {completed.returncode})\n"
                         + detail[-2048:].strip())
    return completed.stdout


def api(endpoint: str, *, missing: bool = False) -> dict | list | None:
    """Read repository metadata, allowing only an explicit HTTP 404 absence."""
    completed = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/{endpoint}"],
                               capture_output=True, timeout=60)
    payload = json.loads(completed.stdout)
    if completed.returncode:
        if missing and isinstance(payload, dict) and str(payload.get("status")) == "404":
            return None
        raise ValueError(f"Repository preflight failed: {endpoint}")
    return payload


def validate_inputs(inputs: dict) -> tuple[str, int | None, tuple[str, str] | None]:
    """Require the four fixed dispatch inputs before any acquisition or publication."""
    expected = {"operation", "prepared_run_id", "approved_wheel_sha256", "approved_sdist_sha256"}
    if set(inputs) != expected or any(not isinstance(value, str) for value in inputs.values()):
        raise ValueError("Recovery inputs must contain exactly the four declared strings")
    operation = inputs["operation"]
    values = tuple(inputs[key] for key in (
        "prepared_run_id", "approved_wheel_sha256", "approved_sdist_sha256"))
    if operation == "prepare-v020":
        if values != ("", "", ""):
            raise ValueError("Preparation inputs must leave run and approved hashes empty")
        return operation, None, None
    if operation != "publish-v020" or not re.fullmatch(r"[1-9][0-9]*", values[0]):
        raise ValueError("Publication requires a positive decimal preparation run")
    if not all(HEX.fullmatch(value) for value in values[1:]):
        raise ValueError("Publication requires two lowercase SHA-256 approvals")
    return operation, int(values[0]), values[1:]


def validate_tag(reference: dict) -> None:
    """Refuse missing, moved or annotated substitutions for the original tag."""
    if reference.get("ref") != f"refs/tags/{TAG}" or reference.get("object") != {
            "sha": SOURCE_SHA, "type": "commit", "url": f"https://api.github.com/repos/{REPOSITORY}/git/commits/{SOURCE_SHA}"}:
        raise ValueError("Original v0.2.0 tag changed")


def context() -> tuple[dict, Path, Path]:
    """Admit dispatches only from the current reviewed main workflow commit."""
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    if (os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or event.get("repository", {}).get("full_name") != REPOSITORY
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/.github/workflows/release.yml@refs/heads/main"):
        raise ValueError("Recovery must run from release.yml on repository main")
    validate_inputs(event.get("inputs", {}))
    if api("git/ref/heads/main")["object"]["sha"] != os.environ["GITHUB_SHA"]:
        raise ValueError("Recovery workflow commit is no longer current main")
    validate_tag(api(f"git/ref/tags/{TAG}"))
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    return event, workspace / "package-source", workspace / ARTIFACT_NAME


def validate_run(run: dict, jobs: dict, current_sha: str, run_id: int) -> None:
    """Reject foreign, stale, failed or non-preparation workflow provenance."""
    if (run.get("id") != run_id or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("path") != ".github/workflows/release.yml"
            or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
            or run.get("head_sha") != current_sha or run.get("status") != "completed"
            or run.get("conclusion") != "success"):
        raise ValueError("Preparation run is foreign, stale or unsuccessful")
    prepared = [job for job in jobs.get("jobs", []) if job.get("name") == "recovery-prepare"]
    if len(prepared) != 1 or prepared[0].get("conclusion") != "success":
        raise ValueError("Preparation job did not succeed")


def validate_artifact(artifact: dict, run_id: int, sha: str, name: str) -> str:
    """Require an unexpired artifact with matching run, source and archive digest."""
    provenance = artifact.get("workflow_run", {})
    digest = artifact.get("digest") or ""
    if (artifact.get("name") != name or artifact.get("expired") is not False
            or provenance.get("id") != run_id or provenance.get("head_sha") != sha
            or not digest.startswith("sha256:") or not HEX.fullmatch(digest[7:])
            or dt.datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc)):
        raise ValueError("Recovery artifact provenance, expiry or digest is invalid")
    return digest[7:]


def unpack_artifact(payload: bytes, expected_digest: str, names: tuple[str, ...], folder: Path) -> None:
    """Verify the archive before writing only its exact ordinary top-level files."""
    if len(payload) > MAX_DOWNLOAD or hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ValueError("Recovery artifact archive digest changed")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if sorted(item.filename for item in members) != sorted(names):
            raise ValueError("Recovery artifact contains unexpected or duplicate files")
        if any(item.is_dir() or item.flag_bits & 1
               or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
               or item.file_size > MAX_DOWNLOAD for item in members):
            raise ValueError("Recovery artifact contains unsafe members")
        if sum(item.file_size for item in members) > MAX_DOWNLOAD:
            raise ValueError("Recovery artifact expanded bytes exceed the limit")
        contents = {item.filename: archive.read(item) for item in members}
    folder.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        (folder / name).write_bytes(content)


def file_identities(folder: Path) -> dict:
    """Identify the exact two ordinary package files without accepting path substitutions."""
    identities = {}
    for name in FILENAMES:
        path = folder / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Recovery package is missing or linked")
        payload = path.read_bytes()
        if len(payload) > MAX_DOWNLOAD:
            raise ValueError("Recovery package exceeds the size limit")
        identities[name] = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    return identities


def check_names(names: list[str]) -> None:
    """Reject duplicate and escaping package archive member paths."""
    if len(names) != len(set(names)) or any(
            PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or "\\" in name for name in names):
        raise ValueError("Recovery package contains unsafe or duplicate names")


def verify_source(source: Path) -> None:
    """Require an unmodified original checkout before its build or package comparison."""
    if command(["git", "rev-parse", "HEAD"], source).decode().strip() != SOURCE_SHA:
        raise ValueError("Recovery package checkout is not the original source")
    command(["git", "diff", "--exit-code", SOURCE_SHA], source)
    if command(["git", "ls-files", "--others", "--exclude-standard"], source):
        raise ValueError("Recovery package checkout contains untracked files")


def verify_packages(folder: Path, source: Path) -> None:
    """Require original source, metadata, safe archives and the original console entry."""
    verify_source(source)
    tracked = command(["git", "ls-tree", "-r", "--name-only", SOURCE_SHA], source).decode().splitlines()
    original = {name.removeprefix("src/"): (source / name).read_bytes()
                for name in tracked if name.startswith("src/brewdoc/")}
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != "0.2.0" or len(original) != 11:
        raise ValueError("Original package metadata changed")
    prefix = "brewdoc-0.2.0.dist-info/"
    allowed = set(original) | {prefix + name for name in (
        "METADATA", "WHEEL", "entry_points.txt", "licenses/LICENSE", "RECORD")}
    with zipfile.ZipFile(folder / FILENAMES[0]) as wheel:
        check_names(wheel.namelist())
        if set(wheel.namelist()) != allowed or any(
                stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
                or item.flag_bits & 1 for item in wheel.infolist()):
            raise ValueError("Original wheel member set changed")
        if sum(item.file_size for item in wheel.infolist()) > MAX_DOWNLOAD:
            raise ValueError("Original wheel expanded bytes exceed the limit")
        if {name: wheel.read(name) for name in original} != original:
            raise ValueError("Wheel source differs from the original tag")
        if wheel.read(prefix + "entry_points.txt") != b"[console_scripts]\nbrewdoc = brewdoc.cli:main\n":
            raise ValueError("Original console entry changed")
        if wheel.read(prefix + "licenses/LICENSE") != (source / "LICENSE").read_bytes():
            raise ValueError("Original wheel license changed")
        metadata = BytesParser().parsebytes(wheel.read(prefix + "METADATA"))
    with tarfile.open(folder / FILENAMES[1]) as sdist:
        members = sdist.getmembers()
        check_names([member.name for member in members])
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Original sdist contains links or special members")
        if sum(member.size for member in members) > MAX_DOWNLOAD:
            raise ValueError("Original sdist expanded bytes exceed the limit")
        seen = set()
        for member in members:
            if not member.isfile():
                continue
            name = member.name.removeprefix("brewdoc-0.2.0/")
            if name == "PKG-INFO":
                continue
            if name not in tracked or sdist.extractfile(member).read() != (source / name).read_bytes():
                raise ValueError("Sdist files differ from the original tag")
            seen.add(name)
        required = {"pyproject.toml", "uv.lock", "LICENSE", "README.md"} | {"src/" + name for name in original}
        if not required.issubset(seen):
            raise ValueError("Original sdist required files are missing")
        source_metadata = BytesParser().parsebytes(sdist.extractfile("brewdoc-0.2.0/PKG-INFO").read())
    for document in (metadata, source_metadata):
        if (document["Name"], document["Version"], document["Requires-Python"],
                document.get_all("Requires-Dist")) != (
                "brewdoc", "0.2.0", project["requires-python"], project["dependencies"]):
            raise ValueError("Original distribution metadata changed")


def installed_checks(folder: Path, source: Path) -> list[dict]:
    """Install wheel and sdist independently with original locked runtime/build constraints."""
    with tempfile.TemporaryDirectory(prefix="brewdoc-v020-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime.txt"
        command(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
                 "--output-file", str(runtime)], source)
        project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        build = root / "build.txt"
        build.write_text("\n".join(project["build-system"]["requires"]
                                   + project["tool"]["uv"]["build-constraint-dependencies"]) + "\n")
        exports = next(ast.literal_eval(node.value) for node in ast.parse(
            (source / "src/brewdoc/__init__.py").read_bytes()).body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
        expected = root / "exports.json"
        expected.write_text(json.dumps(exports))
        script = root / "probe.py"
        script.write_text(INSTALL_PROBE, encoding="utf-8")
        results = []
        for index, name in enumerate(FILENAMES):
            environment = root / f"environment-{index}"
            command(["uv", "venv", "--python", "3.12", str(environment)], root)
            python = environment / "bin/python"
            command(["uv", "pip", "install", "--python", str(python), "--require-hashes",
                     "-r", str(runtime)], root)
            command(["uv", "pip", "install", "--python", str(python), "--no-deps",
                     "--build-constraints", str(build), str((folder / name).resolve())], root)
            result = json.loads(command([str(python), "-I", str(script), str(expected)], root))
            results.append({"filename": name, **result})
        return results


INSTALL_PROBE = '''import contextlib, importlib.metadata, io, json, pathlib, subprocess, sys
import brewdoc
expected = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert brewdoc.__version__ == importlib.metadata.version("brewdoc") == "0.2.0", "original package version differs"
assert brewdoc.__all__ == expected, "original public exports differ"
assert pathlib.Path(brewdoc.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()), "original package imports outside its installed environment"
capture = io.StringIO()
with contextlib.redirect_stdout(capture):
    verdict = brewdoc.self_check()
assert verdict == 0 and capture.getvalue() == "self-check: ok (34 checks)\\n", "original self-check failed or changed"
path = pathlib.Path.cwd() / "probe.html"
path.write_text("<title>Original</title><p>caf\u00e9 \u6771\u4eac</p>", encoding="utf-8")
result = brewdoc.run(path)
assert result[0] == 0 and result[1]["route"] == "html" and result[1]["units"] == 1, "original HTML conversion receipt differs"
assert brewdoc.render_html(path)[0] == result[2], "original HTML wrapper differs from run"
assert "caf\u00e9 \u6771\u4eac" in result[2], "original HTML conversion lost Unicode"
console = pathlib.Path(sys.prefix) / "bin/brewdoc"
completed = subprocess.run([str(console), str(path)], capture_output=True, timeout=60)
expected_line = (json.dumps(result[1], ensure_ascii=True, sort_keys=True) + "\\n").encode("ascii")
assert (completed.returncode, completed.stdout, completed.stderr) == (0, expected_line + result[2].encode("utf-8"), b""), "original CLI receipt or UTF-8 stdout differs"
out = pathlib.Path.cwd() / "out.md"
written = brewdoc.run(path, out)
completed = subprocess.run([str(console), str(path), "--out", str(out)], capture_output=True, timeout=60)
assert (completed.returncode, completed.stdout, completed.stderr, out.read_bytes()) == (0, (json.dumps(written[1], ensure_ascii=True, sort_keys=True) + "\\n").encode("ascii"), b"", result[2].encode("utf-8")), "original CLI output path or bytes differ"
assert brewdoc.run(path) == result, "original conversion is not deterministic"
print(json.dumps({"version": "0.2.0", "origin_verified": True, "contracts_verified": True, "self_check": "self-check: ok (34 checks)", "dependencies": {name: importlib.metadata.version(name) for name in ("pdfplumber", "python-calamine", "selectolax")}}))
'''


def prepare(source: Path, folder: Path) -> None:
    """Prefer verified retained packages; rebuild only confirmed absent or expired artifacts."""
    artifact = api(f"actions/artifacts/{ORIGINAL_ARTIFACT}", missing=True)
    method = "retained"
    if artifact is not None and not artifact.get("expired"):
        digest = validate_artifact(artifact, 36746089723, SOURCE_SHA, "dist")
        if digest != ORIGINAL_DIGEST:
            raise ValueError("Original artifact digest changed")
        unpack_artifact(command(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{ORIGINAL_ARTIFACT}/zip"]),
                        digest, FILENAMES, folder)
        identities = file_identities(folder)
        if tuple(identities[name]["sha256"] for name in FILENAMES) != ORIGINAL_HASHES:
            raise ValueError("Original retained package hashes changed")
    else:
        method = "rebuilt"
        verify_source(source)
        folder.mkdir(parents=True, exist_ok=False)
        command(["uv", "lock", "--check"], source)
        command(["uv", "build", "--no-sources", "--no-create-gitignore",
                 "--out-dir", str(folder)], source)
    if set(path.name for path in folder.iterdir()) != set(FILENAMES):
        raise ValueError("Preparation must produce only the original package pair")
    verify_packages(folder, source)
    verification = installed_checks(folder, source)
    manifest = {"schema": 1, "operation": "prepare-v020", "repository": REPOSITORY,
                "tag": TAG, "source_sha": SOURCE_SHA, "version": "0.2.0",
                "control_sha": os.environ["GITHUB_SHA"], "prepared_run_id": int(os.environ["GITHUB_RUN_ID"]),
                "prepared_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]), "method": method,
                "files": file_identities(folder), "verification": verification}
    (folder / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def validate_manifest(manifest: dict, folder: Path, run: dict, approvals: tuple[str, str]) -> None:
    """Bind approval hashes and installed original-package proof to the preparation run."""
    identity = {key: manifest.get(key) for key in (
        "schema", "operation", "repository", "tag", "source_sha", "version", "control_sha",
        "prepared_run_id", "prepared_run_attempt")}
    if identity != {"schema": 1, "operation": "prepare-v020", "repository": REPOSITORY,
                    "tag": TAG, "source_sha": SOURCE_SHA, "version": "0.2.0",
                    "control_sha": run["head_sha"], "prepared_run_id": run["id"],
                    "prepared_run_attempt": run["run_attempt"]}:
        raise ValueError("Recovery manifest identity differs from the preparation run")
    actual = file_identities(folder)
    if manifest.get("files") != actual or tuple(actual[name]["sha256"] for name in FILENAMES) != approvals:
        raise ValueError("Recovery package bytes differ from manifest or approved hashes")
    verification = manifest.get("verification", [])
    if ([entry.get("filename") for entry in verification] != list(FILENAMES)
            or any(entry.get("version") != "0.2.0" or entry.get("origin_verified") is not True
                   or entry.get("contracts_verified") is not True for entry in verification)
            or manifest.get("method") not in {"retained", "rebuilt"}):
        raise ValueError("Original package installation verification is incomplete")


def admit(source: Path, folder: Path, inputs: dict) -> None:
    """Reacquire accepted provenance and packages before each publication boundary."""
    operation, run_id, approvals = validate_inputs(inputs)
    if operation != "publish-v020":
        raise ValueError("Only publish-v020 admits prepared artifacts")
    run = api(f"actions/runs/{run_id}")
    validate_run(run, api(f"actions/runs/{run_id}/jobs"), os.environ["GITHUB_SHA"], run_id)
    listing = api(f"actions/runs/{run_id}/artifacts")
    if listing.get("total_count") != 1 or len(listing.get("artifacts", [])) != 1:
        raise ValueError("Preparation must contain exactly one recovery artifact")
    artifact = api(f"actions/artifacts/{listing['artifacts'][0]['id']}")
    digest = validate_artifact(artifact, run_id, os.environ["GITHUB_SHA"], ARTIFACT_NAME)
    unpack_artifact(command(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"]),
                    digest, (*FILENAMES, MANIFEST_NAME), folder)
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    validate_manifest(manifest, folder, run, approvals)
    verify_packages(folder, source)


def public_bytes(url: str) -> bytes:
    """Fetch only public package hosts with a bounded response and no credentials."""
    allowed = {"pypi.org", "files.pythonhosted.org", "github.com", "release-assets.githubusercontent.com",
               "objects.githubusercontent.com"}

    def check(target: str) -> None:
        """Refuse targets outside the fixed public distribution hosts."""
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
            raise ValueError("Public package URL is not an admitted HTTPS host")

    class Redirect(urllib.request.HTTPRedirectHandler):
        """Validate public download redirects without carrying authentication."""

        def redirect_request(self, request, response, code, message, headers, target):
            """Apply the public host policy to every redirect."""
            check(target)
            return super().redirect_request(request, response, code, message, headers, target)

    check(url)
    with urllib.request.build_opener(Redirect).open(url, timeout=60) as response:
        payload = response.read(MAX_DOWNLOAD + 1)
    if len(payload) > MAX_DOWNLOAD:
        raise ValueError("Public package response exceeds the size limit")
    return payload


def missing_public_files(expected: dict, entries: list[dict], fetch) -> list[str]:
    """Accept exact existing bytes and return only absent approved filenames."""
    names = [entry.get("filename") for entry in entries]
    if len(names) != len(set(names)) or not set(names).issubset(expected):
        raise ValueError("Public release contains unexpected or duplicate files")
    for entry in entries:
        name = entry["filename"]
        payload = fetch(entry["url"])
        actual = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        if actual != expected[name] or entry.get("size") != actual["size"]:
            raise ValueError("Public release bytes differ from approved packages")
    return [name for name in FILENAMES if name not in names]


def pypi_missing(folder: Path) -> list[str]:
    """Treat only public HTTP 404 as absence; all other preflight errors stop recovery."""
    try:
        metadata = json.loads(public_bytes("https://pypi.org/pypi/brewdoc/0.2.0/json"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return list(FILENAMES)
        raise ValueError("PyPI preflight is unavailable") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("info"), dict):
        raise ValueError("PyPI public-file metadata is malformed")
    if (metadata["info"].get("name"), metadata["info"].get("version")) != ("brewdoc", "0.2.0"):
        raise ValueError("PyPI release identity changed")
    entries = metadata.get("urls")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("PyPI public-file metadata is malformed")
    expected = file_identities(folder)
    for entry in entries:
        if (not isinstance(entry.get("filename"), str) or entry["filename"] not in expected
                or type(entry.get("size")) is not int or entry["size"] < 0
                or not isinstance(entry.get("url"), str) or not isinstance(entry.get("digests"), dict)
                or not isinstance(entry["digests"].get("sha256"), str)
                or not HEX.fullmatch(entry["digests"]["sha256"])):
            raise ValueError("PyPI public-file metadata is malformed")
        target = urllib.parse.urlsplit(entry["url"])
        if (target.scheme != "https" or target.hostname != "files.pythonhosted.org"
                or target.username or target.password or target.port not in {None, 443} or target.fragment):
            raise ValueError("PyPI public-file metadata is malformed")
        if {"size": entry["size"], "sha256": entry["digests"]["sha256"]} != expected[entry["filename"]]:
            raise ValueError("Public release bytes differ from approved packages")
    return missing_public_files(expected, entries, public_bytes)


def stage_upload(folder: Path) -> None:
    """Stage only missing exact PyPI files and expose whether upload is needed."""
    missing = pypi_missing(folder)
    upload = folder.parent / "recovery-upload"
    upload.mkdir(exist_ok=False)
    for name in missing:
        shutil.copyfile(folder / name, upload / name)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"upload_needed={'true' if missing else 'false'}\n")


def complete_github(folder: Path) -> None:
    """Verify PyPI, then add only missing old-tag assets without promoting latest."""
    if pypi_missing(folder):
        raise ValueError("PyPI original release is incomplete")
    release = api(f"releases/tags/{TAG}", missing=True)
    if release is None:
        command(["gh", "release", "create", TAG, "--repo", REPOSITORY, "--verify-tag",
                 "--target", SOURCE_SHA, "--latest=false", "--title", TAG,
                 "--notes", "Original immutable 0.2.0 release."])
        release = api(f"releases/tags/{TAG}")
    if (release.get("tag_name") != TAG or release.get("draft") is not False
            or release.get("prerelease") is not False or len(release.get("assets", [])) > 2):
        raise ValueError("GitHub original release identity is invalid")
    entries = [{"filename": asset["name"], "size": asset["size"], "url": asset["browser_download_url"]}
               for asset in release["assets"]]
    for entry in entries:
        if entry["url"] != f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{entry['filename']}":
            raise ValueError("GitHub release asset URL differs from the original tag")
    missing = missing_public_files(file_identities(folder), entries, public_bytes)
    if missing:
        command(["gh", "release", "upload", TAG, "--repo", REPOSITORY,
                 *(str(folder / name) for name in missing)])
    release = api(f"releases/tags/{TAG}")
    final = [{"filename": asset["name"], "size": asset["size"], "url": asset["browser_download_url"]}
             for asset in release["assets"]]
    if missing_public_files(file_identities(folder), final, public_bytes):
        raise ValueError("GitHub original release assets remain incomplete")


def main() -> None:
    """Run one fixed recovery phase using event inputs and fixed workspace paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "admit", "preflight", "github"))
    phase = parser.parse_args().phase
    event, source, folder = context()
    operation, _, _ = validate_inputs(event["inputs"])
    if phase == "prepare":
        if operation != "prepare-v020":
            raise ValueError("Preparation requires prepare-v020")
        prepare(source, folder)
        return
    admit(source, folder, event["inputs"])
    if phase in {"admit", "preflight"}:
        stage_upload(folder)
        context()
    else:
        complete_github(folder)


if __name__ == "__main__":
    main()
