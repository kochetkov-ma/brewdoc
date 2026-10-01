"""Compare sealed source variants on one runtime, retaining actual elapsed-time evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import signal
import statistics
import subprocess
import sys
import sysconfig
import time
import tomllib
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "dc1f0c7fdedeec024840127ed989e1f58416762a"
SEALS = {"A": "e9c0befe12d413de0b5a4a8a350f5c44c5b508a550aecc5338540f6b56e84528",
         "B": "b5f490e6024b4caf88ef606c1c5f4ea1dee3dd51e3454c180c63cc1dc6d8bcf4"}
MODULE_NAMES = {"__init__", "cli", "common", "docx", "htmltext", "output", "pdf",
                "pptx", "selfcheck", "service", "sheets"}
CASES = ("pdf-arxiv-2302.13971v1", "scale-xlsx-5000-1-sparse", "scale-docx-1000",
         "scale-html-table-90000")
WARM_CASES = CASES[:3]
CLI_ENTRY = ("import signal,sys; signal.pthread_sigmask(signal.SIG_UNBLOCK, "
             "{signal.SIGALRM,signal.SIGINT}); from brewdoc.cli import main; sys.exit(main())")


def load(path, name):
    """Load a public helper without introducing another source import path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load(ROOT / "benchmarks/capture_results.py", "latency_capture")
installer = load(ROOT / ".github/workflows/verify_install.py", "latency_environment")


def write_json(path, value):
    """Refuse overwriting any completed or partial evidence file."""
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def source_record(root):
    """Seal exact eleven source bodies with the accepted path/bytes serialization."""
    paths = sorted((Path(root) / "src/brewdoc").rglob("*.py"))
    if {p.relative_to(Path(root) / "src/brewdoc").as_posix() for p in paths} != {
            name + ".py" for name in MODULE_NAMES} or any(p.is_symlink() for p in paths):
        raise ValueError("Source seal changed")
    files, aggregate = {}, hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix()
        body = path.read_bytes()
        files[name] = hashlib.sha256(body).hexdigest()
        aggregate.update(name.encode() + b"\n" + body + b"\0")
    return files, aggregate.hexdigest()


def source_variants(checkout, work):
    """Export only public baseline blobs and current modules into exclusive roots."""
    checkout, work = Path(checkout).resolve(), Path(work)
    names = sorted("src/brewdoc/" + name + ".py" for name in MODULE_NAMES)
    listed = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASELINE,
                                      "src/brewdoc"], cwd=checkout).decode().splitlines()
    if sorted(listed) != names:
        raise ValueError("Baseline source membership changed")
    source_record(checkout)
    variants = []
    for label in ("A", "B"):
        root = work / label
        (root / "src/brewdoc").mkdir(parents=True, exist_ok=False)
        for name in names:
            body = (subprocess.check_output(["git", "show", BASELINE + ":" + name], cwd=checkout)
                    if label == "A" else (checkout / name).read_bytes())
            (root / name).write_bytes(body)
        files, seal = source_record(root)
        if seal != SEALS[label]:
            raise ValueError("Source seal changed")
        variants.append({"id": label, "root": str(root.resolve()), "files": files,
                         "source_sha256": seal})
    return variants


def archive(path, parts):
    """Keep ZIP member order, timestamps and compression fixed outside measurements."""
    with zipfile.ZipFile(path, "x") as package:
        for name, body in sorted(parts.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            package.writestr(info, body)


def generate_inputs(work):
    """Generate the three accepted scale recipes and bind the licensed arXiv fixture."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=False)
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    types = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
             'package/2006/content-types"><Default Extension="rels" ContentType='
             '"application/vnd.openxmlformats-package.relationships+xml"/><Override '
             'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
             '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rows = "".join('<row r="%d">%s</row>' % (r, "".join(
        '<c r="%s%d"><v>%d</v></c>' % (chr(65 + c), r, r + c) for c in (0, 19)))
        for r in range(1, 5001))
    archive(work / "xlsx-5000-1-sparse.xlsx", {
        "[Content_Types].xml": types,
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="%s"><Relationship Id="rId1" '
                       'Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % (package, office),
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="%s"><Relationship Id="rId1" '
                                    'Type="%s/worksheet" Target="worksheets/sheet1.xml"/></Relationships>' % (package, office),
        "xl/workbook.xml": '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="Sheet1" '
                           'sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, office),
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (ns, rows),
    })
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    table = "<w:tbl>" + "".join("<w:tr>" + "".join(
        '<w:tc><w:p><w:r><w:t>R%dC%d</w:t></w:r></w:p></w:tc>' % (r, c)
        for c in range(3)) + "</w:tr>" for r in range(1000)) + "</w:tbl>"
    body = '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Scale document</w:t></w:r></w:p>' + table
    archive(work / "docx-1000.docx", {
        "_rels/.rels": '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" '
                       'Target="word/document.xml"/></Relationships>' % (package, office),
        "word/document.xml": '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (word, body),
    })
    html = "<html><body><table>" + ("<tr>" + "<td>café 世界</td>" * 10 + "</tr>") * 9000 + "</table></body></html>"
    (work / "html-table-90000.html").write_text(html, encoding="utf-8")
    corpus = json.loads((ROOT / "benchmarks/corpus.json").read_text())["documents"]
    document = next(item for item in corpus if item["id"] == CASES[0])
    source = ROOT / document["path"]
    actual = capture.file_record(source, str(source))
    if (actual["sha256"], actual["size_bytes"]) != (document["sha256"], document["size_bytes"]):
        raise ValueError("Input identity changed: " + document["id"])
    specs = [(CASES[0], source, {"origin": "licensed public corpus"}),
             (CASES[1], work / "xlsx-5000-1-sparse.xlsx", {"rows": 5000, "columns": 20, "occupied_columns": [0, 19], "source_cells": 10000}),
             (CASES[2], work / "docx-1000.docx", {"table_rows": 1000, "table_cells": 3000, "chapters": 1}),
             (CASES[3], work / "html-table-90000.html", {"table_rows": 9000, "table_cells": 90000, "columns": 10})]
    return [{"id": name, **capture.file_record(path, str(path.resolve())), "dimensions": dimensions}
            for name, path, dimensions in specs]


def environment_record():
    """Bind interpreter, installed versions and native library bytes without copying them."""
    distributions = list(importlib.metadata.distributions())
    packages = sorted([item.metadata["Name"], item.version] for item in distributions)
    native = {}
    for item in distributions:
        for name in item.files or ():
            if Path(name).suffix in {".so", ".pyd", ".dylib"}:
                path = Path(item.locate_file(name)).resolve()
                native[str(path)] = installer.digest(path)
    return {"python": str(Path(sys.executable).resolve()), "prefix": sys.prefix, "version": sys.version,
            "binary_sha256": installer.digest(Path(sys.executable).resolve()),
            "packages": packages, "native": native, "platform": platform.platform(),
            "build": {name: sysconfig.get_config_var(name) for name in
                      ("CONFIG_ARGS", "CC", "CFLAGS", "SOABI", "MULTIARCH")}}


def verify_locked_graph(environment):
    """Check active lock edges and installed requirements with the existing locked marker library."""
    from packaging.markers import Marker
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {item["name"]: item for item in lock["package"]}
    if len(packages) != len(lock["package"]):
        raise ValueError("Ambiguous locked package membership")
    project = packages["brewdoc"]
    pending = [("brewdoc", item) for item in project["dependencies"] + project["dev-dependencies"]["dev"]]
    expected, edges = {}, []
    while pending:
        owner, dependency = pending.pop()
        if dependency.get("marker") and not Marker(dependency["marker"]).evaluate():
            continue
        name = dependency["name"]
        package = packages[name]
        edges.append([owner, name, package["version"]])
        if name not in expected:
            expected[name] = package["version"]
            pending.extend((name, child) for child in package.get("dependencies", []))
    observed = {canonicalize_name(name): version for name, version in environment["packages"]}
    if observed != expected:
        raise ValueError("Installed graph differs from active lock")
    metadata_edges = []
    for distribution in importlib.metadata.distributions():
        owner = canonicalize_name(distribution.metadata["Name"])
        for text in distribution.requires or ():
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = canonicalize_name(requirement.name)
            if name not in observed or observed[name] not in requirement.specifier:
                raise ValueError("Installed requirement edge is unsatisfied")
            metadata_edges.append([owner, name, observed[name]])
    locked_edges = {tuple(edge) for edge in edges if edge[0] != "brewdoc"}
    if {tuple(edge) for edge in metadata_edges} != locked_edges:
        raise ValueError("Installed requirement edges differ from active lock")
    return {"active_versions": expected, "active_edges": sorted(edges),
            "metadata_edges": sorted(metadata_edges)}


def check_origin(origins, variant):
    """Reject missing, extra or foreign module origins in a source worker."""
    expected = {name: str((Path(variant["root"]) / "src/brewdoc" / (name + ".py")).resolve())
                for name in MODULE_NAMES}
    if origins != expected:
        raise ValueError("Imported source origins differ from sealed variant")


def check_search_paths(paths, variant):
    """Reject any competing package directory, even if it has not won an import."""
    selected = (Path(variant["root"]) / "src").resolve()
    for path in paths:
        directory = Path(path).resolve()
        if (directory / "brewdoc").is_dir() and directory != selected:
            raise ValueError("Foreign Brewdoc source search path")


def child_environment(variant):
    """Expose only the selected source root and suppress mutable bytecode writes."""
    environment = installer.clean_environment()
    environment.update({"PYTHONPATH": str(Path(variant["root"]) / "src"), "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def owned_capture(args, environment, seconds=120):
    """Arm a blocking-wait deadline only after registering the real child; restore and reap."""
    if signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, set()):
        raise ValueError("Owned deadline requires unblocked SIGALRM")
    owned, expired = [], False
    previous_module = capture.subprocess
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 0)

    def interrupt(_signum, _frame):
        """Convert the owned deadline into the capture helper's interruption path."""
        nonlocal expired
        expired = True
        raise KeyboardInterrupt

    def spawn(*argv, **kwargs):
        """Block interruption until the spawned process can always be reaped."""
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM, signal.SIGINT})
        try:
            process = subprocess.Popen(*argv, env=environment, **kwargs)
            owned.append(process)
            signal.setitimer(signal.ITIMER_REAL, seconds)
            return process
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    signal.signal(signal.SIGALRM, interrupt)
    capture.subprocess = SimpleNamespace(Popen=spawn, TimeoutExpired=subprocess.TimeoutExpired,
                                         DEVNULL=subprocess.DEVNULL)
    try:
        args.timeout = None
        code = capture.capture(args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for process in owned:
            capture.stop_process(process)
        capture.subprocess = previous_module
        signal.signal(signal.SIGALRM, previous_handler)
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        if owned:
            write_json(Path(args.output_dir) / "ownership.json", {
                "pids": [process.pid for process in owned], "deadline_expired": expired,
                "ownership_closed": all(process.poll() is not None for process in owned)})
    result_file = Path(args.output_dir) / "result.json"
    result = json.loads(result_file.read_text())
    result.update(wait_mode="blocking", deadline_seconds=seconds, deadline_expired=expired,
                  ownership_closed=all(process.poll() is not None for process in owned))
    result_file.write_text(json.dumps(result, indent=2) + "\n")
    return code


def verify_context(variant, environment, inputs):
    """Check source/input/runtime seals and retain actual eleven-module import evidence."""
    files, seal = source_record(Path(variant["root"]))
    if (files, seal) != (variant["files"], variant["source_sha256"]):
        raise ValueError("Source seal changed")
    for item in inputs:
        record = capture.file_record(Path(item["path"]), item["path"])
        if (record["sha256"], record["size_bytes"]) != (item["sha256"], item["size_bytes"]):
            raise ValueError("Input identity changed: " + item["id"])
    if environment_record() != environment:
        raise ValueError("Runtime environment changed")
    script = ("import signal; signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGALRM,signal.SIGINT}); "
              "import importlib,json,pathlib,sys; "
              f"names={sorted(MODULE_NAMES)!r}; "
              "mods={n:importlib.import_module('brewdoc' if n=='__init__' else 'brewdoc.'+n) for n in names}; "
              "print(json.dumps({'origins':{n:str(pathlib.Path(m.__file__).resolve()) for n,m in mods.items()},'paths':sys.path}))")
    evidence = Path(variant["root"]).parent / (variant["id"] + "-context-" + str(time.time_ns()))
    args = SimpleNamespace(input=Path(inputs[0]["path"]), output_dir=evidence, tool="source-origin",
                           tool_version=variant["source_sha256"], timeout=None,
                           command=[sys.executable, "-v", "-c", script])
    if owned_capture(args, child_environment(variant)):
        raise ValueError("Source origin probe failed")
    imported = json.loads((evidence / "stdout.bin").read_bytes())
    origins = imported["origins"]
    check_origin(origins, variant)
    check_search_paths(imported["paths"], variant)
    return {"origins": origins, "load": os.getloadavg(), "at_utc": datetime.now(timezone.utc).isoformat(),
            "cpu_count": os.cpu_count(), "cpu_pressure": Path("/proc/pressure/cpu").read_text()
            if Path("/proc/pressure/cpu").exists() else None}


def normalized_result(directory, mode):
    """Compare full output objects while normalizing only the declared output location."""
    directory = Path(directory)
    result = json.loads((directory / "result.json").read_text())
    if result["status"] not in {"success", "nonzero_exit"} or result["capture_errors"]:
        raise ValueError("Conversion capture did not complete")
    stdout, stderr = (directory / "stdout.bin").read_bytes(), (directory / "stderr.bin").read_bytes()
    line, separator, body = stdout.partition(b"\n")
    if not separator:
        raise ValueError("Receipt line is missing")
    receipt = json.loads(line)
    fields = list(receipt)
    for index, item in enumerate(receipt.get("artifacts", [])):
        if item.get("out") is not None:
            payload = directory / "artifacts" / f"artifact-{index}.bin"
            if item["out"] != str(payload):
                raise ValueError("Artifact output path changed")
            actual = capture.file_record(payload, payload.name)
            if (actual["size_bytes"], actual["sha256"]) != (item["byte_size"], item["sha256"]):
                raise ValueError("Artifact payload differs from receipt")
            item["out"] = None
    output = directory / "artifacts/document.md"
    artifacts = result["artifacts"]
    refused = receipt.get("file_ok") is False and result["returncode"] != 0
    if refused:
        if receipt["out"] is not None or body or output.exists() or artifacts:
            raise ValueError("Refused conversion wrote output")
    if mode == "out":
        if not refused and receipt["out"] != str(output):
            raise ValueError("Receipt output path changed")
        if not refused and (output.is_symlink() or not output.is_file()):
            raise ValueError("Successful output file is missing or linked")
        receipt["out"] = None
        if body:
            raise ValueError("File output leaked Markdown to stdout")
        body = output.read_bytes() if output.exists() else b""
    elif mode != "stdout" or receipt["out"] is not None:
        raise ValueError("Unsupported output mode")
    return {"returncode": result["returncode"], "stderr": stderr.decode("utf-8"),
            "receipt": receipt, "receipt_fields": fields, "markdown": body.decode("utf-8"),
            "markdown_sha256": hashlib.sha256(body).hexdigest(), "artifacts": artifacts}


def capture_cli(variant, input_record, mode, evidence):
    """Measure fresh CLI spawn through blocking exit/reap, keeping hashing outside clocks."""
    command = [sys.executable, "-c", CLI_ENTRY, "{input}"]
    if mode == "out":
        command += ["--out", "{output_dir}/document.md"]
    elif mode != "stdout":
        raise ValueError("Unsupported output mode")
    for index, reference in enumerate(input_record.get("artifact_requests", [])):
        if reference.get("availability") == "available":
            command += ["--artifact", reference["key"] + f"={{output_dir}}/artifact-{index}.bin"]
    args = SimpleNamespace(input=Path(input_record["path"]), output_dir=Path(evidence), tool="brewdoc",
                           tool_version=variant["source_sha256"], timeout=None, command=command)
    owned_capture(args, child_environment(variant))
    raw = json.loads((Path(evidence) / "result.json").read_text())
    normalized = normalized_result(evidence, mode)
    return {"variant": variant["id"], "case": input_record["id"], "mode": mode,
            "elapsed_seconds": raw["elapsed_seconds"], "result": normalized,
            "evidence": str(evidence)}


def warm_worker(variant, case, output):
    """Validate real imports before two warmups and nine immediate run-only intervals."""
    files, seal = source_record(Path(variant["root"]))
    if (files, seal) != (variant["files"], variant["source_sha256"]):
        raise ValueError("Source seal changed")
    modules = {name: importlib.import_module("brewdoc" if name == "__init__" else "brewdoc." + name)
               for name in MODULE_NAMES}
    check_origin({name: str(Path(module.__file__).resolve()) for name, module in modules.items()}, variant)
    check_search_paths(sys.path, variant)
    run = modules["__init__"].run
    source = Path(case["path"])
    before = capture.file_record(source, str(source))
    if (before["sha256"], before["size_bytes"]) != (case["sha256"], case["size_bytes"]):
        raise ValueError("Input identity changed: " + case["id"])
    warmups = [run(source) for _ in range(2)]
    if any(code != 0 or receipt.get("file_ok") is not True for code, receipt, _body in warmups):
        raise ValueError("Warm recipe must convert successfully")
    records = []
    for sample in range(9):
        started = time.perf_counter()
        actual = run(source)
        elapsed = time.perf_counter() - started
        code, receipt, markdown = actual
        records.append({"variant": variant["id"], "case": case["id"], "mode": "warm", "sample": sample,
                        "elapsed_seconds": elapsed, "result": {"returncode": code, "receipt": receipt,
                        "markdown": markdown, "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
                        "stderr": "", "artifacts": []}})
    if capture.file_record(source, str(source)) != before or source_record(Path(variant["root"])) != (files, seal):
        raise ValueError("Worker source or input changed")
    if any(item != warmups[0] for item in warmups[1:]) or any(
            (r["result"]["returncode"], r["result"]["receipt"], r["result"]["markdown"]) != warmups[0]
            for r in records):
        raise ValueError("Warm conversion parity changed")
    write_json(output, {"records": records, "environment": environment_record(), "warmups": 2})


def compare_results(records):
    """Fail closed on complete conversion parity before deriving scoped wall statistics."""
    groups = {}
    for record in records:
        key = (record["case"], record["mode"])
        groups.setdefault(key, []).append(record)
    summaries = []
    for (case, mode), group in sorted(groups.items()):
        if {item["variant"] for item in group} != {"A", "B"}:
            raise ValueError("Both source variants are required")
        reference = group[0]["result"]
        if any(item["result"] != reference for item in group):
            raise ValueError(f"Conversion parity changed: {case}/{mode}")
        blocks = {}
        for record in group:
            value = record["elapsed_seconds"]
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid elapsed sample")
            blocks.setdefault((record["variant"], record.get("block", 0)), []).append(value)
        stats = []
        for (variant, block), values in sorted(blocks.items()):
            quartiles = statistics.quantiles(values, method="inclusive") if len(values) > 1 else [values[0]] * 3
            stats.append({"variant": variant, "block": block, "samples": values,
                          "median": statistics.median(values), "IQR": quartiles[2] - quartiles[0],
                          "minimum": min(values), "maximum": max(values)})
        a, b = [x for x in stats if x["variant"] == "A"], [x for x in stats if x["variant"] == "B"]
        if len(a) != len(b):
            raise ValueError("Unmatched source blocks")
        pairs = [{"gain_percent": 100 * (1 - y["median"] / x["median"]),
                  "relative_IQR_percent": max(100 * x["IQR"] / x["median"], 100 * y["IQR"] / y["median"])}
                 for x, y in zip(a, b)]
        decisions = [(x["gain_percent"] > 0, x["gain_percent"] >= 10,
                      x["gain_percent"] <= -5) for x in pairs]
        extra = any(x["relative_IQR_percent"] > 5 or abs(x["gain_percent"] - 10) <= x["relative_IQR_percent"]
                    or abs(x["gain_percent"] + 5) <= x["relative_IQR_percent"] for x in pairs)
        extra |= len(set(decisions)) > 1
        accepted = bool(pairs) and all(x["gain_percent"] - 10 > x["relative_IQR_percent"] for x in pairs)
        summaries.append({"case": case, "mode": mode, "blocks": stats, "pairs": pairs,
                          "extra_BA_eligible": extra, "scoped_gain_supported": accepted,
                          "material_control_slowdown": any(x["gain_percent"] <= -5 for x in pairs)})
    return summaries


def validate_timed_records(records, eligible=()):
    """Check the declared finite timed schedule separately from untimed corpus parity."""
    groups = {(case, mode) for case in CASES for mode in ("stdout", "out")}
    groups |= {(case, "warm") for case in WARM_CASES}
    eligible = set(eligible)
    if not eligible <= groups:
        raise ValueError("Timed schedule is incomplete or duplicated")
    expected = {(block, label, case, mode): 9 if mode == "warm" else 7
                for block, label in enumerate(("A", "B", "B", "A")) for case, mode in groups}
    expected.update({(block, label, case, mode): 9 if mode == "warm" else 7
                     for block, label in ((4, "B"), (5, "A")) for case, mode in eligible})
    actual = {}
    for record in records:
        key = (record.get("block"), record.get("variant"), record.get("case"), record.get("mode"))
        actual.setdefault(key, []).append(record.get("sample"))
    if set(actual) != set(expected) or any(
            values != list(range(expected[key])) for key, values in actual.items()):
        raise ValueError("Timed schedule is incomplete or duplicated")
    compare_results(records)


def warm_records(result):
    """Read the worker's retained records before accepting its measured sample set."""
    if result.get("warmups") != 2 or len(result.get("records", [])) != 9:
        raise ValueError("Warm worker count changed")
    return result["records"]


def verify_transport(records):
    """Check existing untimed stdout/out observations without adding captures."""
    groups = {}
    for record in records:
        groups.setdefault((record["case"], record["variant"]), []).append(record)
    for (case, variant), rows in groups.items():
        if sorted(row["mode"] for row in rows) != ["out", "stdout"]:
            raise ValueError(f"Corpus transport result changed: {case}/{variant}")
        results = []
        for row in rows:
            result = dict(row["result"])
            documents = [item for item in result["artifacts"] if item["path"] == "artifacts/document.md"]
            expected = []
            if row["mode"] == "out" and result["returncode"] == 0:
                expected = [{"path": "artifacts/document.md", "sha256": result["markdown_sha256"],
                             "size_bytes": len(result["markdown"].encode("utf-8"))}]
            if documents != expected:
                raise ValueError(f"Corpus transport result changed: {case}/{variant}")
            result["artifacts"] = [item for item in result["artifacts"] if item["path"] != "artifacts/document.md"]
            results.append(result)
        if results[0] != results[1]:
            raise ValueError(f"Corpus transport result changed: {case}/{variant}")


def run_comparison(output):
    """Execute one sealed finite correctness/ABBA attempt, retaining failures without retries."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    variants = source_variants(ROOT, output / "sources")
    inputs = generate_inputs(output / "inputs")
    environment = environment_record()
    if any(name.lower() == "brewdoc" for name, _version in environment["packages"]):
        raise ValueError("Comparison runtime must not contain an installed Brewdoc")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Comparison requires the selected CPython 3.12 runtime")
    locked_graph = verify_locked_graph(environment)
    bound_paths = [ROOT / "pyproject.toml", ROOT / "uv.lock", Path(__file__),
                   ROOT / "benchmarks/capture_results.py", ROOT / ".github/workflows/verify_install.py",
                   ROOT / "benchmarks/corpus.json", ROOT / "tests/fixtures/receipts.json"]
    support = {str(path): installer.digest(path) for path in bound_paths}
    context = {"environment": environment, "variants": variants, "inputs": inputs,
               "utility": installer.digest(Path(__file__)), "capture_helper": installer.digest(ROOT / "benchmarks/capture_results.py"),
               "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
               "parents": subprocess.check_output(["git", "show", "-s", "--format=%P", "HEAD"], cwd=ROOT).decode().strip(),
               "pr_head": os.environ.get("BREWDOC_PR_HEAD"), "runner_image": os.environ.get("ImageVersion"),
               "runner_arch": platform.machine(), "cpu_count": os.cpu_count(), "support_files": support,
               "locked_graph": locked_graph,
               "memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")}
    write_json(output / "context.json", context)
    corpus = json.loads((ROOT / "benchmarks/corpus.json").read_text())["documents"]
    expected = json.loads((ROOT / "tests/fixtures/receipts.json").read_text())
    if len(corpus) != 47:
        raise ValueError("Full corpus membership changed")
    parity = []
    for variant in variants:
        verify_context(variant, environment, inputs)
        for item in corpus:
            source = ROOT / item["path"]
            actual = capture.file_record(source, str(source))
            if (actual["sha256"], actual["size_bytes"]) != (item["sha256"], item["size_bytes"]):
                raise ValueError("Input identity changed: " + item["id"])
            record = {**item, "path": str(source)}
            snapshot = expected[str(source.relative_to(ROOT / "tests/fixtures"))]
            record["artifact_requests"] = snapshot.get("artifacts", [])
            for mode in ("stdout", "out"):
                row = capture_cli(variant, record, mode, output / "parity" / variant["id"] / item["id"] / mode)
                value = row["result"]
                observed = {key: value["returncode"] if key == "rc" else value["receipt"][key] for key in snapshot}
                if observed != snapshot:
                    raise ValueError("Indexed conversion expectation changed")
                parity.append(row)
        verify_context(variant, environment, inputs)
    compare_results(parity)
    verify_transport(parity)
    records, observations = [], []

    def closure():
        """Reject checkout or helper changes between measured blocks."""
        if {str(path): installer.digest(path) for path in bound_paths} != support:
            raise ValueError("Benchmark support files changed")
        if source_record(ROOT) != (variants[1]["files"], variants[1]["source_sha256"]):
            raise ValueError("Checkout source changed")
        if verify_locked_graph(environment_record()) != locked_graph:
            raise ValueError("Runtime environment changed")

    def block(number, label, selected):
        """Run only the declared case/mode samples in one fresh source block."""
        closure()
        variant = next(item for item in variants if item["id"] == label)
        observations.append(verify_context(variant, environment, inputs))
        for item in inputs:
            modes = {mode for case, mode in selected if case == item["id"]}
            for mode in ("stdout", "out"):
                if mode in modes:
                    for sample in range(7):
                        row = capture_cli(variant, item, mode, output / "timed" / str(number) / item["id"] / mode / str(sample))
                        if row["result"]["returncode"] != 0:
                            raise ValueError("Timed recipe must convert successfully")
                        records.append({**row, "block": number, "sample": sample})
            if "warm" in modes:
                evidence = output / "timed" / str(number) / item["id"] / "warm"
                args = SimpleNamespace(input=Path(item["path"]), output_dir=evidence, tool="warm-worker",
                    tool_version=variant["source_sha256"], timeout=None,
                    command=[sys.executable, str(Path(__file__).resolve()), "worker", "--variant-root", variant["root"],
                             "--source-sha256", variant["source_sha256"], "--input", item["path"],
                             "--case", item["id"], "--output", "{output_dir}/warm.json"])
                if owned_capture(args, child_environment(variant)):
                    raise ValueError("Warm worker failed")
                result = json.loads((evidence / "artifacts/warm.json").read_text())
                if result["environment"] != json.loads(json.dumps(environment)):
                    raise ValueError("Runtime environment changed")
                records.extend({**row, "block": number} for row in warm_records(result))
        observations.append(verify_context(variant, environment, inputs))
        closure()
        write_json(output / f"block-{number}.json", {"records": [r for r in records if r["block"] == number],
                                                    "contexts": observations[-2:]})

    selected = [(case, mode) for case in CASES for mode in ("stdout", "out")]
    selected += [(case, "warm") for case in WARM_CASES]
    for number, label in enumerate(("A", "B", "B", "A")):
        block(number, label, selected)
    validate_timed_records(records)
    summaries = compare_results(records)
    eligible = [(item["case"], item["mode"]) for item in summaries if item["extra_BA_eligible"]]
    eligible += [(case, mode) for case, chosen in eligible if chosen in {"stdout", "out"} for mode in ("stdout", "out")]
    write_json(output / "extra-pair-decision.json", {"assessments": summaries,
                                                    "eligible": sorted(set(eligible))})
    if eligible:
        block(4, "B", set(eligible))
        block(5, "A", set(eligible))
    closure()
    validate_timed_records(records, eligible)
    write_json(output / "comparison.json", {"complete": True, "summaries": compare_results(records),
        "extra_BA": bool(eligible), "parity_captures": len(parity), "records": records,
        "limitations": "Hosted VM isolation is not physical-host exclusivity; no universal gain is implied."})


def main(argv=None):
    """Expose only the frozen comparison and its internal source worker commands."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare", allow_abbrev=False)
    compare.add_argument("--baseline-ref", required=True, choices=[BASELINE])
    compare.add_argument("--output", required=True, type=Path)
    worker = commands.add_parser("worker", allow_abbrev=False)
    worker.add_argument("--variant-root", required=True, type=Path)
    worker.add_argument("--source-sha256", required=True, choices=list(SEALS.values()))
    worker.add_argument("--input", required=True, type=Path)
    worker.add_argument("--case", required=True, choices=WARM_CASES)
    worker.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "compare":
        existed = args.output.exists()
        try:
            run_comparison(args.output)
        except BaseException as exc:
            if not existed and args.output.is_dir():
                write_json(args.output / "failed-attempt.json", {
                    "complete": False, "error": f"{type(exc).__name__}: {exc}",
                    "at_utc": datetime.now(timezone.utc).isoformat()})
            raise
    else:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM, signal.SIGINT})
        files, seal = source_record(args.variant_root)
        if seal != args.source_sha256:
            raise ValueError("Source seal changed")
        label = next(name for name, expected in SEALS.items() if expected == seal)
        context = json.loads((args.variant_root.parent.parent / "context.json").read_text())
        case = next(item for item in context["inputs"] if item["id"] == args.case)
        if str(args.input) != case["path"] or environment_record() != context["environment"]:
            raise ValueError("Runtime environment changed")
        warm_worker({"id": label, "root": str(args.variant_root), "files": files,
                     "source_sha256": seal}, case, args.output)


if __name__ == "__main__":
    main()
