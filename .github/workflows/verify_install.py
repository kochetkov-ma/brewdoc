"""Check built distributions and their independent installations for CI and release."""

from __future__ import annotations

import argparse
import ast
import base64
import compileall
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".epub", ".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"}
PRIVATE_PARTS = {".codex", ".agents", ".claude", ".venv", "docs", "AGENTS.md", "CLAUDE.md"}
UNICODE_ROUTES = {"html", "epub"}
EPUB_INPUTS = {"tests/fixtures/epub/epubcheck-minimal.epub",
               "tests/fixtures/epub/standardebooks-herland.epub"}


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
    if manifest.get("verification_scope", "full") not in ("full", "smoke"):
        raise ValueError("Unknown installed verification scope")
    bundle = bundle.resolve()
    for name, expected in manifest["files"].items():
        source = bundle / name
        path = source.resolve()
        if source.is_symlink() or not path.is_relative_to(bundle) or not path.is_file():
            raise ValueError(f"Artifact file escapes or is missing: {name}")
        actual = {"size": path.stat().st_size, "sha256": digest(path)}
        if actual != expected:
            raise ValueError(f"Artifact bytes changed: {name}")


def check_scope(manifest: dict, smoke: bool) -> None:
    """Refuse a requested scope that differs from explicit or legacy producer coverage."""
    if manifest.get("verification_scope", "full") != ("smoke" if smoke else "full"):
        raise ValueError("Requested installed verification scope differs from its producer")


def prepare(checkout: Path, bundle: Path, *, smoke: bool = False) -> None:
    """Inspect archives and export locked runtime and isolated build constraints."""
    project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))
    wheel, sdist = distributions(bundle / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        wheel_epub = "brewdoc/epub.py" in names
        tree = ast.parse(archive.read("brewdoc/__init__.py"))
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        names += [item.name for item in members]
        sdist_epub = [item for item in members
                      if item.name.endswith("/src/brewdoc/epub.py") and item.isfile()]
    if any(PRIVATE_PARTS.intersection(Path(name).parts) for name in names):
        raise ValueError("Distribution includes private or local project files")
    if not wheel_epub or len(sdist_epub) != 1:
        raise ValueError("Both distributions must include the EPUB adapter")
    exports = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    metadata = bundle / "ci-package"
    metadata.mkdir(parents=True, exist_ok=False)
    runtime = metadata / "runtime-requirements.txt"
    command(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
             "--output-file", str(runtime)], checkout)
    optional = metadata / "render-js-requirements.txt"
    command(["uv", "export", "--locked", "--no-dev", "--no-emit-project",
             "--extra", "render-js", "--output-file", str(optional)], checkout)
    build = metadata / "build-constraints.txt"
    constraints = project["build-system"]["requires"] + project["tool"]["uv"]["build-constraint-dependencies"]
    build.write_text("\n".join(constraints) + "\n", encoding="utf-8")
    manifest = {"version": project["project"]["version"], "exports": exports,
                "verification_scope": "smoke" if smoke else "full",
                "uv": command(["uv", "--version"], checkout).stdout.decode().split()[1],
                "checkout_sha": command(["git", "rev-parse", "HEAD"], checkout).stdout.decode().strip(),
                "action_cache_hit": os.environ.get("BREWDOC_CACHE_HIT"),
                "action_cache_key": os.environ.get("BREWDOC_CACHE_KEY"),
                "source": {name: digest(checkout / name) for name in (
                    ("pyproject.toml", "uv.lock", ".github/workflows/build_standalone.py",
                     ".github/workflows/verify_install.py", "tests/test_contract.py", "tests/test_epub.py",
                     "tests/test_spreadsheets.py") if smoke else (
                    "pyproject.toml", "uv.lock", "benchmarks/corpus.json", "tests/fixtures/receipts.json"))},
                "files": {path.relative_to(bundle).as_posix(): {
                    "size": path.stat().st_size, "sha256": digest(path)}
                    for path in (wheel, sdist, runtime, optional, build)}}
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
    body = markdown.encode("utf-8" if receipt["route"] in UNICODE_ROUTES else "ascii")
    line = (json.dumps(receipt, ensure_ascii=True, sort_keys=True) + os.linesep).encode("ascii")
    stdout_body = body if receipt["route"] in UNICODE_ROUTES else markdown.replace("\n", os.linesep).encode("ascii")
    wanted = line + (stdout_body if output is None and code == 0 else b"")
    if (result.returncode, result.stdout, result.stderr) != (code, wanted, b""):
        raise ValueError("CLI exit, receipt, Markdown placement or stderr changed")
    if output is not None and output.read_bytes() != body:
        raise ValueError("CLI output bytes changed")
    return {"exit": result.returncode, "stdout": result.stdout.decode("utf-8"),
            "stderr": result.stderr.decode("utf-8")}


def select_inputs(checkout: Path) -> list[tuple[dict, dict]]:
    """Select each suffix, a real VBA carrier and both accepted EPUB fixtures."""
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
    epubs = [document for document in documents if Path(document["path"]).suffix.lower() == ".epub"]
    if {document["path"] for document in epubs} != EPUB_INPUTS:
        raise ValueError("Installed EPUB acceptance requires the two reviewed fixtures")
    selected = [chosen[suffix] for suffix in sorted(chosen)]
    selected.extend((document, receipts[Path(document["path"]).relative_to("tests/fixtures").as_posix()])
                    for document in sorted(epubs, key=lambda item: item["id"])
                    if document["path"] != chosen[".epub"][0]["path"])
    return selected


def check_accepted_output(source: Path, actual: tuple[int, dict, str], accepted: dict) -> None:
    """Bind installed EPUB bytes and full receipt to independently reviewed evidence."""
    keys = {"source_sha256", "markdown_sha256", "markdown_utf8_bytes", "receipt",
            "review_reference", "review_scope", "normalization"}
    if (set(accepted) != keys or not accepted["review_reference"] or not accepted["review_scope"]
            or accepted["normalization"] != "receipt.out=null; Markdown bytes unchanged"):
        raise ValueError("EPUB accepted-output evidence is incomplete")
    code, receipt, markdown = actual
    body = markdown.encode("utf-8")
    observed = (code, digest(source), hashlib.sha256(body).hexdigest(), len(body), {**receipt, "out": None})
    expected = (0, accepted["source_sha256"], accepted["markdown_sha256"],
                accepted["markdown_utf8_bytes"], accepted["receipt"])
    if observed != expected:
        raise ValueError(f"Installed EPUB differs from reviewed output: {source.name}")


def probe_epub(path: Path, actual: tuple[int, dict, str], accepted: dict, console: str) -> dict:
    """Exercise EPUB consumers against accepted bytes, including ASCII-configured stdout."""
    import brewdoc

    check_accepted_output(path, actual, accepted)
    code, receipt, markdown = actual
    expected_tally = {"pages": 0, "sheets": 0, "chapters": receipt["units"], "slides": 0,
                      **{key: receipt[key] for key in (
                          "tables", "text_regions", "columns_split", "broken_ligature_words", "dropped")}}
    if brewdoc.render_epub(path) != (markdown, expected_tally):
        raise ValueError("Installed EPUB public wrapper changed")
    out = Path.cwd() / "nested" / (path.name + ".md")
    expected = (code, {**receipt, "out": str(out)}, markdown)
    if brewdoc.run(path, out=out) != expected or out.read_bytes() != markdown.encode("utf-8"):
        raise ValueError("Installed EPUB API output differs from reviewed bytes")
    out.unlink()
    cli = [check_cli(subprocess.run([console, str(path)], capture_output=True, timeout=120), actual)]
    environment = {**clean_environment(), "PYTHONIOENCODING": "ascii"}
    cli.append(check_cli(subprocess.run([console, str(path)], env=environment,
                                        capture_output=True, timeout=120), actual))
    cli.append(check_cli(subprocess.run([console, str(path), "--out", str(out)], env=environment,
                                        capture_output=True, timeout=120), expected, out))
    return {"input": path.name, "review_reference": accepted["review_reference"],
            "review_scope": accepted["review_scope"], "markdown_sha256": accepted["markdown_sha256"],
            "markdown_utf8_bytes": accepted["markdown_utf8_bytes"], "wrapper_tally": expected_tally,
            "cli": cli}


def probe_epub_refusal(console: str) -> dict:
    """Require malformed EPUB to preserve an existing output through API and CLI."""
    import brewdoc

    path = Path.cwd() / "malformed.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip")
        archive.writestr("META-INF/container.xml", b"<container")
    out = Path.cwd() / "epub-refusal.md"
    sentinel = b"existing output\n"
    out.write_bytes(sentinel)
    actual = brewdoc.run(path, out=out)
    code, receipt, markdown = actual
    if ((code, receipt["file_ok"], receipt["route"], receipt["units"], markdown,
         out.read_bytes()) != (1, False, "epub", 0, "", sentinel) or not receipt["reason"]):
        raise ValueError("Installed EPUB refusal changed existing output")
    result = subprocess.run([console, str(path), "--out", str(out)], capture_output=True, timeout=120)
    checked = check_cli(result, actual)
    if out.read_bytes() != sentinel:
        raise ValueError("Installed EPUB console refusal changed existing output")
    return {"receipt": receipt, "cli": checked, "preserved_output_sha256": digest(out)}


def installed_url_runtime(enabled: bool) -> dict:
    """Count all installed brewdoc and optional native bytes, including generated caches."""
    import brewdoc

    package = Path(brewdoc.__file__).parent
    compileall.compile_dir(package, quiet=2)
    files = set(package.rglob("*"))
    for name in ("htmlurl.py", "htmljs.py", "_urljs", "_vendor"):
        if not (package / name).exists():
            raise ValueError(f"Installed URL asset missing: {name}")
    distributions = [importlib.metadata.distribution("brewdoc")]
    if enabled:
        distributions.append(importlib.metadata.distribution("quickjs-ng"))
    for distribution in distributions:
        for item in distribution.files or ():
            path = Path(distribution.locate_file(item))
            files.add(path)
            if path.suffix == ".py":
                compileall.compile_file(path, quiet=2)
                files.update(path.parent.rglob("*.pyc"))
    inventory = [{"path": path.relative_to(Path(sys.prefix)).as_posix(),
                  "bytes": path.stat().st_size, "sha256": digest(path)}
                 for path in sorted(files) if path.is_file()]
    total = sum(item["bytes"] for item in inventory)
    if total > 10_000_000:
        raise ValueError(f"Complete added installed URL runtime exceeds cap: {total}")
    return {"added_installed_bytes": total, "inventory": inventory,
            "shared_exclusion": "Existing Python and base conversion dependencies; entire brewdoc package included conservatively"}


def probe_js(enabled: bool) -> dict:
    """Check installed optional capture or its explicit platform/dependency refusal."""
    import brewdoc
    from brewdoc.htmljs import _check_runtime

    supported = (sys.platform == "linux" and sys.maxsize > 2**32
                 and platform.machine().lower() in {"x86_64", "amd64", "aarch64", "arm64"}
                 and platform.libc_ver()[0] == "glibc") or (
        sys.platform == "darwin" and platform.machine() == "arm64")
    packages = {item.metadata["Name"].lower().replace("_", "-")
                for item in importlib.metadata.distributions()}
    if ("quickjs-ng" in packages) != enabled:
        raise ValueError("Base-only/optional native dependency isolation changed")
    footprint = installed_url_runtime(enabled)
    if not enabled or not supported:
        try:
            _check_runtime()
        except Exception as error:
            if not str(error):
                raise ValueError("Unsupported JS must provide an explicit refusal") from error
            scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
            console = scripts / ("brewdoc.exe" if os.name == "nt" else "brewdoc")
            refused = subprocess.run([str(console), "--url", "https://fixture.test/", "--render-js"],
                                     capture_output=True, timeout=15)
            if refused.returncode != 1 or refused.stderr:
                raise ValueError("Unavailable JS console must refuse with its receipt")
            receipt = json.loads(refused.stdout)
            if receipt.get("receipt_schema") != "brewdoc.receipt/2" or receipt.get("file_ok") is not False:
                raise ValueError("Unavailable JS console refusal receipt changed")
            acquisition = receipt.get("acquisition", {})
            if (acquisition.get("refusal_stage") != "prerequisites"
                    or acquisition.get("counts", {}).get("requests") != 0):
                raise ValueError("Unavailable JS must refuse before acquisition")
            return {**footprint, "enabled": enabled, "supported": supported, "refusal": str(error),
                    "cli_receipt": receipt}
        raise ValueError("Unsupported or missing optional runtime did not refuse")
    _check_runtime()
    from brewdoc.htmljs import render

    source = '<html><body><p id="result">pending</p><script type="module">document.querySelector("#result").textContent=String(await Promise.resolve(42))</script></body></html>'
    captured = render(source, "https://fixture.test/", lambda request: None,
                      time.monotonic() + 10, soft_window=0.1)
    if '<p id="result">42</p>' not in captured["html"] or captured["capture_status"] != "settled":
        raise ValueError("Installed native module/DOM capture changed")
    return {**footprint, "enabled": True, "supported": True,
            "capture_status": captured["capture_status"]}


def probe_smoke(checkout: Path, console: str) -> dict:
    """Check only existing generated EPUB/formula bytes and real CLI/output refusal."""
    spec = importlib.util.spec_from_file_location("standalone_smoke", Path(__file__).with_name("build_standalone.py"))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    inputs = Path.cwd() / "probe-inputs.json"
    builder.write_json(inputs, builder.synthetic_cases(checkout, Path.cwd()))
    observed = json.loads(command([sys.executable, "-I", "-c", builder.ORACLE_CODE,
                                   str(Path(__file__).resolve()), str(checkout), str(inputs), "smoke"], Path.cwd()).stdout)
    for row in observed["probes"]:
        source = Path.cwd() / row["path"]
        arguments = [console, str(source)]
        payload = None
        if "artifact" in row:
            payload = Path.cwd() / "formula.json"
            arguments += ["--artifact", row["artifact"]["key"] + "=" + str(payload)]
        builder.check_output(command(arguments, Path.cwd()), row, artifact_output=payload)
        out = Path.cwd() / (row["probe"] + ".md")
        builder.check_output(command([*arguments, "--out", str(out)], Path.cwd()), row, out, payload)
        if payload is not None and (payload.stat().st_size, digest(payload)) != (
                row["artifact"]["bytes"], row["artifact"]["sha256"]):
            raise ValueError("Installed synthetic formula artifact changed")
    return {"oracle": observed["oracle"], "probes": observed["probes"],
            "epub_refusal": probe_epub_refusal(console)}


def probe(checkout: Path, bundle: Path, render_js: bool = False, *, smoke: bool = False) -> dict:
    """Check installed identity and console/output contracts for the requested scope."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(manifest, smoke)
    import brewdoc
    from brewdoc import epub
    installed = importlib.metadata.distribution("brewdoc")
    direct = json.loads(installed.read_text("direct_url.json") or "{}")
    check_origin(Path(brewdoc.__file__), Path(sys.prefix), checkout, sys.path, direct)
    check_origin(Path(epub.__file__), Path(sys.prefix), checkout, sys.path, direct)
    if (brewdoc.__version__, installed.version, brewdoc.__all__) != (
            manifest["version"], manifest["version"], manifest["exports"]):
        raise ValueError("Installed version or public exports changed")
    for name in brewdoc.__all__:
        getattr(brewdoc, name)
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    console = str(scripts / ("brewdoc.exe" if os.name == "nt" else "brewdoc"))
    checked = command([console, "--self-check"], Path.cwd())
    if (checked.stdout, checked.stderr) != (("self-check: ok (38 checks)" + os.linesep).encode(), b""):
        raise ValueError("Installed console self-check verdict changed")
    if smoke:
        return {"verification_scope": "smoke", "python": sys.version,
                "module": str(Path(brewdoc.__file__).resolve()), "prefix": sys.prefix,
                "self_check": checked.stdout.decode(), **probe_smoke(checkout, console),
                "javascript": probe_js(render_js),
                "packages": sorted((item.metadata["Name"], item.version)
                                   for item in importlib.metadata.distributions())}
    routes = []
    cli = []
    artifacts = []
    epubs = []
    for document, snapshot in select_inputs(checkout):
        source = checkout / document["path"]
        if (source.stat().st_size, digest(source)) != (document["size_bytes"], document["sha256"]):
            raise ValueError(f"Smoke input identity changed: {source.name}")
        path = Path.cwd() / source.name
        shutil.copyfile(source, path)
        actual = brewdoc.run(path)
        code, receipt, markdown = actual
        expected_snapshot = {key: value for key, value in snapshot.items() if key != "accepted_output"}
        if {key: code if key == "rc" else receipt[key] for key in expected_snapshot} != expected_snapshot:
            raise ValueError(f"Installed route receipt changed: {source.name}")
        routes.append({"input": document["path"], "input_sha256": document["sha256"],
                       "receipt": receipt, "markdown": markdown, "markdown_sha256": hashlib.sha256(
                           markdown.encode("utf-8" if receipt["route"] in UNICODE_ROUTES else "ascii")).hexdigest()})
        if path.suffix == ".epub":
            epubs.append(probe_epub(path, actual, snapshot.get("accepted_output", {}), console))
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
    return {"verification_scope": "full", "python": sys.version, "module": str(Path(brewdoc.__file__).resolve()),
            "prefix": sys.prefix, "self_check": checked.stdout.decode(), "routes": routes,
            "cli": cli, "artifacts": artifacts, "epub": epubs,
            "epub_refusal": probe_epub_refusal(console), "javascript": probe_js(render_js),
            "packages": sorted((item.metadata["Name"], item.version)
                               for item in importlib.metadata.distributions())}


def verify(checkout: Path, bundle: Path, *, smoke: bool = False) -> None:
    """Install wheel and sdist separately outside checkout, then invoke isolated probes."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(manifest, smoke)
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
        for number, (distribution, render_js) in enumerate(
                (item, enabled) for item in distributions(bundle / "dist") for enabled in ((False,) if smoke else (False, True))):
            prefix = root / f"env-{number}"
            command(["uv", "venv", "--python", sys.executable, str(prefix)], root)
            python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            command(["uv", "pip", "install", "--python", str(python), "--require-hashes",
                     "--requirements", str(bundle / "ci-package" / (
                         "render-js-requirements.txt" if render_js else "runtime-requirements.txt"))], root)
            command(["uv", "pip", "install", "--python", str(python), "--no-deps", "--no-cache",
                     "--build-constraints", str(bundle / "ci-package/build-constraints.txt"),
                     str(distribution)], root)
            work = root / f"work-{number}"
            work.mkdir()
            argv = [str(python), "-I", str(Path(__file__).resolve()), "probe",
                    "--checkout", str(checkout), "--bundle", str(bundle)]
            if render_js:
                argv.append("--render-js")
            if smoke:
                argv.append("--smoke")
            checked = command(argv, work)
            observed = json.loads(checked.stdout)
            if smoke and (observed.get("verification_scope"), observed.get("oracle"),
                          [row["probe"] for row in observed.get("probes", [])]) != ("smoke", [], ["epub2", "formula"]):
                raise ValueError("Installed smoke consumer evidence is incomplete")
            results.append({"distribution": distribution.name, "render_js": render_js,
                            "sha256": digest(distribution),
                            "probe": observed})
    report = {"checkout_sha": manifest["checkout_sha"], "uv": manifest["uv"],
              "verification_scope": "smoke" if smoke else "full",
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
    parser.add_argument("--render-js", action="store_true", help="Probe the isolated optional installation")
    parser.add_argument("--smoke", action="store_true", help="Use two synthetic probes and only base installations")
    args = parser.parse_args()
    if args.mode == "probe":
        result = probe(args.checkout.resolve(), args.bundle.resolve(), args.render_js, smoke=args.smoke)
    else:
        result = {"prepare": prepare, "verify": verify}[args.mode](
            args.checkout.resolve(), args.bundle.resolve(), smoke=args.smoke)
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
