"""Check distribution identity, installation isolation and console validation failures."""

import importlib.util
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import stat
import sysconfig
import tarfile
import tempfile
import zipfile
import types

import pytest

HELPER = Path(__file__).resolve().parents[1] / ".github/workflows/verify_install.py"
SPEC = importlib.util.spec_from_file_location("verify_install", HELPER)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)
_COMPLETE_INSPECTION_CACHE = None


def complete_inspection_packet():
    """Produce one validated immutable packet and refuse source drift before returning fresh copies."""
    global _COMPLETE_INSPECTION_CACHE
    checkout = HELPER.parents[2]
    controls = {"modules": verification.module_sources(checkout),
                "sources": {name: verification.digest(checkout / name) for name in verification.INSPECTION_SOURCE_FILES}}
    identity = verification.canonical_json(controls)
    if _COMPLETE_INSPECTION_CACHE is None:
        spec = importlib.util.spec_from_file_location("packet_builder", HELPER.with_name("build_standalone.py"))
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix="brewdoc-delivery-fixture-") as temporary:
            root = Path(temporary)
            version = verification.tomllib.loads((checkout / "pyproject.toml").read_text())["project"]["version"]
            source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout).decode().strip()
            control = {"version": version, "source_sha": source_sha, "modules": controls["modules"],
                       "cases": builder.inspection_cases(checkout, root / "sources")}
            path = root / "inputs.json"
            path.write_bytes(verification.canonical_json(control))
            process = subprocess.run([str(checkout / ".venv/bin/python"), "-I", str(HELPER), "seal-inspection",
                                      "--checkout", str(checkout), "--input", str(path)], capture_output=True, check=True, timeout=60)
            packet = verification.validate_inspection_packet(json.loads(process.stdout), version=version, source_sha=source_sha,
                module_sources_sha256=hashlib.sha256(verification.canonical_json(controls["modules"])).hexdigest())
            _COMPLETE_INSPECTION_CACHE = identity, verification.canonical_json(packet)
    current = {"modules": verification.module_sources(checkout),
               "sources": {name: verification.digest(checkout / name) for name in verification.INSPECTION_SOURCE_FILES}}
    assert verification.canonical_json(current) == _COMPLETE_INSPECTION_CACHE[0], "delivery fixture refuses module or builder authority drift after its one source seal"
    return json.loads(_COMPLETE_INSPECTION_CACHE[1])


def test_complete_delivery_packet_retains_fifteen_root_positives_and_original_native_payloads():
    # GIVEN one independently checked source-runtime packet returned as immutable cached bytes
    packet = complete_inspection_packet()
    cases = {case["id"]: case for case in packet["cases"]}
    # WHEN consumers derive the independent descriptor and full expected observations
    descriptor = verification.inspection_descriptor(packet, version=packet["version"], source_sha=packet["source_sha"],
        module_sources_sha256=packet["module_sources_sha256"])
    # THEN roots, literal BIFF tokens and pass-through tripwires are bound alongside report/receipt identities
    assert ([case["id"] for case in packet["cases"]], verification.sealed_bytes(cases[".xls"]["payload"]).hex(),
            cases[".pdf"]["readable"]["code"], cases[".pdf"]["readable"]["receipt"]["source"],
            [verification.sealed_bytes(cases[suffix]["source"])[-9:] for suffix in (".md", ".txt")],
            len(descriptor["result"]["cases"])) == (
        sorted(verification.SUFFIXES), "170c007374617469632d70726f6f6622016e00", 1, "passive.pdf",
        [b"\r\nCaf\xc3\xa9\r\n"] * 2, 15,
    ), "native positive payloads and portable source/readable identities must survive all fifteen rows"
    assert complete_inspection_packet() == packet and complete_inspection_packet() is not packet, "callers must receive fresh copies of the one sealed packet"


@pytest.mark.parametrize("damage", ["missing", "duplicate", "module-map", "foreign-source", "source-byte",
                                   "payload-byte", "report-order", "artifact-order", "parent", "extra"])
def test_complete_inspection_packet_refuses_individually_corrupted_admission_fields(damage):
    # GIVEN a complete positive packet with exactly one changed field or identity
    packet = complete_inspection_packet()
    expected = {key: packet[key] for key in ("version", "source_sha", "module_sources_sha256")}
    row = next(case for case in packet["cases"] if case["id"] == ".html")
    report = json.loads(verification.sealed_bytes(row["report"]))
    mutations = {"missing": lambda: packet["cases"].pop(), "duplicate": lambda: packet["cases"].append(copy.deepcopy(row)),
        "module-map": lambda: packet.update(module_sources_sha256="0" * 64),
        "foreign-source": lambda: packet.update(source_sha="0" * 40),
        "source-byte": lambda: row.update(source=verification.inspection_file(b">" + verification.sealed_bytes(row["source"])[1:])),
        "payload-byte": lambda: row.update(payload=verification.inspection_file(b"brokenAcceptanceProof('literal source');",
                                               key=row["payload"]["key"], kind=row["payload"]["kind"])),
        "report-order": lambda: row.update(report=verification.inspection_file(json.dumps(report, indent=2).encode())),
        "artifact-order": lambda: row["artifacts"].reverse(),
        "parent": lambda: report["documents"][0].update(parent="document/000002"),
        "extra": lambda: row.update(unbound=True)}
    mutations[damage]()
    parent_rows = {"parent": lambda: row.update(report=verification.inspection_file(verification.canonical_json(report)))}
    parent_rows.get(damage, lambda: None)()
    # WHEN the real shared validator receives independent producer controls
    # THEN self-rehashed byte corruption and nested-parent substitution cannot manufacture acceptance
    with pytest.raises(ValueError, match="[Ii]nspection"):
        verification.validate_inspection_packet(packet, **expected)


@pytest.mark.parametrize("suffix", [".md", ".txt"])
@pytest.mark.parametrize("change", ["bom", "crlf"])
def test_complete_text_profile_refuses_missing_bom_or_crlf_even_after_resealing_identity(suffix, change):
    # GIVEN actual pass-through source with independent BOM, CRLF and Unicode tripwires
    packet = complete_inspection_packet()
    row = next(case for case in packet["cases"] if case["id"] == suffix)
    source = verification.sealed_bytes(row["source"])
    assert (source[:3], source[-9:]) == (b"\xef\xbb\xbf", b"\r\nCaf\xc3\xa9\r\n"), "original text must carry all byte tripwires"
    variants = {"bom": source[3:], "crlf": source.replace(b"\r\n", b"\n")}
    altered = variants[change]
    row["source"] = verification.inspection_file(altered)
    report = json.loads(verification.sealed_bytes(row["report"]))
    identity = {"byte_size": len(altered), "sha256": hashlib.sha256(altered).hexdigest()}
    report["source"].update(identity)
    report["documents"][0].update(identity)
    row["report"] = verification.inspection_file(verification.canonical_json(report))
    row["artifacts"][0].update(byte_size=row["report"]["size"], sha256=row["report"]["sha256"])
    row["readable"].update(markdown_size=len(altered), markdown_sha256=hashlib.sha256(altered).hexdigest())
    references = {item["key"]: item for item in row["artifacts"]}
    row["readable"]["receipt"]["artifacts"] = [references.get(item["key"], item)
        for item in row["readable"]["receipt"]["artifacts"]]
    # WHEN source/report/readable/list identities agree on the normalized variant
    # THEN the independent byte tripwire still refuses the forbidden normalization
    with pytest.raises(ValueError, match="Inspection text pass-through bytes differ"):
        verification.validate_inspection_packet(packet, version=packet["version"], source_sha=packet["source_sha"])


def test_actual_installed_packet_api_probe_matches_all_sealed_rows_and_refusal_sentinels(tmp_path):
    # GIVEN the complete packet with portable basenames and a real textless PDF refusal
    packet = complete_inspection_packet()
    # WHEN actual development public APIs and output transactions run twice per format
    observed = verification.probe_inspection_packet(packet, tmp_path)
    # THEN every actual report/payload/readable/destination identity matches its independent packet
    assert observed == verification.expected_inspection_result(packet), "actual APIs must preserve all fifteen rows, byte tripwires and refusal outputs"


@pytest.mark.parametrize("damage", ["missing", "second-report", "payload", "order", "numeric-type"])
def test_exact_consumer_result_refuses_missing_or_relabelled_observation_fields(damage):
    # GIVEN the complete expected result with one missing, changed or reordered observation
    packet = complete_inspection_packet()
    observed = verification.expected_inspection_result(packet)
    mutations = {"missing": lambda: observed["cases"].pop(),
        "second-report": lambda: observed["cases"][0].update(repeat_report_sha256="0" * 64),
        "payload": lambda: observed["cases"][0].update(payload_sha256="0" * 64),
        "order": lambda: observed["cases"].reverse(),
        "numeric-type": lambda: observed["cases"][0].update(payload_size=float(observed["cases"][0]["payload_size"]))}
    mutations[damage]()
    # WHEN a parent admits the complete actual result rather than suffix counts
    # THEN every field, its type and its original ordered position remain mandatory
    with pytest.raises(ValueError, match="Consumer inspection evidence differs"):
        verification.check_inspection_result(observed, packet)


def module_pair(tmp_path):
    """Create matching source and archive bytes without invoking a build tool."""
    checkout, bundle = tmp_path / "checkout", tmp_path / "bundle"
    checkout.mkdir()
    (bundle / "dist").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text(
        "[project]\nname='brewdoc'\nversion='0.8.0'\n[build-system]\nrequires=[]\n"
        "[tool.uv]\nbuild-constraint-dependencies=[]\n")
    modules = {"brewdoc/__init__.py": b"__all__=['run']\n", "brewdoc/epub.py": b"# epub\n",
               "brewdoc/nested/reader.py": b"value=1\n"}
    for name, payload in modules.items():
        path = checkout / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    for name in ("uv.lock", verification.VERIFIER_SOURCE, ".github/workflows/build_standalone.py",
                 "tests/test_contract.py", "tests/test_epub.py", "tests/test_spreadsheets.py",
                 "benchmarks/corpus.json", "tests/fixtures/receipts.json"):
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sealed source\n")
    authority = checkout / "tests/test_inspection_acceptance.py"
    authority.write_bytes((HELPER.parents[2] / "tests/test_inspection_acceptance.py").read_bytes())
    for name in verification.INSPECTION_SOURCE_FILES:
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((HELPER.parents[2] / name).read_bytes())
    write_module_pair(bundle, list(modules.items()), list(modules.items()))
    return checkout, bundle, modules


def write_module_pair(bundle, wheel_rows, sdist_rows):
    """Preserve original archive occurrences, including deliberately duplicated names."""
    with zipfile.ZipFile(bundle / "dist/brewdoc.whl", "w") as archive:
        for name, payload in wheel_rows:
            archive.writestr(name, payload)
    with tarfile.open(bundle / "dist/brewdoc.tar.gz", "w:gz") as archive:
        for name, payload in sdist_rows:
            member = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo("brewdoc-0.8.0/src/" + name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def fake_package_command(arguments, cwd):
    """Replace only dependency export and tool identity subprocess edges."""
    if arguments[1] == "-I":
        control = json.loads(Path(arguments[-1]).read_bytes())
        checkout = HELPER.parents[2]
        actual = {**control, "modules": verification.module_sources(checkout)}
        with tempfile.TemporaryDirectory(prefix="brewdoc-package-process-edge-") as temporary:
            path = Path(temporary) / "inputs.json"
            path.write_bytes(verification.canonical_json(actual))
            process = subprocess.run([str(checkout / ".venv/bin/python"), "-I", str(HELPER), "seal-inspection",
                "--checkout", str(checkout), "--input", str(path)], capture_output=True, check=True, timeout=60)
        packet = json.loads(process.stdout)
        packet["module_sources_sha256"] = hashlib.sha256(verification.canonical_json(control["modules"])).hexdigest()
        return subprocess.CompletedProcess(arguments, 0, verification.canonical_json(packet), b"")
    operations = {"export": lambda: Path(arguments[-1]).write_bytes(b"locked requirements\n"),
                  "--version": lambda: None, "rev-parse": lambda: None}
    operations[arguments[1]]()
    replies = {"export": b"", "--version": b"uv 0.12.21", "rev-parse": b"a" * 40}
    return subprocess.CompletedProcess(arguments, 0, replies[arguments[1]], b"")


@pytest.mark.parametrize("archive", ["wheel", "sdist"])
@pytest.mark.parametrize("damage", ["missing", "extra", "duplicate", "changed"])
@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_preparation_rejects_incomplete_or_changed_whole_module_membership(tmp_path, monkeypatch, archive, damage):
    # GIVEN a complete source package and a distribution with one real module defect
    checkout, bundle, modules = module_pair(tmp_path)
    rows = list(modules.items())
    variants = {"missing": rows[:-1], "extra": rows + [("brewdoc/foreign.py", b"pass\n")],
                "duplicate": rows + [rows[-1]], "changed": rows[:-1] + [(rows[-1][0], b"value=2\n")]}
    selected = {"wheel": (variants[damage], rows), "sdist": (rows, variants[damage])}
    write_module_pair(bundle, *selected[archive])
    monkeypatch.setattr(verification, "command", fake_package_command)
    assert (checkout / "src/brewdoc/nested/reader.py").read_bytes() == b"value=1\n", "source authority must remain original"
    # WHEN existing producer admission inspects both actual archives
    # THEN a valid EPUB module cannot hide a missing, extra, duplicate or changed module
    with pytest.raises(ValueError, match="module|member"):
        verification.prepare(checkout, bundle)


@pytest.mark.parametrize("smoke", [False, True])
def test_preparation_seals_complete_current_modules_and_fixture_authority_in_both_modes(tmp_path, monkeypatch, smoke):
    # GIVEN exact source modules, nested membership and matching distribution bytes
    checkout, bundle, modules = module_pair(tmp_path)
    monkeypatch.setattr(verification, "command", fake_package_command)
    expected = {name: {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                for name, payload in sorted(modules.items())}
    # WHEN the real producer prepares each existing scope
    verification.prepare(checkout, bundle, smoke=smoke)
    manifest = json.loads((bundle / "ci-package/manifest.json").read_bytes())
    # THEN both archives and fixture authority are independently bound without omitting old seals
    assert (manifest["module_sources"], manifest["module_sources_sha256"],
            manifest["source"][verification.INSPECTION_AUTHORITY],
            set(manifest["files"]), verification.inspection_case(bundle, manifest)["source"]["size"]) == (
        expected, hashlib.sha256(verification.canonical_json(expected)).hexdigest(),
        verification.digest(checkout / verification.INSPECTION_AUTHORITY),
        {"dist/brewdoc.whl", "dist/brewdoc.tar.gz", "ci-package/runtime-requirements.txt",
         "ci-package/render-js-requirements.txt", "ci-package/build-constraints.txt", "ci-package/inspection-api.json",
         "ci-package/inspection.json"}, 280,
    ), "producer evidence must bind all current modules, both archives and the reviewed fixture in either scope"


@pytest.mark.parametrize("archive", ["wheel", "sdist"])
@pytest.mark.parametrize("name", ["/foreign.py", "C:/foreign.py", "brewdoc\\alias.py", "brewdoc//alias.py",
                                  "brewdoc/./alias.py", "brewdoc/../alias.py", "foreign.py",
                                  "brewdoc-0.8.0.data/purelib/brewdoc/epub.py"])
def test_archive_original_names_cannot_alias_installable_modules(tmp_path, monkeypatch, archive, name):
    # GIVEN a valid source pair with one original foreign or noncanonical archive occurrence
    checkout, bundle, modules = module_pair(tmp_path)
    rows = list(modules.items())
    additions = {"wheel": (rows + [(name, b"pass\n")], rows),
                 "sdist": (rows, rows + [(name, b"pass\n")])}
    write_module_pair(bundle, *additions[archive])
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("unsafe archive reached dependency export"))
    # WHEN source membership is admitted from original archive entries
    # THEN aliases and installable foreign modules fail before the first command
    with pytest.raises(ValueError, match="member|module"):
        verification.prepare(checkout, bundle)


@pytest.mark.parametrize("archive,kind", [("wheel", "symlink"), ("wheel", "fifo"),
                                         ("wheel", "parent-link"), ("sdist", "symlink"),
                                         ("sdist", "hardlink"), ("sdist", "parent-link")])
def test_linked_or_special_archive_members_refuse_before_export(tmp_path, monkeypatch, archive, kind):
    # GIVEN original archive link metadata beside a complete module pair
    checkout, bundle, modules = module_pair(tmp_path)
    rows = list(modules.items())
    wheel_name = {"parent-link": "brewdoc/nested/"}.get(kind, "brewdoc/link.py")
    linked_zip = zipfile.ZipInfo(wheel_name)
    linked_zip.create_system = 3
    linked_zip.external_attr = ({"fifo": stat.S_IFIFO}.get(kind, stat.S_IFLNK) | 0o777) << 16
    linked_tar = tarfile.TarInfo("brewdoc-0.8.0/src/" + wheel_name.rstrip("/"))
    linked_tar.type = {"hardlink": tarfile.LNKTYPE}.get(kind, tarfile.SYMTYPE)
    linked_tar.linkname = "epub.py"
    additions = {"wheel": (rows + [(linked_zip, b"epub.py")], rows),
                 "sdist": (rows, rows + [(linked_tar, b"")])}
    write_module_pair(bundle, *additions[archive])
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("archive link reached dependency export"))
    # WHEN producer admission examines link types before installation
    # THEN neither module links nor package-parent links can redirect consumers
    with pytest.raises(ValueError, match="member is linked or not regular"):
        verification.prepare(checkout, bundle)


@pytest.mark.parametrize("relative", ["src", "src/brewdoc", "src/brewdoc/nested",
                                    "src/brewdoc/epub.py", "src/brewdoc/empty"])
def test_module_inventory_rejects_source_symlinks_including_unvisited_directories(tmp_path, relative):
    # GIVEN a linked source ancestor, module or directory with no Python children
    checkout, _bundle, _modules = module_pair(tmp_path)
    (checkout / "src/brewdoc/empty").mkdir()
    source = checkout / relative
    actual = tmp_path / "actual-source"
    source.rename(actual)
    source.symlink_to(actual, target_is_directory=actual.is_dir())
    # WHEN current source membership is enumerated
    # THEN traversal cannot silently omit or follow the linked source
    with pytest.raises(ValueError, match="linked|symlink"):
        verification.module_sources(checkout)


@pytest.mark.skipif(not hasattr(verification.os, "mkfifo"), reason="FIFO source metadata requires a POSIX host")
def test_module_inventory_refuses_nonregular_python_source_without_opening_it(tmp_path):
    # GIVEN a named pipe where a new Python module would otherwise be read
    checkout, _bundle, _modules = module_pair(tmp_path)
    verification.os.mkfifo(checkout / "src/brewdoc/pipe.py")
    # WHEN source membership is enumerated
    # THEN metadata refusal must avoid a blocking read of nonregular source
    with pytest.raises(ValueError, match="Package module source is not a regular file"):
        verification.module_sources(checkout)


def test_tar_module_declared_size_refuses_before_advancing_past_its_body(tmp_path, monkeypatch):
    # GIVEN a real sdist entry with declared size larger than its admitted source
    checkout, bundle, modules = module_pair(tmp_path)
    rows = list(modules.items())
    write_module_pair(bundle, rows, rows[:-1] + [(rows[-1][0], b"x" * 1024)])
    with tarfile.open(bundle / "dist/brewdoc.tar.gz") as archive:
        member = archive.getmembers()[-1]
    after_body = member.offset_data + 1024
    original = tarfile.TarFile.next

    def next_member(archive):
        """Refuse any parser advance that would consume the oversized member body."""
        assert archive.offset != after_body, "declared module size must be checked before advancing TAR decompression"
        return original(archive)

    monkeypatch.setattr(tarfile.TarFile, "next", next_member)
    # WHEN original TAR metadata is admitted before payload reads
    # THEN the size discrepancy stops iteration at the offending header
    with pytest.raises(ValueError, match="Distribution module membership or size differs from source"):
        verification.check_module_sources(checkout, bundle)


def test_sdist_requires_one_trusted_root_and_allows_nonpackage_python_tests(tmp_path):
    # GIVEN a complete package plus a legitimate test file under the expected distribution root
    checkout, bundle, modules = module_pair(tmp_path)
    rows = list(modules.items())
    test = tarfile.TarInfo("brewdoc-0.8.0/tests/test_example.py")
    write_module_pair(bundle, rows, rows + [(test, b"pass\n")])
    assert verification.check_module_sources(checkout, bundle) == verification.module_sources(checkout), "sdist test files must not become package modules"
    # WHEN an additional original member names a foreign distribution root
    foreign = tarfile.TarInfo("foreign-0.8.0/tests/test_example.py")
    write_module_pair(bundle, rows, rows + [(foreign, b"pass\n")])
    # THEN a nonpackage file cannot introduce a second source root
    with pytest.raises(ValueError, match="Distribution member has a foreign source root"):
        verification.check_module_sources(checkout, bundle)


@pytest.mark.parametrize("entry", ["verify", "runtime"])
def test_new_module_after_sealing_refuses_before_consumer_commands(tmp_path, monkeypatch, entry):
    # GIVEN a complete producer packet followed by an unsealed new source module
    checkout, bundle, _modules = module_pair(tmp_path)
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=True)
    (checkout / "src/brewdoc/new_reader.py").write_bytes(b"new_module=True\n")
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("source addition reached consumer command"))
    entries = {"verify": lambda: verification.verify(checkout, bundle, smoke=True),
               "runtime": lambda: verification.prepare_runtime(checkout, bundle)}
    # WHEN actual installed/runtime consumer entry points recheck current membership
    # THEN checking only old sealed module keys cannot accept the addition
    with pytest.raises(ValueError, match="Current package module sources differ from the producer"):
        entries[entry]()


def public_api_case(tmp_path):
    """Reuse the actual explicit producer builder and real public source bytes."""
    spec = importlib.util.spec_from_file_location("inspection_builder", HELPER.with_name("build_standalone.py"))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    expected = builder.inspection_api_case(HELPER.parents[2])
    source = tmp_path / expected["path"]
    source.write_bytes(base64.b64decode(expected["source"]["bytes_base64"], validate=True))
    return source, expected


def test_actual_public_inspection_apis_retain_exact_report_list_script_and_unknown_key(tmp_path):
    # GIVEN the reviewed 280-byte HTML source generated without importing test modules
    source, expected = public_api_case(tmp_path)
    assert (source.stat().st_size, verification.digest(source)) == (
        280, "768097c9c8cdf58f7fcce7e99fde2ccb7dafd245e562508aa2c7db190de5ba4b"), "fixture bytes must retain independent authority"
    # WHEN the actual public inspect/list/read entry points and unknown-key refusal execute
    observed = verification.probe_inspection_api(source, expected)
    # THEN source, ordered artifact list and exact script identity survive parent admission
    assert (observed["source"], len(observed["artifacts"]), base64.b64decode(observed["script"]["bytes_base64"]),
            observed["unknown_key"], verification.check_inspection_api(observed, expected)) == (
        {"name": "passive.html", "suffix": ".html", "kind": "local", "byte_size": 280,
         "sha256": "768097c9c8cdf58f7fcce7e99fde2ccb7dafd245e562508aa2c7db190de5ba4b"},
        5, b"staticAcceptanceProof('literal source');", "unknown artifact key: inspection/missing", None,
    ), "actual public evidence must retain literal payload bytes and its exact refusal contract"


@pytest.mark.parametrize("api", ["inspect_document", "list_document_artifacts", "read_document_artifact"])
def test_actual_inspection_probe_refuses_a_missing_public_api(tmp_path, monkeypatch, api):
    # GIVEN real source bytes with one required exported API missing
    import brewdoc

    source, expected = public_api_case(tmp_path)
    monkeypatch.delattr(brewdoc, api)
    # WHEN a fresh consumer invokes its generic API probe
    # THEN remaining imports cannot substitute for the missing operation
    with pytest.raises(ValueError, match="Installed public inspection API is unavailable"):
        verification.probe_inspection_api(source, expected)


@pytest.mark.parametrize("damage", ["script", "report", "list", "unknown"])
def test_actual_inspection_probe_refuses_corrupt_or_unstable_api_results(tmp_path, monkeypatch, damage):
    # GIVEN real public APIs with one corrupted observation at their actual call edge
    import brewdoc

    source, expected = public_api_case(tmp_path)
    original = brewdoc.read_document_artifact
    report = brewdoc.inspect_document(source)
    key = next(item["artifact"] for item in report["findings"] if item["text"] == "staticAcceptanceProof('literal source');")
    reports = iter([original(source, "inspection/report"), original(source, "inspection/report") + b" "])
    readers = {"script": lambda path, name: {key: lambda: b"brokenAcceptanceProof('literal source');"}.get(
                   name, lambda: original(path, name))(),
               "report": lambda path, name: {"inspection/report": lambda: next(reports)}.get(
                   name, lambda: original(path, name))(),
               "list": original,
               "unknown": lambda path, name: {"inspection/missing": lambda: None}.get(
                   name, lambda: original(path, name))()}
    listings = {"list": lambda path: brewdoc.inspection.list_document_artifacts(path)[:-1]}
    monkeypatch.setattr(brewdoc, "read_document_artifact", readers[damage])
    monkeypatch.setattr(brewdoc, "list_document_artifacts", listings.get(damage, brewdoc.list_document_artifacts))
    # WHEN the real probe repeats retrieval and checks whole ordered references
    # THEN corruption, nondeterminism, list drift and accepted unknown keys fail
    with pytest.raises(ValueError, match="Installed inspection API"):
        verification.probe_inspection_api(source, expected)


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("damage", ["missing", "malformed", "foreign-source", "foreign-report", "list", "script"])
def test_parent_verification_requires_actual_inspection_api_evidence(tmp_path, monkeypatch, smoke, damage):
    # GIVEN an otherwise admitted consumer observation with one corrupted inspection record
    checkout, bundle, _modules = module_pair(tmp_path)
    (checkout / verification.VERIFIER_SOURCE).write_bytes(HELPER.read_bytes())
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=smoke)
    source, expected = public_api_case(tmp_path)
    observed = verification.probe_inspection_api(source, expected)
    foreign_report = json.loads(base64.b64decode(observed["report"]["bytes_base64"]))
    foreign_report["source"]["name"] = "foreign.html"
    raw = verification.canonical_json(foreign_report)
    corrupt = b"brokenAcceptanceProof('literal source');"
    changes = {"missing": None, "malformed": [],
               "foreign-source": {**observed, "source": {**observed["source"], "name": "foreign.html"}},
               "foreign-report": {**observed, "report": {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                                         "bytes_base64": base64.b64encode(raw).decode()}},
               "list": {**observed, "artifacts": list(reversed(observed["artifacts"]))},
               "script": {**observed, "script": {**observed["script"], "size": len(corrupt),
                            "sha256": hashlib.sha256(corrupt).hexdigest(), "bytes_base64": base64.b64encode(corrupt).decode()}}}
    child = {"verification_scope": "smoke", "oracle": [], "probes": [{"probe": "epub2"}, {"probe": "formula"}],
             "inspection_api": changes[damage]}
    assert len(corrupt) == len(b"staticAcceptanceProof('literal source');"), "corruption must preserve payload size"

    def command(arguments, cwd):
        """Substitute tool/install edges while retaining actual parent evidence admission."""
        replies = {"--version": b"uv 0.12.21", "rev-parse": b"a" * 40, "venv": b"", "pip": b"",
                   "-I": json.dumps(child).encode()}
        return subprocess.CompletedProcess(arguments, 0, replies[arguments[1]], b"")

    monkeypatch.setattr(verification, "command", command)
    # WHEN the actual parent consumes JSON returned by a fresh-install subprocess
    # THEN missing and internally rehashed foreign/corrupt evidence cannot become accepted installations
    with pytest.raises(ValueError, match="Installed inspection API"):
        verification.verify(checkout, bundle, smoke=smoke)


@pytest.mark.parametrize("field", ["module_sources", "module_sources_sha256"])
def test_consumer_refuses_missing_module_seal_without_producer_fallback(tmp_path, monkeypatch, field):
    # GIVEN valid distribution bytes whose manifest omits one mandatory module seal
    checkout, bundle, _modules = module_pair(tmp_path)
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=True)
    path = bundle / "ci-package/manifest.json"
    manifest = json.loads(path.read_bytes())
    del manifest[field]
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("missing module seal reached installation"))
    # WHEN current source and package identities are admitted for installation
    # THEN absent consumer evidence cannot take the producer sealed=None route
    with pytest.raises(ValueError, match="Package module source seal is missing or invalid"):
        verification.verify(checkout, bundle, smoke=True)


def damage_fixture_authority(checkout, bundle, damage):
    """Alter only the mandatory authority seal or its current source bytes."""
    path = bundle / "ci-package/manifest.json"
    manifest = json.loads(path.read_bytes())
    authority = verification.INSPECTION_AUTHORITY
    mutations = {"missing": lambda: manifest["source"].pop(authority),
                 "missing-and-changed": lambda: (
                     manifest["source"].pop(authority), (checkout / authority).write_bytes(b"changed unreviewed fixture authority\n")),
                 "malformed": lambda: manifest["source"].update({authority: ["not a source digest"]}),
                 "foreign": lambda: manifest["source"].update({authority: "0" * 64}),
                 "changed-current": lambda: (checkout / authority).write_bytes(b"changed unreviewed fixture authority\n")}
    mutations[damage]()
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("damage", ["missing-and-changed", "missing", "malformed", "foreign", "changed-current"])
def test_actual_verify_requires_mandatory_current_fixture_authority_before_commands(tmp_path, monkeypatch, smoke, damage):
    # GIVEN valid archives, module/fixture seals and actual API evidence beside damaged source authority
    checkout, bundle, _modules = module_pair(tmp_path)
    (checkout / verification.VERIFIER_SOURCE).write_bytes(HELPER.read_bytes())
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=smoke)
    source, expected = public_api_case(tmp_path)
    child = {"verification_scope": "smoke", "oracle": [], "probes": [{"probe": "epub2"}, {"probe": "formula"}],
             "inspection_api": verification.probe_inspection_api(source, expected)}
    damage_fixture_authority(checkout, bundle, damage)
    calls = []

    def command(arguments, cwd):
        """Retain actual consumer admission while substituting only install/process edges."""
        calls.append(arguments)
        replies = {"--version": b"uv 0.12.21", "rev-parse": b"a" * 40, "venv": b"", "pip": b"",
                   "-I": json.dumps(child).encode()}
        return subprocess.CompletedProcess(arguments, 0, replies[arguments[1]], b"")

    monkeypatch.setattr(verification, "command", command)
    # WHEN the actual full or smoke parent admits the unchanged packet
    # THEN omission cannot hide source edits or reach any tooling/install command
    with pytest.raises(ValueError, match="fixture authority"):
        verification.verify(checkout, bundle, smoke=smoke)
    assert calls == [], "mandatory authority refusal must precede the first consumer command"


@pytest.mark.parametrize("damage", ["missing-and-changed", "missing", "malformed", "foreign", "changed-current"])
def test_runtime_preparation_requires_current_fixture_authority_before_export(tmp_path, monkeypatch, damage):
    # GIVEN an unchanged valid smoke packet with omitted, malformed or foreign fixture authority
    checkout, bundle, _modules = module_pair(tmp_path)
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=True)
    damage_fixture_authority(checkout, bundle, damage)
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("fixture authority bypass reached runtime export"))
    # WHEN the actual runtime-copy entry admits its source packet
    # THEN every authority failure stops before copying or regenerating constraints
    with pytest.raises(ValueError, match="fixture authority"):
        verification.prepare_runtime(checkout, bundle)
    assert (bundle / "ci-runtime").exists() is False, "invalid authority must not create a runtime packet"


@pytest.mark.parametrize("side", ["root", "runtime"])
@pytest.mark.parametrize("damage", ["missing", "malformed", "foreign"])
def test_runtime_pair_and_actual_verify_bind_fixture_authority_in_both_directions(tmp_path, monkeypatch, side, damage):
    # GIVEN a genuine copied packet whose authority seal differs in exactly one direction
    checkout, bundle, _modules = module_pair(tmp_path)
    (checkout / verification.VERIFIER_SOURCE).write_bytes(HELPER.read_bytes())
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=True)
    verification.prepare_runtime(checkout, bundle)
    runtime = bundle / "ci-runtime"
    damage_fixture_authority(checkout, {"root": bundle, "runtime": runtime}[side], damage)
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("foreign root/runtime authority reached installation"))
    # WHEN both direct pair admission and actual compatibility verification consume that packet
    # THEN matching archives/module maps cannot substitute for identical authority provenance
    with pytest.raises(ValueError, match="fixture authority"):
        verification.check_runtime_pair(runtime)
    with pytest.raises(ValueError, match="fixture authority"):
        verification.verify(checkout, runtime)


@pytest.mark.parametrize("damage", [
    lambda actual: (actual[0], {**actual[1], "inspection": {**actual[1]["inspection"], "findings": -1}}, actual[2]),
    lambda actual: (actual[0], {**actual[1], "artifacts": [
        {**actual[1]["artifacts"][0], "sha256": "0" * 64}, *actual[1]["artifacts"][1:]]}, actual[2]),
    lambda actual: (actual[0], actual[1], actual[2].replace("literal();", "changed();", 1)),
    lambda actual: (actual[0], actual[1], actual[2] + "unbound extra content\n"),
], ids=["summary", "report-artifact", "literal-script", "unknown-extra-content"])
def test_installed_projection_requires_exact_source_bound_inspection_before_legacy_seals(tmp_path, damage):
    # GIVEN a valid full inspected output and its unchanged readable projection
    import brewdoc

    source = tmp_path / "source.html"
    source.write_bytes(b"<p>readable</p><script>literal();</script>")
    actual = brewdoc.run(source)
    expected = brewdoc.render_html(source)[0]
    assert verification.checked_projection(source, actual) == (
        0, {key: [] if key == "artifacts" else value for key, value in actual[1].items() if key != "inspection"},
        expected,
    ), "unchanged source-bound evidence must pass before readable seals are considered"
    # WHEN a summary, artifact hash, script source or unbound suffix changes
    altered = damage(copy.deepcopy(actual))
    # THEN a matching readable prefix cannot excuse changed inspection evidence
    with pytest.raises(ValueError, match="^Installed inspection differs from source-bound evidence$"):
        verification.checked_projection(source, altered)


@pytest.mark.parametrize("change", [
    {"implementation": "PyPy"}, {"version": "3.13.14"}, {"system": "Darwin"},
    {"machine": "aarch64"}, {"libc": ["musl", "1.2.5"]}, {"soabi": ""},
    {"gil_disabled": True}, {"pointer_bits": 32}, {"abi_flags": "t"},
    {"os_name": "debian"}, {"os_version": "24.04"}, {"soabi": "cpython-314-x86_64-linux-gnu"},
])
def test_expected_runtime_rejects_wrong_interpreter_platform_or_abi(change):
    # GIVEN the requested CPython/glibc cell with one incompatible observation
    identity = {"implementation": "CPython", "version": "3.13.15", "system": "Linux",
                "machine": "x86_64", "libc": ["glibc", "2.35"], "soabi": "cpython-313-x86_64-linux-gnu",
                "os_name": "ubuntu", "os_version": "22.04",
                "gil_disabled": False, "pointer_bits": 64, "abi_flags": ""}
    # WHEN parent or installed child identity is admitted
    # THEN the selected compatibility cell cannot be silently substituted
    with pytest.raises(ValueError, match="^Installed verification runtime differs from the selected cell$"):
        verification.check_runtime({**identity, **change}, "3.13.15", "linux-x64")


@pytest.mark.parametrize("version,target,system,machine,libc,soabi,os_name,os_version", [
    ("3.13.15", "linux-x64", "Linux", "x86_64", ["glibc", "2.35"], "cpython-313-x86_64-linux-gnu", "ubuntu", "22.04"),
    ("3.14.7", "macos-arm64", "Darwin", "arm64", ["", ""], "cpython-314-darwin", "Darwin", "15.8.1"),
    ("3.14.7", "windows-x64", "Windows", "AMD64", ["", ""], "cp314-win_amd64", "Windows Server 2025 Datacenter", "10.0.26100"),
])
def test_expected_runtime_accepts_the_exact_selected_cells(version, target, system, machine, libc, soabi, os_name, os_version):
    # GIVEN the normal-GIL CPython identity for a selected native runner
    identity = {"implementation": "CPython", "version": version, "system": system,
                "machine": machine, "libc": libc, "soabi": soabi, "gil_disabled": False,
                "os_name": os_name, "os_version": os_version,
                "pointer_bits": 64, "abi_flags": ""}
    # WHEN the identity is checked
    actual = verification.check_runtime(identity, version, target)
    # THEN all three representative cells are admitted exactly
    assert actual is None, "the observed interpreter must match its requested cell"


@pytest.mark.parametrize("change", [
    {"reason": "unrelated failure"}, {"file_ok": True}, {"receipt_schema": "brewdoc.receipt/1"},
    {"acquisition": {"refusal_stage": "connect", "counts": {"requests": 0}}},
    {"acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 1}}},
])
def test_js_prerequisite_receipt_rejects_wrong_reason_stage_or_requests(change):
    # GIVEN a superficially refused JS call with one incorrect contract field
    receipt = {"reason": "URL acquisition refused during prerequisites", "file_ok": False,
               "receipt_schema": "brewdoc.receipt/2",
               "acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
    # WHEN a missing-extra or Windows JS result is consumed
    # THEN another error cannot be accepted as the intended prerequisite refusal
    with pytest.raises(ValueError, match="^Unavailable JS prerequisite refusal changed$"):
        verification.check_js_refusal({**receipt, **change})


def test_full_verifier_rejects_missing_source_hash(tmp_path, monkeypatch):
    # GIVEN a full producer manifest that does not bind verifier behavior
    (tmp_path / "ci-package").mkdir()
    manifest = {"verification_scope": "full", "files": {}, "source": {}}
    (tmp_path / "ci-package/manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("unsealed verifier reached installation"))
    # WHEN full verification begins
    # THEN missing verifier provenance fails before any command
    with pytest.raises(ValueError, match="^Full installed verification requires the verifier source hash$"):
        verification.verify(tmp_path, tmp_path)


def test_full_verifier_rejects_changed_executed_verifier_before_installation(tmp_path, monkeypatch):
    # GIVEN a producer hash that differs from the executing verification script
    (tmp_path / "ci-package").mkdir()
    manifest = {"verification_scope": "full", "files": {}, "source": {verification.VERIFIER_SOURCE: "0" * 64}}
    (tmp_path / "ci-package/manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(verification, "command", lambda *args: pytest.fail("verifier drift reached installation"))
    # WHEN full verification starts
    # THEN the actual executing verifier must retain producer identity
    with pytest.raises(ValueError, match="^Installed verifier source differs from the producer$"):
        verification.verify(tmp_path, tmp_path)


@pytest.mark.parametrize("change", ["archive", "manifest"])
def test_runtime_pair_rejects_independently_sealed_different_archives_or_manifest(tmp_path, change):
    # GIVEN smoke and compatibility pairs whose separate seals remain internally valid
    runtime = tmp_path / "ci-runtime"
    for folder, scope in ((tmp_path, "smoke"), (runtime, "full")):
        (folder / "dist").mkdir(parents=True)
        (folder / "ci-package").mkdir()
        for name in ("brewdoc.whl", "brewdoc.tar.gz"):
            (folder / "dist" / name).write_bytes(b"original")
        manifest = {"verification_scope": scope, "files": {"dist/" + name: {
            "size": 8, "sha256": verification.digest(folder / "dist" / name)}
            for name in ("brewdoc.whl", "brewdoc.tar.gz")}}
        (folder / "ci-package/manifest.json").write_text(json.dumps(manifest))
    selected = runtime / "dist/brewdoc.whl"
    replacements = {"archive": b"modified", "manifest": b"original"}
    selected.write_bytes(replacements[change])
    manifest_path = runtime / "ci-package/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    hashes = {"archive": verification.digest(selected), "manifest": "0" * 64}
    manifest["files"]["dist/brewdoc.whl"]["sha256"] = hashes[change]
    manifest_path.write_text(json.dumps(manifest))
    # WHEN both archive identities are admitted together
    # THEN separately valid but different package bytes or seals cannot claim one built pair
    with pytest.raises(ValueError, match="^Compatibility distributions differ from the smoke pair$"):
        verification.check_runtime_pair(runtime)


def test_installed_article_probe_checks_api_cli_success_and_preserved_refusal(tmp_path, monkeypatch):
    # GIVEN an isolated directory and the real entry point
    import os

    monkeypatch.chdir(tmp_path)
    console = Path(sysconfig.get_path("scripts")) / ("brewdoc.exe" if os.name == "nt" else "brewdoc")
    # WHEN the article consumer probe runs
    actual = verification.probe_article(str(console))
    # THEN API and console agree on selection, body-only, fallback and output-preserving refusal
    assert ([row["exit"] for row in actual["cli"]], actual["fallback"]["article_fallback"],
            actual["refusal"]["reason"], (tmp_path / "article.md").read_bytes()) == (
        [0, 0, 0, 0, 1], True, "--article is an HTML-only option", b"existing output\n"), \
        "installed article mode must exercise selection, body-only, fallback and refusal preservation"


def test_runtime_preparation_copies_the_built_pair_without_changing_root_smoke(tmp_path, monkeypatch):
    # GIVEN an already sealed smoke pair and locked producer inputs
    checkout, bundle, _modules = module_pair(tmp_path)
    monkeypatch.setattr(verification, "command", fake_package_command)
    verification.prepare(checkout, bundle, smoke=True)
    root_manifest = bundle / "ci-package/manifest.json"
    original = root_manifest.read_bytes()
    fixture = (bundle / "ci-package/inspection-api.json").read_bytes()
    monkeypatch.setattr(verification.importlib.util, "spec_from_file_location",
                        lambda *args: pytest.fail("runtime copy regenerated fixture authority"))
    # WHEN the full runtime bundle is prepared from the existing pair
    verification.prepare_runtime(checkout, bundle)
    # THEN full provenance binds the verifier and actual pair hashes match root smoke
    runtime = bundle / "ci-runtime"
    full = json.loads((runtime / "ci-package/manifest.json").read_text())
    assert (root_manifest.read_bytes(), full["verification_scope"], full["source"][verification.VERIFIER_SOURCE],
            (runtime / "ci-package/inspection-api.json").read_bytes(), verification.check_runtime_pair(runtime)) == (
        original, "full", verification.digest(checkout / verification.VERIFIER_SOURCE), fixture, None), \
        "preparing runtime compatibility must preserve the root smoke and seal unchanged distribution bytes"


@pytest.mark.parametrize("enabled,error,code,stderr,expected_error", [
    (False, "JS engine failed unexpectedly", 1, b"", "Unavailable JS runtime refusal changed"),
    (True, "JS guarded stack prerequisite unavailable", 1, b"", "Unavailable JS runtime refusal changed"),
    (False, "JS runtime unavailable; install brewdoc[render-js]", 0, b"", "CLI exit, receipt, Markdown placement or stderr changed"),
    (True, "JS installed native prerequisites unsupported", 1, b"warning", "CLI exit, receipt, Markdown placement or stderr changed"),
])
def test_optional_refusal_probe_rejects_wrong_direct_reason_exit_or_stderr(monkeypatch, enabled, error, code, stderr, expected_error):
    # GIVEN a Windows optional or missing-extra result with an incorrect direct/console verdict
    import brewdoc
    from brewdoc import htmljs

    monkeypatch.setattr(verification.sys, "platform", "win32")
    packages = {False: [], True: [types.SimpleNamespace(metadata={"Name": "quickjs-ng"})]}[enabled]
    monkeypatch.setattr(verification.importlib.metadata, "distributions", lambda: packages)
    monkeypatch.setattr(verification, "installed_url_runtime", lambda value: {"added_installed_bytes": 1})
    receipt = {"reason": "URL acquisition refused during prerequisites", "file_ok": False,
               "route": "html", "receipt_schema": "brewdoc.receipt/2",
               "acquisition": {"refusal_stage": "prerequisites", "counts": {"requests": 0}}}
    stdout = (json.dumps(receipt, sort_keys=True) + verification.os.linesep).encode()
    monkeypatch.setattr(verification.subprocess, "run", lambda *args, **options:
                        subprocess.CompletedProcess([], code, stdout, stderr))

    def refuse():
        """Raise the observed installed runtime prerequisite error."""
        raise brewdoc.BrewdocError(error)

    monkeypatch.setattr(htmljs, "_check_runtime", refuse)
    # WHEN the complete optional-runtime probe consumes the observations
    # THEN an unrelated native error or incorrect CLI response cannot pass
    with pytest.raises(ValueError, match="^" + expected_error + "$"):
        verification.probe_js(enabled)


def test_subprocess_environment_keeps_cache_but_removes_source_and_interpreter_overrides(monkeypatch):
    # GIVEN source and virtual-environment overrides alongside an intended uv cache
    monkeypatch.setattr(verification.os, "environ", {
        "PATH": "/bin", "UV_CACHE_DIR": "/cache", "PYTHONPATH": "/source",
        "PYTHONHOME": "/source", "VIRTUAL_ENV": "/source", "UV_PYTHON": "/source",
        "UV_PROJECT_ENVIRONMENT": "/source",
    })
    # WHEN installation subprocess environment is derived
    actual = verification.clean_environment()
    # THEN source selection is removed without disabling the package cache
    assert actual == {"PATH": "/bin", "UV_CACHE_DIR": "/cache"}, (
        "independent installations must preserve caching but remove source environment selection")


@pytest.mark.parametrize("names", [
    ("a.whl",), ("a.tar.gz",), ("a.whl", "b.whl", "a.tar.gz"),
    ("a.whl", "a.tar.gz", "credentials.txt"),
])
def test_distribution_pair_refuses_missing_duplicate_or_extra_inputs(tmp_path, names):
    # GIVEN an incomplete or ambiguous publishing directory
    for name in names:
        (tmp_path / name).write_bytes(b"test")
    # WHEN the distribution inputs are selected
    # THEN ambiguity fails before any installation or publication
    with pytest.raises(ValueError, match="^dist must contain exactly one wheel and one .tar.gz sdist$"):
        verification.distributions(tmp_path)


def test_distribution_pair_returns_wheel_then_sdist(tmp_path):
    # GIVEN one wheel and one source archive
    wheel, sdist = tmp_path / "a.whl", tmp_path / "a.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    # WHEN the pair is selected
    actual = verification.distributions(tmp_path)
    # THEN each independent installer receives its intended format
    assert actual == (wheel, sdist), "the two installation consumers must keep their formats"


def test_manifest_accepts_only_the_original_distribution_bytes(tmp_path):
    # GIVEN a recorded artifact and its exact size and hash
    archive = tmp_path / "package.whl"
    archive.write_bytes(b"original")
    manifest = {"files": {archive.name: {"size": 8, "sha256": verification.digest(archive)}}}
    # WHEN the unchanged input is verified
    actual = verification.verify_manifest(tmp_path, manifest)
    # THEN identity succeeds without modifying the archive
    assert (actual, archive.read_bytes()) == (None, b"original"), "verification must be read-only"


def test_manifest_rejects_same_size_artifact_tampering(tmp_path):
    # GIVEN an artifact replaced after its manifest was recorded
    archive = tmp_path / "package.whl"
    archive.write_bytes(b"original")
    manifest = {"files": {archive.name: {"size": 8, "sha256": verification.digest(archive)}}}
    archive.write_bytes(b"modified")
    # WHEN the replacement is verified
    # THEN matching byte size cannot hide different content
    with pytest.raises(ValueError, match="^Artifact bytes changed: package.whl$"):
        verification.verify_manifest(tmp_path, manifest)


def test_manifest_rejects_a_path_outside_the_downloaded_bundle(tmp_path):
    # GIVEN a valid file referenced through a parent traversal
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"original")
    manifest = {"files": {"../outside.whl": {"size": 8, "sha256": verification.digest(outside)}}}
    # WHEN the manifest is consumed
    # THEN only files inside the downloaded artifact can be installed
    with pytest.raises(ValueError, match="^Artifact file escapes or is missing: ../outside.whl$"):
        verification.verify_manifest(bundle, manifest)


@pytest.mark.parametrize("module,search,editable", [
    ("checkout/src/brewdoc/__init__.py", [], False),
    ("environment/lib/brewdoc/__init__.py", ["checkout/src"], False),
    ("environment/lib/brewdoc/__init__.py", [], True),
])
def test_installed_origin_rejects_source_paths_and_editable_metadata(tmp_path, module, search, editable):
    # GIVEN an installation contaminated by source imports or editable state
    paths = [str(tmp_path / item) for item in search]
    # WHEN its origin is checked
    # THEN the source checkout cannot substitute for installation proof
    with pytest.raises(ValueError, match="^Installed package leaked checkout or editable state$"):
        verification.check_origin(tmp_path / module, tmp_path / "environment", tmp_path / "checkout",
                                  paths, {"dir_info": {"editable": editable}})


def test_installed_origin_accepts_a_separate_noneditable_environment(tmp_path):
    # GIVEN a module inside an independent environment with no source search path
    prefix, checkout = tmp_path / "environment", tmp_path / "checkout"
    # WHEN its noneditable metadata and origin are checked
    actual = verification.check_origin(prefix / "lib/brewdoc/__init__.py", prefix, checkout, [], {})
    # THEN origin proof succeeds without importing the checkout
    assert actual is None, "a valid independent installed origin must be accepted"


@pytest.mark.parametrize("newline,route,body,stdout_body", [
    ("\n", "sheet", "one\ntwo\n", b"one\ntwo\n"),
    ("\r\n", "sheet", "one\ntwo\n", b"one\ntwo\n"),
    ("\r\n", "html", "café\n", b"caf\xc3\xa9\n"),
    ("\r\n", "epub", "café\n", b"caf\xc3\xa9\n"),
])
def test_console_validation_preserves_native_receipt_newlines_and_unicode_routes(
        monkeypatch, newline, route, body, stdout_body):
    # GIVEN a native receipt newline and the route's actual stdout encoding
    monkeypatch.setattr(verification.os, "linesep", newline)
    receipt = {"route": route, "out": None}
    line = (json.dumps(receipt, sort_keys=True) + newline).encode("ascii")
    process = subprocess.CompletedProcess([], 0, line + stdout_body, b"")
    # WHEN the entire console response is checked
    actual = verification.check_cli(process, (0, receipt, body))
    # THEN native receipt newlines and literal UTF-8 body bytes remain distinct
    assert actual == {"exit": 0, "stdout": (line + stdout_body).decode("utf-8"), "stderr": ""}, (
        "console validation must retain the platform and route byte contract")


@pytest.mark.parametrize("code,stdout,stderr", [
    (1, b'{"out": null, "route": "sheet"}\nbody\n', b""),
    (0, b'{"out": null, "route": "sheet"}\n', b""),
    (0, b'{"out": null, "route": "sheet"}\nbody\n', b"warning"),
])
def test_console_validation_rejects_exit_body_or_stderr_drift(monkeypatch, code, stdout, stderr):
    # GIVEN a response with one changed console contract
    monkeypatch.setattr(verification.os, "linesep", "\n")
    process = subprocess.CompletedProcess([], code, stdout, stderr)
    # WHEN it is compared against the complete successful response
    # THEN a passing API result cannot mask a broken console consumer
    with pytest.raises(ValueError, match="^CLI exit, receipt, Markdown placement or stderr changed$"):
        verification.check_cli(process, (0, {"route": "sheet", "out": None}, "body\n"))


def test_console_output_mode_rejects_markdown_on_stdout(tmp_path, monkeypatch):
    # GIVEN correct output bytes with an unintended duplicate stdout body
    monkeypatch.setattr(verification.os, "linesep", "\n")
    output = tmp_path / "written.md"
    output.write_bytes(b"body\n")
    receipt = {"route": "sheet", "out": str(output)}
    stdout = (json.dumps(receipt, sort_keys=True) + "\nbody\n").encode("ascii")
    process = subprocess.CompletedProcess([], 0, stdout, b"")
    # WHEN --out placement is validated
    # THEN stdout must contain only its receipt line
    with pytest.raises(ValueError, match="^CLI exit, receipt, Markdown placement or stderr changed$"):
        verification.check_cli(process, (0, receipt, "body\n"), output)


def test_console_output_mode_rejects_altered_written_bytes(tmp_path, monkeypatch):
    # GIVEN a successful receipt and a wrong Markdown file
    monkeypatch.setattr(verification.os, "linesep", "\n")
    output = tmp_path / "written.md"
    output.write_bytes(b"wrong\n")
    receipt = {"route": "sheet", "out": str(output)}
    process = subprocess.CompletedProcess([], 0, (json.dumps(receipt, sort_keys=True) + "\n").encode(), b"")
    # WHEN the output file is checked
    # THEN file corruption cannot pass solely because stdout is correct
    with pytest.raises(ValueError, match="^CLI output bytes changed$"):
        verification.check_cli(process, (0, receipt, "body\n"), output)


def test_private_archive_content_is_rejected_before_export(tmp_path):
    # GIVEN a wheel containing private task data beside its package
    dist = tmp_path / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "a.whl", "w") as archive:
        archive.writestr("brewdoc/__init__.py", "__all__ = ['run']\n")
        archive.writestr(".codex/private.txt", "synthetic sentinel")
    with tarfile.open(dist / "a.tar.gz", "w:gz"):
        pass
    # WHEN producer metadata is prepared
    (tmp_path / "pyproject.toml").write_text("[project]\nversion = '0.2.0'\n")
    # THEN private content fails before any dependency export or installation
    with pytest.raises(ValueError, match="^Distribution includes private or local project files$"):
        verification.prepare(tmp_path, tmp_path)


@pytest.mark.parametrize("wheel_epub,sdist_epub", [(False, True), (True, False), (False, False)])
def test_preparation_rejects_distributions_missing_the_epub_adapter(tmp_path, wheel_epub, sdist_epub):
    # GIVEN a distribution pair with one or both EPUB modules absent
    checkout, bundle, modules = module_pair(tmp_path)
    without_epub = [(name, payload) for name, payload in modules.items() if name != "brewdoc/epub.py"]
    rows = {True: list(modules.items()), False: without_epub}
    write_module_pair(bundle, rows[wheel_epub], rows[sdist_epub])
    # WHEN producer metadata is prepared
    # THEN missing source refuses before dependency export or installation
    with pytest.raises(ValueError, match="^Both distributions must include the EPUB adapter$"):
        verification.prepare(checkout, bundle)


@pytest.mark.parametrize("change", [
    {"source_sha256": "0" * 64}, {"markdown_sha256": "0" * 64},
    {"markdown_utf8_bytes": 4}, {"receipt": {"route": "epub", "out": None, "units": 2}},
])
def test_reviewed_epub_evidence_checks_source_utf8_and_the_full_receipt(tmp_path, change):
    # GIVEN independent accepted bytes and one selected evidence change
    source = tmp_path / "reviewed.epub"
    source.write_bytes(b"reviewed source")
    markdown = "café\n"
    receipt = {"route": "epub", "out": "/temporary/output.md", "units": 1}
    accepted = {"source_sha256": hashlib.sha256(b"reviewed source").hexdigest(),
                "markdown_sha256": hashlib.sha256(b"caf\xc3\xa9\n").hexdigest(),
                "markdown_utf8_bytes": 6, "receipt": {**receipt, "out": None},
                "review_reference": "benchmarks/README.md#epub-acceptance",
                "review_scope": "complete source and output review",
                "normalization": "receipt.out=null; Markdown bytes unchanged"}
    assert markdown.encode("utf-8") == b"caf\xc3\xa9\n", "the independent UTF-8 denominator must be six bytes"
    # WHEN the complete installed result is checked against accepted evidence
    original = verification.check_accepted_output(source, (0, receipt, markdown), accepted)
    # THEN only out normalization is allowed and all identity fields are checked
    assert original is None, "unchanged reviewed evidence must accept exact bytes and full receipt"
    changed = {**accepted, **change}
    with pytest.raises(ValueError, match="^Installed EPUB differs from reviewed output: reviewed.epub$"):
        verification.check_accepted_output(source, (0, receipt, markdown), changed)


@pytest.mark.parametrize("change", [
    {"review_reference": ""}, {"review_scope": ""}, {"normalization": "fold Unicode"},
])
def test_epub_evidence_requires_review_and_explicit_normalization(tmp_path, change):
    # GIVEN evidence whose review or normalization contract is absent
    accepted = dict.fromkeys(("source_sha256", "markdown_sha256", "markdown_utf8_bytes", "receipt"), None)
    accepted.update({"review_reference": "review", "review_scope": "complete",
                     "normalization": "receipt.out=null; Markdown bytes unchanged", **change})
    # WHEN evidence is consumed before reading an installed result
    # THEN unreviewed observations cannot become acceptance expectations
    with pytest.raises(ValueError, match="^EPUB accepted-output evidence is incomplete$"):
        verification.check_accepted_output(tmp_path / "absent.epub", (0, {}, ""), accepted)


def test_installation_selection_keeps_both_epub_fixtures_and_private_acceptance_evidence(tmp_path, monkeypatch):
    # GIVEN the two reviewed EPUB entries with separate receipt and acceptance data
    monkeypatch.setattr(verification, "SUFFIXES", {".epub"})
    fixture_root = tmp_path / "tests/fixtures"
    fixture_root.mkdir(parents=True)
    benchmark_root = tmp_path / "benchmarks"
    benchmark_root.mkdir()
    paths = sorted(verification.EPUB_INPUTS)
    documents = [{"path": path, "id": str(index), "size_bytes": index + 1, "features": []}
                 for index, path in enumerate(paths)]
    receipts = {Path(path).relative_to("tests/fixtures").as_posix(): {
        "rc": 0, "route": "epub", "accepted_output": {"review_scope": "accepted"}} for path in paths}
    (benchmark_root / "corpus.json").write_text(json.dumps({"documents": documents}), encoding="utf-8")
    (fixture_root / "receipts.json").write_text(json.dumps(receipts), encoding="utf-8")
    # WHEN installation smoke inputs are selected
    actual = verification.select_inputs(tmp_path)
    # THEN both source identities and the optional evidence object reach the installed probe
    expected = [(document, receipts[Path(document["path"]).relative_to("tests/fixtures").as_posix()])
                for document in documents]
    assert actual == expected, "both EPUB fixtures must retain acceptance evidence without adding it to public receipts"


@pytest.mark.corpus
def test_epub_probe_accepts_the_real_wrapper_full_tally_and_reviewed_bytes(tmp_path, monkeypatch):
    # GIVEN a reviewed minimal publication and the actual public wrapper's full tally
    import brewdoc

    checkout = HELPER.parents[2]
    source = checkout / "tests/fixtures/epub/epubcheck-minimal.epub"
    path = tmp_path / source.name
    shutil.copyfile(source, path)
    snapshot = json.loads((checkout / "tests/fixtures/receipts.json").read_text(encoding="utf-8"))
    accepted = snapshot["epub/epubcheck-minimal.epub"]["accepted_output"]
    expected_tally = {"pages": 0, "sheets": 0, "chapters": 1, "slides": 0,
                      "tables": 0, "text_regions": 2, "columns_split": 0,
                      "broken_ligature_words": 0, "dropped": {
                          "control_chars": 0, "soft_hyphens": 0, "nbsp": 0, "pua_glyphs": 0,
                          "cid_survivors": 0, "ligatures": 0, "running_heads": 0,
                          "page_numbers": 0, "non_ascii_replaced": 0}}
    markdown, tally = brewdoc.render_epub(path)
    assert tally == expected_tally, "the real EPUB wrapper must retain all four shared unit counters"
    assert hashlib.sha256(markdown.encode("utf-8")).hexdigest() == accepted["markdown_sha256"], (
        "the real wrapper must match independently reviewed minimal EPUB bytes")
    monkeypatch.chdir(tmp_path)
    console = str(Path(sysconfig.get_path("scripts")) / ("brewdoc" + sysconfig.get_config_var("EXE")))
    # WHEN the real API, wrapper and console pass through the package probe
    actual = verification.probe_epub(path, brewdoc.run(path), accepted, console)
    # THEN the verifier accepts the complete tally and all three console output modes
    assert (actual["wrapper_tally"], len(actual["cli"]), actual["markdown_utf8_bytes"]) == (
        expected_tally, 3, 1949), "strict package verification must accept the real wrapper without dropping slides"
