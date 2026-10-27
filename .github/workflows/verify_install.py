"""Check built distributions and their independent installations for CI and release."""

from __future__ import annotations

import argparse
import ast
import base64
import compileall
import contextlib
import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import platform
from pathlib import Path, PurePosixPath
import stat
import shutil
import subprocess
import sys
import sysconfig
import time
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".epub", ".xlsx", ".xlsm", ".xls", ".xlsb", ".ods",
            ".odt", ".odp", ".eml", ".md", ".txt"}
PRIVATE_PARTS = {".codex", ".agents", ".claude", ".venv", "docs", "AGENTS.md", "CLAUDE.md"}
UNICODE_ROUTES = {"html", "epub", "sheet", "text", "odt", "odp", "eml"}
EPUB_INPUTS = {"tests/fixtures/epub/epubcheck-minimal.epub",
               "tests/fixtures/epub/standardebooks-herland.epub"}
VERIFIER_SOURCE = ".github/workflows/verify_install.py"
INSPECTION_AUTHORITY = "tests/test_inspection_acceptance.py"
INSPECTION_TARGET = "https://acceptance.invalid/exact?query=1#fragment"
INSPECTION_SCRIPT = b"staticAcceptanceProof('literal source');"
DELIVERY_INSPECTION_SCHEMA = "brewdoc.delivery-inspection/1"
INSPECTION_SENTINEL = b"preserve existing output\n"
INSPECTION_SOURCE_FILES = tuple("tests/" + name for name in (
    "test_documents.py", "test_extraction_regressions.py", "test_inspect_pdf.py", "test_biff.py",
    "test_biff12.py", "test_workbook_fidelity.py", "test_inspect_workbook.py", "test_inspection_acceptance.py"))
TARGETS = {"linux-x64": ("Linux", {"x86_64", "amd64"}),
           "macos-arm64": ("Darwin", {"arm64", "aarch64"}),
           "windows-x64": ("Windows", {"x86_64", "amd64"})}


def runtime_identity() -> dict:
    """Record interpreter, native target and normal/free-threaded ABI observations."""
    os_name = platform.system()
    if sys.platform == "darwin":
        os_version = platform.mac_ver()[0]
    elif sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            os_name = winreg.QueryValueEx(key, "ProductName")[0]
        os_version = platform.win32_ver()[1]
    elif sys.platform == "linux":
        release = platform.freedesktop_os_release()
        os_name, os_version = release.get("ID", ""), release.get("VERSION_ID", "")
    else:
        os_version = platform.release()
    return {"implementation": platform.python_implementation(), "version": platform.python_version(),
            "system": platform.system(), "system_release": platform.release(),
            "os_version": os_version, "os_name": os_name,
            "machine": platform.machine(), "libc": list(platform.libc_ver()),
            "soabi": sysconfig.get_config_var("SOABI"), "abi_flags": getattr(sys, "abiflags", ""),
            "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
            "pointer_bits": 64 if sys.maxsize > 2**32 else 32}


def check_runtime(identity: dict, expected_python: str, target: str) -> None:
    """Admit only the requested normal-GIL CPython patch and native target."""
    system, machines = TARGETS[target]
    minor = "".join(expected_python.split(".")[:2])
    soabi = {"linux-x64": f"cpython-{minor}-x86_64-linux-gnu",
             "macos-arm64": f"cpython-{minor}-darwin", "windows-x64": f"cp{minor}-win_amd64"}[target]
    os_matches = {"linux-x64": identity.get("os_name") == "ubuntu" and identity.get("os_version") == "22.04",
                  "macos-arm64": identity.get("os_version", "").split(".")[0] == "15",
                  "windows-x64": "Windows Server 2025" in identity.get("os_name", "")}[target]
    if (identity.get("implementation") != "CPython" or identity.get("version") != expected_python
            or identity.get("system") != system or identity.get("machine", "").lower() not in machines
            or not os_matches or identity.get("pointer_bits") != 64 or identity.get("gil_disabled") is not False
            or identity.get("abi_flags") != "" or identity.get("soabi") != soabi
            or (system == "Linux" and identity.get("libc", [""])[0] != "glibc")):
        raise ValueError("Installed verification runtime differs from the selected cell")


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


def canonical_json(value: object) -> bytes:
    """Serialize sealed evidence with the inspection report's exact framing."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def module_sources(checkout: Path) -> dict[str, dict]:
    """Inventory all current package modules without following source symlinks."""
    root = checkout / "src/brewdoc"
    for path in (checkout, checkout / "src", root):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Package module source directory is missing or linked")
    found, pending = {}, [root]
    while pending:
        for path in sorted(pending.pop().iterdir()):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Package module source contains a symlink")
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif path.suffix == ".py":
                if not stat.S_ISREG(mode):
                    raise ValueError("Package module source is not a regular file")
                found[path.relative_to(checkout / "src").as_posix()] = {
                    "size": path.stat().st_size, "sha256": digest(path)}
    return dict(sorted(found.items()))


def sealed_module_sources(manifest: dict) -> dict:
    """Require the producer's complete module map and its canonical digest."""
    modules = manifest.get("module_sources")
    if (not isinstance(modules, dict) or not modules
            or hashlib.sha256(canonical_json(modules)).hexdigest() != manifest.get("module_sources_sha256")):
        raise ValueError("Package module source seal is missing or invalid")
    return modules


def inspection_authority(manifest: dict, checkout: Path | None = None) -> str:
    """Require the fixture source seal and optionally its current checkout identity."""
    sources = manifest.get("source")
    expected = sources.get(INSPECTION_AUTHORITY) if isinstance(sources, dict) else None
    if (not isinstance(expected, str) or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)):
        raise ValueError("Inspection fixture authority source seal is missing or invalid")
    if checkout is not None:
        path = checkout / INSPECTION_AUTHORITY
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError("Inspection fixture authority differs from the producer checkout")
    return expected


def archive_member_name(name: str, directory: bool) -> str:
    """Reject original archive aliases before path normalization can hide them."""
    value = name[:-1] if directory and name.endswith("/") else name
    parts = value.split("/")
    if (not value or "\\" in value or "\0" in value or ":" in parts[0]
            or any(part in ("", ".", "..") for part in parts)):
        raise ValueError("Distribution member name is not canonical")
    return value


def check_module_sources(checkout: Path, bundle: Path, sealed: dict | None = None) -> dict:
    """Admit original archive occurrences against every current source module."""
    wheel, sdist = distributions(bundle / "dist")
    with zipfile.ZipFile(wheel) as zipped, tarfile.open(sdist) as packed:
        wheel_members = zipped.infolist()
        if any(PRIVATE_PARTS.intersection(PurePosixPath(item.orig_filename).parts) for item in wheel_members):
            raise ValueError("Distribution includes private or local project files")
        project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        root = project.get("name", "brewdoc").replace("-", "_") + "-" + project["version"]
        current, source_members = None, []
        for member in packed:
            if PRIVATE_PARTS.intersection(PurePosixPath(member.name).parts):
                raise ValueError("Distribution includes private or local project files")
            name = archive_member_name(member.name, member.isdir())
            if not member.isdir() and not member.isfile():
                raise ValueError("Distribution member is linked or not regular")
            if name.startswith(root + "/src/") and name.endswith(".py") and member.isfile():
                current = module_sources(checkout) if current is None else current
                if sealed is not None and canonical_json(current) != canonical_json(sealed):
                    raise ValueError("Current package module sources differ from the producer")
                module = name[len(root + "/src/"):]
                if module not in current or member.size != current[module]["size"]:
                    raise ValueError("Distribution module membership or size differs from source")
            source_members.append(member)
        if (not any(item.orig_filename == "brewdoc/epub.py" for item in wheel_members)
                or not any(item.name == root + "/src/brewdoc/epub.py" and item.isfile() for item in source_members)):
            raise ValueError("Both distributions must include the EPUB adapter")
        current = module_sources(checkout) if current is None else current
        if sealed is not None and canonical_json(current) != canonical_json(sealed):
            raise ValueError("Current package module sources differ from the producer")
        for archive, members, is_wheel in ((zipped, wheel_members, True), (packed, source_members, False)):
            seen, modules = set(), {}
            for member in members:
                directory = member.is_dir() if is_wheel else member.isdir()
                raw_name = member.orig_filename if is_wheel else member.name
                name = archive_member_name(raw_name, directory)
                if name in seen:
                    raise ValueError("Distribution member occurs more than once")
                seen.add(name)
                mode = stat.S_IFMT(member.external_attr >> 16) if is_wheel else None
                regular = mode in (0, stat.S_IFREG) if is_wheel else member.isfile()
                if (not directory and not regular) or (directory and is_wheel and mode not in (0, stat.S_IFDIR)):
                    raise ValueError("Distribution member is linked or not regular")
                parts = name.split("/")
                if is_wheel:
                    if member.flag_bits & 1 or (parts[0].endswith(".data") and len(parts) > 1
                                                and parts[1] in ("purelib", "platlib")):
                        raise ValueError("Distribution member has an installable alias")
                    module = name
                else:
                    if parts[0] != root:
                        raise ValueError("Distribution member has a foreign source root")
                    module = "/".join(parts[2:]) if len(parts) > 2 and parts[1] == "src" else ""
                if directory or not name.endswith(".py"):
                    continue
                if not is_wheel and not module:
                    continue
                if module not in current:
                    raise ValueError("Distribution contains a foreign package module")
                size = member.file_size if is_wheel else member.size
                if size != current[module]["size"]:
                    raise ValueError("Distribution module size differs from source")
                stream = archive.open(member) if is_wheel else archive.extractfile(member)
                with stream:
                    payload = stream.read(size + 1)
                if payload != (checkout / "src" / module).read_bytes():
                    raise ValueError("Distribution module bytes differ from source")
                modules[module] = current[module]
            if modules != current:
                raise ValueError("Distribution package module membership differs from source")
    return current


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


def sealed_bytes(record: dict) -> bytes:
    """Decode an exact size/hash/base64 record before a consumer uses its bytes."""
    try:
        payload = base64.b64decode(record["bytes_base64"], validate=True)
        valid = type(record["size"]) is int and record["size"] == len(payload)
        valid = valid and record["sha256"] == hashlib.sha256(payload).hexdigest()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Inspection fixture byte seal is invalid") from exc
    if not valid:
        raise ValueError("Inspection fixture byte seal is invalid")
    return payload


def check_inspection_case(expected: dict) -> bytes:
    """Bind the HTML fixture to its reviewed literals and stable source basename."""
    if (not isinstance(expected, dict) or set(expected) != {"path", "source", "target", "script"}
            or expected["path"] != "passive.html" or expected["target"] != INSPECTION_TARGET
            or sealed_bytes(expected["script"]) != INSPECTION_SCRIPT):
        raise ValueError("Inspection fixture authority differs")
    return sealed_bytes(expected["source"])


def inspection_case(bundle: Path, manifest: dict) -> dict:
    """Read only the producer-sealed fixture packet, never regenerate consumer bytes."""
    descriptor = manifest.get("inspection_api", {})
    if (not isinstance(descriptor, dict) or descriptor.get("path") != "ci-package/inspection-api.json"
            or set(descriptor) != {"path", "size", "sha256"}
            or {key: descriptor[key] for key in ("size", "sha256")} != manifest["files"].get(descriptor["path"])):
        raise ValueError("Inspection fixture packet seal is missing or invalid")
    path = bundle / descriptor["path"]
    if path.is_symlink() or {"size": path.stat().st_size, "sha256": digest(path)} != {
            key: descriptor[key] for key in ("size", "sha256")}:
        raise ValueError("Inspection fixture packet bytes changed")
    expected = json.loads(path.read_bytes())
    check_inspection_case(expected)
    return expected


def check_inspection_api(observed: dict, expected: dict) -> None:
    """Admit actual API evidence against independent source and script literals."""
    source = check_inspection_case(expected)
    if (not isinstance(observed, dict) or set(observed) != {"source", "report", "artifacts", "script", "unknown_key"}):
        raise ValueError("Installed inspection API evidence is missing or malformed")
    source_identity = {"name": expected["path"], "suffix": ".html", "kind": "local",
                       "byte_size": len(source), "sha256": hashlib.sha256(source).hexdigest()}
    report_bytes = sealed_bytes(observed["report"])
    report = json.loads(report_bytes)
    if (observed["source"] != source_identity or report.get("source") != source_identity
            or report.get("schema") != "brewdoc.inspection/1" or canonical_json(report) != report_bytes):
        raise ValueError("Installed inspection API report or source differs")
    findings = report["findings"]
    scripts = [item for item in findings if item["document"] == "document/000001"
               and item["capability"] == "scripts" and item["text"] == INSPECTION_SCRIPT.decode()]
    if (len(scripts) != 1 or not any(item["target"] == INSPECTION_TARGET
                                   and item["document"] == "document/000001" for item in findings)):
        raise ValueError("Installed inspection API lost literal source evidence")
    reference = {"key": "inspection/report", "kind": "inspection-report", "availability": "available",
                 "count": 1, "location": "source", "media_type": "application/json", "out": None,
                 "byte_size": len(report_bytes), "sha256": hashlib.sha256(report_bytes).hexdigest()}
    references = [reference, *report["artifacts"]]
    key = scripts[0]["artifact"]
    selected = [item for item in references if item["key"] == key]
    if (observed["artifacts"] != references or len({item["key"] for item in references}) != len(references)
            or len(selected) != 1 or selected[0]["byte_size"] != len(INSPECTION_SCRIPT)
            or selected[0]["sha256"] != hashlib.sha256(INSPECTION_SCRIPT).hexdigest()
            or any(item["out"] is not None for item in references)
            or observed["script"].get("key") != key or sealed_bytes(observed["script"]) != INSPECTION_SCRIPT):
        raise ValueError("Installed inspection API artifact list or script differs")
    if observed["unknown_key"] != "unknown artifact key: inspection/missing":
        raise ValueError("Installed inspection API unknown-key refusal differs")


def probe_inspection_api(source: Path, expected: dict) -> dict:
    """Exercise all three installed public APIs and exact unknown-key refusal."""
    try:
        from brewdoc import (ArtifactRef, BrewdocError, inspect_document,
                             list_document_artifacts, read_document_artifact)
    except ImportError as exc:
        raise ValueError("Installed public inspection API is unavailable") from exc
    if source.name != expected["path"] or source.read_bytes() != check_inspection_case(expected):
        raise ValueError("Installed inspection API source bytes differ")
    report = inspect_document(source)
    raw = read_document_artifact(source, "inspection/report")
    if (report != inspect_document(source) or raw != read_document_artifact(source, "inspection/report")
            or raw != canonical_json(report)):
        raise ValueError("Installed inspection API report is not repeatable")
    references = list_document_artifacts(source)
    if type(references) is not tuple or any(type(item) is not ArtifactRef for item in references):
        raise ValueError("Installed inspection API did not return ArtifactRef values")
    scripts = [item for item in report["findings"] if item["document"] == "document/000001"
               and item["capability"] == "scripts" and item["text"] == INSPECTION_SCRIPT.decode()]
    if len(scripts) != 1:
        raise ValueError("Installed inspection API lost literal source evidence")
    key = scripts[0]["artifact"]
    script = read_document_artifact(source, key)
    try:
        read_document_artifact(source, "inspection/missing")
    except BrewdocError as exc:
        unknown = str(exc)
    else:
        raise ValueError("Installed inspection API accepted an unknown key")
    observed = {"source": report["source"], "artifacts": [item.to_dict() for item in references],
                "report": {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                           "bytes_base64": base64.b64encode(raw).decode("ascii")},
                "script": {"key": key, "size": len(script), "sha256": hashlib.sha256(script).hexdigest(),
                           "bytes_base64": base64.b64encode(script).decode("ascii")}, "unknown_key": unknown}
    check_inspection_api(observed, expected)
    return observed


def inspection_required(version: str) -> bool:
    """Use canonical numeric versions for the mandatory current delivery profile."""
    if not isinstance(version, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Invalid inspection delivery version")
    return tuple(map(int, version.split("."))) >= (0, 8, 0)


def inspection_file(payload: bytes, **fields) -> dict:
    """Seal observed bytes using the existing delivery file vocabulary."""
    return {**fields, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes_base64": base64.b64encode(payload).decode("ascii")}


def inspection_payload(suffix: str) -> bytes:
    """Keep the independent literal script or original native BIFF token bytes."""
    if suffix == ".pdf":
        return b"app.alert('literal-document-script');\n"
    if suffix == ".xls":
        return bytes.fromhex("170c007374617469632d70726f6f6622016e00")
    return INSPECTION_SCRIPT


def inspection_positive(suffix: str, report: dict) -> tuple[dict, dict]:
    """Select concrete root-format evidence and its original payload occurrence."""
    root = [item for item in report["findings"] if item["document"] == "document/000001"]
    target = ("https://button.example.invalid/exact?source=image#fragment" if suffix == ".pdf"
              else INSPECTION_TARGET)
    if not any(item["target"] == target for item in root):
        raise ValueError("Inspection case lost its root literal target")
    text = INSPECTION_SCRIPT.decode()
    if suffix == ".pdf":
        native = [item for item in root if item["kind"] == "pdf-javascript" and item["text"] == inspection_payload(suffix).decode()
                  and item["location"].get("dictionary_path") == "Catalog/OpenAction" and item["location"].get("object_id") == 9]
    elif suffix in (".xls", ".xlsb", ".xlsx", ".xlsm", ".ods"):
        cell = "A1" if suffix == ".ods" else "C5"
        formula = {".xls": 'EXEC("static-proof")', ".xlsb": 'EXEC("static-proof")', ".xlsx": "42", ".xlsm": "42", ".ods": None}[suffix]
        native = [item for item in root if item["kind"] == "cell" and item["location"].get("cell") == cell
                  and item["details"].get("formula") == formula]
        part = {".xls": None, ".xlsb": "xl/worksheets/sheet1.bin", ".xlsx": "xl/worksheets/sheet1.xml",
                ".xlsm": "xl/worksheets/sheet1.xml", ".ods": "content.xml"}[suffix]
        native = [item for item in native if item["location"].get("part") == part
                  and (item["location"].get("stream") == "Book" if suffix == ".xls" else True)
                  and item["details"].get("value") == ("Office source" if suffix == ".ods" else
                                                       42.0 if suffix in (".xlsx", ".xlsm") else 0.0)]
    elif suffix in (".docx", ".pptx", ".odt", ".odp"):
        part = {".docx": "word/document.xml", ".pptx": "ppt/slides/slide1.xml", ".odt": "content.xml", ".odp": "content.xml"}[suffix]
        native = [item for item in root if item["kind"] == "xml-text" and item["text"] == "Office source"
                  and item["location"].get("part") == part]
    else:
        native = [item for item in root if item["kind"] == "inline_script" and item["text"] == text
                  and item["location"].get("part") == {".epub": "unused.xhtml", ".eml": "mime-1.1.2.html"}.get(suffix, "passive" + suffix)]
    if len(native) != 1:
        raise ValueError("Inspection case lost its root native positive")
    if suffix == ".xls":
        payload = [item for item in root if item["kind"] == "xlm-formula" and item["text"] == 'EXEC("static-proof")'
                   and item["location"].get("cell") == "C5" and item["location"].get("stream") == "Book"]
    elif suffix in (".pdf", ".epub", ".eml", ".html", ".md", ".txt"):
        payload = native
    else:
        payload = [item for item in root if item["kind"] == "script-source" and item["text"] == text
                   and item["location"].get("part") == "unused/source.js"]
    if len(payload) != 1 or not payload[0]["artifact"]:
        raise ValueError("Inspection case lost its root payload occurrence")
    return native[0], payload[0]


def _validate_inspection_packet(packet: dict, *, version: str, source_sha: str,
                                module_sources_sha256: str | None = None) -> dict:
    """Admit the exact fifteen-case packet against its independently supplied identity."""
    inspection_required(version)
    if (not isinstance(packet, dict) or set(packet) != {"schema", "version", "source_sha", "module_sources_sha256", "cases"}
            or packet["schema"] != DELIVERY_INSPECTION_SCHEMA or packet["version"] != version or packet["source_sha"] != source_sha
            or not isinstance(source_sha, str) or not re.fullmatch("[0-9a-f]{40}", source_sha)
            or not isinstance(packet["module_sources_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", packet["module_sources_sha256"])
            or module_sources_sha256 is not None and packet["module_sources_sha256"] != module_sources_sha256
            or not isinstance(packet["cases"], list) or any(not isinstance(item, dict) for item in packet["cases"])
            or [item.get("id") for item in packet["cases"]] != sorted(SUFFIXES)):
        raise ValueError("Invalid inspection packet identity or format coverage")
    for case in packet["cases"]:
        if (set(case) != {"id", "path", "source", "positive", "report", "artifacts", "payload", "readable"}
                or case["path"] != "passive" + case["id"]):
            raise ValueError("Invalid inspection case path or fields")
        source, raw, payload = (sealed_bytes(case[key]) for key in ("source", "report", "payload"))
        for key, extras in (("source", set()), ("report", set()), ("payload", {"key", "kind"})):
            if (set(case[key]) != {"size", "sha256", "bytes_base64"} | extras
                    or base64.b64encode(sealed_bytes(case[key])).decode("ascii") != case[key]["bytes_base64"]):
                raise ValueError("Invalid inspection case byte fields")
        report = json.loads(raw)
        identity = {"name": case["path"], "suffix": case["id"], "kind": "local", "byte_size": len(source),
                    "sha256": hashlib.sha256(source).hexdigest()}
        if report.get("schema") != "brewdoc.inspection/1" or report.get("source") != identity or canonical_json(report) != raw:
            raise ValueError("Inspection report source or canonical bytes differ")
        expected_root = {"id": "document/000001", "parent": None, "component": None,
                         "name": case["path"], "suffix": case["id"], "byte_size": len(source),
                         "sha256": identity["sha256"], "location": {"kind": "source"}}
        if (report["documents"][0] != expected_root
                or len({item["id"] for item in report["documents"]}) != len(report["documents"])):
            raise ValueError("Inspection root source document or document identities differ")
        native, selected = inspection_positive(case["id"], report)
        target = "https://button.example.invalid/exact?source=image#fragment" if case["id"] == ".pdf" else INSPECTION_TARGET
        positive = {"target": target, "finding": {key: native[key] for key in ("capability", "kind", "text", "location", "details")}}
        kind = "pdf-decoded-stream" if case["id"] == ".pdf" else "formula-source" if case["id"] == ".xls" else "script"
        if case["positive"] != positive or payload != inspection_payload(case["id"]) or (case["payload"]["key"], case["payload"]["kind"]) != (selected["artifact"], kind):
            raise ValueError("Inspection literal positive or payload differs")
        reference = {"key": "inspection/report", "kind": "inspection-report", "availability": "available", "count": 1,
                     "location": "source", "media_type": "application/json", "out": None,
                     "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        references = [reference, *report["artifacts"]]
        selected_refs = [item for item in references if item["key"] == selected["artifact"]]
        if (case["artifacts"] != references or len({item["key"] for item in references}) != len(references)
                or any(item["out"] is not None for item in references) or len(selected_refs) != 1
                or (selected_refs[0]["kind"], selected_refs[0]["byte_size"], selected_refs[0]["sha256"]) != (
                    kind, len(payload), hashlib.sha256(payload).hexdigest())):
            raise ValueError("Inspection artifact list or payload membership differs")
        readable = case["readable"]
        if (set(readable) != {"code", "receipt", "markdown_size", "markdown_sha256"}
                or type(readable["code"]) is not int or readable["code"] not in (0, 1)
                or type(readable["markdown_size"]) is not int or readable["markdown_size"] < 0
                or not re.fullmatch("[0-9a-f]{64}", readable["markdown_sha256"])
                or readable["receipt"].get("source") != case["path"] or readable["receipt"].get("out") is not None
                or readable["receipt"].get("file_ok") is not (readable["code"] == 0)
                or [item for item in readable["receipt"].get("artifacts", []) if item["key"].startswith("inspection/")] != references):
            raise ValueError("Inspection readable receipt or Markdown identity differs")
        if case["id"] == ".pdf" and readable["code"] != 1:
            raise ValueError("Inspection PDF must retain its readable refusal")
        if case["id"] in (".md", ".txt") and (
                not source.startswith(b"\xef\xbb\xbf") or not source.endswith(b"\r\nCaf\xc3\xa9\r\n")
                or readable["code"] != 0 or readable["markdown_size"] != len(source)
                or readable["markdown_sha256"] != hashlib.sha256(source).hexdigest()):
            raise ValueError("Inspection text pass-through bytes differ")
    canonical_json(packet)
    return packet


def validate_inspection_packet(packet: dict, *, version: str, source_sha: str,
                               module_sources_sha256: str | None = None) -> dict:
    """Refuse malformed private evidence through one explicit metadata error boundary."""
    try:
        return _validate_inspection_packet(packet, version=version, source_sha=source_sha,
                                            module_sources_sha256=module_sources_sha256)
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed inspection packet fields") from exc


def expected_inspection_result(packet: dict) -> dict:
    """Derive the entire required consumer result from an admitted sealed packet."""
    validate_inspection_packet(packet, version=packet["version"], source_sha=packet["source_sha"])
    rows = []
    for case in packet["cases"]:
        rows.append({"id": case["id"], "source_sha256": case["source"]["sha256"],
            "report_sha256": case["report"]["sha256"], "repeat_report_sha256": case["report"]["sha256"],
            "payload_key": case["payload"]["key"], "payload_size": case["payload"]["size"],
            "payload_sha256": case["payload"]["sha256"], "readable_code": case["readable"]["code"],
            "markdown_sha256": case["readable"]["markdown_sha256"],
            "destination_sha256": case["readable"]["markdown_sha256"] if case["readable"]["code"] == 0
                                  else hashlib.sha256(INSPECTION_SENTINEL).hexdigest()})
    return {"schema": DELIVERY_INSPECTION_SCHEMA, "suite_sha256": hashlib.sha256(canonical_json(packet)).hexdigest(),
            "module_sources_sha256": packet["module_sources_sha256"], "cases": rows}


def inspection_descriptor(packet: dict, *, version: str, source_sha: str, module_sources_sha256: str) -> dict:
    """Bind publisher expectations to independently admitted package identity."""
    if not isinstance(module_sources_sha256, str) or not re.fullmatch("[0-9a-f]{64}", module_sources_sha256):
        raise ValueError("Invalid independent inspection module map identity")
    validate_inspection_packet(packet, version=version, source_sha=source_sha, module_sources_sha256=module_sources_sha256)
    result = expected_inspection_result(packet)
    return {"schema": DELIVERY_INSPECTION_SCHEMA, "suite_sha256": result["suite_sha256"],
            "module_sources_sha256": module_sources_sha256, "result": result}


def check_inspection_result(observed: dict, packet: dict) -> None:
    """Require all observed identities and ordered cases, including repeated reports."""
    if canonical_json(observed) != canonical_json(expected_inspection_result(packet)):
        raise ValueError("Consumer inspection evidence differs from its sealed packet")


def admit_inspection(record: dict, *, version: str, source_sha: str, inspection: dict | None = None) -> dict | None:
    """Require current profiles and reject malformed present historical profiles."""
    if "inspection" not in record:
        if inspection_required(version) or inspection is not None:
            raise ValueError("Required inspection packet is missing")
        return None
    packet = record["inspection"]
    validate_inspection_packet(packet, version=version, source_sha=source_sha)
    if inspection is not None:
        expected = inspection_descriptor(packet, version=version, source_sha=source_sha,
                                         module_sources_sha256=packet["module_sources_sha256"])
        if canonical_json(inspection) != canonical_json(expected):
            raise ValueError("Inspection packet differs from the independent package descriptor")
    return packet


def seal_inspection_packet(checkout: Path, cases: list[dict], *, version: str, source_sha: str, modules: dict) -> dict:
    """Seal trusted source-runtime observations only after origin/version/module checks."""
    if (not isinstance(cases, list) or [case.get("id") for case in cases] != sorted(SUFFIXES)
            or any(set(case) != {"id", "path", "source"} or case["path"] != "passive" + case["id"] for case in cases)
            or sys.version_info[:3] != (3, 12, 14)):
        raise ValueError("Inspection source runtime inputs or interpreter differ")
    if module_sources(checkout) != modules:
        raise ValueError("Inspection source runtime module map differs")
    import brewdoc

    if (Path(brewdoc.__file__).resolve() != (checkout / "src/brewdoc/__init__.py").resolve()
            or brewdoc.__version__ != version or importlib.metadata.version("brewdoc") != version):
        raise ValueError("Inspection source runtime origin or version differs")
    packet = {"schema": DELIVERY_INSPECTION_SCHEMA, "version": version, "source_sha": source_sha,
              "module_sources_sha256": hashlib.sha256(canonical_json(modules)).hexdigest(), "cases": []}
    with tempfile.TemporaryDirectory(prefix="brewdoc-inspection-source-") as temporary:
        for source_case in cases:
            work = Path(temporary) / source_case["id"][1:]
            work.mkdir()
            with contextlib.chdir(work):
                path = Path(source_case["path"])
                path.write_bytes(sealed_bytes(source_case["source"]))
                report = brewdoc.inspect_document(path)
                native, selected = inspection_positive(source_case["id"], report)
                raw = brewdoc.read_document_artifact(path, "inspection/report")
                payload = brewdoc.read_document_artifact(path, selected["artifact"])
                if raw != canonical_json(report) or payload != inspection_payload(source_case["id"]):
                    raise ValueError("Inspection source runtime literal or report differs")
                references = [item.to_dict() for item in brewdoc.list_document_artifacts(path)]
                selected_ref = next(item for item in references if item["key"] == selected["artifact"])
                code, receipt, markdown = brewdoc.run(path)
                body = markdown.encode("utf-8")
                packet["cases"].append({**source_case, "positive": {
                    "target": "https://button.example.invalid/exact?source=image#fragment" if path.suffix == ".pdf" else INSPECTION_TARGET,
                    "finding": {key: native[key] for key in ("capability", "kind", "text", "location", "details")}},
                    "report": inspection_file(raw), "artifacts": references,
                    "payload": inspection_file(payload, key=selected["artifact"], kind=selected_ref["kind"]),
                    "readable": {"code": code, "receipt": receipt, "markdown_size": len(body),
                                 "markdown_sha256": hashlib.sha256(body).hexdigest()}})
    if canonical_json(module_sources(checkout)) != canonical_json(modules):
        raise ValueError("Inspection source runtime module map changed during sealing")
    return validate_inspection_packet(packet, version=version, source_sha=source_sha,
                                       module_sources_sha256=packet["module_sources_sha256"])


def inspection_builder_sources(manifest: dict, checkout: Path | None = None) -> dict:
    """Require every explicit builder authority, without accepting supplied-key subsets."""
    inspection_authority(manifest, checkout)
    found = {}
    for name in INSPECTION_SOURCE_FILES:
        value = manifest.get("source", {}).get(name)
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("Inspection builder authority source seal is missing or invalid")
        if checkout is not None and ((checkout / name).is_symlink() or digest(checkout / name) != value):
            raise ValueError("Inspection builder authority differs from the producer checkout")
        found[name] = value
    return found


def load_inspection_packet(bundle: Path, manifest: dict) -> dict | None:
    """Load the canonical package packet bound to its producer and complete module map."""
    if "inspection" not in manifest:
        if inspection_required(manifest["version"]):
            raise ValueError("Required package inspection packet is missing")
        return None
    descriptor = manifest["inspection"]
    if (not isinstance(descriptor, dict) or set(descriptor) != {"path", "size", "sha256"}
            or descriptor["path"] != "ci-package/inspection.json"
            or {key: descriptor[key] for key in ("size", "sha256")} != manifest["files"].get(descriptor["path"])):
        raise ValueError("Package inspection packet seal is invalid")
    path = bundle / descriptor["path"]
    if path.is_symlink() or {"size": path.stat().st_size, "sha256": digest(path)} != {
            key: descriptor[key] for key in ("size", "sha256")}:
        raise ValueError("Package inspection packet bytes differ")
    raw = path.read_bytes()
    packet = json.loads(raw)
    if canonical_json(packet) != raw:
        raise ValueError("Package inspection packet is not canonical")
    sealed_module_sources(manifest)
    inspection_builder_sources(manifest)
    return validate_inspection_packet(packet, version=manifest["version"], source_sha=manifest["checkout_sha"],
                                       module_sources_sha256=manifest["module_sources_sha256"])


def inspection_receipt(case: dict) -> dict:
    """Change only the three explicitly requested relative output destinations."""
    receipt = copy.deepcopy(case["readable"]["receipt"])
    receipt["out"] = "markdown.md" if case["readable"]["code"] == 0 else None
    destinations = {"inspection/report": "report.json", case["payload"]["key"]: "payload.bin"}
    receipt["artifacts"] = [{**item, "out": destinations.get(item["key"], item["out"])} for item in receipt["artifacts"]]
    return receipt


def observed_inspection_case(case: dict, source: bytes, reports: list[bytes], payload: bytes,
                              code: int, markdown: bytes, destination: bytes, payload_key: str) -> dict:
    """Return actual source, repeated report, payload and readable destination identities."""
    return {"id": case["id"], "source_sha256": hashlib.sha256(source).hexdigest(),
            "report_sha256": hashlib.sha256(reports[0]).hexdigest(), "repeat_report_sha256": hashlib.sha256(reports[1]).hexdigest(),
            "payload_key": payload_key, "payload_size": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "readable_code": code, "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
            "destination_sha256": hashlib.sha256(destination).hexdigest()}


def probe_inspection_packet(packet: dict, work: Path) -> dict:
    """Exercise actual installed APIs and output transactions for all sealed sources twice."""
    validate_inspection_packet(packet, version=packet["version"], source_sha=packet["source_sha"])
    from brewdoc import (ArtifactRef, BrewdocError, inspect_document, list_document_artifacts,
                         read_document_artifact, run)

    rows = []
    with tempfile.TemporaryDirectory(prefix="inspection-api-", dir=work) as temporary:
        for case in packet["cases"]:
            parent = Path(temporary) / case["id"][1:]
            parent.mkdir()
            reports = []
            for number in range(2):
                directory = parent / str(number)
                directory.mkdir()
                with contextlib.chdir(directory):
                    source = Path(case["path"])
                    source.write_bytes(sealed_bytes(case["source"]))
                    report = inspect_document(source)
                    raw = read_document_artifact(source, "inspection/report")
                    references = list_document_artifacts(source)
                    payload = read_document_artifact(source, case["payload"]["key"])
                    if (raw != canonical_json(report) or raw != sealed_bytes(case["report"])
                            or type(references) is not tuple or any(type(item) is not ArtifactRef for item in references)
                            or [item.to_dict() for item in references] != case["artifacts"]
                            or payload != sealed_bytes(case["payload"])):
                        raise ValueError("Installed inspection packet API bytes or references differ")
                    try:
                        read_document_artifact(source, "inspection/missing")
                    except BrewdocError as exc:
                        if str(exc) != "unknown artifact key: inspection/missing":
                            raise ValueError("Installed inspection packet unknown-key refusal differs") from exc
                    else:
                        raise ValueError("Installed inspection packet accepted an unknown key")
                    destination, report_path, payload_path = Path("markdown.md"), Path("report.json"), Path("payload.bin")
                    if report_path.exists() or report_path.is_symlink() or payload_path.exists() or payload_path.is_symlink():
                        raise ValueError("Inspection artifact destinations must start absent")
                    destination.write_bytes(INSPECTION_SENTINEL)
                    code, receipt, markdown = run(source, destination, artifact_outputs={
                        "inspection/report": report_path, case["payload"]["key"]: payload_path})
                    body = markdown.encode("utf-8")
                    if any(path.is_symlink() or not path.is_file() for path in (report_path, payload_path, destination)):
                        raise ValueError("Installed inspection artifacts are missing or linked")
                    if (code != case["readable"]["code"] or receipt != inspection_receipt(case)
                            or (len(body), hashlib.sha256(body).hexdigest()) != (
                                case["readable"]["markdown_size"], case["readable"]["markdown_sha256"])
                            or report_path.read_bytes() != raw or payload_path.read_bytes() != payload
                            or destination.read_bytes() != (body if code == 0 else INSPECTION_SENTINEL)):
                        raise ValueError("Installed inspection packet readable or artifact transaction differs")
                    reports.append(report_path.read_bytes())
                    observed_source, observed_destination = source.read_bytes(), destination.read_bytes()
                    actual_key = next(item["key"] for item in receipt["artifacts"] if item["out"] == "payload.bin")
            rows.append(observed_inspection_case(case, observed_source, reports, payload, code, body, observed_destination, actual_key))
    result = {"schema": DELIVERY_INSPECTION_SCHEMA, "suite_sha256": hashlib.sha256(canonical_json(packet)).hexdigest(),
              "module_sources_sha256": packet["module_sources_sha256"], "cases": rows}
    check_inspection_result(result, packet)
    return result


def prepare(checkout: Path, bundle: Path, *, smoke: bool = False, _inspection_case: dict | None = None,
            _inspection_packet: dict | None = None) -> None:
    """Inspect archives and export locked runtime and isolated build constraints."""
    project = tomllib.loads((checkout / "pyproject.toml").read_text(encoding="utf-8"))
    modules = check_module_sources(checkout, bundle)
    wheel, sdist = distributions(bundle / "dist")
    with zipfile.ZipFile(wheel) as archive:
        tree = ast.parse(archive.read("brewdoc/__init__.py"))
    exports = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    metadata = bundle / "ci-package"
    metadata.mkdir(parents=True, exist_ok=False)
    case = _inspection_case
    if case is None:
        spec = importlib.util.spec_from_file_location("standalone_fixture", Path(__file__).with_name("build_standalone.py"))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        case = helper.inspection_api_case(checkout)
    check_inspection_case(case)
    fixture = metadata / "inspection-api.json"
    fixture.write_bytes(canonical_json(case))
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
                "module_sources": modules, "module_sources_sha256": hashlib.sha256(canonical_json(modules)).hexdigest(),
                "inspection_api": {"path": "ci-package/inspection-api.json", "size": fixture.stat().st_size,
                                   "sha256": digest(fixture)},
                "verification_scope": "smoke" if smoke else "full",
                "uv": command(["uv", "--version"], checkout).stdout.decode().split()[1],
                "checkout_sha": command(["git", "rev-parse", "HEAD"], checkout).stdout.decode().strip(),
                "action_cache_hit": os.environ.get("BREWDOC_CACHE_HIT"),
                "action_cache_key": os.environ.get("BREWDOC_CACHE_KEY"),
                "source": {name: digest(checkout / name) for name in dict.fromkeys((
                    ("pyproject.toml", "uv.lock", ".github/workflows/build_standalone.py",
                     ".github/workflows/verify_install.py", "tests/test_contract.py", "tests/test_epub.py",
                     "tests/test_spreadsheets.py", INSPECTION_AUTHORITY) if smoke else (
                    "pyproject.toml", "uv.lock", VERIFIER_SOURCE,
                    "benchmarks/corpus.json", "tests/fixtures/receipts.json", INSPECTION_AUTHORITY)) +
                    (INSPECTION_SOURCE_FILES if inspection_required(project["project"]["version"]) else ()))},
                "files": {path.relative_to(bundle).as_posix(): {
                    "size": path.stat().st_size, "sha256": digest(path)}
                    for path in (wheel, sdist, runtime, optional, build, fixture)}}
    if inspection_required(manifest["version"]) or _inspection_packet is not None:
        inspection_builder_sources(manifest, checkout)
        packet = _inspection_packet
        if packet is None:
            spec = importlib.util.spec_from_file_location("standalone_cases", Path(__file__).with_name("build_standalone.py"))
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            with tempfile.TemporaryDirectory(prefix="brewdoc-inspection-producer-") as temporary:
                control = Path(temporary) / "inputs.json"
                cases = helper.inspection_cases(checkout, Path(temporary) / "sources")
                control.write_bytes(canonical_json({"version": manifest["version"], "source_sha": manifest["checkout_sha"],
                                                     "modules": modules, "cases": cases}))
                packet = json.loads(command([str(checkout / ".venv/bin/python"), "-I", str(Path(__file__).resolve()),
                    "seal-inspection", "--checkout", str(checkout), "--input", str(control)], checkout).stdout)
                if [{key: row[key] for key in ("id", "path", "source")} for row in packet["cases"]] != cases:
                    raise ValueError("Inspection source runtime replaced the generated source bytes")
        validate_inspection_packet(packet, version=manifest["version"], source_sha=manifest["checkout_sha"],
                                   module_sources_sha256=manifest["module_sources_sha256"])
        inspection_builder_sources(manifest, checkout)
        packet_path = metadata / "inspection.json"
        packet_path.write_bytes(canonical_json(packet))
        manifest["inspection"] = {"path": "ci-package/inspection.json", "size": packet_path.stat().st_size,
                                  "sha256": digest(packet_path)}
        manifest["files"]["ci-package/inspection.json"] = {key: manifest["inspection"][key] for key in ("size", "sha256")}
    (metadata / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def prepare_runtime(checkout: Path, bundle: Path) -> None:
    """Seal full compatibility inputs copied from the already-built smoke pair."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(manifest, True)
    verify_manifest(bundle, manifest)
    check_module_sources(checkout, bundle, sealed_module_sources(manifest))
    inspection_authority(manifest, checkout)
    if {name: digest(checkout / name) for name in manifest["source"]} != manifest["source"]:
        raise ValueError("Compatibility sources differ from the producer checkout")
    case = inspection_case(bundle, manifest)
    packet = load_inspection_packet(bundle, manifest)
    if packet is not None:
        inspection_builder_sources(manifest, checkout)
    runtime = bundle / "ci-runtime"
    (runtime / "dist").mkdir(parents=True, exist_ok=False)
    original = distributions(bundle / "dist")
    for source in original:
        shutil.copyfile(source, runtime / "dist" / source.name)
    prepare(checkout, runtime, _inspection_case=case, _inspection_packet=packet)
    copied = distributions(runtime / "dist")
    if [(path.name, digest(path)) for path in copied] != [(path.name, digest(path)) for path in original]:
        raise ValueError("Compatibility distributions differ from the smoke pair")
    check_runtime_pair(runtime)


def check_runtime_pair(bundle: Path) -> None:
    """Compare both manifests and actual archives before CI compatibility installation."""
    parent = bundle.parent
    root_manifest = json.loads((parent / "ci-package/manifest.json").read_text(encoding="utf-8"))
    runtime_manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(root_manifest, True)
    verify_manifest(parent, root_manifest)
    original = distributions(parent / "dist")
    copied = distributions(bundle / "dist")
    for source, copy in zip(original, copied, strict=True):
        name = "dist/" + source.name
        expected = {"size": source.stat().st_size, "sha256": digest(source)}
        if (source.name != copy.name or digest(copy) != expected["sha256"]
                or root_manifest["files"].get(name) != expected
                or runtime_manifest["files"].get(name) != expected):
            raise ValueError("Compatibility distributions differ from the smoke pair")
    if inspection_authority(root_manifest) != inspection_authority(runtime_manifest):
        raise ValueError("Compatibility fixture authority differs from the smoke pair")
    root_packet, runtime_packet = load_inspection_packet(parent, root_manifest), load_inspection_packet(bundle, runtime_manifest)
    if (root_packet != runtime_packet or root_manifest.get("inspection") != runtime_manifest.get("inspection")
            or root_packet is not None and inspection_builder_sources(root_manifest) != inspection_builder_sources(runtime_manifest)):
        raise ValueError("Compatibility inspection packet or builder authorities differ")
    if (sealed_module_sources(root_manifest) != sealed_module_sources(runtime_manifest)
            or root_manifest["module_sources_sha256"] != runtime_manifest["module_sources_sha256"]
            or inspection_case(parent, root_manifest) != inspection_case(bundle, runtime_manifest)
            or root_manifest["inspection_api"] != runtime_manifest["inspection_api"]):
        raise ValueError("Compatibility module or inspection seals differ from the smoke pair")


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
    encoding = markdown_encoding(receipt)
    body = markdown.encode(encoding)
    line = (json.dumps(receipt, ensure_ascii=True, sort_keys=True) + os.linesep).encode("ascii")
    stdout_body = body if encoding == "utf-8" else markdown.replace("\n", os.linesep).encode("ascii")
    wanted = line + (stdout_body if output is None and code == 0 else b"")
    if (result.returncode, result.stdout, result.stderr) != (code, wanted, b""):
        raise ValueError("CLI exit, receipt, Markdown placement or stderr changed")
    if output is not None and output.read_bytes() != body:
        raise ValueError("CLI output bytes changed")
    return {"exit": result.returncode, "stdout": result.stdout.decode("utf-8"),
            "stderr": result.stderr.decode("utf-8")}


def markdown_encoding(receipt: dict) -> str:
    """Keep historical seals while current inspected output carries literal UTF-8."""
    inspected = receipt.get("inspection", {}).get("schema") == "brewdoc.inspection/1"
    return "utf-8" if inspected or receipt["route"] in UNICODE_ROUTES else "ascii"


def checked_projection(source: Path, actual: tuple[int, dict, str]) -> tuple[int, dict, str]:
    """Verify full new evidence before comparing an immutable readable-output seal."""
    code, receipt, markdown = actual
    if "inspection" not in receipt:
        return actual
    from brewdoc.inspection import (_inspect_document, inspection_artifacts, inspection_markdown,
                                    inspection_summary, readable_projection)
    from brewdoc.service import _append_inspection

    report, payloads = _inspect_document(source)
    references = [item.to_dict() for item in inspection_artifacts(report, payloads)]
    observed = [item for item in receipt.get("artifacts", ()) if item["key"].startswith("inspection/")]
    readable = markdown if receipt["route"] == "text" else readable_projection(markdown)
    supplement = "" if receipt["route"] == "text" else inspection_markdown(report)
    if (receipt["inspection"] != inspection_summary(report) or observed != references
            or markdown != _append_inspection(readable, supplement)):
        raise ValueError("Installed inspection differs from source-bound evidence")
    legacy = [item for item in receipt["artifacts"] if not item["key"].startswith("inspection/")]
    rendering = {key: legacy if key == "artifacts" else value for key, value in receipt.items()
                 if key != "inspection"}
    return code, rendering, readable


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
    code, receipt, markdown = checked_projection(source, actual)
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
    if brewdoc.render_epub(path) != (checked_projection(path, actual)[2], expected_tally):
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


def probe_new_refusals(console: str) -> list[dict]:
    """Check new local routes refuse malformed input without changing destinations."""
    import brewdoc

    observed = []
    for suffix in ("md", "txt", "odt", "odp", "eml"):
        path = Path.cwd() / ("refused." + suffix)
        if suffix in {"odt", "odp"}:
            with zipfile.ZipFile(path, "w"):
                pass
        else:
            path.write_bytes(b"Content-Type: text/plain; charset=unknown-brewdoc\n\nbody"
                             if suffix == "eml" else b"invalid UTF-8: \xff")
        out = Path.cwd() / (suffix + "-refusal.md")
        sentinel = b"existing output\n"
        out.write_bytes(sentinel)
        actual = brewdoc.run(path, out=out)
        code, receipt, markdown = actual
        if ((code, receipt["file_ok"], receipt["route"], receipt["units"], markdown,
             out.read_bytes()) != (1, False, "text" if suffix in {"md", "txt"} else suffix,
                                  0, "", sentinel) or not receipt["reason"]):
            raise ValueError(f"Installed {suffix} refusal changed existing output")
        result = subprocess.run([console, str(path), "--out", str(out)], capture_output=True, timeout=120)
        checked = check_cli(result, actual)
        if out.read_bytes() != sentinel:
            raise ValueError(f"Installed {suffix} console refusal changed existing output")
        observed.append({"receipt": receipt, "cli": checked, "preserved_output_sha256": digest(out)})
    return observed


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


def check_js_refusal(receipt: dict) -> None:
    """Require an unavailable runtime to stop before any acquisition request."""
    acquisition = receipt.get("acquisition", {})
    if (receipt.get("reason") != "URL acquisition refused during prerequisites"
            or receipt.get("receipt_schema") != "brewdoc.receipt/2" or receipt.get("file_ok") is not False
            or acquisition.get("refusal_stage") != "prerequisites"
            or acquisition.get("counts", {}).get("requests") != 0):
        raise ValueError("Unavailable JS prerequisite refusal changed")


def probe_js(enabled: bool) -> dict:
    """Check installed optional capture or its explicit platform/dependency refusal."""
    import brewdoc
    from brewdoc import BrewdocError
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
        except BrewdocError as error:
            expected = ("JS installed native prerequisites unsupported" if enabled else
                        "JS runtime unavailable; install brewdoc[render-js]")
            if str(error) != expected:
                raise ValueError("Unavailable JS runtime refusal changed") from error
            scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
            console = scripts / ("brewdoc.exe" if os.name == "nt" else "brewdoc")
            refused = subprocess.run([str(console), "--url", "https://fixture.test/", "--render-js"],
                                     capture_output=True, timeout=15)
            receipt = json.loads(refused.stdout)
            check_js_refusal(receipt)
            check_cli(refused, (1, receipt, ""))
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


def probe_article(console: str) -> dict:
    """Check installed article selection, fallback, body-only and non-HTML refusal."""
    import brewdoc

    source = Path.cwd() / "article.html"
    source.write_text('<html><head><title>Document title</title></head><body>'
                      '<nav>outside navigation</nav><p>outside paragraph</p><article><h1>Selected heading</h1>'
                      '<p>Retained café fact.</p></article><footer>outside footer</footer></body></html>',
                      encoding="utf-8")
    default = brewdoc.run(source)
    actual = brewdoc.run(source, article=True)
    code, receipt, markdown = actual
    omission = "body content outside the selected article or main subtree; complete page coverage is not claimed"
    chapter = '<a id="brewdoc-chapter-000001"></a>\n## Chapter 1: "Document title"\n\n'
    body = "### Selected heading\n\nRetained café fact.\n"
    readable = checked_projection(source, actual)[2]
    default_readable = checked_projection(source, default)[2]
    if ((code, receipt["file_ok"], receipt["route"], receipt["not_carried"][-1]) != (0, True, "html", omission)
            or "article_fallback" in receipt or not readable.endswith(chapter + body)
            or not default_readable.endswith(chapter + "outside paragraph\n\n" + body) or omission in default_readable
            or brewdoc.run(source, article=False) != default or brewdoc.render_html(source, article=True)[0] != readable):
        raise ValueError("Installed article selection or default landmark drop changed")
    cli = [check_cli(subprocess.run([console, str(source), "--article"], capture_output=True, timeout=120), actual)]
    out = Path.cwd() / "article.md"
    expected = (code, {**receipt, "out": str(out)}, markdown)
    cli.append(check_cli(subprocess.run([console, str(source), "--article", "--out", str(out)],
                                        capture_output=True, timeout=120), expected, out))
    body_only = brewdoc.run(source, article=True, body_only=True)
    if (body_only[:2] != (code, receipt) or checked_projection(source, body_only)[2] != body):
        raise ValueError("Installed article body-only output changed")
    cli.append(check_cli(subprocess.run([console, str(source), "--article", "--body-only"],
                                        capture_output=True, timeout=120), body_only))
    source.write_text('<html><body><p>Unmarked page</p></body></html>', encoding="utf-8")
    plain = brewdoc.run(source)
    fallback = brewdoc.run(source, article=True)
    if fallback != (0, {**plain[1], "article_fallback": True}, plain[2]):
        raise ValueError("Installed article fallback differs from the default page")
    cli.append(check_cli(subprocess.run([console, str(source), "--article"], capture_output=True, timeout=120),
                         fallback))
    text = Path.cwd() / "article.txt"
    text.write_text("Plain text\n", encoding="utf-8")
    sentinel = b"existing output\n"
    out.write_bytes(sentinel)
    refused = brewdoc.run(text, out=out, article=True)
    if ((refused[0], refused[1]["file_ok"], refused[1]["reason"], refused[2], out.read_bytes()) !=
            (1, False, "--article is an HTML-only option", "", sentinel)):
        raise ValueError("Installed non-HTML article refusal changed existing output")
    cli.append(check_cli(subprocess.run([console, str(text), "--article", "--out", str(out)],
                                        capture_output=True, timeout=120), refused))
    if out.read_bytes() != sentinel:
        raise ValueError("Installed article console refusal changed existing output")
    return {"receipt": receipt, "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "fallback": fallback[1], "refusal": refused[1], "preserved_output_sha256": digest(out), "cli": cli}


def probe(checkout: Path, bundle: Path, render_js: bool = False, *, smoke: bool = False,
          expected_python: str | None = None, target: str | None = None) -> dict:
    """Check installed identity and console/output contracts for the requested scope."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(manifest, smoke)
    identity = runtime_identity()
    if expected_python is not None:
        check_runtime(identity, expected_python, target)
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
    if Path.cwd().resolve().is_relative_to(checkout.resolve()):
        raise ValueError("Installed inspection source must be outside the checkout")
    expected_inspection = inspection_case(bundle, manifest)
    inspection_source = Path.cwd() / expected_inspection["path"]
    inspection_source.write_bytes(check_inspection_case(expected_inspection))
    inspection_api = probe_inspection_api(inspection_source, expected_inspection)
    packet = load_inspection_packet(bundle, manifest)
    inspection = probe_inspection_packet(packet, Path.cwd()) if packet is not None else None
    packet_evidence = {"inspection": inspection} if packet is not None else {}
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    console = str(scripts / ("brewdoc.exe" if os.name == "nt" else "brewdoc"))
    checked = command([console, "--self-check"], Path.cwd())
    if (checked.stdout, checked.stderr) != (("self-check: ok (38 checks)" + os.linesep).encode(), b""):
        raise ValueError("Installed console self-check verdict changed")
    if smoke:
        return {"verification_scope": "smoke", "python": sys.version, "runtime": identity,
                "module": str(Path(brewdoc.__file__).resolve()), "prefix": sys.prefix,
                "self_check": checked.stdout.decode(), "inspection_api": inspection_api, **packet_evidence, **probe_smoke(checkout, console),
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
        _code, rendering, _readable = checked_projection(path, actual)
        expected_snapshot = {key: value for key, value in snapshot.items() if key != "accepted_output"}
        if {key: code if key == "rc" else rendering[key] for key in expected_snapshot} != expected_snapshot:
            raise ValueError(f"Installed route receipt changed: {source.name}")
        routes.append({"input": document["path"], "input_sha256": document["sha256"],
                       "receipt": receipt, "markdown": markdown, "markdown_sha256": hashlib.sha256(
                           markdown.encode(markdown_encoding(receipt))).hexdigest()})
        if path.suffix == ".epub":
            epubs.append(probe_epub(path, actual, snapshot.get("accepted_output", {}), console))
        if path.suffix in {".html", ".xlsx", ".odt", ".odp", ".eml", ".md", ".txt"}:
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
    unsupported = Path("unsupported.bin")
    cli.append(check_cli(subprocess.run([console, str(unsupported)], capture_output=True, timeout=120), brewdoc.run(unsupported)))
    return {"verification_scope": "full", "python": sys.version, "runtime": identity,
            "module": str(Path(brewdoc.__file__).resolve()),
            "prefix": sys.prefix, "self_check": checked.stdout.decode(), "inspection_api": inspection_api, **packet_evidence, "routes": routes,
            "cli": cli, "artifacts": artifacts, "epub": epubs,
            "article": probe_article(console),
            "epub_refusal": probe_epub_refusal(console), "new_refusals": probe_new_refusals(console),
            "javascript": probe_js(render_js),
            "packages": sorted((item.metadata["Name"], item.version)
                               for item in importlib.metadata.distributions())}


def verify(checkout: Path, bundle: Path, *, smoke: bool = False,
           expected_python: str | None = None, target: str | None = None) -> None:
    """Install wheel and sdist separately outside checkout, then invoke isolated probes."""
    started = time.monotonic()
    identity = runtime_identity()
    manifest = json.loads((bundle / "ci-package/manifest.json").read_text(encoding="utf-8"))
    check_scope(manifest, smoke)
    if expected_python is not None:
        check_runtime(identity, expected_python, target)
    if not smoke and VERIFIER_SOURCE not in manifest["source"]:
        raise ValueError("Full installed verification requires the verifier source hash")
    if not smoke and digest(Path(__file__)) != manifest["source"][VERIFIER_SOURCE]:
        raise ValueError("Installed verifier source differs from the producer")
    verify_manifest(bundle, manifest)
    check_module_sources(checkout, bundle, sealed_module_sources(manifest))
    inspection_authority(manifest, checkout)
    expected_inspection = inspection_case(bundle, manifest)
    packet = load_inspection_packet(bundle, manifest)
    if packet is not None:
        inspection_builder_sources(manifest, checkout)
    if bundle.name == "ci-runtime":
        check_runtime_pair(bundle)
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
            install_started = time.monotonic()
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
            if expected_python is not None:
                argv.extend(["--expected-python", expected_python, "--target", target])
            checked = command(argv, work)
            observed = json.loads(checked.stdout)
            check_inspection_api(observed.get("inspection_api"), expected_inspection)
            if packet is not None:
                check_inspection_result(observed.get("inspection"), packet)
            elif "inspection" in observed:
                raise ValueError("Unexpected installed inspection evidence without a sealed packet")
            if expected_python is not None:
                check_runtime(observed.get("runtime", {}), expected_python, target)
                if observed["runtime"] != identity:
                    raise ValueError("Installed child ABI differs from the parent verifier")
            if smoke and (observed.get("verification_scope"), observed.get("oracle"),
                          [row["probe"] for row in observed.get("probes", [])]) != ("smoke", [], ["epub2", "formula"]):
                raise ValueError("Installed smoke consumer evidence is incomplete")
            results.append({"distribution": distribution.name, "render_js": render_js,
                            "sha256": digest(distribution),
                            "elapsed_seconds": time.monotonic() - install_started,
                            "probe": observed})
    report = {"checkout_sha": manifest["checkout_sha"], "uv": manifest["uv"],
              "verification_scope": "smoke" if smoke else "full",
              "runtime": identity, "expected_python": expected_python, "target": target,
              "verifier_sha256": digest(Path(__file__)), "elapsed_seconds": time.monotonic() - started,
              "action_cache_hit": os.environ.get("BREWDOC_CACHE_HIT"),
              "action_cache_key": os.environ.get("BREWDOC_CACHE_KEY"), "installations": results}
    suffix = f"-{expected_python}" if expected_python is not None else ""
    (bundle / "ci-package" / f"verification-{sys.platform}{suffix}.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")


def probe_public_inspection(checkout: Path, bundle: Path, *, version: str, source_sha: str, route: str) -> dict:
    """Check a normal public installation without installing local artifacts or loading builders."""
    manifest = json.loads((bundle / "ci-package/manifest.json").read_bytes())
    if route not in ("wheel", "sdist") or (manifest["version"], manifest["checkout_sha"]) != (version, source_sha):
        raise ValueError("Public inspection route or producer identity differs")
    packet = load_inspection_packet(bundle, manifest)
    if packet is None:
        raise ValueError("Public inspection requires a sealed packet")
    inspection_descriptor(packet, version=version, source_sha=source_sha,
                          module_sources_sha256=manifest["module_sources_sha256"])
    import brewdoc

    distribution = importlib.metadata.distribution("brewdoc")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    check_origin(Path(brewdoc.__file__), Path(sys.prefix), checkout, sys.path, direct)
    if (brewdoc.__version__, distribution.version, distribution.metadata["Name"].lower()) != (version, version, "brewdoc"):
        raise ValueError("Public installed distribution version or identity differs")
    with tempfile.TemporaryDirectory(prefix="brewdoc-public-inspection-") as temporary:
        inspection = probe_inspection_packet(packet, Path(temporary))
    return {"schema": 1, "verdict": "PASS", "route": route, "version": version, "source_sha": source_sha,
            "distribution": {"name": "brewdoc", "version": distribution.version}, "inspection": inspection}


def main() -> None:
    """Prepare a producer manifest or verify its unchanged installation inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "prepare-runtime", "verify", "probe", "seal-inspection", "probe-inspection"))
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--bundle", type=Path, default=Path.cwd())
    parser.add_argument("--render-js", action="store_true", help="Probe the isolated optional installation")
    parser.add_argument("--smoke", action="store_true", help="Use two synthetic probes and only base installations")
    parser.add_argument("--expected-python", help="Require this exact normal-GIL CPython patch")
    parser.add_argument("--target", choices=tuple(TARGETS), help="Require this native runtime target")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--source-sha")
    parser.add_argument("--route", choices=("wheel", "sdist"))
    args = parser.parse_args()
    if (args.expected_python is None) != (args.target is None):
        parser.error("--expected-python and --target must be supplied together")
    if args.mode == "seal-inspection":
        if args.input is None:
            parser.error("seal-inspection requires --input")
        control = json.loads(args.input.read_bytes())
        result = seal_inspection_packet(args.checkout.resolve(), control["cases"], version=control["version"],
                                        source_sha=control["source_sha"], modules=control["modules"])
        sys.stdout.buffer.write(canonical_json(result))
        return
    if args.mode == "probe-inspection":
        if None in (args.version, args.source_sha, args.route, args.output):
            parser.error("probe-inspection requires --version, --source-sha, --route and --output")
        result = probe_public_inspection(args.checkout.resolve(), args.bundle.resolve(), version=args.version,
                                         source_sha=args.source_sha, route=args.route)
        args.output.write_bytes(canonical_json(result))
        return
    if args.mode == "probe":
        result = probe(args.checkout.resolve(), args.bundle.resolve(), args.render_js, smoke=args.smoke,
                       expected_python=args.expected_python, target=args.target)
    elif args.mode == "verify":
        result = verify(args.checkout.resolve(), args.bundle.resolve(), smoke=args.smoke,
                        expected_python=args.expected_python, target=args.target)
    elif args.mode == "prepare-runtime":
        result = prepare_runtime(args.checkout.resolve(), args.bundle.resolve())
    else:
        result = prepare(args.checkout.resolve(), args.bundle.resolve(), smoke=args.smoke)
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
