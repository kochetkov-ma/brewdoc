"""Artifact admission rejects unsafe paths and stale native acceptance."""

import importlib.util
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

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
    ({'oracle': []}, 'eleven selected'),
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
