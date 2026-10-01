"""Protect isolated latency measurements from source and result drift."""

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
from types import SimpleNamespace
import zipfile
from xml.etree import ElementTree as ET

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks/compare_latency.py"
MODULES = ("__init__", "cli", "common", "docx", "htmltext", "output", "pdf",
           "pptx", "selfcheck", "service", "sheets")
CASES = ("pdf-arxiv-2302.13971v1", "scale-xlsx-5000-1-sparse",
         "scale-docx-1000", "scale-html-table-90000")


@pytest.fixture
def latency():
    """Load the benchmark without running its command-line entry point."""
    spec = importlib.util.spec_from_file_location("compare_latency_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def variant(tmp_path):
    """Seal an external synthetic source tree with the expected module membership."""
    root = tmp_path / "variant"
    package = root / "src/brewdoc"
    package.mkdir(parents=True)
    files = {}
    for name in MODULES:
        path = package / f"{name}.py"
        path.write_text(f"IDENTITY = {name!r}\n", encoding="utf-8")
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    aggregate = hashlib.sha256()
    for name in sorted(files):
        aggregate.update(name.encode() + b"\n" + (root / name).read_bytes() + b"\0")
    return {"id": "A", "root": str(root), "files": files, "source_sha256": aggregate.hexdigest()}


@pytest.fixture
def generated_inputs(latency, tmp_path, monkeypatch):
    """Substitute a tiny synthetic PDF so recipe guards never read the corpus."""
    checkout = tmp_path / "checkout"
    (checkout / "benchmarks").mkdir(parents=True)
    pdf = checkout / "synthetic.pdf"
    pdf.write_bytes(b"synthetic PDF substitute\n")
    document = {"id": "pdf-arxiv-2302.13971v1", "path": "synthetic.pdf",
                "size_bytes": 25, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    (checkout / "benchmarks/corpus.json").write_text(json.dumps({"documents": [document]}))
    monkeypatch.setattr(latency, "ROOT", checkout)
    first = latency.generate_inputs(tmp_path / "first")
    second = latency.generate_inputs(tmp_path / "second")
    return {record["id"]: (record, second[index]) for index, record in enumerate(first)}


@pytest.fixture
def ready_capture(latency, tmp_path, monkeypatch):
    """Wait for a self-expiring owned child before the capture adapter arms its timer."""
    fifo = tmp_path / "ready.fifo"
    os.mkfifo(fifo)
    ready = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    spawned = []
    original_handler = signal.getsignal(signal.SIGALRM)
    original_timer = signal.getitimer(signal.ITIMER_REAL)
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    sentinel = lambda *_: None
    signal.signal(signal.SIGALRM, sentinel)
    signal.setitimer(signal.ITIMER_REAL, 60, 20)
    source = tmp_path / "input.txt"
    source.write_bytes(b"synthetic\n")
    code = ("import os,signal,sys; signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGALRM,signal.SIGINT}); "
            "signal.alarm(5); "
            "fd=os.open(sys.argv[1],os.O_WRONLY); os.write(fd,b'READY'); "
            "os.close(fd); signal.pause()")
    args = argparse.Namespace(input=source, output_dir=tmp_path / "capture", tool="synthetic",
                              tool_version="1", timeout=None, command=[sys.executable, "-c", code, str(fifo)])

    def spawn(*argv, **kwargs):
        """Bound readiness before returning the actual child for ownership registration."""
        process = subprocess.Popen(*argv, **kwargs)
        spawned.append(process)
        try:
            available, _, _ = select.select([ready], [], [], 3)
            assert available == [ready], "the child must send readiness before the bounded adapter timer"
            assert os.read(ready, 5) == b"READY", "readiness must identify the actual spawned child"
        except BaseException:
            latency.capture.stop_process(process)
            raise
        return process

    monkeypatch.setattr(latency, "subprocess", SimpleNamespace(
        Popen=spawn, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL))
    try:
        yield args, spawned, sentinel, original_mask
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for process in spawned:
            if process.poll() is None:
                latency.capture.stop_process(process)
        signal.signal(signal.SIGALRM, original_handler)
        signal.setitimer(signal.ITIMER_REAL, *original_timer)
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        os.close(ready)


@pytest.fixture
def locked_graph(latency, tmp_path, monkeypatch):
    """Isolate lock and installed metadata without any package installation."""
    (tmp_path / "uv.lock").write_text('''[[package]]
name = "brewdoc"
version = "1.0"
dependencies = [{ name = "alpha" }]
[package.dev-dependencies]
dev = []
[[package]]
name = "alpha"
version = "1.0"
dependencies = [{ name = "beta" }]
[[package]]
name = "beta"
version = "2.0"
''')
    installed = [SimpleNamespace(metadata={"Name": "alpha"}, requires=["beta>=2"]),
                 SimpleNamespace(metadata={"Name": "beta"}, requires=[])]
    monkeypatch.setattr(latency, "ROOT", tmp_path)
    monkeypatch.setattr(latency, "importlib", SimpleNamespace(
        metadata=SimpleNamespace(distributions=lambda: installed)))
    return {"packages": [["alpha", "1.0"], ["beta", "2.0"]]}, installed


@pytest.fixture
def paired_results():
    """Keep semantic receipt fields and refusal reasons in each comparison sample."""
    result = {
        "returncode": 0, "stderr": "", "markdown_sha256": "a" * 64,
        "receipt": {"file_ok": True, "route": "doc", "source": "input.docx",
                    "reason": "doc rendered", "units": 1, "tables": 2,
                    "unit_keys": ["chapter/000001", "chapter/000002"],
                    "dropped": {"controls": 1}, "not_carried": ["images", "comments"],
                    "artifacts": []},
        "artifacts": [{"path": "image.png", "size_bytes": 3, "sha256": "b" * 64}],
    }
    return [{"variant": name, "block": block, "case": "scale-docx-1000",
             "mode": "stdout", "elapsed_seconds": 1.0, "result": deepcopy(result)}
            for block, name in enumerate(("A", "B", "B", "A"))]


@pytest.fixture
def timed_records(paired_results):
    """Build the approved four-block CLI and warm schedule without running conversions."""
    groups = [(case, mode, 7) for case in CASES for mode in ("stdout", "out")]
    groups += [(case, "warm", 9) for case in CASES[:3]]
    return [{"variant": variant, "block": block, "case": case, "mode": mode,
             "sample": sample, "elapsed_seconds": 1.0,
             "result": deepcopy(paired_results[0]["result"])}
            for case, mode, count in groups for block, variant in enumerate(("A", "B", "B", "A"))
            for sample in range(count)]


@pytest.fixture
def transport_records():
    """Keep one real payload inventory while varying only legitimate saved Markdown."""
    markdown = "# same body\n"
    digest = hashlib.sha256(markdown.encode()).hexdigest()
    result = {"returncode": 0, "stderr": "", "markdown": markdown, "markdown_sha256": digest,
              "receipt": {"file_ok": True, "out": None, "reason": "sheet rendered", "tables": 1},
              "receipt_fields": ["file_ok", "out", "reason", "tables"],
              "artifacts": [{"path": "artifacts/artifact-0.bin", "size_bytes": 4, "sha256": "a" * 64}]}
    saved = deepcopy(result)
    saved["artifacts"].append({"path": "artifacts/document.md", "size_bytes": len(markdown.encode()), "sha256": digest})
    modes = {"stdout": result, "out": saved}
    return [{"variant": variant, "block": block, "case": "synthetic", "mode": mode,
             "elapsed_seconds": 1.0, "result": deepcopy(modes[mode])}
            for block, variant in enumerate(("A", "B")) for mode in ("stdout", "out")]


@pytest.mark.parametrize("field,value", [
    ("returncode", 1), ("stderr", "different error"),
    ("markdown_sha256", "c" * 64),
    ("artifacts", [{"path": "image.png", "size_bytes": 4, "sha256": "b" * 64}]),
    ("receipt", {"file_ok": True}),
], ids=["exit", "stderr", "markdown", "artifact", "receipt-fields"])
def test_comparison_rejects_complete_result_mismatch(latency, paired_results, field, value):
    # GIVEN equivalent A/B observations except one changed semantic result
    assert paired_results[0]["result"] == paired_results[1]["result"], "the controls must match initially"
    paired_results[1]["result"][field] = value
    # WHEN comparison reaches parity validation
    # THEN no timing claim is computed from mismatched results
    with pytest.raises(ValueError, match="^Conversion parity changed: scale-docx-1000/stdout$"):
        latency.compare_results(paired_results)


@pytest.mark.parametrize("field,value", [
    ("reason", "different reason"), ("tables", 3), ("dropped", {"controls": 2}),
    ("unit_keys", ["chapter/000002", "chapter/000001"]),
    ("not_carried", ["comments", "images"]),
    ("future_semantic_field", {"exact": 4}),
], ids=["reason", "counter", "drop-counter", "unit-order", "omission-order", "extra-field"])
def test_comparison_keeps_receipt_counters_reasons_and_order(latency, paired_results, field, value):
    # GIVEN one changed receipt field in otherwise matching samples
    assert paired_results[0]["result"] == paired_results[1]["result"], "the controls must match initially"
    paired_results[1]["result"]["receipt"][field] = value
    # WHEN comparison validates the receipt
    # THEN normalization cannot drop semantic fields or reorder lists
    with pytest.raises(ValueError, match="^Conversion parity changed: scale-docx-1000/stdout$"):
        latency.compare_results(paired_results)


@pytest.mark.parametrize("location", ["foreign", "other-variant"], ids=["foreign-source", "other-variant"])
def test_loaded_origin_must_belong_to_the_selected_snapshot(latency, variant, tmp_path, location):
    # GIVEN eleven expected imports with one actual file outside the selected variant
    origins = {name: str(Path(variant["root"], "src/brewdoc", f"{name}.py")) for name in MODULES}
    foreign = tmp_path / location / "pdf.py"
    foreign.parent.mkdir()
    foreign.write_bytes(b"IDENTITY = 'pdf'\n")
    origins["pdf"] = str(foreign)
    assert len(origins) == 11, "a foreign import must not be confused with missing module membership"
    # WHEN actual loaded module paths are validated
    # THEN the matching filename cannot disguise a foreign source tree
    with pytest.raises(ValueError, match="^Imported source origins differ from sealed variant$"):
        latency.check_origin(origins, variant)


@pytest.mark.parametrize("names", [MODULES[:-1], (*MODULES, "unexpected")], ids=["missing", "extra"])
def test_loaded_origin_membership_is_exact(latency, variant, names):
    # GIVEN a source-origin map with missing or extra module membership
    origins = {name: str(Path(variant["root"], "src/brewdoc", f"{name}.py")) for name in names}
    assert tuple(origins) == names, "the origin map must preserve the selected membership defect"
    # WHEN actual module membership is validated
    # THEN all eleven intended modules and no extra module are required
    with pytest.raises(ValueError, match="^Imported source origins differ from sealed variant$"):
        latency.check_origin(origins, variant)


def test_loaded_origins_accept_exact_external_snapshot(latency, variant):
    # GIVEN the complete eleven-module map under one external sealed source root
    origins = {name: str(Path(variant["root"], "src/brewdoc", f"{name}.py")) for name in MODULES}
    assert len(origins) == 11, "the positive control must include every module"
    # WHEN all loaded paths identify that exact snapshot
    actual = latency.check_origin(origins, variant)
    # THEN the matching source map is accepted
    assert actual is None, "an exact origin match must pass without replacement values"


def test_matching_refusal_exit_cannot_hide_a_changed_reason(latency, paired_results):
    # GIVEN matching refused A/B results with the same exit and empty Markdown
    for record in paired_results:
        record["result"].update(returncode=2, markdown_sha256=hashlib.sha256(b"").hexdigest(), artifacts=[])
        record["result"]["receipt"].update(file_ok=False, reason="unsupported input")
    assert paired_results[0]["result"] == paired_results[1]["result"], "both controls must begin as identical refusals"
    paired_results[1]["result"]["receipt"]["reason"] = "input limit exceeded"
    # WHEN comparison validates the failure outputs
    # THEN distinct refusal causes must retain their meaning
    with pytest.raises(ValueError, match="^Conversion parity changed: scale-docx-1000/stdout$"):
        latency.compare_results(paired_results)


def test_untimed_two_row_parity_remains_separate_from_frozen_timed_acceptance(latency, paired_results):
    # GIVEN one matching A/B observation for the independent untimed parity consumer
    records = paired_results[:2]
    assert [record["variant"] for record in records] == ["A", "B"], "the control must use the minimal untimed pair"
    # WHEN the pure parity and statistics helper compares that pair
    actual = latency.compare_results(records)
    # THEN exact parity remains valid without claiming a timed experiment was complete
    assert actual == [{"case": "scale-docx-1000", "mode": "stdout", "blocks": [
        {"variant": "A", "block": 0, "samples": [1.0], "median": 1.0, "IQR": 0.0, "minimum": 1.0, "maximum": 1.0},
        {"variant": "B", "block": 1, "samples": [1.0], "median": 1.0, "IQR": 0.0, "minimum": 1.0, "maximum": 1.0},
    ], "pairs": [{"gain_percent": 0.0, "relative_IQR_percent": 0.0}],
        "extra_BA_eligible": False, "scoped_gain_supported": False, "material_control_slowdown": False}], "the small helper must retain its untimed compatibility without a performance claim"


@pytest.mark.parametrize("mutation", [
    lambda root: (root / "src/brewdoc/pdf.py").write_bytes(b"foreign source\n"),
    lambda root: (root / "src/brewdoc/pdf.py").unlink(),
    lambda root: (root / "src/brewdoc/unexpected.py").write_bytes(b"extra source\n"),
], ids=["changed-body", "missing-module", "extra-module"])
def test_context_rejects_source_drift_before_starting_probe(latency, variant, mutation, monkeypatch):
    # GIVEN a sealed source tree followed by one content or membership change
    root = Path(variant["root"])
    assert latency.source_record(root) == (variant["files"], variant["source_sha256"]), "the original seal must match"
    mutation(root)
    monkeypatch.setattr(latency, "owned_capture", lambda *args: pytest.fail("a changed source must not spawn"))
    # WHEN preflight verifies the frozen source
    # THEN drift is rejected before imports or timing
    with pytest.raises(ValueError, match="^Source seal changed$"):
        latency.verify_context(variant, {}, [])


def test_context_rejects_equal_size_input_content_drift_before_starting_probe(latency, variant, tmp_path, monkeypatch):
    # GIVEN a frozen input whose bytes change without changing its recorded size
    source = tmp_path / "input.txt"
    source.write_bytes(b"before")
    record = {"id": "synthetic", "path": str(source), "size_bytes": 6,
              "sha256": hashlib.sha256(b"before").hexdigest()}
    source.write_bytes(b"after!")
    assert source.stat().st_size == record["size_bytes"], "the defect must exercise content rather than size"
    monkeypatch.setattr(latency, "owned_capture", lambda *args: pytest.fail("a changed input must not spawn"))
    # WHEN preflight verifies the frozen input hash
    # THEN equal size cannot conceal different source bytes
    with pytest.raises(ValueError, match="^Input identity changed: synthetic$"):
        latency.verify_context(variant, {}, [record])


@pytest.mark.parametrize("case,dimensions,hashes", [
    ("scale-xlsx-5000-1-sparse", {"rows": 5000, "columns": 20, "occupied_columns": [0, 19], "source_cells": 10000}, {
        "[Content_Types].xml": "812830651299e06c79d3867ffa5203db7bc576f8d3321c12afba62f53bc45418",
        "_rels/.rels": "0b3936235a1f7b4ea68da3a54b7748f7f03f10be6cc5f25e5a3ffaca5039bfc5",
        "xl/_rels/workbook.xml.rels": "cd08e5907cb6dac158f533d606c8654824bf1f22b42b22122d3722d25413bfdf",
        "xl/workbook.xml": "7381c3d03e9749bc383f2609ffc34e47f93ff0989291cdf317535e8cd7681513",
        "xl/worksheets/sheet1.xml": "56ff2ebb8acc778f7f3697e1715c9a3b7627b93033a7509ad8d2be3b58395f3a",
    }),
    ("scale-docx-1000", {"table_rows": 1000, "table_cells": 3000, "chapters": 1}, {
        "_rels/.rels": "22c01d12e912dc45bdf191c382dd5b80cdeca1a9e95af86a477798a9f7e541e9",
        "word/document.xml": "5a0d64e577d9729b5f763356c72477ede7dfb71e53594c2d2fffca6e778d4867",
    }),
], ids=["sparse-workbook", "docx-table"])
def test_container_recipes_preserve_original_payloads_and_fixed_archive_metadata(generated_inputs, case, dimensions, hashes):
    # GIVEN two independent generations of the accepted synthetic recipe
    record, repeated = generated_inputs[case]
    first, second = Path(record["path"]), Path(repeated["path"])
    assert first.parent.name == "first", "recipe generation must use the external temporary root"
    # WHEN original uncompressed members and ZIP metadata are inventoried
    with zipfile.ZipFile(first) as archive:
        actual_hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}
        metadata = [(item.filename, item.date_time, item.compress_type) for item in archive.infolist()]
    # THEN original content and deterministic metadata survive without platform-specific compressed hashes
    assert (record["dimensions"], actual_hashes, metadata) == (
        dimensions, hashes, [(name, (1980, 1, 1, 0, 0, 0), zipfile.ZIP_DEFLATED) for name in sorted(hashes)],
    ), "original payload hashes bind all sparse cells, DOCX rows and packaging members"
    assert (first.read_bytes(), record["sha256"], record["size_bytes"]) == (
        second.read_bytes(), repeated["sha256"], repeated["size_bytes"],
    ), "two fresh generations must preserve all bytes and recorded identity"


def test_html_recipe_preserves_original_unicode_cells_and_accepted_dom_size(generated_inputs):
    # GIVEN the original 90000-cell synthetic table recipe
    record, repeated = generated_inputs["scale-html-table-90000"]
    assert record["dimensions"] == {"table_rows": 9000, "table_cells": 90000, "columns": 10}, "the workload dimensions must match"
    # WHEN exact bytes and actual element membership are read
    data = Path(record["path"]).read_bytes()
    tree = ET.fromstring(data)
    # THEN the frozen scalar values remain below the accepted 200000 DOM-element cap
    assert (hashlib.sha256(data).hexdigest(), len(list(tree.iter())), len(tree.findall(".//tr")),
            [cell.text for cell in tree.findall(".//td")]) == (
        "7ff46853b91868b467f9a08bde49b7bcfcb4d62ea3955a03591a3a9ca1efb5f5",
        99003, 9000, ["café 世界"] * 90000,
    ), "the full original HTML identity and converting workload shape must be retained"
    assert data == Path(repeated["path"]).read_bytes(), "repeat generation must preserve the exact Unicode bytes"


@pytest.mark.parametrize("mode,output_path,stdout_tail", [
    ("stdout", lambda directory: None, b"# exact body\n"),
    ("out", lambda directory: str(directory / "artifacts/document.md"), b""),
])
def test_result_normalization_preserves_every_semantic_field_and_receipt_order(latency, tmp_path, mode, output_path, stdout_tail):
    # GIVEN a complete capture with ordered receipt fields and an exact Markdown body
    directory = tmp_path / "result"
    (directory / "artifacts").mkdir(parents=True)
    markdown = b"# exact body\n"
    payload = b"=A1\n"
    payload_hash = hashlib.sha256(payload).hexdigest()
    receipt = {"reason": "sheet rendered", "out": output_path(directory), "file_ok": True,
               "dropped": {"controls": 2}, "unit_keys": ["sheet/000002", "sheet/000001"],
               "artifacts": [{"key": "formula/sheet/000002", "out": str(directory / "artifacts/artifact-0.bin"),
                              "byte_size": 4, "sha256": payload_hash}],
               "future_semantic_field": {"exact": 4}}
    artifacts = [{"path": "artifacts/document.md", "size_bytes": len(markdown),
                  "sha256": hashlib.sha256(markdown).hexdigest()},
                 {"path": "artifacts/artifact-0.bin", "size_bytes": 4, "sha256": payload_hash}]
    (directory / "artifacts/document.md").write_bytes(markdown)
    (directory / "artifacts/artifact-0.bin").write_bytes(payload)
    (directory / "stdout.bin").write_bytes(json.dumps(receipt).encode() + b"\n" + stdout_tail)
    (directory / "stderr.bin").write_bytes(b"exact stderr\n")
    (directory / "result.json").write_text(json.dumps({
        "status": "success", "returncode": 0, "capture_errors": [], "artifacts": artifacts}))
    assert list(receipt) == ["reason", "out", "file_ok", "dropped", "unit_keys", "artifacts", "future_semantic_field"], "the receipt order precondition must be explicit"
    # WHEN the comparison object normalizes its declared output location
    actual = latency.normalized_result(directory, mode)
    # THEN only that output location changes; all semantic fields and bytes survive
    assert actual == {
        "returncode": 0, "stderr": "exact stderr\n", "receipt": {
            **receipt, "out": None, "artifacts": [{**receipt["artifacts"][0], "out": None}]},
        "receipt_fields": list(receipt), "markdown": markdown.decode(),
        "markdown_sha256": hashlib.sha256(markdown).hexdigest(), "artifacts": artifacts,
    }, "normalization must retain the full receipt, field order, Markdown and artifact identity"


def test_refused_file_output_keeps_receipt_and_empty_body_without_requiring_a_target(latency, tmp_path):
    # GIVEN a refused conversion whose public receipt has no output path or file
    directory = tmp_path / "refused"
    directory.mkdir()
    receipt = {"file_ok": False, "route": "pdf", "source": "scan.pdf", "out": None,
               "reason": "no text layer", "units": 0, "dropped": {"controls": 0}}
    (directory / "stdout.bin").write_bytes(json.dumps(receipt).encode() + b"\n")
    (directory / "stderr.bin").write_bytes(b"")
    (directory / "result.json").write_text(json.dumps({
        "status": "nonzero_exit", "returncode": 1, "capture_errors": [], "artifacts": []}))
    assert (directory / "artifacts/document.md").exists() is False, "a refused conversion must start without an output target"
    # WHEN file-output refusal is normalized for exact A/B parity
    actual = latency.normalized_result(directory, "out")
    # THEN an authentic refusal is retained with every field and an empty body
    assert actual == {"returncode": 1, "stderr": "", "receipt": receipt, "receipt_fields": list(receipt),
                      "markdown": "", "markdown_sha256": hashlib.sha256(b"").hexdigest(), "artifacts": []}, "file-output mode must accept the unchanged public refusal contract"


def test_successful_file_output_cannot_accept_a_missing_markdown_target(latency, tmp_path):
    # GIVEN a successful receipt declaring an output file that does not exist
    directory = tmp_path / "missing"
    directory.mkdir()
    receipt = {"file_ok": True, "out": str(directory / "artifacts/document.md"), "artifacts": []}
    (directory / "stdout.bin").write_bytes(json.dumps(receipt).encode() + b"\n")
    (directory / "stderr.bin").write_bytes(b"")
    (directory / "result.json").write_text(json.dumps({
        "status": "success", "returncode": 0, "capture_errors": [], "artifacts": []}))
    assert (directory / "artifacts/document.md").exists() is False, "the missing target is the intended failure boundary"
    # WHEN file output is normalized
    # THEN absent bytes cannot masquerade as a successful empty Markdown document
    with pytest.raises(ValueError, match="^Successful output file is missing or linked$"):
        latency.normalized_result(directory, "out")


def test_warm_worker_refuses_failed_recipe_before_starting_timed_calls(latency, variant, tmp_path, monkeypatch):
    # GIVEN exact source origins and input identity with a refused warmup result
    source = tmp_path / "input.txt"
    source.write_bytes(b"synthetic\n")
    case = {"id": "synthetic", "path": str(source), "size_bytes": 10,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    modules = {"brewdoc." + name: SimpleNamespace(__file__=str(Path(variant["root"], "src/brewdoc", name + ".py"))) for name in MODULES}
    modules["brewdoc"] = modules.pop("brewdoc.__init__")
    modules["brewdoc"].run = lambda _: (1, {"file_ok": False, "reason": "no text layer"}, "")
    monkeypatch.setattr(latency, "importlib", SimpleNamespace(import_module=lambda name: modules[name]))
    monkeypatch.setattr(latency, "sys", SimpleNamespace(path=[str(Path(variant["root"], "src"))]))
    monkeypatch.setattr(latency, "environment_record", lambda: {})
    clock_calls = []
    monkeypatch.setattr(latency, "time", SimpleNamespace(perf_counter=lambda: clock_calls.append("timed") or 1.0))
    assert source.stat().st_size == case["size_bytes"], "the recipe identity must be valid before testing refusal"
    # WHEN the worker's untimed warmup refuses the recipe
    # THEN no timed sample or accepted worker output is produced
    with pytest.raises(ValueError, match="^Warm recipe must convert successfully$"):
        latency.warm_worker(variant, case, tmp_path / "worker.json")
    assert (clock_calls, (tmp_path / "worker.json").exists()) == ([], False), "a refusal must stop before the timing region"


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGALRM signal-mask ownership")
def test_preblocked_alarm_is_rejected_before_any_child_spawn(latency, tmp_path, monkeypatch):
    # GIVEN a caller whose alarm delivery is blocked
    isolated_signal = SimpleNamespace(**vars(signal))
    isolated_signal.pthread_sigmask = lambda *_: {signal.SIGALRM}
    monkeypatch.setattr(latency, "signal", isolated_signal)
    monkeypatch.setattr(latency, "subprocess", SimpleNamespace(Popen=lambda *args, **kwargs: pytest.fail("a blocked alarm must not spawn")))
    target = tmp_path / "capture"
    assert target.exists() is False, "blocked signal state must be rejected before creating evidence"
    # WHEN a blocking capture would rely on the undeliverable deadline
    # THEN the unsafe caller state fails before process creation
    with pytest.raises(ValueError, match="^Owned deadline requires unblocked SIGALRM$"):
        latency.owned_capture(argparse.Namespace(output_dir=target), {}, seconds=1)
    assert target.exists() is False, "the rejected caller state must not start capture setup"


def test_observed_runtime_versions_must_match_the_active_lock(latency, locked_graph):
    # GIVEN unchanged package names with one version outside the frozen lock
    environment, _ = locked_graph
    environment["packages"][0][1] = "1.1"
    assert environment["packages"] == [["alpha", "1.1"], ["beta", "2.0"]], "the intended drift must be a version change"
    # WHEN the runtime graph is verified
    # THEN merely matching A/B observed environments is insufficient
    with pytest.raises(ValueError, match="^Installed graph differs from active lock$"):
        latency.verify_locked_graph(environment)


def test_locked_package_versions_cannot_hide_an_unsatisfied_active_dependency_edge(latency, locked_graph):
    # GIVEN exact locked versions but installed metadata requiring an incompatible dependency
    environment, installed = locked_graph
    installed[0].requires = ["beta>=3"]
    assert environment["packages"] == [["alpha", "1.0"], ["beta", "2.0"]], "the version map must still match the lock"
    # WHEN the actual dependency requirements are verified
    # THEN an active unsatisfied edge rejects the measurement environment
    with pytest.raises(ValueError, match="^Installed requirement edge is unsatisfied$"):
        latency.verify_locked_graph(environment)


@pytest.mark.parametrize("mutation", [
    lambda records: records.__delitem__(slice(14, 21)),
    lambda records: records.append(deepcopy(records[0])),
    lambda records: records.pop(0),
    lambda records: records[0].__setitem__("variant", "B"),
    lambda records: records[0].__setitem__("block", 4),
    lambda records: records[0].__setitem__("sample", 8),
], ids=["missing-B-block", "duplicate-row", "truncated-block", "wrong-variant", "unapproved-block", "wrong-sample-index"])
def test_timed_acceptance_rejects_incomplete_or_mislabeled_schedule(latency, timed_records, mutation):
    # GIVEN the complete approved 224 CLI and 108 warm observations
    assert len(timed_records) == 332, "the positive starting schedule must contain every declared sample"
    mutation(timed_records)
    # WHEN timed acceptance validates schedule identity before statistics
    # THEN no missing, duplicate or mislabeled observation can disappear through pairing
    with pytest.raises(ValueError, match="^Timed schedule is incomplete or duplicated$"):
        latency.validate_timed_records(timed_records)


def test_timed_acceptance_keeps_every_complete_approved_group(latency, timed_records):
    # GIVEN all eleven declared case/mode groups and all four approved blocks
    assert len(timed_records) == 332, "the control must include every CLI and warm sample"
    # WHEN the complete fixed schedule is verified
    actual = latency.validate_timed_records(timed_records)
    # THEN the exact approved schedule is accepted
    assert actual is None, "a complete frozen schedule must pass the integrity gate"


@pytest.mark.parametrize("count,warmups", [(8, 2), (9, 1)], ids=["missing-measured-call", "missing-warmup"])
def test_warm_receipt_requires_all_nine_measurements_and_two_warmups(latency, timed_records, count, warmups):
    # GIVEN an incomplete warm-worker receipt with otherwise valid measured records
    measured = timed_records[224:233]
    assert [(record["case"], record["mode"], record["sample"]) for record in measured] == [
        (CASES[0], "warm", sample) for sample in range(9)], "the control must retain all intended warm samples"
    result = {"records": measured[:count], "warmups": warmups}
    # WHEN the parent accepts one warm-worker result
    # THEN short measurements or warmup counts are rejected before extension
    with pytest.raises(ValueError, match="^Warm worker count changed$"):
        latency.warm_records(result)


def test_complete_warm_receipt_returns_every_original_measured_record(latency, timed_records):
    # GIVEN exactly nine measured records after two warmups
    measured = timed_records[224:233]
    assert [(record["case"], record["mode"], record["sample"]) for record in measured] == [
        (CASES[0], "warm", sample) for sample in range(9)], "the control must retain all intended warm samples"
    # WHEN the parent accepts the full worker result
    actual = latency.warm_records({"records": measured, "warmups": 2})
    # THEN every original record remains available to paired acceptance
    assert actual == measured, "the integrity gate must preserve all measured observations"


def test_transport_accepts_only_the_legitimate_saved_markdown_inventory_difference(latency, transport_records):
    # GIVEN matching full results except the actual Markdown file in out-mode inventory
    assert [record["mode"] for record in transport_records] == ["stdout", "out", "stdout", "out"], "both variants must include the two real transports"
    # WHEN cross-mode parity verifies the normalized full results
    actual = latency.verify_transport(transport_records)
    # THEN saved Markdown is accounted for without dropping the independent payload inventory
    assert actual is None, "the legitimate saved Markdown inventory must not create false parity failures"
    assert transport_records[1]["result"]["artifacts"][0] == {
        "path": "artifacts/artifact-0.bin", "size_bytes": 4, "sha256": "a" * 64,
    }, "transport validation must retain actual workbook payload metadata"


def test_transport_rejects_cross_mode_body_drift_even_when_each_variant_pair_matches(latency, transport_records):
    # GIVEN A/B parity within each mode but consistently different saved Markdown
    for record in transport_records[1::2]:
        result = record["result"]
        result["markdown"] = "# changed body\n"
        result["markdown_sha256"] = hashlib.sha256(result["markdown"].encode()).hexdigest()
        result["artifacts"][-1].update(size_bytes=len(result["markdown"].encode()), sha256=result["markdown_sha256"])
    assert transport_records[1]["result"] == transport_records[3]["result"], "out-mode A/B parity must still hold"
    # WHEN the two transports are compared for the same input and variant
    # THEN separate A/B comparisons cannot conceal cross-mode body drift
    with pytest.raises(ValueError, match="^Corpus transport result changed: synthetic/A$"):
        latency.verify_transport(transport_records)


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGALRM and process-group deadline ownership")
def test_owned_deadline_reaps_ready_child_and_restores_signal_state(latency, ready_capture):
    # GIVEN a readiness-gated child, an existing handler and a periodic caller timer
    args, spawned, sentinel, original_mask = ready_capture
    original_capture_subprocess = latency.capture.subprocess
    assert spawned == [], "capture must own the spawn rather than a pre-existing process"
    # WHEN a real blocking wait crosses its short deadline after child registration
    code = latency.owned_capture(args, dict(os.environ), seconds=0.05)
    result = json.loads((args.output_dir / "result.json").read_text())
    # THEN the owned child is killed and reaped, and caller signal state is restored
    assert (code, result["status"], result["returncode"], result["signal"], result["deadline_seconds"],
            result["deadline_expired"], result["ownership_closed"], result["capture_errors"]) == (
        1, "interrupted", -signal.SIGKILL, signal.SIGKILL, 0.05, True, True, [],
    ), "deadline evidence must retain actual forced termination and closed ownership"
    assert [process.poll() for process in spawned] == [-signal.SIGKILL], "the exact child must already be reaped"
    assert json.loads((args.output_dir / "ownership.json").read_text()) == {
        "pids": [spawned[0].pid], "deadline_expired": True, "ownership_closed": True,
    }, "retained ownership evidence must identify the exact reaped child"
    with pytest.raises(ChildProcessError):
        os.waitpid(spawned[0].pid, os.WNOHANG)
    assert (signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)[1],
            signal.pthread_sigmask(signal.SIG_BLOCK, set()), latency.capture.subprocess) == (
        sentinel, 20.0, original_mask, original_capture_subprocess,
    ), "handler, periodic interval, signal mask and capture namespace must be restored"
    assert 59 <= signal.getitimer(signal.ITIMER_REAL)[0] <= 60, "restored timer must retain the caller's bounded remaining interval"


@pytest.mark.skipif(os.name != "posix", reason="POSIX child registration and signal-mask restoration")
def test_interrupt_between_registration_and_capture_assignment_reaps_child(latency, ready_capture, monkeypatch):
    # GIVEN a real child ready before the capture function receives its process handle
    args, spawned, sentinel, original_mask = ready_capture
    real_mask = signal.pthread_sigmask

    def restore_then_interrupt(how, mask):
        """Interrupt before capture receives the registered child handle."""
        real_mask(how, mask)
        raise KeyboardInterrupt

    calls = {signal.SIG_BLOCK: real_mask, signal.SIG_SETMASK: restore_then_interrupt}
    isolated_signal = SimpleNamespace(**vars(signal))
    isolated_signal.pthread_sigmask = lambda how, mask: calls[how](how, mask)
    monkeypatch.setattr(latency, "signal", isolated_signal)
    assert spawned == [], "the test must start before the capture-owned spawn"
    # WHEN interruption occurs immediately after real unmask and ownership registration
    with pytest.raises(KeyboardInterrupt):
        latency.owned_capture(args, dict(os.environ), seconds=2)
    # THEN cleanup closes the real ownership gap even without a completed result JSON
    assert [process.poll() for process in spawned] == [-signal.SIGKILL], "the registered child must be killed and reaped"
    assert json.loads((args.output_dir / "ownership.json").read_text()) == {
        "pids": [spawned[0].pid], "deadline_expired": False, "ownership_closed": True,
    }, "registration interruption must retain the exact child identity and cleanup result"
    with pytest.raises(ChildProcessError):
        os.waitpid(spawned[0].pid, os.WNOHANG)
    assert (signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)[1],
            signal.pthread_sigmask(signal.SIG_BLOCK, set())) == (
        sentinel, 20.0, original_mask,
    ), "interruption must also restore the caller's handler, timer and mask"
    assert args.output_dir.is_dir() is True, "partial raw capture evidence must be retained"
