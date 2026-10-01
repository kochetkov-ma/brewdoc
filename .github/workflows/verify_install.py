"""Check built distributions and their independent installations for CI and release."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"}
PRIVATE_PARTS = {".codex", ".agents", ".claude", ".venv", "docs", "AGENTS.md", "CLAUDE.md"}


def digest(path: Path) -> str:
    """Identify file bytes independently of their location."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_environment() -> dict[str, str]:
    """Remove Python and project-environment overrides from installation subprocesses."""
    return {key: value for key, value in os.environ.items() if key not in {
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON"}}


def command(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a bounded check with isolated environment selection and retain failure output."""
    result = subprocess.run(argv, cwd=cwd, env=clean_environment(), capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv}\n"
                           + (result.stdout + result.stderr).decode("utf-8", errors="replace"))
    return result


def distributions(folder: Path) -> tuple[Path, Path]:
    """Require exactly one wheel and one sdist, without extra publisher inputs."""
    wheels = sorted(folder.glob("*.whl"))
    sources = sorted(folder.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1 or set(folder.iterdir()) != set(wheels + sources):
        raise ValueError("dist must contain exactly one wheel and one .tar.gz sdist")
    return wheels[0], sources[0]


def verify_manifest(bundle: Path, manifest: dict) -> None:
    """Reject altered bytes, path escapes and symlink substitutions before installation."""
    bundle = bundle.resolve()
    for name, expected in manifest["files"].items():
        source = bundle / name
        path = source.resolve()
        if source.is_symlink() or not path.is_relative_to(bundle) or not path.is_file():
            raise ValueError(f"Artifact file escapes or is missing: {name}")
        actual = {"size": path.stat().st_size, "sha256": digest(path)}
        if actual != expected:
            raise ValueError(f"Artifact bytes changed: {name}")


def prepare(checkout: Path, bundle: Path) -> None:
    """Inspect archives and export locked runtime and isolated build constraints."""
    project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))
    wheel, sdist = distributions(bundle / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        tree = ast.parse(archive.read("brewdoc/__init__.py"))
    with tarfile.open(sdist) as archive:
        names += archive.getnames()
    if any(PRIVATE_PARTS.intersection(Path(name).parts) for name in names):
        raise ValueError("Distribution includes private or local project files")
    exports = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    metadata = bundle / "ci-package"
    metadata.mkdir(parents=True, exist_ok=False)
    runtime = metadata / "runtime-requirements.txt"
    command(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
             "--output-file", str(runtime)], checkout)
    build = metadata / "build-constraints.txt"
    constraints = project["build-system"]["requires"] + project["tool"]["uv"]["build-constraint-dependencies"]
    build.write_text("\n".join(constraints) + "\n", encoding="utf-8")
    manifest = {"version": project["project"]["version"], "exports": exports,
                "uv": command(["uv", "--version"], checkout).stdout.decode().split()[1],
                "checkout_sha": command(["git", "rev-parse", "HEAD"], checkout).stdout.decode().strip(),
                "action_cache_hit": os.environ.get("BREWDOC_CACHE_HIT"),
                "action_cache_key": os.environ.get("BREWDOC_CACHE_KEY"),
                "source": {name: digest(checkout / name) for name in ("pyproject.toml", "uv.lock")},
                "files": {path.relative_to(bundle).as_posix(): {
                    "size": path.stat().st_size, "sha256": digest(path)}
                    for path in (wheel, sdist, runtime, build)}}
    (metadata / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def check_origin(module: Path, prefix: Path, checkout: Path, search_paths: list[str], direct_url: dict) -> None:
    """Require an installed module and reject checkout search paths or editable metadata."""
    module, prefix, checkout = module.resolve(), prefix.resolve(), checkout.resolve()
    if (not module.is_relative_to(prefix) or module.is_relative_to(checkout)
            or any(Path(path).resolve().is_relative_to(checkout) for path in search_paths)
            or direct_url.get("dir_info", {}).get("editable", False)):
        raise ValueError("Installed package leaked checkout or editable state")


def check_cli(result: subprocess.CompletedProcess, expected: tuple[int, dict, str], output: Path | None = None) -> dict:
    """Require the complete receipt line and stdout/file Markdown contract."""
    code, receipt, markdown = expected
    body = markdown.encode("utf-8" if receipt["route"] == "html" else "ascii")
    line = (json.dumps(receipt, ensure_ascii=True, sort_keys=True) + os.linesep).encode("ascii")
    stdout_body = body if receipt["route"] == "html" else markdown.replace("\n", os.linesep).encode("ascii")
    wanted = line + (stdout_body if output is None and code == 0 else b"")
    if (result.returncode, result.stdout, result.stderr) != (code, wanted, b""):
        raise ValueError("CLI exit, receipt, Markdown placement or stderr changed")
    if output is not None and output.read_bytes() != body:
        raise ValueError("CLI output bytes changed")
    return {"exit": result.returncode, "stdout": result.stdout.decode("utf-8"),
            "stderr": result.stderr.decode("utf-8")}


def select_inputs(checkout: Path) -> list[tuple[dict, dict]]:
    """Select the smallest indexed success per suffix, including a real VBA carrier."""
    receipts = json.loads((checkout / "tests/fixtures/receipts.json").read_text(encoding="utf-8"))
    documents = json.loads((checkout / "benchmarks/corpus.json").read_text(encoding="utf-8"))["documents"]
    chosen = {}
    for document in sorted(documents, key=lambda item: (item["size_bytes"], item["id"])):
        path = Path(document["path"])
        suffix = path.suffix.lower()
        expected = receipts[path.relative_to("tests/fixtures").as_posix()]
        if suffix == ".xlsm" and not expected.get("artifacts"):
            continue
        if suffix == ".xlsx" and "sheet-order" not in document["features"]:
            continue
        if suffix in SUFFIXES and suffix not in chosen and expected["rc"] == 0:
            chosen[suffix] = (document, expected)
    if set(chosen) != SUFFIXES:
        raise ValueError("Indexed installation smoke is missing a supported suffix")
    return [chosen[suffix] for suffix in sorted(chosen)]


def probe(checkout: Path, bundle: Path) -> dict:
    """Exercise real installed imports, every suffix and bounded console/output contracts."""
    import brewdoc

    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    installed = importlib.metadata.distribution("brewdoc")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    check_origin(Path(brewdoc.__file__), Path(sys.prefix), checkout, sys.path, direct)
    if (brewdoc.__version__, installed.version, brewdoc.__all__) != (
            manifest["version"], manifest["version"], manifest["exports"]):
        raise ValueError("Installed version or public exports changed")
    for name in brewdoc.__all__:
        getattr(brewdoc, name)
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    console = str(scripts / ("brewdoc.exe" if os.name == "nt" else "brewdoc"))
    checked = command([console, "--self-check"], Path.cwd())
    if (checked.stdout, checked.stderr) != (("self-check: ok (34 checks)" + os.linesep).encode(), b""):
        raise ValueError("Installed console self-check verdict changed")
    routes = []
    cli = []
    artifacts = []
    for document, snapshot in select_inputs(checkout):
        source = checkout / document["path"]
        if (source.stat().st_size, digest(source)) != (document["size_bytes"], document["sha256"]):
            raise ValueError(f"Smoke input identity changed: {source.name}")
        path = Path.cwd() / source.name
        shutil.copyfile(source, path)
        actual = brewdoc.run(path)
        code, receipt, markdown = actual
        if {key: code if key == "rc" else receipt[key] for key in snapshot} != snapshot:
            raise ValueError(f"Installed route receipt changed: {source.name}")
        routes.append({"input": document["path"], "input_sha256": document["sha256"],
                       "receipt": receipt, "markdown": markdown, "markdown_sha256": hashlib.sha256(
                           markdown.encode("utf-8" if path.suffix == ".html" else "ascii")).hexdigest()})
        if path.suffix in {".html", ".xlsx"}:
            cli.append(check_cli(subprocess.run([console, str(path)], capture_output=True, timeout=120), actual))
            out = Path.cwd() / "nested" / (path.name + ".md")
            expected = (code, {**receipt, "out": str(out)}, markdown)
            result = subprocess.run([console, str(path), "--out", str(out)], capture_output=True, timeout=120)
            cli.append(check_cli(result, expected, out))
        if path.suffix == ".xlsx":
            with zipfile.ZipFile(path) as archive:
                sheets = ET.fromstring(archive.read("xl/workbook.xml"))
            names = [node.attrib["name"] for node in sheets.iter()
                     if node.tag == "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"]
            selection = list(reversed(names[:2]))
            if len(selection) != 2:
                raise ValueError("Selection smoke needs two source worksheets")
            selected = brewdoc.run(path, sheets=selection)
            if (selected[0], selected[1]["selected_sheets"]) != (0, selection):
                raise ValueError("Installed caller sheet order changed")
            argv = [console, str(path), "--sheet", selection[0], "--sheet", selection[1]]
            cli.append(check_cli(subprocess.run(argv, capture_output=True, timeout=120), selected))
        if path.suffix == ".xlsm":
            reference = next(item for item in snapshot["artifacts"] if item["kind"] == "vba-project")
            payload = Path.cwd() / "nested" / "vba.bin"
            artifact_result = brewdoc.run(path, artifact_outputs={reference["key"]: payload})
            if (artifact_result[0], payload.stat().st_size, digest(payload)) != (
                    0, reference["byte_size"], reference["sha256"]):
                raise ValueError("Installed VBA artifact bytes changed")
            payload.unlink()
            argv = [console, str(path), "--artifact", f"{reference['key']}={payload}"]
            cli.append(check_cli(subprocess.run(argv, capture_output=True, timeout=120), artifact_result))
            if digest(payload) != reference["sha256"]:
                raise ValueError("Console VBA artifact bytes changed")
            artifacts.append({"receipt": artifact_result[1], "sha256": digest(payload),
                              "bytes_base64": base64.b64encode(payload.read_bytes()).decode("ascii")})
    absent = Path("missing.pdf")
    cli.append(check_cli(subprocess.run([console, str(absent)], capture_output=True, timeout=120), brewdoc.run(absent)))
    unsupported = Path("unsupported.txt")
    cli.append(check_cli(subprocess.run([console, str(unsupported)], capture_output=True, timeout=120), brewdoc.run(unsupported)))
    return {"python": sys.version, "module": str(Path(brewdoc.__file__).resolve()),
            "prefix": sys.prefix, "self_check": checked.stdout.decode(), "routes": routes,
            "cli": cli, "artifacts": artifacts,
            "packages": sorted((item.metadata["Name"], item.version)
                               for item in importlib.metadata.distributions())}


def verify(checkout: Path, bundle: Path) -> None:
    """Install wheel and sdist separately outside checkout, then invoke isolated probes."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    verify_manifest(bundle, manifest)
    if {name: digest(checkout / name) for name in manifest["source"]} != manifest["source"]:
        raise ValueError("Consumer manifests differ from the producer checkout")
    if command(["uv", "--version"], checkout).stdout.decode().split()[1] != manifest["uv"]:
        raise ValueError("Producer and consumer uv versions differ")
    if command(["git", "rev-parse", "HEAD"], checkout).stdout.decode().strip() != manifest["checkout_sha"]:
        raise ValueError("Producer and consumer checkout commits differ")
    results = []
    with tempfile.TemporaryDirectory(prefix="brewdoc-install-") as temporary:
        root = Path(temporary).resolve()
        if root.is_relative_to(checkout.resolve()):
            raise ValueError("Installation directory must be outside checkout")
        for number, distribution in enumerate(distributions(bundle / "dist")):
            prefix = root / f"env-{number}"
            command(["uv", "venv", "--python", sys.executable, str(prefix)], root)
            python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            command(["uv", "pip", "install", "--python", str(python), "--require-hashes",
                     "--requirements", str(bundle / "ci-package/runtime-requirements.txt")], root)
            command(["uv", "pip", "install", "--python", str(python), "--no-deps", "--no-cache",
                     "--build-constraints", str(bundle / "ci-package/build-constraints.txt"),
                     str(distribution)], root)
            work = root / f"work-{number}"
            work.mkdir()
            checked = command([str(python), "-I", str(Path(__file__).resolve()), "probe",
                               "--checkout", str(checkout), "--bundle", str(bundle)], work)
            results.append({"distribution": distribution.name, "sha256": digest(distribution),
                            "probe": json.loads(checked.stdout)})
    report = {"checkout_sha": manifest["checkout_sha"], "uv": manifest["uv"],
              "action_cache_hit": os.environ.get("BREWDOC_CACHE_HIT"),
              "action_cache_key": os.environ.get("BREWDOC_CACHE_KEY"), "installations": results}
    (bundle / "ci-package" / f"verification-{sys.platform}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """Prepare a producer manifest or verify its unchanged installation inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify", "probe"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--bundle", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = {"prepare": prepare, "verify": verify, "probe": probe}[args.mode](
        args.checkout.resolve(), args.bundle.resolve())
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
