"""Pack admitted native bundles and verify npm publication identities."""

from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import platform
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "https://registry.npmjs.org"
NODE_VERSION = "24.21.0"
NPM_VERSION = "12.2.0"
PACKAGES = {"linux-x64": "brewdoc-linux-x64-glibc", "windows-x64": "brewdoc-win32-x64",
            "macos-arm64": "brewdoc-darwin-arm64"}
TARGETS = {"linux-x64": {"os": "linux", "cpu": "x64", "libc": "glibc", "executable": "binary/brewdoc"},
           "windows-x64": {"os": "win32", "cpu": "x64", "executable": "binary/brewdoc.exe"},
           "macos-arm64": {"os": "darwin", "cpu": "arm64", "executable": "binary/brewdoc"}}


def digest(payload: bytes) -> str:
    """Bind bytes to the native and npm inventories."""
    return hashlib.sha256(payload).hexdigest()


def integrity(payload: bytes) -> str:
    """Return npm's SHA-512 subresource integrity string."""
    return "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")


def packed_mode(mode: int, executable: bool = False) -> int:
    """Match pinned npm's portable modes and executable bin normalization."""
    return (mode | (0o111 if executable else 0) | 0o600) & ~0o022


def write_json(path: Path, value: dict) -> None:
    """Write stable evidence outside the Python distribution directory."""
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    """Require an object at a fixed evidence path."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def native_builder():
    """Reuse standalone manifest admission and sealed CLI output checks."""
    spec = importlib.util.spec_from_file_location("npm_native_builder", Path(__file__).with_name("build_standalone.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_version() -> str:
    """Use the existing Python metadata as the sole version source."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("npm release requires an X.Y.Z project version")
    return version


def package_metadata(version: str, target: str | None = None) -> dict:
    """Generate exact dependency and platform metadata without lifecycle scripts."""
    metadata = {"name": PACKAGES[target] if target else "brewdoc", "version": version,
                "description": "Convert documents to deterministic Markdown with Brewdoc.",
                "license": "MIT", "repository": {"type": "git", "url": "git+https://github.com/kochetkov-ma/brewdoc.git"},
                "homepage": "https://github.com/kochetkov-ma/brewdoc#readme",
                "publishConfig": {"access": "public", "registry": REGISTRY}}
    if target:
        metadata.update({key: [value] for key, value in TARGETS[target].items() if key != "executable"})
        metadata["files"] = ["binary/", "native-manifest.json", "native-verification.json"]
    else:
        minimum = read_json(ROOT / ".github/distribution.json")["targets"]["linux-x64"]["minimum_glibc"]
        metadata.update({"bin": {"brewdoc": "bin/brewdoc.cjs"}, "files": ["bin/"],
                         "engines": {"node": ">=24.21.0 <25"},
                         "optionalDependencies": {name: version for name in PACKAGES.values()},
                         "brewdocTargets": {key: {**value, "package": PACKAGES[key],
                             **({"minimumLibc": minimum} if key == "linux-x64" else {})} for key, value in TARGETS.items()}})
    return metadata


def safe_name(name: str) -> str:
    """Reject ambiguous archive and inventory paths on every target."""
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError(f"Unsafe inventory path: {name}")
    if any(":" in part for part in path.parts):
        raise ValueError(f"Unsafe inventory path: {name}")
    return name


def terminal_file(name: str, inventory: dict) -> str:
    """Resolve only admitted relative file links, with a bounded cycle check."""
    visited = set()
    while name in inventory and inventory[name]["kind"] == "symlink":
        if name in visited:
            raise ValueError(f"Native link cycle: {name}")
        visited.add(name)
        target = inventory[name]["target"]
        if "\\" in target or PurePosixPath(target).is_absolute():
            raise ValueError(f"Unsafe native link: {name}")
        name = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        safe_name(name)
        if not name.startswith("brewdoc/"):
            raise ValueError("Native link leaves bundle")
    if name not in inventory or inventory[name]["kind"] != "file":
        raise ValueError(f"Native link terminal is not an admitted file: {name}")
    return name


def stage_native(native: Path, inventory: dict, output: Path) -> dict:
    """Validate the full native tree before materializing npm-only file aliases."""
    if native.is_symlink() or not native.is_dir() or output.exists():
        raise ValueError("Native input or npm staging directory is invalid")
    actual = {"brewdoc/" + path.relative_to(native).as_posix(): path for path in native.rglob("*")}
    if "brewdoc" in inventory:
        actual["brewdoc"] = native
    if set(actual) != set(inventory):
        raise ValueError("Native inventory differs from source tree")
    if len({name.casefold() for name in inventory}) != len(inventory):
        raise ValueError("Native inventory has case-colliding paths")
    planned = {}
    mappings = {}
    for name, entry in sorted(inventory.items()):
        safe_name(name)
        if name != "brewdoc" and not name.startswith("brewdoc/"):
            raise ValueError("Native inventory path leaves bundle")
        path = actual[name]
        observed = path.lstat()
        kind = entry["kind"]
        if kind == "directory" and stat.S_ISDIR(observed.st_mode):
            continue
        if kind == "symlink" and path.is_symlink() and os.readlink(path) == entry["target"]:
            terminal = terminal_file(name, inventory)
            source = actual[terminal]
            expected = inventory[terminal]
            relative = "binary/" + name.removeprefix("brewdoc/")
            mappings[relative] = {"link": entry["target"], "terminal": terminal,
                                  "sha256": expected["sha256"], "mode": expected["mode"],
                                  "packed_mode": packed_mode(expected["mode"])}
        elif kind == "file" and stat.S_ISREG(observed.st_mode):
            source, expected = path, entry
        else:
            raise ValueError(f"Native inventory kind changed: {name}")
        if source.is_symlink() or not source.resolve().is_relative_to(native.resolve()):
            raise ValueError(f"Native file path escapes: {name}")
        mode = source.stat().st_mode & 0o777
        payload = source.read_bytes()
        if (len(payload), digest(payload), mode) != (expected["bytes"], expected["sha256"], expected["mode"]):
            raise ValueError(f"Native file inventory changed: {name}")
        planned[name.removeprefix("brewdoc/")] = (source, expected)
    output.mkdir()
    for name, (source, expected) in planned.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(expected["mode"])
    for name, (_, expected) in planned.items():
        path = output / name
        if (path.stat().st_size, digest(path.read_bytes()), path.stat().st_mode & 0o777) != (
                expected["bytes"], expected["sha256"], expected["mode"]):
            raise ValueError(f"npm staging identity changed: {name}")
    return mappings


def isolated_environment(folder: Path) -> dict[str, str]:
    """Keep read/install tools away from private npm configuration and tokens."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.lower().startswith("npm_") and key not in {"NODE_AUTH_TOKEN", "NPM_TOKEN", "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME"}}
    folder.mkdir(parents=True, exist_ok=True)
    user_config, global_config = folder / "user.npmrc", folder / "global.npmrc"
    user_config.touch()
    global_config.touch()
    environment.update({"HOME": str(folder), "USERPROFILE": str(folder),
                        "NPM_CONFIG_USERCONFIG": str(user_config), "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                        "NPM_CONFIG_CACHE": str(folder / "cache"), "NPM_CONFIG_AUDIT": "false", "NPM_CONFIG_FUND": "false",
                        "NPM_CONFIG_LOGLEVEL": "silent"})
    return environment


def npm_command(arguments: list[str], npx: bool = False) -> list[str]:
    """Invoke npm through Node, avoiding Windows shell interpolation."""
    node = shutil.which("node")
    if not node:
        raise ValueError("Pinned Node is unavailable")
    executable = Path(node).resolve()
    candidates = [executable.parent / "node_modules/npm/bin/npm-cli.js",
                  executable.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"]
    npm = next((path for path in candidates if path.is_file()), None)
    if npm is None:
        command = shutil.which("npm")
        npm = Path(command).resolve() if command and not command.endswith(".cmd") else None
    if npm is None:
        raise ValueError("Pinned npm CLI is unavailable")
    if npx:
        npm = npm.with_name("npx-cli.js")
    return [str(executable), str(npm), *arguments]


def run_npm(arguments: list[str], cwd: Path, environment: dict, timeout: int = 180,
            npx: bool = False) -> subprocess.CompletedProcess:
    """Bound npm tool calls and keep failure output separate from receipt stdout."""
    result = subprocess.run(npm_command(arguments, npx), cwd=cwd, env=environment, capture_output=True, timeout=timeout)
    if result.returncode:
        if arguments[0] == "publish":
            raise RuntimeError(f"npm publish failed ({result.returncode}); authenticated output was suppressed")
        raise RuntimeError(f"npm command failed ({result.returncode}): {arguments[0]}\n"
                           + (result.stdout + result.stderr).decode("utf-8", errors="replace"))
    return result


def check_tools(environment: dict, cwd: Path) -> None:
    """Require the exact reviewed Node and npm tool versions."""
    node = subprocess.run([shutil.which("node"), "--version"], env=environment, capture_output=True, check=True)
    npm = run_npm(["--version"], cwd, environment)
    if (node.stdout.strip(), npm.stdout.strip()) != (f"v{NODE_VERSION}".encode(), NPM_VERSION.encode()):
        raise ValueError(f"npm tooling must be Node {NODE_VERSION} and npm {NPM_VERSION}")


def file_inventory(folder: Path) -> dict:
    """Record every package file before packing, including native licenses."""
    bins = set(read_json(folder / "package.json").get("bin", {}).values())
    return {path.relative_to(folder).as_posix(): {"bytes": path.stat().st_size,
            "sha256": digest(path.read_bytes()), "mode": packed_mode(path.stat().st_mode & 0o777,
                                       path.relative_to(folder).as_posix() in bins)}
            for path in sorted(folder.rglob("*")) if path.is_file()}


def verify_tarball(path: Path, record: dict) -> None:
    """Check the actual npm archive's complete file list, bytes and modes."""
    payload = path.read_bytes()
    if (len(payload), digest(payload), integrity(payload)) != (record["bytes"], record["sha256"], record["integrity"]):
        raise ValueError(f"npm tarball identity changed: {path.name}")
    observed = {}
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            safe_name(member.name)
            if not member.name.startswith("package/") or not member.isfile():
                raise ValueError("npm archive contains an unsupported member")
            name = member.name.removeprefix("package/")
            if name in observed:
                raise ValueError("npm archive contains duplicate paths")
            body = archive.extractfile(member).read()
            observed[name] = {"bytes": len(body), "sha256": digest(body), "mode": member.mode & 0o777}
        metadata = json.loads(archive.extractfile("package/package.json").read())
        if "target" in record:
            native = json.loads(archive.extractfile("package/native-manifest.json").read())
            verified = json.loads(archive.extractfile("package/native-verification.json").read())
            native_builder().validate_manifest(native)
            if (native["target"], native["version"], native["source_sha"], native["archive"]) != (
                    record["target"], record["version"], record["source_sha"], record["native_archive"]):
                raise ValueError("npm package native source binding differs")
            if (verified["verdict"], verified["target"], verified["version"], verified["source_sha"], verified["archive_sha256"]) != (
                    "PASS", native["target"], native["version"], native["source_sha"], native["archive"]["sha256"]):
                raise ValueError("npm native verification does not match its source")
            binary_files, mappings = {}, {}
            for name, entry in native["inventory"].items():
                if entry["kind"] == "directory":
                    continue
                terminal = terminal_file(name, native["inventory"]) if entry["kind"] == "symlink" else name
                identity = native["inventory"][terminal]
                destination = "binary/" + name.removeprefix("brewdoc/")
                binary_files[destination] = {"bytes": identity["bytes"], "sha256": identity["sha256"],
                                             "mode": packed_mode(identity["mode"])}
                if entry["kind"] == "symlink":
                    mappings[destination] = {"link": entry["target"], "terminal": terminal,
                                             "sha256": identity["sha256"], "mode": identity["mode"],
                                             "packed_mode": packed_mode(identity["mode"])}
            if ({name: value for name, value in observed.items() if name.startswith("binary/")} != binary_files
                    or record["links"] != mappings):
                raise ValueError("npm native files or materialized link identities differ")
    if observed != record["files"] or metadata != record["metadata"]:
        raise ValueError("npm archive contents, modes or metadata changed")


def pack_directory(folder: Path, output: Path, extra: dict) -> dict:
    """Pack an allowlisted staging tree and independently reopen the resulting bytes."""
    with tempfile.TemporaryDirectory(prefix="brewdoc-npm-config-") as temporary:
        environment = isolated_environment(Path(temporary))
        check_tools(environment, folder)
        result = run_npm(["pack", "--ignore-scripts", "--json", "--pack-destination", str(output)], folder, environment)
    packed = json.loads(result.stdout)
    name = read_json(folder / "package.json")["name"]
    if not isinstance(packed, dict) or set(packed) != {name}:
        raise ValueError("npm pack did not return exactly one archive")
    filename = packed[name]["filename"]
    if Path(filename).name != filename:
        raise ValueError("npm pack returned an unsafe filename")
    path = output / filename
    payload = path.read_bytes()
    record = {**extra, "filename": filename, "bytes": len(payload), "sha256": digest(payload),
              "integrity": integrity(payload), "metadata": read_json(folder / "package.json"), "files": file_inventory(folder)}
    if packed[name]["integrity"] != record["integrity"]:
        raise ValueError("npm pack integrity differs from archive bytes")
    verify_tarball(path, record)
    return record


def write_package(folder: Path, metadata: dict) -> None:
    """Generate public package files from existing repository license and guidance."""
    folder.mkdir(parents=True)
    write_json(folder / "package.json", metadata)
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE")
    shutil.copyfile(ROOT / "README.md", folder / "README.md")


def pack_native(native_dir: Path, output: Path) -> None:
    """Pack one already verified native target without changing its archive."""
    manifest = native_builder().admit_manifest(native_dir / "manifest.json")
    verification = read_json(native_dir / "verification.json")
    target, version = manifest["target"], manifest["version"]
    if target not in PACKAGES or version != release_version() or manifest["schema"] != 1:
        raise ValueError("Native manifest target, version or schema differs")
    archive = manifest["archive"]
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="brewdoc-npm-stage-") as temporary:
        folder = Path(temporary) / "package"
        write_package(folder, package_metadata(version, target))
        mappings = stage_native(native_dir / "bundle/brewdoc", manifest["inventory"], folder / "binary")
        write_json(folder / "native-manifest.json", manifest)
        write_json(folder / "native-verification.json", verification)
        record = pack_directory(folder, output, {"target": target, "version": version, "source_sha": manifest["source_sha"],
                                "native_archive": archive, "links": mappings})
    write_json(output / "platform-record.json", record)


def aggregate(input_dir: Path, output: Path) -> None:
    """Admit the complete platform set before creating the main CLI package."""
    paths = sorted(input_dir.rglob("platform-record.json"))
    records = {read_json(path)["target"]: (path, read_json(path)) for path in paths}
    if len(paths) != 3 or set(records) != set(PACKAGES):
        raise ValueError("npm aggregation requires exactly three platform records")
    version = release_version()
    identities = {(record["version"], record["source_sha"]) for _, record in records.values()}
    if len(identities) != 1 or next(iter(identities))[0] != version:
        raise ValueError("npm target versions or sources differ")
    output.mkdir(parents=True, exist_ok=False)
    packages = []
    for target in PACKAGES:
        path, record = records[target]
        if record["metadata"] != package_metadata(version, target):
            raise ValueError("Platform package metadata differs")
        source = path.parent / safe_name(record["filename"])
        verify_tarball(source, record)
        shutil.copyfile(source, output / source.name)
        packages.append(record)
    with tempfile.TemporaryDirectory(prefix="brewdoc-npm-main-") as temporary:
        folder = Path(temporary) / "package"
        write_package(folder, package_metadata(version))
        (folder / "bin").mkdir()
        shutil.copyfile(ROOT / "npm/bin/brewdoc.cjs", folder / "bin/brewdoc.cjs")
        (folder / "bin/brewdoc.cjs").chmod(0o755)
        packages.append(pack_directory(folder, output, {"version": version, "source_sha": packages[0]["source_sha"]}))
    write_json(output / "npm-manifest.json", {"schema": 1, "version": version, "source_sha": packages[0]["source_sha"],
               "node": NODE_VERSION, "npm": NPM_VERSION, "packages": packages})


def verify_release(input_dir: Path) -> dict:
    """Admit exactly four npm package archives with one version and source identity."""
    manifest = read_json(input_dir / "npm-manifest.json")
    version = manifest["version"]
    expected_names = [*PACKAGES.values(), "brewdoc"]
    records = manifest["packages"]
    if (manifest["schema"], manifest["node"], manifest["npm"], version) != (1, NODE_VERSION, NPM_VERSION, release_version()):
        raise ValueError("npm release metadata differs from the reviewed source")
    if [record["metadata"]["name"] for record in records] != expected_names:
        raise ValueError("npm publication order or package set differs")
    for record in records:
        target = record.get("target")
        if record["metadata"] != package_metadata(version, target) or (record["version"], record["source_sha"]) != (version, manifest["source_sha"]):
            raise ValueError("npm package source, version or metadata differs")
        verify_tarball(input_dir / safe_name(record["filename"]), record)
    if {path.name for path in input_dir.glob("*.tgz")} != {record["filename"] for record in records}:
        raise ValueError("npm release has missing or extra tarballs")
    return manifest


def public_record(record: dict) -> bool:
    """Accept an existing version only after registry metadata and actual bytes agree."""
    metadata = record["metadata"]
    url = f"{REGISTRY}/{metadata['name']}/{metadata['version']}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            public = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    preserved = {"name", "version", "description", "license", "repository", "homepage", "bin", "engines",
                 "optionalDependencies", "os", "cpu", "libc"}
    if any(public.get(key) != metadata[key] for key in preserved & metadata.keys()):
        raise ValueError("Existing npm version metadata differs")
    location = urllib.parse.urlparse(public["dist"]["tarball"])
    if location.scheme != "https" or location.hostname != "registry.npmjs.org" or location.username or location.password:
        raise ValueError("Existing npm tarball URL is outside the public registry")
    with urllib.request.urlopen(location.geturl(), timeout=60) as response:
        payload = response.read(record["bytes"] + 1)
    if (len(payload), digest(payload), integrity(payload), public["dist"]["integrity"]) != (
            record["bytes"], record["sha256"], record["integrity"], record["integrity"]):
        raise ValueError("Existing npm version integrity differs")
    return True


def publish(input_dir: Path) -> None:
    """Publish verified platform archives first, with exact identity-only retry admission."""
    manifest = verify_release(input_dir)
    with tempfile.TemporaryDirectory(prefix="brewdoc-npm-version-") as temporary:
        check_tools(isolated_environment(Path(temporary)), input_dir)
    existing = [public_record(record) for record in manifest["packages"]]
    for record, admitted in zip(manifest["packages"], existing, strict=True):
        if not admitted:
            run_npm(["publish", str((input_dir / record["filename"]).resolve()), "--ignore-scripts", "--access", "public", "--provenance"],
                    input_dir, os.environ.copy())
        if not public_record(record):
            raise ValueError("Published npm version is absent from the registry")


def check_public(input_dir: Path) -> None:
    """Require every public version and tarball to match the admitted release."""
    for record in verify_release(input_dir)["packages"]:
        if not public_record(record):
            raise ValueError(f"Public npm version is missing: {record['metadata']['name']}")


def local_registry(input_dir: Path, manifest: dict) -> ThreadingHTTPServer:
    """Serve only the four admitted packages for real offline npm resolution."""
    records = {record["metadata"]["name"]: record for record in manifest["packages"]}

    class Handler(BaseHTTPRequestHandler):
        """Provide package metadata and exact tarball bytes without install hooks."""

        def log_message(self, _format, *arguments):
            """Keep npm test transport away from production receipt streams."""

        def do_GET(self):
            """Return only a selected metadata or tarball endpoint."""
            parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
            record = records.get(parts[0])
            if record is None:
                self.send_error(404)
                return
            if len(parts) == 3 and parts[1:] == ["-", record["filename"]]:
                payload = (input_dir / record["filename"]).read_bytes()
            elif len(parts) == 1 or parts[1:] == [manifest["version"]]:
                url = f"http://127.0.0.1:{self.server.server_port}/{parts[0]}/-/{record['filename']}"
                metadata = {**record["metadata"], "dist": {"tarball": url, "integrity": record["integrity"]}}
                value = metadata if len(parts) == 2 else {"name": parts[0],
                        "versions": {manifest["version"]: metadata}, "dist-tags": {"latest": manifest["version"]}}
                payload = json.dumps(value).encode("utf-8")
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream" if len(parts) == 3 else "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def installed_identity(folder: Path, record: dict, dependencies: bool = False) -> None:
    """Check the installed package's complete admitted file bytes."""
    observed = {path.relative_to(folder).as_posix() for path in folder.rglob("*")
                if path.is_file() and not (dependencies and "node_modules" in path.relative_to(folder).parts)}
    if observed != set(record["files"]):
        raise ValueError("Installed npm package file inventory differs")
    for name, expected in record["files"].items():
        path = folder / name
        if path.is_symlink() or (path.stat().st_size, digest(path.read_bytes())) != (expected["bytes"], expected["sha256"]):
            raise ValueError("Installed npm package bytes differ")


def cli_result(arguments: list[str], suffix: list[str], work: Path, environment: dict,
               timeout: int = 90) -> subprocess.CompletedProcess:
    """Exercise npm's installed bin links or Windows command shims with inherited bytes."""
    command = [*arguments, *suffix]
    if os.name == "nt" and command[0].endswith(".cmd"):
        command = [os.environ["COMSPEC"], "/d", "/s", "/c", '"' + subprocess.list2cmdline(command) + '"']
    return subprocess.run(command, cwd=work, env=environment, capture_output=True, timeout=timeout)


def consumer_probe(arguments: list[str], work: Path, environment: dict, native: dict,
                   selected_package: str) -> dict:
    """Check installed CLI outputs against the same sealed native oracle."""
    helper = native_builder()
    check = cli_result(arguments, ["--self-check"], work, environment, 45)
    if (check.returncode, check.stdout, check.stderr) != (0, ("self-check: ok (38 checks)" + os.linesep).encode("ascii"), b""):
        raise ValueError("npm consumer self-check changed")
    help_result = cli_result(arguments, ["--help"], work, environment, 45)
    if help_result.returncode != 0 or help_result.stderr or not help_result.stdout.startswith(b"usage: brewdoc"):
        raise ValueError("npm consumer help changed")
    fixtures = []
    for row in native["oracle"]:
        source = ROOT / row["path"]
        if digest(source.read_bytes()) != row["source_sha256"]:
            raise ValueError("npm consumer fixture identity changed")
        copied = work / source.name
        shutil.copyfile(source, copied)
        result = cli_result(arguments, [str(copied)], work, environment)
        helper.check_output(result, row)
        fixtures.append({"path": row["path"], "markdown_sha256": row["markdown_sha256"]})
    malformed, destination = work / "malformed.epub", work / "atomic refusal.md"
    malformed.write_bytes(b"not an EPUB ZIP")
    destination.write_bytes(b"preserve existing output\n")
    result = cli_result(arguments, [str(malformed), "--out", str(destination)], work, environment, 45)
    receipt = json.loads(result.stdout)
    if (result.returncode, receipt["file_ok"], receipt["units"], destination.read_bytes(), result.stderr) != (
            1, False, 0, b"preserve existing output\n", b""):
        raise ValueError("npm consumer changed atomic malformed refusal")
    return {"self_check": 38, "selected_package": selected_package, "fixtures": fixtures,
            "atomic_refusal": True, "help_sha256": digest(help_result.stdout)}


def process_snapshot() -> dict:
    """Read process identities without persisting unrelated process commands."""
    if os.name == "nt":
        result = subprocess.run([shutil.which("powershell"), "-NoProfile", "-Command",
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine) | ConvertTo-Json -Compress"],
            capture_output=True, check=True, timeout=10)
        return {int(row["ProcessId"]): {"parent": int(row["ParentProcessId"]),
                "start": row["CreationDate"], "command": row["CommandLine"] or ""}
                for row in json.loads(result.stdout)}
    result = subprocess.run([shutil.which("ps"), "-axo", "pid=,ppid=,lstart=,command="],
                            capture_output=True, check=True, timeout=5)
    rows = {}
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        fields = line.split(maxsplit=7)
        if len(fields) == 8:
            rows[int(fields[0])] = {"parent": int(fields[1]), "start": " ".join(fields[2:7]), "command": fields[7]}
    return rows


def cancellation_probe(launcher: Path, work: Path, environment: dict, javascript: bool) -> dict:
    """Observe owned native and separate-session JS children before cancellation."""
    arguments = [npm_command([])[0], str(launcher), "--url", "https://example.org/", "--timeout", "30"]
    if javascript:
        arguments.append("--render-js")
    child = subprocess.Popen(arguments, cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    observed = {}
    try:
        deadline = time.monotonic() + 35
        ready = False
        while child.poll() is None and time.monotonic() < deadline:
            rows = process_snapshot()
            owned = {child.pid}
            previous = set()
            while owned != previous:
                previous = owned.copy()
                owned.update(pid for pid, row in rows.items() if row["parent"] in owned)
            observed.update({pid: rows[pid] for pid in owned - {child.pid} if pid in rows})
            if observed and (not javascript or any("--brewdoc-internal js " in row["command"] for row in observed.values())):
                ready = True
                break
            time.sleep(0.02)
        if not ready:
            raise ValueError("npm native/JS child was not observed before cancellation")
        child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        child.communicate(timeout=10)
        deadline = time.monotonic() + 5
        surviving = observed
        while surviving and time.monotonic() < deadline:
            rows = process_snapshot()
            surviving = {pid: row for pid, row in observed.items() if pid in rows
                         and (rows[pid]["start"], rows[pid]["command"]) == (row["start"], row["command"])}
            if surviving:
                time.sleep(0.02)
        if surviving:
            raise ValueError("Owned native or separate-session JS child survived npm cancellation")
        if os.name != "nt" and child.returncode != -signal.SIGTERM:
            raise ValueError("npm launcher did not preserve SIGTERM status")
        return {"native_observed": True, "native_terminated": True,
                "javascript": "observed-and-terminated" if javascript else "unsupported",
                "dns_observed": any("--brewdoc-internal dns " in row["command"] for row in observed.values()),
                "owned_processes": len(observed)}
    finally:
        if child.poll() is None:
            child.terminate()
            child.communicate(timeout=10)
        rows = process_snapshot()
        for pid, row in observed.items():
            if pid in rows and (rows[pid]["start"], rows[pid]["command"]) == (row["start"], row["command"]):
                os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)


def probe(input_dir: Path, target: str, mode: str, output: Path) -> None:
    """Prove npm global/local and actual npx consumers outside the checkout."""
    manifest = verify_release(input_dir)
    selected = next(record for record in manifest["packages"] if record.get("target") == target)
    system, machine = platform.system(), platform.machine().lower()
    expected = {"linux-x64": ("Linux", "x86_64"), "windows-x64": ("Windows", "amd64"),
                "macos-arm64": ("Darwin", "arm64")}
    if (system, machine) != expected[target]:
        raise ValueError("npm consumer must run on its matching native target")
    if mode == "public":
        check_public(input_dir)
    server = local_registry(input_dir, manifest) if mode == "local" else None
    thread = threading.Thread(target=server.serve_forever, daemon=True) if server else None
    if thread:
        thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="brewdoc npm consumer Unicode-é ") as temporary:
            work = Path(temporary).resolve()
            environment = isolated_environment(work / "config")
            check_tools(environment, work)
            environment["NPM_CONFIG_REGISTRY"] = f"http://127.0.0.1:{server.server_port}" if server else REGISTRY
            node = npm_command([])[0]
            environment["PATH"] = str(Path(node).parent)
            if os.name != "nt":
                environment["NPM_CONFIG_SCRIPT_SHELL"] = str(Path(shutil.which("sh")).resolve())
            package = f"brewdoc@{manifest['version']}"
            global_prefix, local_prefix = work / "global", work / "local"
            run_npm(["install", "--global", "--prefix", str(global_prefix), "--ignore-scripts", package], work, environment)
            local_prefix.mkdir()
            write_json(local_prefix / "package.json", {"name": "brewdoc-consumer-proof", "version": "1.0.0", "private": True})
            run_npm(["install", "--prefix", str(local_prefix), "--ignore-scripts", "--save-exact", package], work, environment)
            run_npm(["--yes", f"--package={package}", "brewdoc", "--self-check"], work, environment, npx=True)
            npx_packages = list((work / "config/cache/_npx").glob("*/node_modules/brewdoc"))
            if len(npx_packages) != 1:
                raise ValueError("npx did not install exactly one isolated main package")
            global_modules = global_prefix / ("node_modules" if os.name == "nt" else "lib/node_modules")
            packages = {"global": global_modules / "brewdoc", "local": local_prefix / "node_modules/brewdoc", "npx": npx_packages[0]}
            consumers = {}
            for name, folder in packages.items():
                installed_identity(folder, manifest["packages"][-1], dependencies=True)
                resolution = subprocess.run([node, "-e", "process.stdout.write(require.resolve(process.argv[1],{paths:[process.argv[2]]}))",
                         selected["metadata"]["name"] + "/package.json", str(folder)], env=environment, capture_output=True, timeout=10, check=True)
                platform_package = Path(resolution.stdout.decode("utf-8")).parent
                if not platform_package.resolve().is_relative_to(work):
                    raise ValueError("npm resolved a native package outside the isolated installation")
                installed_identity(platform_package, selected)
                native = read_json(platform_package / "native-manifest.json")
                if (native["source_sha"], native["version"], native["target"], native["archive"]) != (
                        manifest["source_sha"], manifest["version"], target, selected["native_archive"]):
                    raise ValueError("Installed npm native source identity differs")
                bin_paths = {"global": global_prefix / ("brewdoc.cmd" if os.name == "nt" else "bin/brewdoc"),
                             "local": local_prefix / "node_modules/.bin" / ("brewdoc.cmd" if os.name == "nt" else "brewdoc")}
                arguments = [str(bin_paths[name])] if name != "npx" else []
                if name == "npx":
                    arguments = npm_command(["--yes", f"--package={package}", "brewdoc"], npx=True)
                consumers[name] = consumer_probe(arguments, work, environment, native, selected["metadata"]["name"])
            cancellation = cancellation_probe(packages["local"] / "bin/brewdoc.cjs", work, environment, target != "windows-x64")
            signatures = None
            if mode == "public":
                verified = run_npm(["audit", "signatures"], local_prefix, environment)
                report = verified.stdout.decode("utf-8")
                if not re.search(r"2 packages? (?:have |has )?verified registry signatures", report) or not re.search(r"2 packages? (?:have |has )?verified attestations", report):
                    raise ValueError("npm registry signatures or provenance attestations are incomplete")
                signatures = {"verified_packages": 2, "verified_attestations": 2, "stdout_sha256": digest(verified.stdout)}
            write_json(output, {"schema": 1, "verdict": "PASS", "mode": mode, "target": target,
                       "version": manifest["version"], "source_sha": manifest["source_sha"],
                       "node": NODE_VERSION, "npm": NPM_VERSION,
                       "packages": {record["metadata"]["name"]: record["integrity"] for record in manifest["packages"]},
                       "consumers": consumers, "signatures": signatures, "cancellation": cancellation})
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def main() -> None:
    """Dispatch only bounded npm distribution operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["pack", "aggregate", "verify", "publish", "check-public", "probe"])
    parser.add_argument("--native-dir", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--target", choices=list(PACKAGES))
    parser.add_argument("--mode", choices=["local", "public"], default="local")
    args = parser.parse_args()
    required = {"pack": ("native_dir", "output"), "aggregate": ("input", "output"),
                "probe": ("input", "target", "output")}.get(args.operation, ("input",))
    for name in required:
        if getattr(args, name) is None:
            parser.error(f"{args.operation} requires --{name.replace('_', '-')}")
    if args.operation == "pack":
        pack_native(args.native_dir.resolve(), args.output.resolve())
    elif args.operation == "aggregate":
        aggregate(args.input.resolve(), args.output.resolve())
    elif args.operation == "verify":
        verify_release(args.input.resolve())
    elif args.operation == "publish":
        publish(args.input.resolve())
    elif args.operation == "check-public":
        check_public(args.input.resolve())
    else:
        probe(args.input.resolve(), args.target, args.mode, args.output.resolve())


if __name__ == "__main__":
    main()
