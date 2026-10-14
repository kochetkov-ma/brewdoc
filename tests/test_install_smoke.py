"""Synthetic installed smoke excludes licensed corpus and optional installations."""

import importlib.util
from contextlib import nullcontext
import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest

CHECKOUT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_smoke', CHECKOUT / '.github/workflows/verify_install.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


def test_installed_smoke_makes_only_two_fresh_base_installations(tmp_path, monkeypatch):
    # GIVEN one sealed wheel and sdist with exact producer tool and source identity
    checkout, bundle = tmp_path / 'checkout', tmp_path / 'bundle'
    checkout.mkdir()
    (bundle / 'dist').mkdir(parents=True)
    (bundle / 'ci-package').mkdir()
    (bundle / 'dist/brewdoc.whl').write_bytes(b'wheel')
    (bundle / 'dist/brewdoc.tar.gz').write_bytes(b'sdist')
    manifest = {'verification_scope': 'smoke', 'source': {}, 'files': {}, 'checkout_sha': 'a' * 40,
                'uv': '0.12.21'}
    (bundle / 'ci-package/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    calls = []

    def command(arguments, cwd):
        """Substitute only install/process edges while retaining driver admission."""
        calls.append(arguments)
        replies = {('uv', '--version'): b'uv 0.12.21', ('git', 'rev-parse'): ('a' * 40).encode()}
        report = {'verification_scope': 'smoke', 'oracle': [], 'probes': [{'probe': 'epub2'}, {'probe': 'formula'}]}
        return subprocess.CompletedProcess(arguments, 0, replies.get(tuple(arguments[:2]), json.dumps(report).encode()), b'')

    monkeypatch.setattr(INSTALL, 'command', command)
    assert INSTALL.distributions(bundle / 'dist') == (
        bundle / 'dist/brewdoc.whl', bundle / 'dist/brewdoc.tar.gz'), 'the pair is complete before installation'
    # WHEN the explicit smoke driver creates isolated consumers
    INSTALL.verify(checkout, bundle, smoke=True)
    # THEN wheel and sdist each receive one base install and an explicit smoke probe
    report = json.loads(next((bundle / 'ci-package').glob('verification-*.json')).read_text())
    assert [(row['distribution'], row['render_js']) for row in report['installations']] == [
        ('brewdoc.whl', False), ('brewdoc.tar.gz', False)], 'CI installs only the two base artifacts'
    probes = [arguments for arguments in calls if 'probe' in arguments]
    assert [arguments[-1] for arguments in probes] == ['--smoke', '--smoke'], 'both consumers use synthetic smoke'
    assert report['verification_scope'] == 'smoke', 'the evidence cannot be mistaken for full coverage'


@pytest.mark.parametrize('child_change,count,expectation', [
    ({}, 4, nullcontext()),
    ({'version': '3.14.6'}, 1, pytest.raises(ValueError, match='Installed verification runtime differs from the selected cell')),
    ({'soabi': 'cpython-314t-darwin'}, 1, pytest.raises(ValueError, match='Installed verification runtime differs from the selected cell')),
])
def test_full_installations_bind_the_same_pair_to_four_independent_expected_runtimes(tmp_path, monkeypatch, child_change, count, expectation):
    # GIVEN the sealed full pair and one exact representative runtime
    checkout, bundle = tmp_path / 'checkout', tmp_path / 'bundle'
    source = checkout / '.github/workflows/verify_install.py'
    source.parent.mkdir(parents=True)
    shutil.copyfile(INSTALL.__file__, source)
    (bundle / 'dist').mkdir(parents=True)
    (bundle / 'ci-package').mkdir()
    wheel, sdist = bundle / 'dist/brewdoc.whl', bundle / 'dist/brewdoc.tar.gz'
    wheel.write_bytes(b'wheel')
    sdist.write_bytes(b'sdist')
    manifest = {'verification_scope': 'full', 'source': {INSTALL.VERIFIER_SOURCE: INSTALL.digest(source)},
                'files': {'dist/' + path.name: {'size': path.stat().st_size, 'sha256': INSTALL.digest(path)}
                          for path in (wheel, sdist)}, 'checkout_sha': 'a' * 40, 'uv': '0.12.21'}
    (bundle / 'ci-package/manifest.json').write_text(json.dumps(manifest))
    identity = {'implementation': 'CPython', 'version': '3.14.7', 'system': 'Darwin', 'machine': 'arm64',
                'soabi': 'cpython-314-darwin', 'abi_flags': '', 'gil_disabled': False, 'pointer_bits': 64,
                'libc': ['', ''], 'os_name': 'Darwin', 'os_version': '15.8.1'}
    calls = []

    def command(arguments, cwd):
        """Substitute installer processes while retaining artifact/runtime admission."""
        calls.append(arguments)
        replies = {('uv', '--version'): b'uv 0.12.21', ('git', 'rev-parse'): ('a' * 40).encode()}
        child = {'runtime': {**identity, **child_change}, 'verification_scope': 'full'}
        return subprocess.CompletedProcess(arguments, 0, replies.get(tuple(arguments[:2]), json.dumps(child).encode()), b'')

    monkeypatch.setattr(INSTALL, 'command', command)
    monkeypatch.setattr(INSTALL, 'runtime_identity', lambda: identity)
    # WHEN an exact cell consumes independent wheel/sdist base/optional environments
    with expectation:
        INSTALL.verify(checkout, bundle, expected_python='3.14.7', target='macos-arm64')
    # THEN every admitted child receives explicit runtime expectations and fresh environments
    probes = [arguments for arguments in calls if 'probe' in arguments]
    assert [arguments[-4:] for arguments in probes] == [
        ['--expected-python', '3.14.7', '--target', 'macos-arm64']
    ] * count, 'a substituted interpreter must stop immediately'
    environments = [arguments[-1] for arguments in calls if arguments[:2] == ['uv', 'venv']]
    assert [Path(path).name for path in environments] == [f'env-{index}' for index in range(count)], \
        'every admitted consumer requires an independent environment'
    installs = [arguments for arguments in calls if arguments[:3] == ['uv', 'pip', 'install'] and '--no-deps' in arguments]
    assert [Path(arguments[-1]).name for arguments in installs] == [
        'brewdoc.whl', 'brewdoc.whl', 'brewdoc.tar.gz', 'brewdoc.tar.gz'][:count], 'four installs reuse exactly the same pair'


def test_installed_smoke_generates_and_checks_synthetic_cli_without_corpus(tmp_path, monkeypatch):
    # GIVEN a corpus selector that fails whenever smoke attempts licensed input selection
    from brewdoc import __version__

    monkeypatch.chdir(tmp_path)

    def forbidden(*arguments):
        """Fail immediately on a licensed corpus selection attempt."""
        raise AssertionError('smoke selected licensed corpus')

    monkeypatch.setattr(INSTALL, 'select_inputs', forbidden)
    assert list(tmp_path.iterdir()) == [], 'the smoke starts without documents from any corpus'
    synthetic_checkout = tmp_path / 'synthetic-source'
    (synthetic_checkout / 'tests').mkdir(parents=True)
    for name in ('test_contract.py', 'test_epub.py', 'test_spreadsheets.py'):
        shutil.copyfile(CHECKOUT / 'tests' / name, synthetic_checkout / 'tests' / name)
    # WHEN smoke exercises existing synthetic EPUB and formula generators through the actual CLI
    console = Path(sysconfig.get_path('scripts')) / {'nt': 'brewdoc.exe', 'posix': 'brewdoc'}[os.name]
    result = INSTALL.probe_smoke(synthetic_checkout, str(console))
    # THEN both synthetic output and artifact contracts pass without licensed fixtures
    assert [row['probe'] for row in result['probes']] == ['epub2', 'formula'], 'only the existing two synthetic probes run'
    assert result['oracle'] == [], 'no licensed oracle is emitted by installed smoke'
    assert result['epub_refusal']['receipt']['file_ok'] is False, 'the actual malformed EPUB CLI refusal is retained'
    spec = importlib.util.spec_from_file_location('native_smoke', CHECKOUT / '.github/workflows/build_standalone.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    manifest = {'schema': 1, 'verification_scope': 'smoke', 'source_sha': 'a' * 40, 'version': __version__,
                'python': '3.12.14', 'target': 'linux-x64', 'wheel_sha256': 'b' * 64, 'lock_sha256': 'c' * 64,
                'executable': 'brewdoc/brewdoc', 'oracle': [], 'probes': result['probes'],
                'archive': {'filename': f'brewdoc-{__version__}-linux-x64.tar.gz', 'bytes': 1, 'sha256': 'd' * 64},
                'inventory': {'brewdoc': {'kind': 'directory', 'mode': 0o755},
                              'brewdoc/brewdoc': {'kind': 'file', 'mode': 0o755, 'bytes': 1, 'sha256': 'e' * 64}}}
    assert builder.validate_manifest(manifest) is None, 'actual installed synthetic records also satisfy the native seal'


@pytest.mark.parametrize('operation', ['verify', 'probe'])
@pytest.mark.parametrize('producer,smoke', [({}, True), ({'verification_scope': 'full'}, True),
                                          ({'verification_scope': 'smoke'}, False)])
def test_installed_scope_mismatch_refuses_before_installation_or_probe(tmp_path, monkeypatch, operation, producer, smoke):
    # GIVEN a producer whose explicit or legacy scope differs from the requested operation
    (tmp_path / 'ci-package').mkdir()
    (tmp_path / 'ci-package/manifest.json').write_text(json.dumps(producer), encoding='utf-8')

    def forbidden(*arguments, **options):
        """Expose any process or smoke probe started before scope admission."""
        raise AssertionError('scope mismatch reached process/probe side effects')

    monkeypatch.setattr(INSTALL, 'command', forbidden)
    monkeypatch.setattr(INSTALL, 'probe_smoke', forbidden)
    assert producer.get('verification_scope', 'full') != ('smoke' if smoke else 'full'), \
        'the producer and requested operation deliberately disagree'
    # WHEN the installed verifier admits the producer before any operation
    with pytest.raises(ValueError, match='Requested installed verification scope differs from its producer'):
        getattr(INSTALL, operation)(CHECKOUT, tmp_path, smoke=smoke)
    # THEN the rejection creates no installed or probe outputs
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob('*')) == [
        'ci-package', 'ci-package/manifest.json'], 'scope refusal preserves the producer evidence without consumer output'


@pytest.mark.parametrize('route', ['sheet', 'text', 'odt', 'odp', 'eml'])
@pytest.mark.parametrize('file_output', [False, True])
def test_installed_cli_admits_exact_unicode_bytes_for_new_utf8_routes(tmp_path, route, file_output):
    # GIVEN a complete receipt line and UTF-8 content with source-preserved newlines
    markdown = '\ufeffcafé 東京\r\nno final newline'
    destination = tmp_path / 'output.md'
    output = {False: None, True: destination}[file_output]
    receipt = {'file_ok': True, 'route': route, 'out': {False: None, True: str(destination)}[file_output]}
    line = (json.dumps(receipt, ensure_ascii=True, sort_keys=True) + os.linesep).encode('ascii')
    body = markdown.encode('utf-8')
    stdout = line + {False: body, True: b''}[file_output]
    result = subprocess.CompletedProcess(['brewdoc'], 0, stdout, b'')
    destination.write_bytes(body)
    assert body.endswith(b'no final newline'), 'the fixture has no implicit final newline'
    # WHEN installed verification checks stdout or destination placement
    checked = INSTALL.check_cli(result, (0, receipt, markdown), output)
    # THEN accepted bytes retain Unicode, BOM and CRLF without ASCII encoding or rewriting
    assert checked == {'exit': 0, 'stdout': stdout.decode('utf-8'), 'stderr': ''}, \
        'the installed verifier must accept every supported UTF-8 route verbatim'


def test_installed_full_selection_includes_each_new_supported_suffix(tmp_path):
    # GIVEN a bounded indexed corpus with all released suffixes and both accepted EPUBs
    names = ['sample' + suffix for suffix in (
        '.pdf', '.docx', '.pptx', '.html', '.xlsx', '.xlsm', '.xls', '.xlsb', '.ods',
        '.odt', '.odp', '.eml', '.md', '.txt')]
    paths = ['tests/fixtures/' + name for name in names] + sorted(INSTALL.EPUB_INPUTS)
    documents = [{'id': str(index), 'path': path, 'size_bytes': index + 1,
                  'features': ['sheet-order']} for index, path in enumerate(paths)]
    snapshots = {Path(path).relative_to('tests/fixtures').as_posix():
                 {'rc': 0, 'artifacts': [{'kind': 'vba-project'}]} for path in paths}
    (tmp_path / 'benchmarks').mkdir()
    (tmp_path / 'tests/fixtures').mkdir(parents=True)
    (tmp_path / 'benchmarks/corpus.json').write_text(json.dumps({'documents': documents}))
    (tmp_path / 'tests/fixtures/receipts.json').write_text(json.dumps(snapshots))
    assert len(paths) == 16, 'the fixture has fifteen suffixes and a second accepted EPUB'
    # WHEN full installed verification chooses one successful consumer input per suffix
    selected = INSTALL.select_inputs(tmp_path)
    # THEN the new formats receive installed verification alongside every existing format
    assert sorted(row['path'] for row, _snapshot in selected) == sorted(paths), \
        'full installed selection must include text, ODF and EML inputs'


@pytest.mark.parametrize('suffix', ['.md', '.txt', '.odt', '.odp', '.eml'])
def test_shipped_self_check_exercises_and_detects_a_broken_new_route(monkeypatch, capsys, suffix):
    # GIVEN one refused route in an otherwise working shipped self-check
    import brewdoc.selfcheck as shipped

    original = shipped.run
    observed = []

    def broken(path, *arguments, **options):
        """Expose the selected broken route while preserving every other consumer."""
        observed.append(Path(path).suffix)
        result = original(path, *arguments, **options)
        return {True: (1, result[1], ''), False: result}[Path(path).suffix == suffix]

    monkeypatch.setattr(shipped, 'run', broken)
    assert observed == [], 'the self-check has not run before fault injection'
    # WHEN the shipped consumer evaluates its deterministic synthetic documents
    code = shipped.self_check()
    output = capsys.readouterr()
    # THEN every added route is executed and its refusal makes the public verdict fail
    assert suffix in observed, 'the self-check must execute the newly supported suffix'
    assert (code, output.out.splitlines()[-1], output.err) == (1, 'self-check: FAIL (38 checks)', ''), \
        'a broken new route must fail the existing shipped verdict without changing seal counts'


def test_installed_new_route_refusals_preserve_destinations_through_actual_api_and_cli(tmp_path, monkeypatch):
    # GIVEN independent existing output destinations and the actual local console
    monkeypatch.chdir(tmp_path)
    console = Path(sysconfig.get_path('scripts')) / {'nt': 'brewdoc.exe', 'posix': 'brewdoc'}[os.name]
    assert list(tmp_path.iterdir()) == [], 'refusal consumers begin without generated inputs'
    # WHEN malformed text, ODF and EML are consumed through API and CLI
    observed = INSTALL.probe_new_refusals(str(console))
    # THEN every public route refuses and preserves its own existing output bytes
    assert [(row['receipt']['route'], row['receipt']['file_ok'], row['cli']['exit'])
            for row in observed] == [('text', False, 1), ('text', False, 1),
                                    ('odt', False, 1), ('odp', False, 1), ('eml', False, 1)], \
        'both text suffixes and all new document routes must refuse through their own consumers'
    assert [path.read_bytes() for path in sorted(tmp_path.glob('*-refusal.md'))] == [b'existing output\n'] * 5, \
        'none of the API or console refusals may replace a destination'
