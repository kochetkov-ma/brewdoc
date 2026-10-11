"""Admit and publish the complete immutable Brewdoc release asset set."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
import stat

REPOSITORY = "kochetkov-ma/brewdoc"
CHECKOUT = Path(__file__).resolve().parents[2]
TARGETS = ("linux-x64", "windows-x64", "macos-arm64")
MAX_ASSET = 512 * 1024 * 1024
CONTROLS = {"release-manifest.json", "SHA256SUMS", "staged-body.md"}
CI_ARTIFACTS = ("ci-dist", "ci-native-assets", "ci-npm-release",
                *("ci-npm-consumer-" + target for target in TARGETS))
TOOLS = {"python": "3.12.14", "uv": "0.12.21", "node": "24.21.0", "npm": "12.2.0"}


def needs_smoke(paths: list[str]) -> bool:
    """Skip only a complete, ordinary documentation-only diff outside packaged inputs."""
    return not paths or any(not name.startswith("docs/") or ".." in Path(name).parts
                            or "\\" in name or name.endswith("/") for name in paths)


def source_inputs(checkout: Path, source_sha: str) -> dict:
    """Bind every tracked input and tool pin to the immutable producer checkout."""
    names = command(["git", "-C", str(checkout), "ls-files", "-z"]).decode().split("\0")
    return {"schema": 1, "source_sha": source_sha, "tools": TOOLS,
            "files": {name: identity(checkout / name) for name in names if name}}


def admit_producer(run: dict, source_sha: str) -> None:
    """Reject foreign workflows, PRs and incomplete or different-source producers."""
    expected = {"event": "push", "head_branch": "main", "head_sha": source_sha,
                "status": "completed", "conclusion": "success", "path": ".github/workflows/ci.yml"}
    if ({key: run.get(key) for key in expected} != expected
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or type(run.get("id")) is not int or run["id"] <= 0
            or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0):
        raise ValueError("CI producer identity differs from the trusted exact-source main run")


def admit_ci_artifacts(rows: list[dict], run: dict) -> dict:
    """Require unique complete unexpired artifacts and GitHub transport digests."""
    selected = {}
    for row in rows:
        name = row["name"]
        if name not in CI_ARTIFACTS:
            continue
        try:
            expiry = dt.datetime.fromisoformat(row.get("expires_at", "").replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("CI artifact expiry is unavailable") from None
        if (name in selected or row.get("expired") is not False or type(row.get("id")) is not int or row["id"] <= 0
                or expiry.tzinfo is None or expiry <= dt.datetime.now(dt.timezone.utc)
                or type(row.get("size_in_bytes")) is not int or not 0 < row["size_in_bytes"] <= MAX_ASSET
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("digest", ""))
                or row.get("workflow_run", {}).get("id") != run["id"]
                or row.get("workflow_run", {}).get("head_sha") != run["head_sha"]):
            raise ValueError("CI artifact is duplicated, expired or unverifiable")
        selected[name] = row
    if set(selected) != set(CI_ARTIFACTS):
        raise ValueError("CI artifact set is incomplete")
    if len({row["id"] for row in selected.values()}) != len(CI_ARTIFACTS):
        raise ValueError("CI artifact identifiers are duplicated")
    return selected


def select_producer(source_sha: str) -> tuple[dict, dict]:
    """Select one bounded canonical successful main run, with no rebuild fallback."""
    result = api(f"actions/workflows/ci.yml/runs?event=push&branch=main&status=success&head_sha={source_sha}&per_page=100")
    if result["total_count"] > 100:
        raise ValueError("CI producer selection exceeds its bounded page")
    candidates = []
    for row in result["workflow_runs"]:
        admit_producer(row, source_sha)
        candidates.append(row)
    if not candidates:
        raise ValueError("No successful exact-source main CI producer; rerun that producer")
    run = max(candidates, key=lambda row: (row["id"], row["run_attempt"]))
    result = api(f"actions/runs/{run['id']}/artifacts?per_page=100")
    if result["total_count"] > 100:
        raise ValueError("CI artifact selection exceeds its bounded page")
    return run, admit_ci_artifacts(result["artifacts"], run)


def download_ci_artifact(row: dict, destination: Path) -> None:
    """Verify GitHub ZIP transport before extracting bounded ordinary members."""
    with tempfile.TemporaryDirectory(prefix="brewdoc-ci-download-") as temporary:
        archive = Path(temporary) / "artifact.zip"
        authenticated_download(f"actions/artifacts/{row['id']}/zip", archive, row["size_in_bytes"])
        if identity(archive) != {"bytes": row["size_in_bytes"], "sha256": row["digest"].removeprefix("sha256:")}:
            raise ValueError("CI artifact transport digest differs")
        destination.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(archive) as zipped:
            seen, total = set(), 0
            for member in zipped.infolist():
                name = member.filename.rstrip("/")
                path = Path(name)
                total += member.file_size
                if (not name or name in seen or path.is_absolute() or ".." in path.parts or "\\" in name
                        or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1
                        or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or total > 2 * MAX_ASSET):
                    raise ValueError("CI artifact ZIP member is unsafe or oversized")
                seen.add(name)
                target = destination / path
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)


def reuse_ci(checkout: Path, output: Path, tag: str, source_sha: str) -> dict:
    """Admit downloaded main bytes and all smoke evidence before publication."""
    version = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if tag != "v" + version or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Release tag or source identity differs")
    tag_preflight({"tag": tag, "source_sha": source_sha})
    run, artifacts = select_producer(source_sha)
    output.mkdir(parents=True, exist_ok=False)
    for name, row in artifacts.items():
        download_ci_artifact(row, output / name)
    if read_json(output / "ci-dist/ci-package/producer-inputs.json") != source_inputs(checkout, source_sha):
        raise ValueError("CI producer input or tool hashes differ")
    binaries = read_json(output / "ci-native-assets/binaries.json")
    npm = helper("build_npm").verify_release(output / "ci-npm-release")
    if binaries.get("verification_scope") != "smoke" or npm.get("verification_scope") != "smoke":
        raise ValueError("CI reuse requires explicit smoke profiles")
    for target in TARGETS:
        native = binaries["targets"][target]
        native_report(read_json(output / f"ci-native-assets/verification/{target}.json"), native, source_sha, version, target)
        report = read_json(output / f"ci-npm-consumer-{target}/ci-npm-probe-{target}.json")
        npm_report(report, target, {"version": version, "source_sha": source_sha}, native, npm, "local")
    refreshed = api(f"actions/runs/{run['id']}")
    admit_producer(refreshed, source_sha)
    if refreshed["run_attempt"] != run["run_attempt"]:
        raise ValueError("CI producer attempt changed during admission")
    refreshed_artifacts = api(f"actions/runs/{run['id']}/artifacts?per_page=100")
    if (refreshed_artifacts["total_count"] > 100
            or admit_ci_artifacts(refreshed_artifacts["artifacts"], run) != artifacts):
        raise ValueError("CI artifact identities changed during admission")
    record = {"schema": 1, "source_sha": source_sha, "version": version, "run_id": run["id"],
              "run_attempt": run["run_attempt"], "artifacts": {name: {key: row[key] for key in
                  ("id", "digest", "size_in_bytes")} for name, row in artifacts.items()}}
    write_json(output / "producer.json", record)
    return record


def helper(name: str):
    """Load the existing artifact owner without importing the conversion engine."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    """Require a regular JSON object at a fixed evidence path."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or linked release evidence")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Release evidence must be an object")
    return value


def write_json(path: Path, value: dict) -> None:
    """Write portable, ordered release evidence with one trailing newline."""
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def identity(path: Path) -> dict:
    """Hash only bounded ordinary files without following substitutions."""
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_ASSET:
        raise ValueError("Missing, linked or oversized release asset")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def command(arguments: list[str], payload: bytes | None = None) -> bytes:
    """Keep authentication in gh's environment and suppress untrusted error output."""
    result = subprocess.run(arguments, input=payload, capture_output=True, timeout=600)
    if result.returncode:
        raise ValueError(f"Release command failed: {Path(arguments[0]).name} (exit {result.returncode})")
    return result.stdout


def api(endpoint: str, *, missing: bool = False, payload: dict | None = None) -> dict | list | None:
    """Treat only an explicit HTTP 404 as absence, never an unavailable preflight."""
    arguments = ["gh", "api", f"repos/{REPOSITORY}/{endpoint}"]
    body = None
    if payload is not None:
        arguments += ["--method", "POST", "--input", "-"]
        body = json.dumps(payload).encode("utf-8")
    result = subprocess.run(arguments, input=body, capture_output=True, timeout=60)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise ValueError("Repository preflight returned invalid JSON") from None
    if result.returncode:
        if missing and isinstance(value, dict) and str(value.get("status")) == "404":
            return None
        raise ValueError("Repository preflight unavailable")
    return value


def prepare(checkout: Path, bundle: Path, native: Path, output: Path, body: Path,
            npm: Path | None = None, producer_record: Path | None = None) -> dict:
    """Reuse artifact admission, then seal the exact upload set and draft body."""
    if npm is None:
        raise ValueError("The new release requires all four npm packages")
    packages = helper("verify_install")
    producer = read_json(bundle / "ci-package/manifest.json")
    packages.verify_manifest(bundle, producer)
    wheel, sdist = packages.distributions(bundle / "dist")
    version = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    source_sha = command(["git", "-C", str(checkout), "rev-parse", "HEAD"]).decode().strip()
    if (producer["version"], producer["checkout_sha"]) != (version, source_sha):
        raise ValueError("Package source or version differs from checkout")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Release version or source identity is invalid")
    if {name: packages.digest(checkout / name) for name in producer["source"]} != producer["source"]:
        raise ValueError("Producer source inventory changed")
    if (wheel.name, sdist.name) != (f"brewdoc-{version}-py3-none-any.whl", f"brewdoc-{version}.tar.gz"):
        raise ValueError("Package filenames differ from release version")
    binaries = read_json(native / "binaries.json")
    if (binaries["schema"], binaries["version"], binaries["source_sha"], set(binaries["targets"])) != (
            1, version, source_sha, set(TARGETS)):
        raise ValueError("Native aggregate identity or target set differs")
    paths = [wheel, sdist, native / "binaries.json"]
    builder = helper("build_standalone")
    for target in TARGETS:
        record = binaries["targets"][target]
        builder.validate_manifest(record)
        if (record["target"], record["version"], record["source_sha"], record["wheel_sha256"], record["lock_sha256"]) != (
                target, version, source_sha, packages.digest(wheel), packages.digest(checkout / "uv.lock")):
            raise ValueError("Native target differs from accepted package source")
        archive = native / record["archive"]["filename"]
        if identity(archive) != {key: record["archive"][key] for key in ("bytes", "sha256")}:
            raise ValueError("Native archive bytes changed")
        with tempfile.TemporaryDirectory(prefix="brewdoc-native-admission-") as temporary:
            builder.extract_archive(archive, Path(temporary) / "bundle", record["inventory"])
        paths.append(archive)
    expected_native = {path.name for path in paths[2:]} | {"SHA256SUMS"}
    if (native / "verification").is_dir():
        expected_native.add("verification")
        for target in TARGETS:
            native_report(read_json(native / f"verification/{target}.json"), binaries["targets"][target],
                          source_sha, version, target)
    if {path.name for path in native.iterdir()} != expected_native:
        raise ValueError("Native aggregate has unexpected files")
    npm_record = helper("build_npm").verify_release(npm)
    if (npm_record["version"], npm_record["source_sha"]) != (version, source_sha):
        raise ValueError("npm identity differs from package source")
    npm_paths = [npm / "npm-manifest.json", *(npm / row["filename"] for row in npm_record["packages"])]
    if {path.name for path in npm.iterdir()} != {path.name for path in npm_paths}:
        raise ValueError("npm aggregate has unexpected files")
    paths.extend(npm_paths)
    assets = {path.name: identity(path) for path in paths}
    if len(assets) != len(paths) or set(assets).intersection(CONTROLS):
        raise ValueError("Release asset names collide")
    staged_body = identity(body)
    body.read_text(encoding="utf-8")
    manifest = {"schema": 1, "repository": REPOSITORY, "version": version, "tag": "v" + version,
                "source_sha": source_sha, "assets": assets, "body": staged_body, "npm": True}
    if producer_record is not None:
        manifest["producer"] = read_json(producer_record)
    output.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copyfile(path, output / path.name)
    shutil.copyfile(body, output / "staged-body.md")
    write_json(output / "release-manifest.json", manifest)
    (output / "SHA256SUMS").write_text(checksums(assets), encoding="ascii")
    admit(output, manifest["tag"], source_sha)
    return manifest


def checksums(assets: dict) -> str:
    """Order payload checksums without including their own output."""
    return "".join(f"{row['sha256']}  {name}\n" for name, row in sorted(assets.items()))


def admit(folder: Path, tag: str, source_sha: str) -> dict:
    """Require the sealed complete set before every remote operation."""
    manifest = read_json(folder / "release-manifest.json")
    if manifest.get("npm") is not True:
        raise ValueError("The new release requires all four npm packages")
    if (manifest["schema"], manifest["repository"], manifest["tag"], manifest["source_sha"]) != (
            1, REPOSITORY, tag, source_sha) or tag != "v" + manifest["version"]:
        raise ValueError("Release tag, repository or source differs")
    assets = manifest["assets"]
    binaries = read_json(folder / "binaries.json")
    if (binaries["schema"], binaries["version"], binaries["source_sha"], set(binaries["targets"])) != (
            1, manifest["version"], source_sha, set(TARGETS)):
        raise ValueError("Sealed native aggregate identity differs")
    version = manifest["version"]
    names = {f"brewdoc-{version}-py3-none-any.whl", f"brewdoc-{version}.tar.gz", "binaries.json"}
    builder = helper("build_standalone")
    for target in TARGETS:
        record = binaries["targets"][target]
        builder.validate_manifest(record)
        archive = record["archive"]
        if (record["target"], record["version"], record["source_sha"], record["wheel_sha256"]) != (
                target, version, source_sha, assets[f"brewdoc-{version}-py3-none-any.whl"]["sha256"]):
            raise ValueError("Sealed native source or package differs")
        if assets.get(archive["filename"]) != {key: archive[key] for key in ("bytes", "sha256")}:
            raise ValueError("Sealed native archive differs")
        names.add(archive["filename"])
    npm = helper("build_npm").verify_release(folder)
    if (npm["version"], npm["source_sha"]) != (version, source_sha):
        raise ValueError("Sealed npm identity differs")
    for row in npm["packages"][:-1]:
        if row["native_archive"] != binaries["targets"][row["target"]]["archive"]:
            raise ValueError("Sealed npm native archive differs")
    names.update({"npm-manifest.json", *(record["filename"] for record in npm["packages"])})
    if set(assets) != names:
        raise ValueError("Sealed release asset set differs")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in CONTROLS for name in assets):
        raise ValueError("Unsafe release asset name")
    if {path.name for path in folder.iterdir()} != set(assets) | CONTROLS:
        raise ValueError("Release directory has missing or unexpected files")
    if {name: identity(folder / name) for name in assets} != assets:
        raise ValueError("Release asset bytes changed")
    if identity(folder / "staged-body.md") != manifest["body"]:
        raise ValueError("Staged release body changed")
    if (folder / "SHA256SUMS").read_text(encoding="ascii") != checksums(assets):
        raise ValueError("Release checksums changed")
    return manifest


def tag_preflight(manifest: dict) -> None:
    """Resolve only the expected immutable tag to its admitted commit."""
    tag = manifest["tag"]
    reference = api(f"git/ref/tags/{tag}")
    if reference.get("ref") != f"refs/tags/{tag}":
        raise ValueError("Release tag reference differs")
    record = reference["object"]
    if record["type"] == "tag":
        record = api(f"git/tags/{record['sha']}")["object"]
    if (record["type"], record["sha"]) != ("commit", manifest["source_sha"]):
        raise ValueError("Release tag moved or source differs")


def release_state(folder: Path, manifest: dict, *, missing: bool = False) -> tuple[dict | None, dict]:
    """Reject foreign releases and unexpected, duplicate or differently sized assets."""
    if manifest.get("npm") is not True:
        raise ValueError("The new release requires all four npm packages")
    tag_preflight(manifest)
    release = api(f"releases/tags/{manifest['tag']}", missing=missing)
    if release is None:
        return None, {}
    if (type(release["id"]) is not int or release["id"] <= 0 or type(release["draft"]) is not bool
            or (release["tag_name"], release["target_commitish"], release["prerelease"]) != (
                manifest["tag"], manifest["source_sha"], False)):
        raise ValueError("Existing release identity differs")
    rows = api(f"releases/{release['id']}/assets?per_page=100")
    if len(rows) >= 100 or len({row["name"] for row in rows}) != len(rows):
        raise ValueError("Release assets are duplicated or truncated")
    assets = {row["name"]: row for row in rows}
    if any(type(row["id"]) is not int or row["id"] <= 0 for row in rows):
        raise ValueError("Existing asset identifier is invalid")
    expected = upload_identities(folder, manifest)
    if set(assets) - set(expected) or any(assets[name]["size"] != expected[name]["bytes"] for name in assets):
        raise ValueError("Existing release assets are unexpected or differently sized")
    return release, assets


def upload_identities(folder: Path, manifest: dict) -> dict:
    """Include the manifest and checksum files without creating a hash cycle."""
    return manifest["assets"] | {name: identity(folder / name) for name in ("release-manifest.json", "SHA256SUMS")}


def verify_downloads(folder: Path, manifest: dict, release: dict, assets: dict) -> None:
    """Verify every existing draft or public asset by downloaded bytes before reuse."""
    expected = upload_identities(folder, manifest)
    with tempfile.TemporaryDirectory(prefix="brewdoc-release-readback-") as temporary:
        for name, row in assets.items():
            path = Path(temporary) / name
            if release["draft"]:
                draft_download(row["id"], path, expected[name])
            else:
                download(public_url(manifest["tag"], name), path, expected[name])
            if identity(path) != expected[name]:
                raise ValueError("Existing release asset bytes differ")


def draft_download(asset_id: int, path: Path, expected: dict) -> None:
    """Bound authenticated gh output by accepted bytes and a total deadline."""
    authenticated_download(f"releases/assets/{asset_id}", path, expected["bytes"])


def authenticated_download(endpoint: str, path: Path, size: int) -> None:
    """Read one authenticated artifact under a byte ceiling and process watchdog."""
    with subprocess.Popen(["gh", "api", f"repos/{REPOSITORY}/{endpoint}",
                           "--header", "Accept: application/octet-stream"], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        watchdog = threading.Timer(120, process.kill)
        watchdog.start()
        try:
            with path.open("xb") as output:
                remaining = size + 1
                while remaining:
                    chunk = process.stdout.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    output.write(chunk)
                    remaining -= len(chunk)
            if remaining == 0:
                process.kill()
                raise ValueError("Draft asset exceeds its admitted size")
            if process.wait(timeout=5):
                raise ValueError("Draft asset readback unavailable")
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def stage(folder: Path, manifest: dict) -> None:
    """Create or resume only a matching draft, uploading absent exact assets."""
    release, assets = release_state(folder, manifest, missing=True)
    body = (folder / "staged-body.md").read_text(encoding="utf-8")
    if release is not None:
        verify_downloads(folder, manifest, release, assets)
        if not release["draft"] or release["body"] != body:
            raise ValueError("Existing release is public or its staged body differs")
    else:
        api("releases", payload={"tag_name": manifest["tag"], "target_commitish": manifest["source_sha"],
                                 "name": manifest["tag"], "body": body, "draft": True, "prerelease": False})
        release, assets = release_state(folder, manifest)
    for name in sorted(set(upload_identities(folder, manifest)) - set(assets)):
        command(["gh", "release", "upload", manifest["tag"], str(folder / name), "--repo", REPOSITORY])
    release, assets = release_state(folder, manifest)
    if not release["draft"] or release["body"] != body:
        raise ValueError("Draft release state changed during staging")
    require_complete(folder, manifest, release, assets)


def require_complete(folder: Path, manifest: dict, release: dict, assets: dict) -> None:
    """Reject a partial remote set before verifying any publication or final body."""
    if set(assets) != set(upload_identities(folder, manifest)):
        raise ValueError("Release asset set is incomplete")
    verify_downloads(folder, manifest, release, assets)


def publish(folder: Path, manifest: dict) -> None:
    """Publish only the byte-verified complete draft with its admitted staged body."""
    release, assets = release_state(folder, manifest)
    require_complete(folder, manifest, release, assets)
    if release["body"] != (folder / "staged-body.md").read_text(encoding="utf-8"):
        raise ValueError("Release body differs from admitted staged body")
    if release["draft"]:
        tag_preflight(manifest)
        command(["gh", "release", "edit", manifest["tag"], "--draft=false", "--repo", REPOSITORY])
    verify_public(folder, manifest)


def public_url(tag: str, name: str) -> str:
    """Use only immutable official release asset URLs."""
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    """Keep credential-free public reads on reviewed artifact hosts."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        """Refuse foreign hosts, plaintext and URL credentials before following a redirect."""
        location = urllib.parse.urlsplit(new_url)
        if (location.scheme != "https" or location.username or location.password or location.port not in (None, 443)
                or location.hostname not in {"github.com", "release-assets.githubusercontent.com",
                                             "objects.githubusercontent.com", "files.pythonhosted.org"}):
            raise ValueError("Public download redirected outside artifact hosts")
        return super().redirect_request(request, response, code, message, headers, new_url)


def download(url: str, path: Path, expected: dict) -> None:
    """Read no more than the admitted asset bytes and refuse changed public output."""
    if path.exists() or path.is_symlink() or not 0 < expected["bytes"] <= MAX_ASSET:
        raise ValueError("Download destination exists or size is invalid")
    location = urllib.parse.urlsplit(url)
    if (location.scheme != "https" or location.username or location.password or location.port not in (None, 443)
            or location.hostname not in {"github.com", "files.pythonhosted.org"}):
        raise ValueError("Public download starts outside artifact hosts")
    opener = urllib.request.build_opener(PublicRedirect())
    deadline = time.monotonic() + 120
    with opener.open(url, timeout=30) as response, path.open("xb") as output:
        remaining = expected["bytes"]
        while remaining:
            if time.monotonic() > deadline:
                raise ValueError("Public asset download deadline exceeded")
            chunk = response.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("Public asset download is incomplete")
            output.write(chunk)
            remaining -= len(chunk)
        if response.read(1):
            raise ValueError("Public asset exceeds its admitted size")
    if identity(path) != expected:
        raise ValueError("Public asset bytes differ")


def verify_public(folder: Path, manifest: dict) -> tuple[dict, dict]:
    """Prove the complete public asset set separately from upload success."""
    release, assets = release_state(folder, manifest)
    if release["draft"]:
        raise ValueError("Release is still a draft")
    if set(assets) != set(upload_identities(folder, manifest)):
        raise ValueError("Public release asset set is incomplete")
    require_complete(folder, manifest, release, assets)
    return release, assets


def download_target(folder: Path, manifest: dict, target: str, output: Path) -> None:
    """Bind the exact public native download to the later native runtime report."""
    release, assets = release_state(folder, manifest)
    if release["draft"]:
        raise ValueError("Release is still a draft")
    record = read_json(folder / "binaries.json")["targets"][target]["archive"]
    name = record["filename"]
    if name not in assets:
        raise ValueError("Public native asset is missing")
    if set(assets) != set(upload_identities(folder, manifest)):
        raise ValueError("Public release asset set is incomplete")
    receipt_path = output.with_name(output.name + ".download.json")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("Native download evidence destination exists")
    download(public_url(manifest["tag"], name), output, manifest["assets"][name])
    write_json(receipt_path, {
        "schema": 1, "tag": manifest["tag"], "source_sha": manifest["source_sha"], "target": target,
        "release_id": release["id"], "asset_id": assets[name]["id"], "asset_filename": name,
        "url": public_url(manifest["tag"], name), "download_sha256": record["sha256"], "bytes": record["bytes"],
        "verified_at": dt.datetime.now(dt.timezone.utc).isoformat()})


def pypi_public(folder: Path, manifest: dict) -> None:
    """Bind both public Python archives to the admitted release bytes."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/brewdoc/{manifest['version']}/json", timeout=30) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("PyPI metadata exceeds its limit")
    metadata = json.loads(payload)
    names = {f"brewdoc-{manifest['version']}-py3-none-any.whl", f"brewdoc-{manifest['version']}.tar.gz"}
    rows = metadata["urls"]
    if {row["filename"] for row in rows} != names or len(rows) != 2:
        raise ValueError("Public PyPI archive set differs")
    with tempfile.TemporaryDirectory(prefix="brewdoc-pypi-readback-") as temporary:
        for row in rows:
            expected = manifest["assets"][row["filename"]]
            location = urllib.parse.urlsplit(row["url"])
            if (location.scheme, location.hostname, location.username, location.password) != (
                    "https", "files.pythonhosted.org", None, None):
                raise ValueError("Public PyPI URL differs")
            if (row["size"], row["digests"]["sha256"], row["yanked"]) != (
                    expected["bytes"], expected["sha256"], False):
                raise ValueError("Public PyPI metadata differs")
            download(row["url"], Path(temporary) / row["filename"], expected)


def native_evidence(directory: Path, target: str, manifest: dict, native: dict,
                    release: dict, assets: dict) -> None:
    """Bind the native owner's consumer report to an actual public asset id."""
    paths = list(directory.glob("*.download.json"))
    if len(paths) != 1:
        raise ValueError("Native public download evidence is missing or duplicated")
    downloaded = read_json(paths[0])
    archive = native["archive"]
    name = archive["filename"]
    expected = {"schema": 1, "tag": manifest["tag"], "source_sha": manifest["source_sha"], "target": target,
                "release_id": release["id"], "asset_id": assets[name]["id"], "asset_filename": name,
                "url": public_url(manifest["tag"], name), "download_sha256": archive["sha256"], "bytes": archive["bytes"]}
    if {key: downloaded.get(key) for key in expected} != expected:
        raise ValueError("Native public download identity differs")
    timestamp = dt.datetime.fromisoformat(downloaded["verified_at"])
    if timestamp.tzinfo is None or timestamp > dt.datetime.now(dt.timezone.utc):
        raise ValueError("Native download timestamp is invalid")
    report = read_json(directory / "verification.json")
    native_report(report, native, manifest["source_sha"], manifest["version"], target)


def native_report(report: dict, native: dict, source_sha: str, version: str, target: str) -> None:
    """Require the exact native profile, synthetic outputs and platform prerequisites."""
    builder = helper("build_standalone")
    builder.validate_manifest(native)
    scope = builder.verification_scope(native)
    if builder.verification_scope(report) != scope:
        raise ValueError("Native consumer verification scope differs")
    wanted = {"schema": 1, "verdict": "PASS", "source_sha": source_sha, "version": version,
              "target": target, "archive_sha256": native["archive"]["sha256"], "self_check": 38,
              "probes": [row["probe"] for row in native["probes"]],
              "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]]}
    if {key: report.get(key) for key in wanted} != wanted:
        raise ValueError("Native public consumer evidence differs")
    builder.validate_verification(native, report)
    if (report["https"]["file_ok"], report["https"]["acquisition"]["capture_status"],
            report["invalid_tls"]["acquisition"]["refusal_stage"]) != (True, "static", "tls"):
        raise ValueError("Native public TLS evidence differs")
    if not report["children"]["dns"] or set(report["children"]["dns"]) - {"127.0.0.1", "::1"}:
        raise ValueError("Native public DNS evidence differs")
    javascript = report["javascript"]["acquisition"]
    if target == "windows-x64":
        if report["children"].get("exited_owner") != {"code": 1, "stdout_bytes": 0, "stderr_bytes": 0}:
            raise ValueError("Native public Windows exited-owner evidence differs")
        if (javascript["refusal_stage"], javascript["counts"]["requests"]) != ("prerequisites", 0):
            raise ValueError("Native public Windows JS refusal differs")
    elif javascript["capture_status"] not in {"settled", "partial"} or "worker" not in report["children"]:
        raise ValueError("Native public JS worker evidence differs")


def npm_evidence(directory: Path, target: str, manifest: dict, native: dict, npm: dict) -> None:
    """Bind normal registry installs and npx reports to the exact admitted packages."""
    report = read_json(directory / "npm-public.json")
    npm_report(report, target, manifest, native, npm, "public")


def npm_report(report: dict, target: str, manifest: dict, native: dict, npm: dict, mode: str) -> None:
    """Require the exact npm coverage profile and its actual cancellation evidence."""
    builder = helper("build_npm")
    scope = builder.verification_scope(npm)
    if builder.verification_scope(native) != scope or builder.verification_scope(report) != scope:
        raise ValueError("npm consumer verification scope differs")
    expected = {"schema": 1, "verdict": "PASS", "mode": mode, "target": target,
                "version": manifest["version"], "source_sha": manifest["source_sha"], "node": npm["node"],
                "npm": npm["npm"], "packages": {row["metadata"]["name"]: row["integrity"] for row in npm["packages"]}}
    consumers = {"global", "npx"} if scope == "smoke" else {"global", "local", "npx"}
    if {key: report.get(key) for key in expected} != expected or set(report["consumers"]) != consumers:
        raise ValueError("npm public consumer identity differs")
    package = next(row["metadata"]["name"] for row in npm["packages"] if row.get("target") == target)
    wanted = {"self_check": 38, "selected_package": package, "atomic_refusal": True,
              "fixtures": [{"path": row["path"], "markdown_sha256": row["markdown_sha256"]} for row in native["oracle"]]}
    for name, consumer in report["consumers"].items():
        if scope == "smoke":
            epub = next(row for row in native["probes"] if row["probe"] == "epub2")
            smoke = {"self_check": 38, "selected_package": package, "fixtures": [],
                     "probes": [{"probe": "epub2", "path": epub["path"], "markdown_sha256": epub["markdown_sha256"]}]
                         if name == "global" else []}
            if {key: consumer.get(key) for key in smoke} != smoke:
                raise ValueError("npm smoke conversion or npx evidence differs")
        elif {key: consumer.get(key) for key in wanted} != wanted or not re.fullmatch(r"[0-9a-f]{64}", consumer["help_sha256"]):
            raise ValueError("npm public install or npx behavior differs")
    if mode == "public":
        signatures = report["signatures"]
        if (signatures["verified_packages"], signatures["verified_attestations"]) != (2, 2) or not re.fullmatch(
                r"[0-9a-f]{64}", signatures["stdout_sha256"]):
            raise ValueError("npm public signature or provenance evidence differs")
    cancellation = report["cancellation"]
    expected_cancellation = {"native_observed": True, "native_terminated": True,
                             "javascript": "unsupported" if target == "windows-x64" else "observed-and-terminated"}
    if target == "windows-x64" and scope == "smoke":
        expected_cancellation.update({"dns_observed": False, "trigger": "offline-html-output", "launcher_exit": "nonzero"})
    if ({key: cancellation.get(key) for key in expected_cancellation} != expected_cancellation
            or type(cancellation.get("dns_observed")) is not bool
            or type(cancellation["owned_processes"]) is not int or cancellation["owned_processes"] < 1):
        raise ValueError("npm public cancellation evidence differs")


def admit_public(folder: Path, manifest: dict, evidence: Path) -> dict:
    """Produce guide facts only after public bytes and all native consumers are bound."""
    release, assets = verify_public(folder, manifest)
    if {path.name for path in evidence.iterdir() if path.is_dir()} != set(TARGETS):
        raise ValueError("Public acceptance requires exactly three native report directories")
    binaries = read_json(folder / "binaries.json")
    npm = read_json(folder / "npm-manifest.json")
    for target in TARGETS:
        native = binaries["targets"][target]
        native_evidence(evidence / target, target, manifest, native, release, assets)
        npm_evidence(evidence / target, target, manifest, native, npm)
    pypi_public(folder, manifest)
    if helper("build_npm").verification_scope(npm) == "smoke":
        expected_registry = {"schema": 1, "verdict": "PASS", "version": manifest["version"],
                             "source_sha": manifest["source_sha"], "verification_scope": "smoke",
                             "packages": {row["metadata"]["name"]: row["integrity"] for row in npm["packages"]}}
        if read_json(evidence / "npm-public-registry.json") != expected_registry:
            raise ValueError("Central public npm byte evidence differs")
    else:
        helper("build_npm").check_public(folder)
    return {"schema": 1, "version": manifest["version"], "source_sha": manifest["source_sha"],
            "pypi": {"published": True}, "github": {"assets": {record["archive"]["filename"]: {
                key: record["archive"][key] for key in ("bytes", "sha256")} for record in binaries["targets"].values()}},
            "npm": {"packages": {row["metadata"]["name"]: {"version": manifest["version"], "integrity": row["integrity"]}
                                  for row in npm["packages"]}},
            "consumers": {target: {"native": "PASS", "npm": "PASS", "npx": "PASS"} for target in TARGETS}}


def finalize(folder: Path, manifest: dict, evidence: Path, body: Path) -> None:
    """Update public notes only with the deterministic guide from fresh accepted evidence."""
    availability = admit_public(folder, manifest, evidence)
    identity(body)
    supplied = body.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="brewdoc-release-guide-") as temporary:
        path = Path(temporary) / "public-acceptance.json"
        write_json(path, availability)
        guide = helper("release_guide")
        expected = guide.render(guide.admit(CHECKOUT, folder / "binaries.json",
                                           folder / "npm-manifest.json", "public", path))
    if supplied != expected:
        raise ValueError("Final release body differs from admitted deterministic guide")
    tag_preflight(manifest)
    command(["gh", "release", "edit", manifest["tag"], "--notes-file", str(body), "--repo", REPOSITORY])
    release, assets = release_state(folder, manifest)
    if release["draft"] or release["body"] != supplied:
        raise ValueError("Final public release body readback differs")
    require_complete(folder, manifest, release, assets)


def main(argv: list[str] | None = None) -> int:
    """Expose only explicit preparation, publication and evidence admission operations."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    preparation = commands.add_parser("prepare", allow_abbrev=False)
    for name in ("checkout", "bundle", "native", "output", "body"):
        preparation.add_argument("--" + name, type=Path, required=True)
    preparation.add_argument("--npm", type=Path, required=True)
    preparation.add_argument("--producer", type=Path)
    preparation.add_argument("--github-output", type=Path)
    inputs = commands.add_parser("record-inputs", allow_abbrev=False)
    inputs.add_argument("--checkout", type=Path, required=True)
    inputs.add_argument("--output", type=Path, required=True)
    reuse = commands.add_parser("reuse-ci", allow_abbrev=False)
    reuse.add_argument("--checkout", type=Path, required=True)
    reuse.add_argument("--output", type=Path, required=True)
    reuse.add_argument("--tag", required=True)
    reuse.add_argument("--source-sha", required=True)
    scope = commands.add_parser("ci-scope", allow_abbrev=False)
    scope.add_argument("--checkout", type=Path, required=True)
    scope.add_argument("--base", required=True)
    scope.add_argument("--head", required=True)
    scope.add_argument("--github-output", type=Path, required=True)
    for operation in ("stage", "publish", "verify-public", "download-target", "admit-public", "finalize"):
        subparser = commands.add_parser(operation, allow_abbrev=False)
        subparser.add_argument("--input", type=Path, required=True)
        subparser.add_argument("--tag", required=True)
        subparser.add_argument("--source-sha", required=True)
        if operation == "download-target":
            subparser.add_argument("--target", choices=TARGETS, required=True)
            subparser.add_argument("--output", type=Path, required=True)
        if operation in ("admit-public", "finalize"):
            subparser.add_argument("--evidence", type=Path, required=True)
        if operation == "admit-public":
            subparser.add_argument("--output", type=Path, required=True)
        if operation == "finalize":
            subparser.add_argument("--body", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.operation == "record-inputs":
        source_sha = command(["git", "-C", str(arguments.checkout), "rev-parse", "HEAD"]).decode().strip()
        write_json(arguments.output, source_inputs(arguments.checkout, source_sha))
        return 0
    if arguments.operation == "reuse-ci":
        reuse_ci(arguments.checkout, arguments.output, arguments.tag, arguments.source_sha)
        return 0
    if arguments.operation == "ci-scope":
        selected = True
        if all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (arguments.base, arguments.head)):
            try:
                paths = command(["git", "-C", str(arguments.checkout), "diff", "--name-only", "-z",
                                 "--no-renames", arguments.base, arguments.head]).decode().rstrip("\0").split("\0")
                selected = needs_smoke(paths)
            except (ValueError, UnicodeError):
                selected = True
        with arguments.github_output.open("a", encoding="utf-8") as output:
            output.write(f"smoke={'true' if selected else 'false'}\n")
        return 0
    if arguments.operation == "prepare":
        manifest = prepare(arguments.checkout, arguments.bundle, arguments.native, arguments.output, arguments.body,
                           arguments.npm, arguments.producer)
        if arguments.github_output:
            with arguments.github_output.open("a", encoding="utf-8") as output:
                output.write("".join(f"{key}={manifest[key]}\n" for key in ("version", "tag", "source_sha")))
        return 0
    manifest = admit(arguments.input, arguments.tag, arguments.source_sha)
    if arguments.operation == "stage":
        stage(arguments.input, manifest)
    elif arguments.operation == "publish":
        publish(arguments.input, manifest)
    elif arguments.operation == "verify-public":
        verify_public(arguments.input, manifest)
    elif arguments.operation == "download-target":
        download_target(arguments.input, manifest, arguments.target, arguments.output)
    elif arguments.operation == "admit-public":
        write_json(arguments.output, admit_public(arguments.input, manifest, arguments.evidence))
    else:
        finalize(arguments.input, manifest, arguments.evidence, arguments.body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
