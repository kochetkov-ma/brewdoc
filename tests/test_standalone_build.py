"""Artifact admission rejects unsafe paths and stale native acceptance."""

import importlib.util
import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


CHECKOUT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('standalone_build', CHECKOUT / '.github/workflows/build_standalone.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def native_record(tmp_path, target='linux-x64'):
    """Create a bounded real archive and its sealed admission record."""
    directory = tmp_path / target
    directory.mkdir()
    root = directory / 'bundle/brewdoc'
    root.mkdir(parents=True)
    executable = root / ('brewdoc.exe' if target == 'windows-x64' else 'brewdoc')
    executable.write_bytes(b'fixture executable identity')
    filename = f'brewdoc-0.3.0-{target}.' + ('zip' if target == 'windows-x64' else 'tar.gz')
    asset = directory / filename
    records = BUILDER.archive_bundle(root, asset, 'zip' if target == 'windows-x64' else 'tar.gz')
    rows = [{'path': f'tests/fixtures/input-{number}.pdf', 'source_sha256': 'a' * 64,
             'code': 0, 'receipt': {'out': None}, 'markdown_bytes': 1, 'markdown_sha256': 'b' * 64}
            for number in range(11)]
    manifest = {'schema': 1, 'source_sha': 'a' * 40, 'version': '0.3.0', 'python': '3.12.14',
                'target': target, 'wheel_sha256': 'b' * 64, 'lock_sha256': 'c' * 64,
                'executable': 'brewdoc/' + executable.name, 'inventory': records, 'oracle': rows,
                'archive': {'filename': filename, 'bytes': asset.stat().st_size, 'sha256': BUILDER.digest(asset)},
                'probes': [{'probe': kind, 'path': 'tests/fixtures/fixture.xlsx'}
                           for kind in ('sheet-order', 'vba', 'epub2', 'formula')]}
    verification = {'verdict': 'PASS', 'source_sha': manifest['source_sha'], 'version': '0.3.0',
                    'target': target, 'archive_sha256': manifest['archive']['sha256']}
    BUILDER.write_json(directory / 'manifest.json', manifest)
    BUILDER.write_json(directory / 'verification.json', verification)
    return directory, manifest


def linux_runtime_fixture(tmp_path, monkeypatch):
    """Provide actual copied library bytes with package-query I/O isolated at its edge."""
    root, host = tmp_path / 'brewdoc', tmp_path / 'host'
    (root / '_internal').mkdir(parents=True)
    sources = []
    owners = {'libgcc_s.so.1': 'libgcc-s1:amd64', 'libstdc++.so.6': 'libstdc++6:amd64'}
    for filename, package in owners.items():
        source = host / 'usr/lib' / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(('actual ' + filename).encode())
        (root / '_internal' / filename).write_bytes(source.read_bytes())
        sources.append({'name': filename, 'source': str(source), 'bytes': source.stat().st_size,
                        'sha256': BUILDER.digest(source)})
    notice = host / 'usr/share/doc/gcc-12-base/copyright'
    notice.parent.mkdir(parents=True)
    notice.write_text('GCC copyright\nGCC RUNTIME LIBRARY EXCEPTION\nVersion 3.1, 31 March 2009\n'
                      '0. Definitions.\n1. Grant of Additional Permission.\n2. No Weakening of GCC Copyleft.\n'
                      'The availability of this Exception does not imply any general presumption that third-party '
                      'software is unaffected by the copyleft requirements of the license of GCC.\n'
                      'GPL3: /usr/share/common-licenses/GPL-3\n')
    for package in owners.values():
        folder = host / 'usr/share/doc' / package.split(':')[0]
        folder.mkdir(parents=True)
        (folder / 'copyright').symlink_to(notice)
    gpl = host / 'usr/share/common-licenses/GPL-3'
    gpl.parent.mkdir(parents=True)
    gpl.write_text('GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n'
                   '0. Definitions.\n17. Interpretation of Sections 15 and 16.\n'
                   'END OF TERMS AND CONDITIONS\nHow to Apply These Terms to Your New Programs\n'
                   '<http://www.gnu.org/licenses/why-not-lgpl.html>.\n')
    versions = {**{package: '12.3.0-1ubuntu1~22.04.2' for package in owners.values()},
                'gcc-12-base:amd64': '12.3.0-1ubuntu1~22.04.2', 'base-files': '12ubuntu4.7'}

    def query(arguments, **kwargs):
        """Emulate only dpkg's installed package ownership and file metadata."""
        operation, name = arguments[1], arguments[-1]
        if operation == '-W':
            source_package = {'base-files': 'base-files'}.get(name, 'gcc-12')
            output = '\t'.join((name, versions[name], source_package, versions[name], 'installed')) + '\n'
        elif operation == '-L':
            paths = {'libgcc-s1:amd64': ['/usr/lib/libgcc_s.so.1'],
                     'libstdc++6:amd64': ['/usr/lib/libstdc++.so.6'],
                     'gcc-12-base:amd64': ['/usr/share/doc/gcc-12-base/copyright']}
            output = '\n'.join(paths[name]) + '\n'
        else:
            output = {'/usr/share/common-licenses/GPL-3': 'base-files: /usr/share/common-licenses/GPL-3\n'}.get(
                name, 'gcc-12-base:amd64: /usr/share/doc/gcc-12-base/copyright\n')
        return subprocess.CompletedProcess(arguments, 0, output.encode(), b'')

    monkeypatch.setattr(BUILDER, 'command', query)
    return root, host, sources, notice, gpl, versions


def test_linux_native_runtime_libraries_retain_matching_package_notices_and_hashes(tmp_path, monkeypatch):
    # GIVEN copied compiler libraries whose installed owners and source notices agree
    root, host, sources, notice, gpl, _versions = linux_runtime_fixture(tmp_path, monkeypatch)
    assert len(sources) == 2, 'both actual compiler runtime payloads need retained notices'
    # WHEN the pre-archive collector binds host ownership to the copied runtime bytes
    retained, libraries = BUILDER.collect_linux_runtime_notices(root, sources, host=host)
    # THEN complete notice files and actual library hashes enter the existing retention records
    assert [row['name'] for row in libraries] == ['libgcc_s.so.1', 'libstdc++.so.6'], 'the fixed runtime pair is recorded'
    assert [(root / row['path']).read_bytes() for row in retained] == [gpl.read_bytes(), notice.read_bytes()], (
        'the collector retains complete original copyright and GPL3 bytes without rewriting them')
    copyright_record = {'package': 'gcc-12-base:amd64', 'version': '12.3.0-1ubuntu1~22.04.2',
                        'source_package': 'gcc-12', 'source_version': '12.3.0-1ubuntu1~22.04.2'}
    gpl_record = {'package': 'base-files', 'version': '12ubuntu4.7', 'source_package': 'base-files', 'source_version': '12ubuntu4.7'}
    expected_notices = [{'path': 'licenses/installed/native-runtime/' + name, 'bytes': len(payload),
                         'sha256': hashlib.sha256(payload).hexdigest()} for name, payload in
                        [('gcc-12-base-amd64-copyright', notice.read_bytes()), ('GPL-3', gpl.read_bytes())]]
    expected = [{'name': name, 'package': package, 'version': '12.3.0-1ubuntu1~22.04.2',
                 'source_package': 'gcc-12', 'source_version': '12.3.0-1ubuntu1~22.04.2',
                 'path': '_internal/' + name, 'bytes': len(('actual ' + name).encode()),
                 'sha256': hashlib.sha256(('actual ' + name).encode()).hexdigest(),
                 'copyright_package': copyright_record, 'gpl_package': gpl_record, 'notices': expected_notices}
                for name, package in [('libgcc_s.so.1', 'libgcc-s1:amd64'), ('libstdc++.so.6', 'libstdc++6:amd64')]]
    assert libraries == expected, 'complete public records bind exact binary/source versions, shipped bytes and full notice identities'


@pytest.mark.parametrize('change', ['missing-notice', 'truncated-exception', 'missing-gpl', 'truncated-gpl', 'wrong-source',
                                  'copied-bytes', 'source-bytes', 'missing-library', 'missing-source', 'duplicate-source'])
def test_linux_native_notice_collection_refuses_unbound_or_incomplete_license_inputs(tmp_path, monkeypatch, change):
    # GIVEN an otherwise complete actual library/package/notice source mapping
    root, host, sources, notice, gpl, versions = linux_runtime_fixture(tmp_path, monkeypatch)
    assert (root / '_internal/libgcc_s.so.1').read_bytes() == b'actual libgcc_s.so.1', 'the source copy starts exact'
    changes = {'missing-notice': notice.unlink, 'truncated-exception': lambda: notice.write_text('GCC copyright\n'),
               'missing-gpl': gpl.unlink, 'wrong-source': lambda: versions.update({'gcc-12-base:amd64': '12.2.0'}),
               'truncated-gpl': lambda: gpl.write_text(gpl.read_text().replace('why-not-lgpl.html>.', '')),
               'copied-bytes': lambda: (root / '_internal/libgcc_s.so.1').write_bytes(b'substituted library'),
               'source-bytes': lambda: Path(sources[0]['source']).write_bytes(b'changed host library'),
               'missing-library': lambda: (root / '_internal/libgcc_s.so.1').unlink(),
               'missing-source': lambda: sources.pop(),
               'duplicate-source': lambda: sources.append(sources[0])}
    changes[change]()
    # WHEN admission runs before any distributable archive is written
    with pytest.raises(ValueError, match='Linux runtime'):
        BUILDER.collect_linux_runtime_notices(root, sources, host=host)
    # THEN the incomplete notice set cannot become archive input
    assert (root / 'licenses/installed/native-runtime').exists() is False, 'all evidence is validated before retention'


def runtime_hook_fixture(tmp_path, monkeypatch):
    """Model two installed tool owners while retaining real original runtime-hook files."""
    environment = tmp_path / 'environment'
    paths = {'pyinstaller': 'lib/site-packages/PyInstaller/hooks/rthooks/pyi_rth_inspect.py',
             'pyinstaller-hooks-contrib': 'lib/site-packages/_pyinstaller_hooks_contrib/rthooks/pyi_rth_cryptography_openssl.py'}
    versions = {'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.8'}
    packages, scripts = {}, []
    for component, name in paths.items():
        source = environment / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(('Original full hook source, copyright 2024: ' + component + '\n').encode())
        packages[component] = SimpleNamespace(version=versions[component], files=[name], locate_file=lambda item: environment / item)
        scripts.append((source.stem, str(source), 'PYSOURCE'))
    monkeypatch.setattr(BUILDER.importlib.metadata, 'distribution', packages.__getitem__)
    return environment, scripts, versions, packages


def test_actual_included_hook_sources_keep_full_original_attribution_and_pinned_tool_identity(tmp_path, monkeypatch):
    # GIVEN Analysis-selected hook sources installed inside the isolated pinned build environment
    environment, scripts, versions, _packages = runtime_hook_fixture(tmp_path, monkeypatch)
    assert len(scripts) == 2, 'the fixture distinguishes core and contributed runtime-hook owners'
    # WHEN the collector captures before EXE and retains unchanged full source files before archiving
    captured = BUILDER.capture_native_sources([], scripts, environment, versions)
    root = tmp_path / 'brewdoc'
    retained, hooks = BUILDER.collect_runtime_hook_notices(root, captured['hooks'])
    # THEN every copied notice keeps complete source bytes and exact installed-tool provenance
    assert [(root / row['path']).read_bytes() for row in retained] == [Path(row[1]).read_bytes() for row in sorted(scripts)], (
        'runtime hook attribution retains original full file contents including the contributed 2024 header')
    assert [(row['component'], row['version']) for row in hooks] == [('pyinstaller-hooks-contrib', '2026.8'), ('pyinstaller', '6.22.3')], (
        'both installed tool versions stay attached to their actual captured hook sources')
    expected = []
    for name, component, version in [('pyi_rth_cryptography_openssl.py', 'pyinstaller-hooks-contrib', '2026.8'),
                                     ('pyi_rth_inspect.py', 'pyinstaller', '6.22.3')]:
        payload = ('Original full hook source, copyright 2024: ' + component + '\n').encode()
        expected.append({'name': name, 'component': component, 'version': version, 'bytes': len(payload),
                         'sha256': hashlib.sha256(payload).hexdigest(), 'notice': {
                             'path': 'licenses/installed/runtime-hooks/' + name, 'bytes': len(payload),
                             'sha256': hashlib.sha256(payload).hexdigest()}})
    assert hooks == expected, 'full original hook source and retained notice identities are bound to each exact installed tool'
    assert (str(environment) in json.dumps(hooks)) is False, 'shipped runtime metadata contains no private host source paths'


@pytest.mark.parametrize('change', ['changed', 'missing', 'duplicate', 'unsafe-name', 'empty'])
def test_runtime_hook_retention_refuses_changed_missing_or_unsafe_captured_sources(tmp_path, monkeypatch, change):
    # GIVEN an exact pre-EXE capture of installed runtime-hook sources
    environment, scripts, versions, _packages = runtime_hook_fixture(tmp_path, monkeypatch)
    captured = BUILDER.capture_native_sources([], scripts, environment, versions)['hooks']
    assert next(row['bytes'] for row in captured if row['component'] == 'pyinstaller') == 55, 'the core captured source has the exact authored byte count'
    changes = {'changed': lambda: Path(captured[0]['source']).write_bytes(b'changed hook source'),
               'missing': lambda: Path(captured[0]['source']).unlink(), 'duplicate': lambda: captured.append(captured[0]),
               'unsafe-name': lambda: captured[0].update(name='../pyi_rth_bad.py'), 'empty': captured.clear}
    changes[change]()
    # WHEN post-freeze retention compares source/name identities before writing any notice
    with pytest.raises(ValueError, match='Runtime hook'):
        BUILDER.collect_runtime_hook_notices(tmp_path / 'brewdoc', captured)
    # THEN refused hook evidence cannot become a distributable notice set
    assert (tmp_path / 'brewdoc/licenses').exists() is False, 'all hooks are admitted before the first notice write'


@pytest.mark.parametrize('change', ['outside-environment', 'wrong-version', 'duplicate', 'wrong-directory'])
def test_runtime_hook_capture_requires_unique_installed_pinned_owners_inside_the_build_environment(tmp_path, monkeypatch, change):
    # GIVEN a real hook file and the exact installed build-tool package file lists
    environment, scripts, versions, packages = runtime_hook_fixture(tmp_path, monkeypatch)
    assert scripts[0][0] == 'pyi_rth_inspect', 'the control source is the normal core runtime hook'
    relocated = tmp_path / 'pyi_rth_inspect.py'
    relocated.write_bytes(Path(scripts[0][1]).read_bytes())
    wrong_folder = environment / 'lib/site-packages/pyi_rth_inspect.py'
    wrong_folder.write_bytes(relocated.read_bytes())
    changes = {'outside-environment': lambda: scripts.__setitem__(0, (scripts[0][0], str(relocated), 'PYSOURCE')),
               'wrong-version': lambda: setattr(packages['pyinstaller'], 'version', '6.22.2'),
               'duplicate': lambda: scripts.append(scripts[0]),
               'wrong-directory': lambda: (scripts.__setitem__(0, (scripts[0][0], str(wrong_folder), 'PYSOURCE')),
                   setattr(packages['pyinstaller'], 'files', ['lib/site-packages/pyi_rth_inspect.py']))}
    changes[change]()
    # WHEN capture checks the actual files and pinned package ownership before EXE
    with pytest.raises(ValueError, match='Runtime hook'):
        BUILDER.capture_native_sources([], scripts, environment, versions)
    # THEN invalid attribution never writes a build-local source sidecar
    assert (tmp_path / 'native-notice-sources.json').exists() is False, 'the caller writes evidence only after capture succeeds'


@pytest.mark.parametrize('name', ['../brewdoc/file', '/brewdoc/file', 'brewdoc/../file',
                                 'brewdoc\\file', 'brewdoc//file', 'brewdoc/file\x00', 'brewdoc/C:file'])
def test_archive_names_reject_escape_aliases_and_controls(name):
    # GIVEN an archive name outside the supported single-directory shape
    assert isinstance(name, str) is True, 'the adversarial archive name is text'
    # WHEN the consumer admits that member before extraction
    with pytest.raises(ValueError, match='Unsafe standalone path'):
        BUILDER.relative_name(name)
    # THEN the same fixed validator accepts its ordinary counterpart
    assert BUILDER.relative_name('brewdoc/file') == 'brewdoc/file', 'ordinary members remain supported'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX native archives retain internal symbolic links')
def test_archive_roundtrip_retains_internal_links_and_all_file_hashes(tmp_path):
    # GIVEN a regular payload and one relative internal link
    root = tmp_path / 'bundle/brewdoc'
    root.mkdir(parents=True)
    (root / 'library').write_bytes(b'native payload')
    (root / 'alias').symlink_to('library')
    assert os.readlink(root / 'alias') == 'library', 'the test link remains inside its bundle'
    # WHEN the complete real tar archive is admitted and extracted
    asset = tmp_path / 'bundle.tar.gz'
    expected = BUILDER.archive_bundle(root, asset, 'tar.gz')
    actual = BUILDER.extract_archive(asset, tmp_path / 'consumer', expected)
    # THEN bytes, types, permissions and relative link targets remain identical
    assert BUILDER.inventory(actual) == expected, 'extraction preserves the whole admitted inventory'


def symlink_tar(tmp_path, link_mode):
    """Write a synthetic TAR with explicit sealed modes and an internal link."""
    payload = b'native payload'
    expected = {'brewdoc': {'kind': 'directory', 'mode': 0o755},
                'brewdoc/library': {'kind': 'file', 'mode': 0o644, 'bytes': len(payload),
                                    'sha256': hashlib.sha256(payload).hexdigest()},
                'brewdoc/alias': {'kind': 'symlink', 'mode': link_mode, 'target': 'library'}}
    asset = tmp_path / 'bundle.tar.gz'
    directory = tarfile.TarInfo('brewdoc')
    directory.type, directory.mode = tarfile.DIRTYPE, 0o755
    library = tarfile.TarInfo('brewdoc/library')
    library.mode, library.size = 0o644, len(payload)
    alias = tarfile.TarInfo('brewdoc/alias')
    alias.type, alias.mode, alias.linkname = tarfile.SYMTYPE, link_mode, 'library'
    with tarfile.open(asset, 'w:gz') as archive:
        archive.addfile(directory)
        archive.addfile(library, io.BytesIO(payload))
        archive.addfile(alias)
    return asset, expected


@pytest.mark.skipif(os.name == 'nt', reason='POSIX TAR links acquire the extracting host mode')
def test_extraction_admits_sealed_symlink_mode_from_another_host(tmp_path):
    # GIVEN a real TAR whose accepted link mode differs from this host's link mode
    probe = tmp_path / 'probe'
    probe.symlink_to('library')
    host_mode = stat.S_IMODE(probe.lstat().st_mode)
    archived_mode = host_mode ^ 0o022
    assert archived_mode ^ host_mode == 0o022, 'the sealed mode differs from the actual host mode'
    asset, expected = symlink_tar(tmp_path, archived_mode)
    sealed = json.loads(json.dumps(expected))
    with tarfile.open(asset) as archive:
        assert archive.getmember('brewdoc/alias').mode == archived_mode, 'the real header matches the sealed link mode'
    # WHEN strict header admission extracts the archive onto this host
    actual = BUILDER.extract_archive(asset, tmp_path / 'consumer', expected)
    # THEN only observed link permissions differ, while the sealed record stays unchanged
    observed = {**sealed, 'brewdoc/alias': {'kind': 'symlink', 'mode': host_mode, 'target': 'library'}}
    assert BUILDER.inventory(actual) == observed, 'file bytes, file/directory modes and link targets survive extraction'
    assert expected == sealed, 'admission must preserve the original sealed evidence'


@pytest.mark.skipif(os.name == 'nt', reason='Windows bundles forbid symbolic links')
@pytest.mark.parametrize('name,delta,reason', [
    ('brewdoc/alias', {'mode': 0o755}, 'Standalone tar declared size or mode changed'),
    ('brewdoc/alias', {'target': './library'}, 'Standalone link target changed'),
    ('brewdoc/library', {'mode': 0o755}, 'Standalone tar declared size or mode changed'),
    ('brewdoc', {'mode': 0o700}, 'Standalone tar declared size or mode changed'),
], ids=['link-mode', 'link-target', 'file-mode', 'directory-mode'])
def test_extraction_refuses_tar_headers_that_differ_from_sealed_evidence(tmp_path, name, delta, reason):
    # GIVEN real TAR headers with one conflicting sealed mode or link target
    asset, expected = symlink_tar(tmp_path, 0o777)
    with tarfile.open(asset) as archive:
        assert archive.getmember(name).mode == expected[name]['mode'], 'the original sealed mode matches the raw header'
    expected[name].update(delta)
    # WHEN admission compares raw headers before any payload is written
    with pytest.raises(ValueError, match='^' + reason + '$'):
        BUILDER.extract_archive(asset, tmp_path / 'consumer', expected)
    # THEN host link permission handling cannot bypass raw archive integrity
    assert list((tmp_path / 'consumer').iterdir()) == [], 'conflicting headers refuse before extraction'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX native archives contain symbolic links')
@pytest.mark.parametrize('mutate', [
    lambda root: (root / 'library').chmod(0o755),
    lambda root: root.chmod(0o700),
    lambda root: ((root / 'alias').unlink(), (root / 'alias').symlink_to('./library')),
    lambda root: ((root / 'alias').unlink(), (root / 'alias').write_bytes(b'native payload')),
    lambda root: (root / 'library').write_bytes(b'changed payload'),
    lambda root: (root / 'library').write_bytes(b'changed bytes!'),
], ids=['file-mode', 'directory-mode', 'link-target', 'link-kind', 'file-size', 'file-hash'])
def test_extraction_refuses_changes_to_extracted_files_directories_and_link_identity(tmp_path, monkeypatch, mutate):
    # GIVEN an admitted real TAR and a filesystem change before final inventory
    asset, expected = symlink_tar(tmp_path, 0o777)
    sealed = json.loads(json.dumps(expected))
    original_inventory = BUILDER.inventory
    assert expected['brewdoc/library']['bytes'] == 14, 'the file-size and same-size hash cases start with 14 bytes'

    def changed_inventory(root):
        """Change actual extracted state at the final observation boundary."""
        mutate(root)
        return original_inventory(root)

    monkeypatch.setattr(BUILDER, 'inventory', changed_inventory)
    # WHEN final admission observes the changed filesystem
    with pytest.raises(ValueError, match='^Extracted standalone bytes, modes or link targets changed$'):
        BUILDER.extract_archive(asset, tmp_path / 'consumer', expected)
    # THEN accepting host link permissions does not alter the original seal
    assert expected == sealed, 'file, directory and link integrity failures preserve accepted evidence'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX temporary roots may use an internal filesystem alias')
def test_archive_internal_link_is_admitted_under_a_symlinked_temporary_parent(tmp_path):
    # GIVEN a safe archive and a consumer parent reached through a filesystem alias
    root = tmp_path / 'bundle/brewdoc'
    root.mkdir(parents=True)
    (root / 'library').write_bytes(b'native payload')
    (root / 'alias').symlink_to('library')
    real_parent = tmp_path / 'real-parent'
    real_parent.mkdir()
    parent_alias = tmp_path / 'parent-alias'
    parent_alias.symlink_to(real_parent, target_is_directory=True)
    assert parent_alias.resolve() == real_parent, 'the temporary parent alias resolves to the intended directory'
    asset = tmp_path / 'bundle.tar.gz'
    expected = BUILDER.archive_bundle(root, asset, 'tar.gz')
    # WHEN archive admission resolves its internal link below that aliased parent
    actual = BUILDER.extract_archive(asset, parent_alias / 'consumer', expected)
    # THEN a safe filesystem alias does not masquerade as a bundle escape
    assert BUILDER.inventory(actual) == expected, 'the admitted inventory survives relocation through an alias'


@pytest.mark.skipif(os.name == 'nt', reason='Windows bundles forbid symbolic links')
def test_inventory_refuses_an_existing_external_link_before_archiving(tmp_path):
    # GIVEN a link to a real file outside the bundle
    root = tmp_path / 'brewdoc'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'external')
    (root / 'link').symlink_to('../outside')
    assert (root / 'link').resolve() == outside, 'the precondition is an actual outside link'
    # WHEN inventory runs before any archive is written
    with pytest.raises(ValueError, match='link escapes'):
        BUILDER.inventory(root)
    # THEN the original external payload is untouched
    assert outside.read_bytes() == b'external', 'refusal does not mutate referenced files'


def test_extraction_refuses_duplicate_archive_members_before_payload_write(tmp_path):
    # GIVEN an otherwise admitted archive with duplicate root declarations
    directory, manifest = native_record(tmp_path)
    asset = directory / manifest['archive']['filename']
    with tarfile.open(asset, 'w:gz') as archive:
        member = tarfile.TarInfo('brewdoc')
        member.type = tarfile.DIRTYPE
        member.mode = manifest['inventory']['brewdoc']['mode']
        archive.addfile(member)
        archive.addfile(member)
    with tarfile.open(asset) as archive:
        assert len(archive.getmembers()) == 2, 'the archive has exactly the intended duplicate'
    # WHEN the consumer validates the complete member set
    with pytest.raises(ValueError, match='Duplicate'):
        BUILDER.extract_archive(asset, tmp_path / 'consumer', manifest['inventory'])
    # THEN no executable payload has been extracted
    assert list((tmp_path / 'consumer').iterdir()) == [], 'preflight occurs before payload extraction'


def test_zip_special_file_type_is_refused_before_extraction(tmp_path):
    # GIVEN a real ZIP whose executable member declares a FIFO instead of a regular file
    directory, manifest = native_record(tmp_path, 'windows-x64')
    asset = directory / manifest['archive']['filename']
    with zipfile.ZipFile(asset) as archive:
        members = {member.filename: member for member in archive.infolist()}
        payloads = {name: archive.read(name) for name in members}
    member = members['brewdoc/brewdoc.exe']
    member.create_system = 3
    member.external_attr = (stat.S_IFIFO | manifest['inventory']['brewdoc/brewdoc.exe']['mode']) << 16
    with zipfile.ZipFile(asset, 'w') as archive:
        for name, info in members.items():
            archive.writestr(info, payloads[name])
    assert stat.S_IFMT(member.external_attr >> 16) == stat.S_IFIFO, 'the malicious header preserves a concrete FIFO type'
    # WHEN archive admission checks types before allocating payload files
    with pytest.raises(ValueError, match='special'):
        BUILDER.extract_archive(asset, tmp_path / 'consumer', manifest['inventory'])
    # THEN no member is extracted as a regular-file substitute
    assert list((tmp_path / 'consumer').iterdir()) == [], 'the special entry refuses during preflight'


@pytest.mark.parametrize('delta,reason', [
    ({'target': 'linux-arm64'}, 'manifest identity'),
    ({'python': '3.12.3'}, 'manifest identity'),
    ({'source_sha': 'f' * 39}, 'source or artifact hash'),
    ({'archive': {'filename': '../external', 'bytes': 1, 'sha256': 'a' * 64}}, 'archive identity'),
    ({'oracle': []}, 'oracle differs'),
])
def test_manifest_rejects_unreviewed_target_interpreter_identity_and_archive_path(tmp_path, delta, reason):
    # GIVEN a valid sealed artifact record and one unsupported change
    _directory, manifest = native_record(tmp_path)
    BUILDER.validate_manifest(manifest)
    altered = {**manifest, **delta}
    assert altered.keys() == manifest.keys(), 'the failure changes a value rather than omitting unrelated keys'
    # WHEN manifest admission occurs before artifact access
    with pytest.raises(ValueError, match=reason):
        BUILDER.validate_manifest(altered)
    # THEN the original record is still admitted
    assert BUILDER.validate_manifest(manifest) is None, 'the supported manifest remains valid'


@pytest.mark.parametrize('delta', [{'verdict': 'FAIL'}, {'source_sha': 'd' * 40}])
def test_native_admission_rejects_stale_verification(tmp_path, delta):
    # GIVEN an admitted actual archive and its matching native receipt
    directory, manifest = native_record(tmp_path)
    assert BUILDER.admit_manifest(directory / 'manifest.json') == manifest, 'the baseline is independently admissible'
    # WHEN one public-delivery identity is altered
    record = json.loads((directory / 'verification.json').read_text())
    BUILDER.write_json(directory / 'verification.json', {**record, **delta})
    # THEN admission refuses the mismatch without publishing a replacement
    with pytest.raises(ValueError, match='changed|matching native acceptance'):
        BUILDER.admit_manifest(directory / 'manifest.json')


def test_native_admission_rejects_changed_archive_bytes(tmp_path):
    # GIVEN actual admitted bytes and a successful native verification receipt
    directory, manifest = native_record(tmp_path)
    assert BUILDER.admit_manifest(directory / 'manifest.json') == manifest, 'the baseline archive is admitted'
    # WHEN the archive payload is changed without updating accepted evidence
    (directory / manifest['archive']['filename']).write_bytes(b'altered archive')
    # THEN the byte identity check refuses it before delivery
    with pytest.raises(ValueError, match='accepted archive changed'):
        BUILDER.admit_manifest(directory / 'manifest.json')


def test_aggregation_requires_all_targets_with_one_source_identity(tmp_path):
    # GIVEN only two native targets with successful independent receipts
    native_record(tmp_path, 'linux-x64')
    native_record(tmp_path, 'windows-x64')
    assert len(list(tmp_path.glob('*/manifest.json'))) == 2, 'the missing target is the only denominator gap'
    # WHEN release aggregation requires the whole reviewed matrix
    with pytest.raises(ValueError, match='three native targets'):
        BUILDER.aggregate(tmp_path, tmp_path / 'release')
    # THEN no release assets are staged before all targets pass
    assert (tmp_path / 'release').exists() is False, 'the failed aggregate has no publishable output'


@pytest.mark.parametrize('host_newline', ['\n', '\r\n'], ids=['lf-host', 'crlf-host'])
def test_native_aggregation_checksum_bytes_use_lf_on_every_host(tmp_path, monkeypatch, host_newline):
    # GIVEN three admitted archives and real text I/O using the selected host's default newline
    records = [smoke_record(tmp_path, target)[1] for target in ('linux-x64', 'macos-arm64', 'windows-x64')]
    write_text = Path.write_text

    def host_write_text(path, data, encoding=None, errors=None, newline=None):
        """Use actual text translation while retaining explicit newline choices."""
        return write_text(path, data, encoding=encoding, errors=errors,
                          newline={None: host_newline}.get(newline, newline))

    monkeypatch.setattr(Path, 'write_text', host_write_text)
    control = tmp_path / 'host-newline.txt'
    control.write_text('control\n', encoding='ascii')
    assert control.read_bytes() == ('control' + host_newline).encode('ascii'), 'real text I/O must reproduce the host newline'
    lines = [record['archive']['sha256'] + '  ' + record['archive']['filename'] for record in records]
    expected = ('\n'.join(sorted(lines)) + '\n').encode('ascii')
    # WHEN the native aggregate writes checksums for the complete admitted target set
    output = tmp_path / 'release'
    BUILDER.aggregate(tmp_path, output)
    # THEN checksum bytes retain canonical ordering, every archive identity and LF line endings
    assert (output / 'SHA256SUMS').read_bytes() == expected, 'checksum bytes must be identical on LF and CRLF hosts'


@pytest.mark.parametrize('refusal', [(0, b'["127.0.0.1"]\n', b''), (1, b'output', b''),
                                   (1, b'', b'warning'), (2, b'', b'')])
def test_windows_frozen_dns_gate_rejects_inexact_exited_owner_refusal(tmp_path, monkeypatch, refusal):
    # GIVEN a live-owner DNS success and a real exited owner retained by its Popen object
    binary = tmp_path / 'brewdoc.exe'
    environment = BUILDER.consumer_environment(tmp_path)
    owners = []
    original_popen = subprocess.Popen

    def owner_process(*arguments, **options):
        """Keep the actual owner available for the admission precondition."""
        owner = original_popen(*arguments, **options)
        owners.append(owner)
        return owner

    def exited_dns(arguments, **options):
        """Simulate the binary response after the retained owner has exited."""
        assert owners[0].poll() == 0, 'the actual owner has exited before frozen DNS starts'
        assert arguments == [str(binary), '--brewdoc-internal', 'dns', 'localhost', '80', str(owners[0].pid)], \
            'the admitted executable receives exactly the exited owner PID'
        return subprocess.CompletedProcess(arguments, *refusal)

    monkeypatch.setattr(BUILDER, 'sys', SimpleNamespace(platform='win32', _base_executable=sys._base_executable))
    monkeypatch.setattr(BUILDER, 'command', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b'["127.0.0.1"]', b''))
    monkeypatch.setattr(BUILDER.subprocess, 'Popen', owner_process)
    monkeypatch.setattr(BUILDER.subprocess, 'run', exited_dns)
    assert owners == [], 'no owner process exists before the gate executes'
    # WHEN the Windows gate receives an inexact refusal from the frozen child
    with pytest.raises(ValueError, match='Frozen DNS child admitted an exited owner'):
        BUILDER.child_checks(binary, tmp_path, environment, javascript=False)
    # THEN the actual owner was reaped and retained throughout child admission
    assert [owner.returncode for owner in owners] == [0], 'the isolated owner exited successfully and was waited'


def test_windows_frozen_dns_gate_records_exact_exited_owner_refusal(tmp_path, monkeypatch):
    # GIVEN successful loopback DNS and an exact exited-owner refusal
    binary = tmp_path / 'brewdoc.exe'
    environment = BUILDER.consumer_environment(tmp_path)
    monkeypatch.setattr(BUILDER, 'sys', SimpleNamespace(platform='win32', _base_executable=sys._base_executable))
    monkeypatch.setattr(BUILDER, 'command', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b'["127.0.0.1"]', b''))
    monkeypatch.setattr(BUILDER.subprocess, 'run', lambda arguments, **options: subprocess.CompletedProcess(arguments, 1, b'', b''))
    assert binary.exists() is False, 'the unit check substitutes only the frozen binary response'
    # WHEN the Windows gate waits for its actual isolated owner and admits the child response
    result = BUILDER.child_checks(binary, tmp_path, environment, javascript=False)
    # THEN the receipt retains live-owner DNS and records only the observed exited-owner result
    assert result == {'dns': ['127.0.0.1'], 'exited_owner': {'code': 1, 'stdout_bytes': 0, 'stderr_bytes': 0}}, \
        'the exact refusal is recorded without changing the existing DNS field'


def smoke_record(tmp_path, target='linux-x64'):
    """Seal synthetic source identities without any licensed document input."""
    directory, manifest = native_record(tmp_path, target)
    probes = []
    for name, path, route in [('epub2', 'sample.epub', 'epub'), ('formula', 'formulas.xlsx', 'sheet')]:
        payload = name.encode('ascii')
        probes.append({'probe': name, 'path': path, 'receipt': {'out': None, 'route': route}, 'code': 0,
                       'bytes_base64': base64.b64encode(payload).decode('ascii'),
                       'source_sha256': BUILDER.hashlib.sha256(payload).hexdigest(),
                       'markdown_bytes': 1, 'markdown_sha256': 'b' * 64})
    probes[1]['artifact'] = {'key': 'formula/sheet/000002', 'bytes': 1, 'sha256': 'c' * 64}
    manifest.update(verification_scope='smoke', oracle=[], probes=probes)
    BUILDER.write_json(directory / 'manifest.json', manifest)
    verification = json.loads((directory / 'verification.json').read_text())
    BUILDER.write_json(directory / 'verification.json', {**verification, 'verification_scope': 'smoke',
                       'schema': 1, 'self_check': 38, 'fixtures': [], 'probes': ['epub2', 'formula']})
    return directory, manifest


def inspection_native_record(tmp_path, target='linux-x64'):
    """Extend the real bounded archive fixture with one complete independently sealed packet."""
    from test_install_verification import complete_inspection_packet

    directory, manifest = smoke_record(tmp_path, target)
    packet = complete_inspection_packet()
    old = directory / manifest['archive']['filename']
    filename = 'brewdoc-' + packet['version'] + '-' + target + ('.zip' if target == 'windows-x64' else '.tar.gz')
    old.rename(directory / filename)
    manifest.update(version=packet['version'], source_sha=packet['source_sha'], inspection=packet)
    manifest['archive']['filename'] = filename
    report = json.loads((directory / 'verification.json').read_bytes())
    report.update(version=packet['version'], source_sha=packet['source_sha'],
                  inspection=BUILDER.verifier().expected_inspection_result(packet))
    BUILDER.write_json(directory / 'manifest.json', manifest)
    BUILDER.write_json(directory / 'verification.json', report)
    return directory, manifest


def test_actual_native_archive_admission_keeps_complete_inspection_beside_the_old_two_probes(tmp_path):
    # GIVEN a bounded real archive with complete modern packet and native observation
    directory, manifest = inspection_native_record(tmp_path)
    # WHEN actual archive admission binds the manifest, verification and bytes
    actual = BUILDER.admit_manifest(directory / 'manifest.json')
    # THEN the additive profile retains every exact old smoke rule and fifteen case identities
    assert (actual, len(actual['inspection']['cases']), [row['probe'] for row in actual['probes']], actual['oracle']) == (
        manifest, 15, ['epub2', 'formula'], [],
    ), 'modern native admission must preserve source/byte seals and exactly the existing two probes'


@pytest.mark.parametrize('damage', ['missing-packet', 'unknown-packet', 'missing-result', 'second-report', 'payload', 'order'])
def test_real_native_archive_admission_refuses_missing_or_corrupt_inspection_evidence(tmp_path, damage):
    # GIVEN an admitted complete packet plus exactly one damaged manifest or observed result
    directory, manifest = inspection_native_record(tmp_path)
    assert BUILDER.admit_manifest(directory / 'manifest.json') == manifest, 'the entire modern archive fixture must pass first'
    report = json.loads((directory / 'verification.json').read_bytes())
    changes = {'missing-packet': lambda: manifest.pop('inspection'),
               'unknown-packet': lambda: manifest['inspection'].update(schema='unknown/9'),
               'missing-result': lambda: report.pop('inspection'),
               'second-report': lambda: report['inspection']['cases'][0].update(repeat_report_sha256='0' * 64),
               'payload': lambda: report['inspection']['cases'][0].update(payload_size=0),
               'order': lambda: report['inspection']['cases'].reverse()}
    changes[damage]()
    BUILDER.write_json(directory / 'manifest.json', manifest)
    BUILDER.write_json(directory / 'verification.json', report)
    # WHEN the real archive admission rechecks its original producer/evidence objects
    # THEN whole packet/result identity cannot be replaced by a PASS string or matching old fields
    with pytest.raises(ValueError, match='[Ii]nspection'):
        BUILDER.admit_manifest(directory / 'manifest.json')


def test_native_aggregation_refuses_three_matching_old_identities_with_a_foreign_profile(tmp_path):
    # GIVEN all three real target archives with matching source/tool/old smoke fields
    source = tmp_path / 'targets'
    source.mkdir()
    records = [inspection_native_record(source, target) for target in ('linux-x64', 'windows-x64', 'macos-arm64')]
    directory, foreign = records[-1]
    foreign['inspection']['module_sources_sha256'] = '0' * 64
    report = json.loads((directory / 'verification.json').read_bytes())
    report['inspection'] = BUILDER.verifier().expected_inspection_result(foreign['inspection'])
    BUILDER.write_json(directory / 'manifest.json', foreign)
    BUILDER.write_json(directory / 'verification.json', report)
    assert BUILDER.admit_manifest(directory / 'manifest.json') == foreign, 'self-consistent foreign evidence must reach cross-target admission'
    # WHEN actual aggregation compares the three admitted target identities
    # THEN a foreign but mutually agreeing target packet/result cannot claim the same producer packet
    with pytest.raises(ValueError, match='Standalone release requires three native targets with one source identity'):
        BUILDER.aggregate(source, tmp_path / 'aggregate')


def test_explicit_native_smoke_admits_only_two_sealed_synthetic_probes(tmp_path):
    # GIVEN two generated source payloads and a complete archive identity
    directory, manifest = smoke_record(tmp_path)
    assert manifest['oracle'] == [], 'native smoke contains no licensed oracle rows'
    # WHEN native admission verifies bytes and explicit smoke scope
    actual = BUILDER.admit_manifest(directory / 'manifest.json')
    # THEN synthetic-only evidence is admitted without being labeled full
    assert (actual['verification_scope'], [row['probe'] for row in actual['probes']]) == (
        'smoke', ['epub2', 'formula']), 'the exact two-probe scope remains explicit'


@pytest.mark.parametrize('delta', [{'verification_scope': 'unknown'}, {'verification_scope': 'full'},
                                  {'probes': []}, {'oracle': [{'path': 'tests/fixtures/corpus.pdf'}]}])
def test_native_smoke_rejects_wrong_scope_or_missing_synthetic_coverage(tmp_path, delta):
    # GIVEN an admissible synthetic-only native seal
    directory, manifest = smoke_record(tmp_path)
    assert BUILDER.admit_manifest(directory / 'manifest.json') == manifest, 'the complete smoke seal passes first'
    # WHEN required scope or probe coverage is altered
    manifest.update(delta)
    # THEN admission fails before any replay or archive access
    with pytest.raises(ValueError, match='scope|oracle|probes'):
        BUILDER.validate_manifest(manifest)


@pytest.mark.parametrize('version', ['0.8.0', '0.10.0', '1.0.0'])
def test_current_native_manifest_requires_the_inspection_profile(tmp_path, version):
    # GIVEN otherwise complete historical smoke fields rebound to a current release
    _directory, manifest = smoke_record(tmp_path)
    manifest['version'] = version
    manifest['archive']['filename'] = 'brewdoc-' + version + '-linux-x64.tar.gz'
    assert manifest['probes'][0]['probe'] in {'epub2', 'formula'}, 'existing smoke probes must remain present'
    # WHEN real native manifest admission sees no inspection profile
    # THEN numeric current versions cannot use historical omission rules
    with pytest.raises(ValueError, match='inspection'):
        BUILDER.validate_manifest(manifest)


@pytest.mark.parametrize('version', ['0.7.1', '0.8.0'])
def test_native_manifest_never_accepts_an_unknown_present_inspection_profile(tmp_path, version):
    # GIVEN a complete smoke manifest with a present but unknown inspection schema
    _directory, manifest = smoke_record(tmp_path)
    manifest['version'] = version
    manifest['archive']['filename'] = 'brewdoc-' + version + '-linux-x64.tar.gz'
    manifest['inspection'] = {'schema': 'foreign.inspection/9'}
    # WHEN the real validator admits original manifest fields
    # THEN historical optionality cannot excuse a malformed present block
    with pytest.raises(ValueError, match='inspection'):
        BUILDER.validate_manifest(manifest)


def test_native_smoke_rejects_legacy_full_verification_for_a_smoke_manifest(tmp_path):
    # GIVEN a smoke manifest paired with old full evidence lacking an explicit scope
    directory, manifest = smoke_record(tmp_path)
    report = json.loads((directory / 'verification.json').read_text())
    report.pop('verification_scope')
    BUILDER.write_json(directory / 'verification.json', report)
    assert BUILDER.verification_scope(report) == 'full', 'missing scope deliberately means legacy full coverage'
    # WHEN smoke archive admission compares the report and manifest
    with pytest.raises(ValueError, match='scope differs'):
        BUILDER.admit_manifest(directory / 'manifest.json')
    # THEN the explicit smoke manifest retains its original scope
    assert manifest['verification_scope'] == 'smoke', 'legacy evidence is never silently relabeled smoke'


def test_native_aggregation_exports_uniform_smoke_reports_without_duplicate_archives(tmp_path):
    # GIVEN one admitted smoke bundle for each target
    for target in ('linux-x64', 'windows-x64', 'macos-arm64'):
        smoke_record(tmp_path, target)
    assert len(list(tmp_path.glob('*/manifest.json'))) == 3, 'all three target seals exist'
    # WHEN aggregation stages the single complete archive set and compact reports
    output = tmp_path / 'release'
    BUILDER.aggregate(tmp_path, output)
    # THEN downstream release admission receives scope-bound evidence beside unchanged archives
    aggregate = json.loads((output / 'binaries.json').read_text())
    assert aggregate['verification_scope'] == 'smoke', 'aggregation binds one verification scope'
    assert sorted(path.name for path in (output / 'verification').iterdir()) == [
        'linux-x64.json', 'macos-arm64.json', 'windows-x64.json'], 'compact reports replace duplicate archive downloads'


def test_native_aggregation_rejects_mixed_full_and_smoke_targets(tmp_path):
    # GIVEN matching source identities but different conversion coverage on one target
    smoke_record(tmp_path, 'linux-x64')
    smoke_record(tmp_path, 'windows-x64')
    native_record(tmp_path, 'macos-arm64')
    assert len(list(tmp_path.glob('*/manifest.json'))) == 3, 'the complete matrix differs only in verification scope'
    # WHEN release aggregation compares the target seals
    with pytest.raises(ValueError, match='one source identity'):
        BUILDER.aggregate(tmp_path, tmp_path / 'release')
    # THEN mixed coverage cannot be published as one accepted smoke set
    assert (tmp_path / 'release').exists() is False, 'scope disagreement fails before staging any archive'


@pytest.mark.parametrize('delta', [{'self_check': 37}, {'fixtures': [{'path': 'corpus.pdf'}]}, {'probes': ['epub2']}])
def test_native_smoke_refuses_incomplete_consumer_coverage(tmp_path, delta):
    # GIVEN an admitted complete synthetic consumer report
    directory, manifest = smoke_record(tmp_path)
    report = json.loads((directory / 'verification.json').read_text())
    assert BUILDER.validate_verification(manifest, report) is None, 'the full synthetic consumer report is accepted'
    # WHEN an essential recorded result is missing or claims corpus replay
    report.update(delta)
    # THEN native and packed npm admission reject incomplete smoke evidence
    with pytest.raises(ValueError, match='verification is incomplete'):
        BUILDER.validate_verification(manifest, report)
