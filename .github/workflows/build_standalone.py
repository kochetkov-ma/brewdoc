"""Build and admit native one-folder bundles from the accepted Python wheel."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.util
import importlib.metadata
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import unicodedata
import zipfile
from multiprocessing.connection import Connection
from pathlib import Path, PurePosixPath


def digest(path: Path) -> str:
    """Hash actual artifact bytes without assuming their filenames establish identity."""
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path: Path, value: object) -> None:
    """Write portable JSON metadata without platform newline translation."""
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8', newline='\n')


def command(arguments: list[str], *, cwd: Path | None = None, env: dict | None = None,
            timeout: float = 300) -> subprocess.CompletedProcess:
    """Stop at failed preparation commands and retain useful bounded diagnostics."""
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError(f'Command failed: {arguments[0]}\n{result.stderr.decode("utf-8", "replace")}')
    return result


def tls_receipt(result: subprocess.CompletedProcess) -> dict:
    """Require TLS refusal and report only the observed exit and a known stage."""
    receipt = json.loads(result.stdout.splitlines()[0])
    stage = receipt['acquisition']['refusal_stage']
    if (result.returncode, stage) != (1, 'tls'):
        stages = {'timeout', 'options', 'url', 'dns', 'connect', 'tls', 'status', 'redirect',
                  'media', 'bytes', 'encoding', 'resource', 'challenge', 'prerequisites',
                  'javascript', 'snapshot', 'conversion', 'output'}
        observed = stage if isinstance(stage, str) and stage in stages else 'invalid'
        logging.getLogger(__name__).warning('Frozen TLS probe failed: exit=%d refusal_stage=%s',
                                            result.returncode, observed)
        raise ValueError('Frozen invalid TLS certificate was not refused at TLS')
    return receipt


def zip_xml_refusal_case(work: Path) -> tuple[Path, Path, dict]:
    """Create a valid DOCX one member beyond the approved archive-entry bound."""
    source, output = work / 'zip-member-limit.docx', work / 'zip-member-output.md'
    members = (
        ('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
         'officeDocument" Target="word/document.xml"/></Relationships>'),
        ('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
         '<w:body><w:p><w:r><w:t>Member bound probe.</w:t></w:r></w:p></w:body></w:document>'),
    )
    with zipfile.ZipFile(source, 'w') as package:
        for name, payload in members:
            package.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
        for ordinal in range(4095):
            package.writestr(zipfile.ZipInfo(f'ignored/{ordinal:04d}', date_time=(1980, 1, 1, 0, 0, 0)), b'')
    output.write_bytes(b'preserve existing output\n')
    receipt = {
        'file_ok': False, 'route': 'doc', 'reason': 'OOXML members limit exceeded: 4096',
        'source': source.name, 'out': None, 'receipt_schema': 'brewdoc.receipt/1',
        'unit_kind': 'chapter', 'units': 0, 'tables': 0, 'text_regions': 0,
        'columns_split': 0, 'broken_ligature_words': 0,
        'dropped': {'control_chars': 0, 'soft_hyphens': 0, 'nbsp': 0, 'pua_glyphs': 0,
                    'cid_survivors': 0, 'ligatures': 0, 'running_heads': 0, 'page_numbers': 0,
                    'non_ascii_replaced': 0},
        'not_carried': ['images, charts and the text drawn inside them',
                        'tracked changes, comments, footnotes, headers and footers',
                        "a table's own formatting - only its cells, row by row"],
    }
    return source, output, receipt


def zip_xml_refusal_probe(arguments: list[str], work: Path, environment: dict) -> dict:
    """Require the real CLI to refuse excess ZIP members without changing output."""
    source, output, expected = zip_xml_refusal_case(work)
    result = subprocess.run([*arguments, str(source), '--out', str(output)], cwd=work,
                            env=environment, capture_output=True, timeout=10)
    stdout = (json.dumps(expected, ensure_ascii=True, sort_keys=True) + os.linesep).encode('ascii')
    if (result.returncode, result.stdout, result.stderr, output.read_bytes()) != (
            1, stdout, b'', b'preserve existing output\n'):
        raise ValueError('ZIP/XML member refusal changed its receipt or existing output')
    return expected


def verifier():
    """Reuse the existing pure package checks without importing the conversion engine."""
    spec = importlib.util.spec_from_file_location('install_verifier', Path(__file__).with_name('verify_install.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def facts(checkout: Path, target: str) -> dict:
    """Admit only the reviewed native target table and exact embedded interpreter."""
    data = json.loads((checkout / '.github/distribution.json').read_text(encoding='utf-8'))
    if data['schema'] != 1 or data['python'] != '3.12.14' or target not in data['targets']:
        raise ValueError('Unknown standalone target or interpreter policy')
    selected = data['targets'][target]
    if (platform.system(), platform.machine().lower()) != (selected['system'], selected['machine'].lower()):
        raise ValueError('Standalone builds require the matching native operating system and architecture')
    if target == 'linux-x64':
        library, version = platform.libc_ver()
        if library != 'glibc' or tuple(map(int, version.split('.'))) < (2, 35):
            raise ValueError('Linux standalone requires the tested glibc 2.35 floor')
    if target == 'macos-arm64' and int(platform.mac_ver()[0].split('.')[0]) < 15:
        raise ValueError('macOS standalone requires the tested macOS 15 floor')
    return data


def relative_name(value: str) -> str:
    """Reject archive aliases, controls and paths outside the single bundle directory."""
    path = PurePosixPath(value)
    if (not value or '\\' in value or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or path.is_absolute() or '..' in path.parts or '.' in path.parts
            or ':' in value or path.as_posix() != value or path.parts[0] != 'brewdoc'):
        raise ValueError(f'Unsafe standalone path: {value}')
    return value


def verification_scope(record: dict) -> str:
    """Treat legacy records as full coverage and reject unknown verification scopes."""
    scope = record.get('verification_scope', 'full')
    if scope not in ('full', 'smoke'):
        raise ValueError('Unknown standalone verification scope')
    return scope


def validate_manifest(manifest: dict) -> None:
    """Reject mutable identities and untrusted filenames before any artifact access."""
    formats = {'linux-x64': 'tar.gz', 'windows-x64': 'zip', 'macos-arm64': 'tar.gz'}
    target, version = manifest.get('target'), manifest.get('version', '')
    if (manifest.get('schema') != 1 or target not in formats or manifest.get('python') != '3.12.14'
            or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version)):
        raise ValueError('Invalid standalone manifest identity')
    for key in ('source_sha', 'wheel_sha256', 'lock_sha256'):
        length = 40 if key == 'source_sha' else 64
        if not re.fullmatch('[0-9a-f]{%d}' % length, manifest.get(key, '')):
            raise ValueError('Invalid standalone source or artifact hash')
    asset = manifest['archive']
    if (asset['filename'] != f'brewdoc-{version}-{target}.{formats[target]}'
            or not isinstance(asset['bytes'], int) or asset['bytes'] <= 0
            or not re.fullmatch('[0-9a-f]{64}', asset['sha256'])):
        raise ValueError('Invalid standalone archive identity')
    if manifest['executable'] != 'brewdoc/brewdoc' + ('.exe' if target == 'windows-x64' else ''):
        raise ValueError('Invalid standalone executable path')
    records, aliases = manifest['inventory'], set()
    if records.get('brewdoc', {}).get('kind') != 'directory':
        raise ValueError('Standalone inventory has no root directory')
    for name, record in records.items():
        relative_name(name)
        alias = unicodedata.normalize('NFC', name).casefold()
        if alias in aliases or not isinstance(record['mode'], int) or not 0 <= record['mode'] <= 0o777:
            raise ValueError('Ambiguous standalone inventory or invalid mode')
        aliases.add(alias)
        if record['kind'] == 'file':
            if record['bytes'] < 0 or not re.fullmatch('[0-9a-f]{64}', record['sha256']):
                raise ValueError('Invalid standalone file identity')
        elif record['kind'] == 'symlink':
            if PurePosixPath(record['target']).is_absolute() or '\\' in record['target']:
                raise ValueError('Invalid standalone link target')
        elif record['kind'] != 'directory':
            raise ValueError('Unsupported standalone inventory kind')
    if records.get(manifest['executable'], {}).get('kind') != 'file':
        raise ValueError('Standalone inventory has no executable')
    scope = verification_scope(manifest)
    rows = manifest['oracle']
    current_formats = tuple(map(int, version.split('.'))) >= (0, 5, 0)
    count = 0 if scope == 'smoke' else (16 if current_formats else 11)
    if len(rows) != count or len({row['path'] for row in rows}) != count:
        raise ValueError('Standalone oracle differs from its verification scope')
    if current_formats and scope == 'full':
        suffixes = [PurePosixPath(row['path']).suffix.lower() for row in rows]
        if set(suffixes) != verifier().SUFFIXES or suffixes.count('.epub') != 2:
            raise ValueError('Standalone oracle differs from its format coverage')
        if any('markdown_encoding' not in row for row in rows):
            raise ValueError('Standalone Markdown encoding is missing')
    for row in rows:
        path = PurePosixPath(row['path'])
        if (not path.is_relative_to('tests/fixtures') or '..' in path.parts or '\\' in row['path']
                or row['code'] != 0 or row['receipt']['out'] is not None
                or row['markdown_bytes'] <= 0 or not re.fullmatch('[0-9a-f]{64}', row['markdown_sha256'])
                or not re.fullmatch('[0-9a-f]{64}', row['source_sha256'])):
            raise ValueError('Invalid standalone sealed oracle')
    probes = manifest.get('probes', [])
    required = {'epub2', 'formula'} if scope == 'smoke' else {'sheet-order', 'vba', 'epub2', 'formula'}
    if len(probes) != len(required) or {row['probe'] for row in probes} != required:
        raise ValueError('Standalone supplementary probes are incomplete')
    for row in probes:
        path = PurePosixPath(row['path'])
        if scope == 'smoke':
            expected_path = {'epub2': 'sample.epub', 'formula': 'formulas.xlsx'}[row['probe']]
            payload = base64.b64decode(row.get('bytes_base64', ''), validate=True)
            if (row['path'] != expected_path or not payload
                    or hashlib.sha256(payload).hexdigest() != row.get('source_sha256')
                    or row.get('code') != 0 or row.get('receipt', {}).get('out') is not None
                    or row.get('receipt', {}).get('route') != {'epub2': 'epub', 'formula': 'sheet'}[row['probe']]
                    or row.get('markdown_bytes', 0) <= 0
                    or not re.fullmatch('[0-9a-f]{64}', row.get('markdown_sha256', ''))):
                raise ValueError('Invalid standalone synthetic smoke probe')
            if row['probe'] == 'formula':
                artifact = row.get('artifact', {})
                if (artifact.get('key') != 'formula/sheet/000002' or artifact.get('bytes', 0) <= 0
                        or not re.fullmatch('[0-9a-f]{64}', artifact.get('sha256', ''))):
                    raise ValueError('Invalid standalone synthetic formula artifact')
        if 'bytes_base64' in row:
            if row['path'] not in ('sample.epub', 'formulas.xlsx'):
                raise ValueError('Invalid generated standalone probe path')
        elif not path.is_relative_to('tests/fixtures') or '..' in path.parts or '\\' in row['path']:
            raise ValueError('Invalid standalone supplementary source path')
    unicode_routes = verifier().UNICODE_ROUTES if current_formats else {'html', 'epub'}
    for row in rows + probes:
        if 'markdown_encoding' in row:
            expected = 'utf-8' if row['receipt']['route'] in unicode_routes else 'ascii'
            if row['markdown_encoding'] != expected:
                raise ValueError('Standalone Markdown encoding differs from its route')


def validate_verification(manifest: dict, verification: dict) -> None:
    """Bind native consumer identities and explicit synthetic coverage to their seal."""
    if verification_scope(verification) != verification_scope(manifest):
        raise ValueError('Standalone verification scope differs from its manifest')
    if (verification['verdict'], verification['source_sha'], verification['version'],
        verification['target'], verification['archive_sha256']) != (
            'PASS', manifest['source_sha'], manifest['version'], manifest['target'], manifest['archive']['sha256']):
        raise ValueError('Standalone artifact lacks matching native acceptance')
    if verification_scope(manifest) == 'smoke':
        expected = {'schema': 1, 'self_check': 38, 'fixtures': [],
                    'probes': [row['probe'] for row in manifest['probes']]}
        if {key: verification.get(key) for key in expected} != expected:
            raise ValueError('Standalone synthetic smoke verification is incomplete')


def admit_manifest(path: Path) -> dict:
    """Bind a sealed archive manifest to its actual native acceptance and bytes."""
    manifest = json.loads(path.read_text(encoding='utf-8'))
    validate_manifest(manifest)
    verification = json.loads(path.with_name('verification.json').read_text(encoding='utf-8'))
    validate_verification(manifest, verification)
    asset = path.parent / manifest['archive']['filename']
    if asset.is_symlink() or (asset.stat().st_size, digest(asset)) != (manifest['archive']['bytes'], manifest['archive']['sha256']):
        raise ValueError('Standalone accepted archive changed')
    return manifest


def inventory(root: Path, *, logical_root: str | None = None) -> dict:
    """Record actual files and safe links under the admitted logical bundle root."""
    if logical_root not in (None, 'brewdoc'):
        raise ValueError('Invalid standalone logical root')
    logical_name = root.name if logical_root is None else logical_root
    records, aliases = {}, set()
    paths = [root, *sorted(root.rglob('*'))]
    for path in paths:
        name = relative_name((PurePosixPath(logical_name) / path.relative_to(root)).as_posix())
        alias = unicodedata.normalize('NFC', name).casefold()
        if alias in aliases:
            raise ValueError(f'Ambiguous standalone path: {name}')
        aliases.add(alias)
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            resolved = path.resolve(strict=True)
            if os.path.isabs(target) or '\\' in target or not resolved.is_relative_to(root.resolve()):
                raise ValueError(f'Standalone link escapes its bundle: {name}')
            records[name] = {'kind': 'symlink', 'target': target, 'mode': mode}
        elif path.is_dir():
            records[name] = {'kind': 'directory', 'mode': mode}
        elif path.is_file():
            records[name] = {'kind': 'file', 'bytes': path.stat().st_size, 'sha256': digest(path), 'mode': mode}
        else:
            raise ValueError(f'Unsupported standalone member: {name}')
    return records


def archive_bundle(root: Path, destination: Path, archive_format: str) -> dict:
    """Write one complete directory while preserving its admitted relative links."""
    records = inventory(root)
    if archive_format == 'zip':
        if any(value['kind'] == 'symlink' for value in records.values()):
            raise ValueError('Windows standalone ZIP cannot contain symbolic links')
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, record in records.items():
                archive.write(root.parent / name, name + ('/' if record['kind'] == 'directory' else ''))
    elif archive_format == 'tar.gz':
        with tarfile.open(destination, 'w:gz', dereference=False) as archive:
            for name in records:
                archive.add(root.parent / name, arcname=name, recursive=False)
    else:
        raise ValueError('Unsupported standalone archive format')
    return records


def extract_archive(archive_path: Path, destination: Path, expected: dict) -> Path:
    """Validate headers and bytes against accepted build evidence before extraction."""
    destination.mkdir(parents=True, exist_ok=False)
    seen, links = set(), []
    if archive_path.name.endswith('.zip'):
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            for member in members:
                name = relative_name(member.filename.rstrip('/'))
                mode = member.external_attr >> 16
                if name in seen or stat.S_ISLNK(mode) or member.flag_bits & 1:
                    raise ValueError('Duplicate, encrypted or linked standalone ZIP member')
                if stat.S_IFMT(mode) not in (stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('Unsupported special standalone ZIP member')
                seen.add(name)
                if name not in expected or member.is_dir() != (expected[name]['kind'] == 'directory'):
                    raise ValueError('Standalone ZIP inventory changed')
                record = expected[name]
                if ((mode & 0o777) != record['mode']
                        or member.file_size != (record['bytes'] if record['kind'] == 'file' else 0)):
                    raise ValueError('Standalone ZIP declared size or mode changed')
                if record['kind'] == 'file':
                    with archive.open(member) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                            raise ValueError('Standalone ZIP file bytes changed')
            if seen != set(expected):
                raise ValueError('Standalone ZIP inventory is incomplete')
            archive.extractall(destination)
            for name, record in expected.items():
                os.chmod(destination / name, record['mode'])
    else:
        with tarfile.open(archive_path) as archive:
            members = archive.getmembers()
            for member in members:
                name = relative_name(member.name)
                if name in seen or not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError('Duplicate or unsupported standalone tar member')
                seen.add(name)
                record = expected.get(name)
                kind = 'symlink' if member.issym() else 'directory' if member.isdir() else 'file'
                if record is None or record['kind'] != kind:
                    raise ValueError('Standalone tar inventory changed')
                if member.mode != record['mode'] or member.size != (record['bytes'] if kind == 'file' else 0):
                    raise ValueError('Standalone tar declared size or mode changed')
                if kind == 'file':
                    with archive.extractfile(member) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                            raise ValueError('Standalone tar file bytes changed')
                if member.issym():
                    if member.linkname != record['target'] or PurePosixPath(member.linkname).is_absolute():
                        raise ValueError('Standalone link target changed')
                    terminal = (destination / name).parent.joinpath(member.linkname).resolve()
                    if not terminal.is_relative_to((destination / 'brewdoc').resolve()):
                        raise ValueError('Standalone archive link escapes its bundle')
                    links.append(member)
            if seen != set(expected):
                raise ValueError('Standalone tar inventory is incomplete')
            for member in members:
                if not member.issym():
                    archive.extract(member, destination, filter='data')
            for member in links:
                archive.extract(member, destination, filter='data')
    root = destination / 'brewdoc'
    observed = inventory(root)
    # Symlink permissions depend on the host; their archive modes were checked above.
    for name, record in observed.items():
        if record['kind'] == 'symlink' and expected.get(name, {}).get('kind') == 'symlink':
            record['mode'] = expected[name]['mode']
    if observed != expected:
        raise ValueError('Extracted standalone bytes, modes or link targets changed')
    return root


def copy_notices(checkout: Path, destination: Path) -> None:
    """Retain the reviewed native notices only after checking their source manifest."""
    source = checkout / '.github/standalone-licenses'
    manifest = json.loads((source / 'provenance.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 1:
        raise ValueError('Unknown standalone notice schema')
    declared = set()
    for component in manifest['sources']:
        for record in component['files']:
            name = record['path']
            path = source / name
            if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name
                    or name in declared or path.is_symlink() or not path.resolve().is_relative_to(source.resolve())
                    or (path.stat().st_size, digest(path)) != (record['bytes'], record['sha256'])):
                raise ValueError(f'Standalone notice identity changed: {name}')
            declared.add(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    shutil.copyfile(source / 'provenance.json', destination / 'provenance.json')


def linux_package(package: str) -> dict:
    """Require one installed Debian package with complete binary and source identities."""
    fields = command(['dpkg-query', '-W', '--showformat=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\t${db:Status-Status}\n',
                      package]).stdout.decode().strip().split('\t')
    if len(fields) != 5 or any(not field or '\n' in field for field in fields) or fields[0] != package or fields[4] != 'installed':
        raise ValueError('Linux runtime package identity is incomplete or not installed')
    return dict(zip(('package', 'version', 'source_package', 'source_version'), fields[:4], strict=True))


def capture_native_sources(binaries: list, scripts: list, environment: Path, tool_versions: dict) -> dict:
    """Capture selected Analysis payload identities before EXE with installed hook ownership."""
    libraries = [{'name': Path(name).name, 'source': source, 'bytes': Path(source).stat().st_size,
                  'sha256': digest(Path(source))} for name, source, _kind in binaries
                 if Path(name).name in ('libgcc_s.so.1', 'libstdc++.so.6')]
    packages = {name: importlib.metadata.distribution(name) for name in ('pyinstaller', 'pyinstaller-hooks-contrib')}
    if {name: package.version for name, package in packages.items()} != tool_versions:
        raise ValueError('Runtime hook build-tool versions differ from the pinned source')
    hooks, seen = [], set()
    for _name, source_name, _kind in scripts:
        source = Path(source_name)
        if not source.name.startswith('pyi_rth_'):
            continue
        if (source.name in seen or not re.fullmatch(r'pyi_rth_[a-zA-Z0-9_]+\.py', source.name)
                or source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(environment.resolve())
                or not 0 < source.stat().st_size <= 256 * 1024):
            raise ValueError('Runtime hook Analysis source is missing, duplicated or outside its environment')
        owners = [name for name, package in packages.items() if any(
            Path(package.locate_file(item)).resolve() == source.resolve() for item in package.files or [])]
        if len(owners) != 1:
            raise ValueError('Runtime hook has no unique installed build-tool owner')
        prefix = {'pyinstaller': ('PyInstaller', 'hooks', 'rthooks'),
                  'pyinstaller-hooks-contrib': ('_pyinstaller_hooks_contrib', 'rthooks')}[owners[0]]
        parts = source.resolve().relative_to(environment.resolve()).parts
        if not any(parts[index:index + len(prefix)] == prefix for index in range(len(parts))):
            raise ValueError('Runtime hook source is outside the installed runtime-hook directory')
        seen.add(source.name)
        hooks.append({'name': source.name, 'source': str(source), 'bytes': source.stat().st_size,
                      'sha256': digest(source), 'component': owners[0], 'version': packages[owners[0]].version})
    if not hooks:
        raise ValueError('Runtime hook Analysis evidence is empty')
    return {'libraries': sorted(libraries, key=lambda row: row['name']),
            'hooks': sorted(hooks, key=lambda row: row['name'])}


def collect_runtime_hook_notices(root: Path, records: list) -> tuple[list, list]:
    """Retain complete original included hook sources only after their captured bytes agree."""
    records = sorted(records, key=lambda row: row['name'])
    seen = set()
    for record in records:
        source = Path(record['source'])
        name = record['name']
        if (name in seen or not re.fullmatch(r'pyi_rth_[a-zA-Z0-9_]+\.py', name) or source.name != name
                or source.is_symlink() or not source.is_file()
                or (source.stat().st_size, digest(source)) != (record['bytes'], record['sha256'])):
            raise ValueError('Runtime hook source changed after Analysis or has an unsafe name')
        seen.add(name)
    if not seen:
        raise ValueError('Runtime hook source evidence is empty')
    retained, hooks = [], []
    for record in records:
        source = Path(record['source'])
        destination = root / 'licenses/installed/runtime-hooks' / record['name']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        notice = {'path': destination.relative_to(root).as_posix(), 'bytes': destination.stat().st_size,
                  'sha256': digest(destination)}
        if (notice['bytes'], notice['sha256']) != (record['bytes'], record['sha256']):
            raise ValueError('Runtime hook retained notice bytes changed')
        retained.append(notice)
        hooks.append({**{key: record[key] for key in ('name', 'component', 'version', 'bytes', 'sha256')},
                      'notice': notice})
    return retained, hooks


def collect_linux_runtime_notices(root: Path, sources: list, *, host: Path = Path('/')) -> tuple[list, list]:
    """Bind the copied GCC runtimes to installed Ubuntu owners and complete original notices."""
    owners = {'libgcc_s.so.1': 'libgcc-s1:amd64', 'libstdc++.so.6': 'libstdc++6:amd64'}
    records, notices, seen = [], {}, set()
    for captured in sources:
        name = captured['name']
        if name not in owners or name in seen:
            raise ValueError('Linux runtime source mapping is unknown or duplicated')
        seen.add(name)
        source, copied = Path(captured['source']).resolve(), root / '_internal' / name
        if (not source.is_file() or copied.is_symlink() or not copied.is_file()
                or (source.stat().st_size, digest(source)) != (captured['bytes'], captured['sha256'])
                or (captured['bytes'], captured['sha256']) != (copied.stat().st_size, digest(copied))):
            raise ValueError('Linux runtime source and copied library bytes differ')
        package = linux_package(owners[name])
        paths = command(['dpkg-query', '-L', package['package']]).stdout.decode().splitlines()
        if not any((host / path.lstrip('/')).resolve() == source for path in paths if path.startswith('/')):
            raise ValueError('Linux runtime library is not owned by its installed package')
        copyright = host / 'usr/share/doc' / package['package'].split(':')[0] / 'copyright'
        copyright = copyright.resolve()
        if not copyright.is_file() or not copyright.is_relative_to((host / 'usr/share/doc').resolve()):
            raise ValueError('Linux runtime package copyright is missing or escapes documentation')
        logical = '/' + copyright.relative_to(host.resolve()).as_posix()
        ownership = command(['dpkg-query', '-S', logical]).stdout.decode().strip().split(': ', 1)
        if len(ownership) != 2 or ownership[1] != logical or '\n' in ownership[0]:
            raise ValueError('Linux runtime copyright owner is ambiguous')
        document_package = linux_package(ownership[0])
        if (document_package['source_package'], document_package['source_version']) != (
                package['source_package'], package['source_version']):
            raise ValueError('Linux runtime copyright belongs to a different source version')
        notice = ' '.join(copyright.read_text(encoding='utf-8').split())
        markers = ('GCC RUNTIME LIBRARY EXCEPTION', 'Version 3.1, 31 March 2009', '0. Definitions.',
                   '1. Grant of Additional Permission.', '2. No Weakening of GCC Copyleft.',
                   'The availability of this Exception does not imply any general presumption that third-party '
                   'software is unaffected by the copyleft requirements of the license of GCC.')
        if any(marker not in notice for marker in markers) or '/usr/share/common-licenses/GPL-3' not in notice:
            raise ValueError('Linux runtime copyright lacks the complete GCC exception')
        notice_name = document_package['package'].replace(':', '-') + '-copyright'
        notices[notice_name] = copyright
        records.append({'name': name, **package, 'path': copied.relative_to(root).as_posix(),
                        'bytes': copied.stat().st_size, 'sha256': digest(copied),
                        'copyright_package': document_package,
                        'notices': ['licenses/installed/native-runtime/' + notice_name,
                                    'licenses/installed/native-runtime/GPL-3']})
    if seen != set(owners):
        raise ValueError('Linux runtime source mapping is incomplete')
    gpl = host / 'usr/share/common-licenses/GPL-3'
    if gpl.is_symlink() or not gpl.is_file():
        raise ValueError('Linux runtime GPL-3 notice is missing or linked')
    text = ' '.join(gpl.read_text(encoding='utf-8').split())
    markers = ('GNU GENERAL PUBLIC LICENSE', 'Version 3, 29 June 2007', '0. Definitions.',
               '17. Interpretation of Sections 15 and 16.', 'END OF TERMS AND CONDITIONS',
               'How to Apply These Terms to Your New Programs', 'why-not-lgpl.html>.')
    if any(marker not in text for marker in markers):
        raise ValueError('Linux runtime GPL-3 notice is incomplete')
    ownership = command(['dpkg-query', '-S', '/usr/share/common-licenses/GPL-3']).stdout.decode().strip().split(': ', 1)
    if len(ownership) != 2 or ownership[1] != '/usr/share/common-licenses/GPL-3' or '\n' in ownership[0]:
        raise ValueError('Linux runtime GPL-3 package owner is ambiguous')
    gpl_package = linux_package(ownership[0])
    notices['GPL-3'] = gpl
    retained = []
    for name, source in sorted(notices.items()):
        destination = root / 'licenses/installed/native-runtime' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if digest(destination) != digest(source):
            raise ValueError('Linux runtime retained notice bytes changed')
        retained.append({'path': destination.relative_to(root).as_posix(), 'bytes': destination.stat().st_size,
                         'sha256': digest(destination)})
    by_path = {row['path']: row for row in retained}
    for record in records:
        record['gpl_package'] = gpl_package
        record['notices'] = [by_path[name] for name in record['notices']]
    return retained, sorted(records, key=lambda row: row['path'])


ORACLE_CODE = '''import hashlib, importlib.util, json, sys
import tempfile,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
import brewdoc
spec=importlib.util.spec_from_file_location('verifier',sys.argv[1])
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
checkout=Path(sys.argv[2]);rows=[];extra=[]
def observe(source,options=None):
 code,receipt,markdown=brewdoc.run(source,**(options or {}))
 if code!=0: raise ValueError('Installed probe refused '+str(source))
 encoding='utf-8' if receipt['route'] in helper.UNICODE_ROUTES else 'ascii'
 body=markdown.encode(encoding)
 return {'path':source.name,'source_sha256':helper.digest(source),'receipt':receipt,'code':code,
         'markdown_encoding':encoding,
         'markdown_sha256':hashlib.sha256(body).hexdigest(),'markdown_bytes':len(body)}
for document,expected in ([] if sys.argv[4]=='smoke' else helper.select_inputs(checkout)):
 source=checkout/document['path'];actual=brewdoc.run(source)
 code,receipt,markdown=actual
 if code!=0: raise ValueError('Accepted installed engine refused '+str(source))
 if 'accepted_output' in expected: helper.check_accepted_output(source,actual,expected['accepted_output'])
 encoding='utf-8' if receipt['route'] in helper.UNICODE_ROUTES else 'ascii'
 body=markdown.encode(encoding)
 rows.append({'path':document['path'],'source_sha256':helper.digest(source),'receipt':receipt,
              'markdown_encoding':encoding,
              'code':code,'markdown_sha256':hashlib.sha256(body).hexdigest(),'markdown_bytes':len(body)})
 if source.suffix=='.xlsx':
  with zipfile.ZipFile(source) as package: tree=ET.fromstring(package.read('xl/workbook.xml'))
  names=[node.attrib['name'] for node in tree.iter() if node.tag=='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet']
  selection=list(reversed(names[:2]))
  if len(selection)!=2: raise ValueError('Selection probe requires two worksheets')
  row=observe(source,{'sheets':selection});row.update(path=document['path'],sheets=selection,probe='sheet-order');extra.append(row)
 if source.suffix=='.xlsm':
  reference=next(item for item in expected['artifacts'] if item['kind']=='vba-project')
  with tempfile.TemporaryDirectory() as temporary:
   payload=Path(temporary)/'vba.bin';row=observe(source,{'artifact_outputs':{reference['key']:payload}})
   if (payload.stat().st_size,helper.digest(payload))!=(reference['byte_size'],reference['sha256']): raise ValueError('Source VBA evidence changed')
  for artifact in row['receipt']['artifacts']: artifact['out']=None
  row.update(path=document['path'],artifact={'key':reference['key'],'bytes':reference['byte_size'],'sha256':reference['sha256']},probe='vba');extra.append(row)
for case in json.loads(Path(sys.argv[3]).read_text()):
 source=Path(case['source']);row=observe(source)
 if 'expected' in case:
  if row['receipt']!=case['expected']['receipt'] or row['markdown_sha256']!=case['expected']['markdown_sha256']: raise ValueError('Synthetic EPUB2 differs from literal expectations')
 else:
  key='formula/sheet/000002'
  with tempfile.TemporaryDirectory() as temporary:
   payload=Path(temporary)/'formula.json';row=observe(source,{'artifact_outputs':{key:payload}})
   row['artifact']={'key':key,'bytes':payload.stat().st_size,'sha256':helper.digest(payload)}
  for artifact in row['receipt']['artifacts']: artifact['out']=None
 row.update(path=source.name,bytes_base64=case['bytes_base64'],probe=case['probe']);extra.append(row)
print(json.dumps({'version':brewdoc.__version__,'origin':brewdoc.__file__,'oracle':rows,'probes':extra}))
'''


def synthetic_cases(checkout: Path, output: Path) -> list[dict]:
    """Reuse fixed trusted test builders and EPUB literals without importing pytest."""
    namespace = {'zipfile': zipfile, 'hashlib': hashlib, 'Path': Path}
    selectors = {
        'test_contract.py': {'NOT_APPLICABLE', 'SCHEMA_TWO', 'zero_tally'},
        'test_epub.py': {'CONTAINER', 'METADATA', 'ITEMS', 'SPINE', 'NCX', 'EPUB_OMISSIONS',
                        'PUBLICATION_ROWS', 'PUBLICATION_NAV', 'TWO_CHAPTER_BODY', 'xhtml',
                        'epub2_members', 'write_epub', 'write_epub_bytes', 'expected_receipt', 'expected_document'},
        'test_spreadsheets.py': {'formula_book'},
    }
    for filename, names in selectors.items():
        tree = ast.parse((checkout / 'tests' / filename).read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if
                 isinstance(node, ast.FunctionDef) and node.name in names or
                 isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                 and target.id in names for target in node.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
    epub = namespace['write_epub'](output / 'sample.epub', namespace['epub2_members']())
    expected = {'receipt': namespace['expected_receipt'](epub),
                'markdown_sha256': hashlib.sha256(namespace['expected_document'](epub).encode('utf-8')).hexdigest()}
    formula = namespace['formula_book'](output / 'formulas.xlsx')
    return [{'source': str(epub), 'bytes_base64': base64.b64encode(epub.read_bytes()).decode('ascii'),
             'probe': 'epub2', 'expected': expected},
            {'source': str(formula), 'bytes_base64': base64.b64encode(formula.read_bytes()).decode('ascii'),
             'probe': 'formula'}]


RUNTIME_CODE = '''import importlib.metadata,json,sys
from pathlib import Path
names=json.loads(sys.argv[1]);licenses=[];packages=[]
for name in names:
 distribution=importlib.metadata.distribution(name)
 packages.append({'name':distribution.metadata['Name'],'version':distribution.version})
 for item in distribution.files or []:
  if any('license' in part.lower() or 'copying' in part.lower() or 'notice' in part.lower() for part in item.parts):
   path=distribution.locate_file(item)
   if path.is_file(): licenses.append({'component':name,'path':str(path),'name':str(item)})
base=Path(sys.base_prefix)
for folder in (base/'licenses',base.parent/'licenses',base/'share/doc/python3.12'):
 if folder.is_dir():
  for path in folder.rglob('*'):
   if path.is_file(): licenses.append({'component':'interpreter','path':str(path),'name':str(path.relative_to(folder))})
for path in (base/'LICENSE',base/'LICENSE.txt',base.parent/'LICENSE',
             base/'lib/python3.12/LICENSE.txt',base/'Lib/LICENSE.txt'):
 if path.is_file(): licenses.append({'component':'interpreter','path':str(path),'name':path.name})
print(json.dumps({'python':'.'.join(map(str,sys.version_info[:3])),'base':str(base),
                  'packages':packages,'licenses':licenses}))
'''


def build(checkout: Path, bundle: Path, output: Path, target: str, *, smoke: bool = False) -> None:
    """Freeze the accepted wheel in an isolated managed interpreter and seal its oracle."""
    data = facts(checkout, target)
    helper = verifier()
    producer = json.loads((bundle / 'ci-package/manifest.json').read_text(encoding='utf-8'))
    helper.verify_manifest(bundle, producer)
    if producer['uv'] != data['uv'] or command(['uv', '--version']).stdout.decode().split()[1] != data['uv']:
        raise ValueError('Standalone build tooling differs from the accepted producer')
    for name, expected in producer['source'].items():
        if digest(checkout / name) != expected:
            raise ValueError('Standalone checkout differs from producer source manifests')
    wheel, _sdist = helper.distributions(bundle / 'dist')
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith('brewdoc/') and name.endswith('.py'):
                if archive.read(name) != (checkout / 'src' / name).read_bytes():
                    raise ValueError('Standalone wheel source differs from the checkout')
    output.mkdir(parents=True, exist_ok=False)
    environment = output / 'build-env'
    command(['uv', 'venv', '--managed-python', '--python', data['python'], str(environment)])
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    runtime = bundle / 'ci-package/render-js-requirements.txt'
    command(['uv', 'pip', 'install', '--python', str(python), '--require-hashes', '-r', str(runtime)])
    command(['uv', 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)])
    names = json.loads(command([str(python), '-I', '-c',
        'import importlib.metadata,json;print(json.dumps([d.metadata["Name"] for d in importlib.metadata.distributions()]))']).stdout)
    tools = output / 'standalone-requirements.txt'
    command(['uv', 'export', '--locked', '--only-group', 'standalone', '--no-emit-project',
             '--output-file', str(tools)], cwd=checkout)
    command(['uv', 'pip', 'install', '--python', str(python), '--require-hashes', '-r', str(tools)])
    runtime_info = json.loads(command([str(python), '-I', '-c', RUNTIME_CODE, json.dumps(names)]).stdout)
    if runtime_info['python'] != data['python']:
        raise ValueError('Embedded interpreter patch differs from the target policy')
    if not any(record['component'] == 'interpreter' for record in runtime_info['licenses']):
        raise ValueError('Actual embedded interpreter license was not found')
    runtime_info.pop('base')
    inputs = output / 'probe-inputs.json'
    write_json(inputs, synthetic_cases(checkout, output))
    observed = json.loads(command([str(python), '-I', '-c', ORACLE_CODE,
                                   str(checkout / '.github/workflows/verify_install.py'), str(checkout), str(inputs),
                                   'smoke' if smoke else 'full']).stdout)
    if observed['version'] != producer['version'] or not Path(observed['origin']).is_relative_to(environment):
        raise ValueError('Standalone oracle did not use the accepted installed wheel')
    entry = Path(observed['origin']).with_name('_standalone.py')
    specification = output / 'standalone.spec'
    native_sources = output / 'native-notice-sources.json'
    project = tomllib.loads((checkout / 'pyproject.toml').read_text(encoding='utf-8'))
    tool_versions = {requirement.split('==')[0]: requirement.split('==')[1].split(';')[0].strip()
                     for requirement in project['dependency-groups']['standalone']}
    specification.write_text(f'''from PyInstaller.utils.hooks import collect_data_files,collect_dynamic_libs,copy_metadata
import json,runpy,sys
from pathlib import Path
datas=collect_data_files('brewdoc')+collect_data_files('pdfminer')
datas+=collect_data_files('pypdfium2')+collect_data_files('pypdfium2_raw')
for name in {names!r}: datas+=copy_metadata(name)
analysis=Analysis([{str(entry)!r}],datas=datas,binaries=collect_dynamic_libs('pypdfium2_raw'),
 hiddenimports=['_quickjs','selectolax.lexbor','python_calamine'],excludes=['selectolax.parser'])
analysis.datas=[item for item in analysis.datas if not item[0].replace('\\\\','/').startswith('selectolax/parser.')]
capture=runpy.run_path({str(Path(__file__).resolve())!r})['capture_native_sources']
Path({str(native_sources)!r}).write_text(json.dumps(capture(analysis.binaries,analysis.scripts,Path(sys.prefix),{tool_versions!r})),encoding='utf-8')
archive=PYZ(analysis.pure)
executable=EXE(archive,analysis.scripts,[],exclude_binaries=True,name='brewdoc',console=True,strip=False,upx=False)
collection=COLLECT(executable,analysis.binaries,analysis.datas,strip=False,upx=False,name='brewdoc')
''', encoding='utf-8')
    result = command([str(python), '-I', '-m', 'PyInstaller', '--noconfirm', '--clean',
                      '--distpath', str(output / 'bundle'), '--workpath', str(output / 'freeze-work'),
                      str(specification)], cwd=output)
    (output / 'freeze.log').write_bytes(result.stdout + result.stderr)
    root = output / 'bundle/brewdoc'
    copy_notices(checkout, root / 'licenses/supplemental')
    installed_licenses = []
    for record in runtime_info.pop('licenses'):
        source = Path(record['path'])
        name = PurePosixPath(record['name'])
        if name.is_absolute() or '..' in name.parts:
            raise ValueError('Installed license path escapes its component')
        destination = root / 'licenses/installed' / record['component'] / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        installed_licenses.append({'path': destination.relative_to(root).as_posix(),
                                  'bytes': destination.stat().st_size, 'sha256': digest(destination)})
    if not installed_licenses:
        raise ValueError('Standalone runtime notices were not retained')
    sources = json.loads(native_sources.read_text(encoding='utf-8'))
    retained, runtime_info['native_hooks'] = collect_runtime_hook_notices(root, sources['hooks'])
    installed_licenses.extend(retained)
    if target == 'linux-x64':
        retained, runtime_info['native_libraries'] = collect_linux_runtime_notices(root, sources['libraries'])
        installed_licenses.extend(retained)
    write_json(root / 'licenses/installed-manifest.json', sorted(installed_licenses, key=lambda row: row['path']))
    version = producer['version']
    write_json(root / 'version.json', {'version': version, 'source_sha': producer['checkout_sha'],
                                      'python': data['python'], 'target': target})
    archive_format = data['targets'][target]['archive']
    asset = output / f'brewdoc-{version}-{target}.{archive_format}'
    records = archive_bundle(root, asset, archive_format)
    if any(name.startswith('brewdoc/_internal/selectolax/parser.') for name in records):
        raise ValueError('Unused selectolax parser entered the standalone bundle')
    executable = 'brewdoc/brewdoc' + ('.exe' if target == 'windows-x64' else '')
    manifest = {'schema': 1, 'source_sha': producer['checkout_sha'], 'version': version,
                'verification_scope': 'smoke' if smoke else 'full',
                'python': data['python'], 'target': target, 'wheel_sha256': digest(wheel),
                'lock_sha256': digest(checkout / 'uv.lock'), 'executable': executable,
                'archive': {'filename': asset.name, 'bytes': asset.stat().st_size, 'sha256': digest(asset)},
                'inventory': records, 'oracle': observed['oracle'], 'probes': observed['probes'], 'runtime': runtime_info,
                'signing': data['signing'], 'minimum_tested': data['targets'][target]['verification_floor']}
    validate_manifest(manifest)
    write_json(output / 'manifest.json', manifest)


def consumer_environment(work: Path) -> dict:
    """Give the binary only fresh user directories and OS-required Windows settings."""
    environment = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR') if name in os.environ}
    environment.update({'PATH': str(work), 'HOME': str(work), 'USERPROFILE': str(work),
                        'TMPDIR': str(work), 'TMP': str(work), 'TEMP': str(work),
                        'LANG': 'en_US.UTF-8', 'PYTHONIOENCODING': 'ascii'})
    return environment


def check_output(result: subprocess.CompletedProcess, row: dict, output: Path | None = None,
                 artifact_output: Path | None = None) -> None:
    """Compare complete receipt framing and sealed Markdown hashes without bundled imports."""
    receipt = {**row['receipt'], 'out': str(output) if output is not None else None}
    if artifact_output is not None:
        receipt['artifacts'] = [{**item, 'out': str(artifact_output) if item['key'] == row['artifact']['key'] else item['out']}
                               for item in receipt['artifacts']]
    line = (json.dumps(receipt, ensure_ascii=True, sort_keys=True) + os.linesep).encode('ascii')
    if result.returncode != row['code'] or result.stderr or not result.stdout.startswith(line):
        raise ValueError('Standalone receipt, exit or stderr differs from the sealed engine')
    body = output.read_bytes() if output is not None else result.stdout[len(line):]
    if output is not None and result.stdout != line:
        raise ValueError('Standalone --out also emitted Markdown to stdout')
    encoding = row.get('markdown_encoding', 'utf-8' if receipt['route'] in ('html', 'epub') else 'ascii')
    if encoding not in ('ascii', 'utf-8'):
        raise ValueError('Invalid standalone Markdown encoding')
    if output is None and encoding == 'ascii':
        body = body.replace(os.linesep.encode('ascii'), b'\n')
    if (len(body), hashlib.sha256(body).hexdigest()) != (row['markdown_bytes'], row['markdown_sha256']):
        raise ValueError('Standalone Markdown differs from the sealed engine')


def child_checks(binary: Path, work: Path, environment: dict, javascript: bool) -> dict:
    """Exercise actual frozen DNS, native signal and inherited private IPC boundaries."""
    dns = command([str(binary), '--brewdoc-internal', 'dns', 'localhost', '80', str(os.getpid())],
                  cwd=work, env=environment, timeout=10)
    addresses = json.loads(dns.stdout)
    if dns.stderr or not addresses or set(addresses) - {'127.0.0.1', '::1'}:
        raise ValueError('Frozen DNS child returned unexpected localhost addresses')
    result = {'dns': addresses}
    if sys.platform == 'win32':
        owner = subprocess.Popen([sys._base_executable, '-I', '-S', '-c', 'pass'],
                                 cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if owner.wait(timeout=10) != 0:
                raise ValueError('Frozen DNS test owner did not exit successfully')
            # Retain Popen's Windows handle after exit so its PID cannot be reused.
            refused = subprocess.run([str(binary), '--brewdoc-internal', 'dns', 'localhost', '80', str(owner.pid)],
                                     cwd=work, env=environment, capture_output=True, timeout=10)
            if (refused.returncode, refused.stdout, refused.stderr) != (1, b'', b''):
                raise ValueError('Frozen DNS child admitted an exited owner')
            result['exited_owner'] = {'code': 1, 'stdout_bytes': 0, 'stderr_bytes': 0}
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
    if not javascript:
        return result
    probe = subprocess.run([str(binary), '--brewdoc-internal', 'signal'],
                           cwd=work, env=environment, capture_output=True, timeout=10)
    if (probe.returncode, probe.stdout, probe.stderr) != (int(signal.SIGUSR1), b'', b''):
        raise ValueError('Frozen native signal guard failed')
    parent_socket, child_socket = socket.socketpair()
    parent = Connection(parent_socket.detach())
    descriptor = child_socket.fileno()
    process = subprocess.Popen([str(binary), '--brewdoc-internal', 'js', str(descriptor), str(os.getpid())],
                               cwd=work, env=environment, pass_fds=(descriptor,), start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_socket.close()
    try:
        source = ('<html><body><p id="result">pending</p><script type="module">'
                  'document.querySelector("#result").textContent=String(await Promise.resolve(42))'
                  '</script></body></html>')
        record = {'source': source,
                  'url': 'https://example.org/', 'soft_window': 0.02, 'deadline': time.monotonic() + 10}
        parent.send_bytes(json.dumps(record).encode('utf-8'))
        if not parent.poll(15):
            raise ValueError('Frozen private worker did not return')
        reply = json.loads(parent.recv_bytes(18 * 1048576))
        process.wait(timeout=5)
        expected_html = source.replace('>pending</p>', '>42</p>')
        if (process.returncode, reply.get('kind'), reply.get('result', {}).get('html'),
            reply.get('result', {}).get('capture_status')) != (0, 'result', expected_html, 'settled'):
            raise ValueError('Frozen private worker capture changed')
        result.update({'signal': probe.returncode, 'worker': reply})
    finally:
        parent.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    return result


def verify(checkout: Path, bundle: Path, output: Path, target: str, archive: Path | None = None,
           *, smoke: bool = False) -> None:
    """Launch a relocated extracted asset outside checkout with no Python on its PATH."""
    data = facts(checkout, target)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest)
    scope = 'smoke' if smoke else 'full'
    if verification_scope(manifest) != scope:
        raise ValueError('Requested standalone verification scope differs from its manifest')
    producer = json.loads((bundle / 'ci-package/manifest.json').read_text(encoding='utf-8'))
    if (manifest['schema'], manifest['source_sha'], manifest['version'], manifest['target'], manifest['python']) != (
            1, producer['checkout_sha'], producer['version'], target, data['python']):
        raise ValueError('Standalone source, target, version or interpreter identity changed')
    selected = [] if smoke else verifier().select_inputs(checkout)
    if {row['path'] for row in manifest['oracle']} != {document['path'] for document, _expected in selected}:
        raise ValueError('Standalone oracle selection differs from the accepted smoke inputs')
    for document, expected in selected:
        if 'accepted_output' in expected:
            row = next(row for row in manifest['oracle'] if row['path'] == document['path'])
            accepted = expected['accepted_output']
            if (row['source_sha256'], row['markdown_sha256'], row['markdown_bytes'], row['receipt']) != (
                    accepted['source_sha256'], accepted['markdown_sha256'], accepted['markdown_utf8_bytes'], accepted['receipt']):
                raise ValueError('Standalone EPUB oracle differs from independently reviewed output')
    asset = archive or output / manifest['archive']['filename']
    if (asset.name, asset.stat().st_size, digest(asset)) != (
            manifest['archive']['filename'], manifest['archive']['bytes'], manifest['archive']['sha256']):
        raise ValueError('Standalone archive identity changed')
    with tempfile.TemporaryDirectory(prefix='brewdoc standalone consumer ') as temporary:
        work = Path(temporary)
        root = extract_archive(asset, work / 'relocated bundle', manifest['inventory'])
        version = json.loads((root / 'version.json').read_text(encoding='utf-8'))
        if version != {key: manifest[key] for key in ('version', 'source_sha', 'python', 'target')}:
            raise ValueError('Shipped standalone version identity differs from its accepted manifest')
        binary = root.parent / manifest['executable']
        environment = consumer_environment(work)
        check = command([str(binary), '--self-check'], cwd=work, env=environment, timeout=30)
        if (check.stdout, check.stderr) != ((f'self-check: ok (38 checks){os.linesep}').encode('ascii'), b''):
            raise ValueError('Frozen shipped self-check differs from the supported interpreter contract')
        fixture_results = []
        for ordinal, row in enumerate(manifest['oracle'], 1):
            source = checkout / row['path']
            if digest(source) != row['source_sha256']:
                raise ValueError('Standalone source fixture identity changed')
            copied = work / source.name
            shutil.copyfile(source, copied)
            result = command([str(binary), str(copied)], cwd=work, env=environment, timeout=60)
            check_output(result, row)
            destination = work / f'output-{ordinal}.md'
            result = command([str(binary), str(copied), '--out', str(destination)],
                             cwd=work, env=environment, timeout=60)
            check_output(result, row, destination)
            fixture_results.append({'path': row['path'], 'markdown_sha256': row['markdown_sha256']})
        for row in manifest['probes']:
            source = work / Path(row['path']).name
            if 'bytes_base64' in row:
                source.write_bytes(base64.b64decode(row['bytes_base64'], validate=True))
            else:
                shutil.copyfile(checkout / row['path'], source)
            if digest(source) != row['source_sha256']:
                raise ValueError('Standalone supplementary probe source changed')
            arguments = [str(binary), str(source)]
            for sheet in row.get('sheets', []):
                arguments += ['--sheet', sheet]
            artifact_output = None
            if 'artifact' in row:
                artifact_output = work / (row['probe'] + '.payload')
                arguments += ['--artifact', row['artifact']['key'] + '=' + str(artifact_output)]
            result = command(arguments, cwd=work, env=environment, timeout=60)
            check_output(result, row, artifact_output=artifact_output)
            if smoke:
                destination = work / (row['probe'] + '.md')
                result = command([*arguments, '--out', str(destination)], cwd=work, env=environment, timeout=60)
                check_output(result, row, destination, artifact_output)
            if artifact_output is not None and (artifact_output.stat().st_size, digest(artifact_output)) != (
                    row['artifact']['bytes'], row['artifact']['sha256']):
                raise ValueError('Standalone formula/VBA artifact differs from its sealed source bytes')
        malformed = work / 'malformed.epub'
        malformed.write_bytes(b'not an EPUB ZIP')
        sentinel = work / 'sentinel.md'
        sentinel.write_bytes(b'preserve existing output\n')
        refused = subprocess.run([str(binary), str(malformed), '--out', str(sentinel)],
                                 cwd=work, env=environment, capture_output=True, timeout=10)
        receipt = json.loads(refused.stdout)
        if (refused.returncode, receipt['file_ok'], receipt['units'], sentinel.read_bytes(), refused.stderr) != (
                1, False, 0, b'preserve existing output\n', b''):
            raise ValueError('Frozen refusal changed the existing output')
        zip_xml_refusal_probe([str(binary)], work, environment)
        children = child_checks(binary, work, environment, data['targets'][target]['render_js'])
        public = command([str(binary), '--url', 'https://example.org/', '--timeout', '30'],
                         cwd=work, env=environment, timeout=35)
        public_receipt = json.loads(public.stdout.splitlines()[0])
        if not public_receipt['file_ok'] or public_receipt['acquisition']['capture_status'] != 'static':
            raise ValueError('Frozen valid HTTPS acquisition failed')
        invalid = subprocess.run([str(binary), '--url', 'https://self-signed.badssl.com/', '--timeout', '30'],
                                 cwd=work, env=environment, capture_output=True, timeout=35)
        invalid_receipt = tls_receipt(invalid)
        js = subprocess.run([str(binary), '--url', 'https://example.org/', '--render-js', '--timeout', '5'],
                            cwd=work, env=environment, capture_output=True, timeout=10)
        js_receipt = json.loads(js.stdout.splitlines()[0])
        if data['targets'][target]['render_js']:
            if js.returncode != 0 or js_receipt['acquisition']['capture_status'] not in ('settled', 'partial'):
                raise ValueError('Frozen admitted JavaScript capture failed')
        elif (js.returncode, js_receipt['acquisition']['refusal_stage'],
              js_receipt['acquisition']['counts']['requests']) != (1, 'prerequisites', 0):
            raise ValueError('Frozen unsupported JavaScript did not refuse before HTTP')
        write_json(output / 'verification.json', {'schema': 1, 'source_sha': manifest['source_sha'],
                   'verification_scope': scope,
                   'version': manifest['version'], 'target': target, 'archive_sha256': digest(asset),
                   'verdict': 'PASS', 'host': platform.platform(), 'consumer_environment_keys': sorted(environment),
                   'self_check': 38, 'fixtures': fixture_results, 'children': children,
                   'probes': [row['probe'] for row in manifest['probes']],
                   'https': public_receipt, 'invalid_tls': invalid_receipt, 'javascript': js_receipt})


def aggregate(source: Path, output: Path) -> None:
    """Require one verified artifact per exact target before preparing release assets."""
    expected_targets = {'linux-x64', 'windows-x64', 'macos-arm64'}
    records, identities = {}, set()
    for path in sorted(source.rglob('manifest.json')):
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('schema') != 1 or 'target' not in manifest:
            continue
        manifest = admit_manifest(path)
        target = manifest['target']
        if target not in expected_targets or target in records:
            raise ValueError('Unknown or duplicated standalone target')
        identities.add((manifest['source_sha'], manifest['version'], manifest['python'], manifest['wheel_sha256'],
                        manifest['lock_sha256'], verification_scope(manifest)))
        records[target] = manifest
    if set(records) != expected_targets or len(identities) != 1:
        raise ValueError('Standalone release requires three native targets with one source identity')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'verification').mkdir()
    checksums = []
    for target, manifest in records.items():
        filename = manifest['archive']['filename']
        candidates = list(source.rglob(filename))
        if len(candidates) != 1:
            raise ValueError('Standalone release archive location is ambiguous')
        shutil.copyfile(candidates[0], output / filename)
        shutil.copyfile(candidates[0].with_name('verification.json'), output / 'verification' / (target + '.json'))
        checksums.append(manifest['archive']['sha256'] + '  ' + filename)
    write_json(output / 'binaries.json', {'schema': 1, 'source_sha': next(iter(identities))[0],
                                        'version': next(iter(identities))[1],
                                        'verification_scope': next(iter(identities))[5], 'targets': records})
    (output / 'SHA256SUMS').write_text('\n'.join(sorted(checksums)) + '\n', encoding='ascii', newline='\n')


def main(argv: list[str] | None = None) -> int:
    """Expose fixed native preparation, consumer verification and release admission."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='operation', required=True)
    for operation in ('build', 'verify'):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument('--checkout', type=Path, required=True)
        subparser.add_argument('--bundle', type=Path, required=True)
        subparser.add_argument('--output', type=Path, required=True)
        subparser.add_argument('--target', choices=('linux-x64', 'windows-x64', 'macos-arm64'), required=True)
        subparser.add_argument('--smoke', action='store_true', help='Use only the two synthetic CI probes')
        if operation == 'verify':
            subparser.add_argument('--archive', type=Path)
    subparser = subparsers.add_parser('aggregate')
    subparser.add_argument('--input', type=Path, required=True)
    subparser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.operation == 'build':
        build(arguments.checkout.resolve(), arguments.bundle.resolve(), arguments.output.resolve(), arguments.target,
              smoke=arguments.smoke)
    elif arguments.operation == 'verify':
        verify(arguments.checkout.resolve(), arguments.bundle.resolve(), arguments.output.resolve(),
               arguments.target, arguments.archive.resolve() if arguments.archive else None, smoke=arguments.smoke)
    else:
        aggregate(arguments.input.resolve(), arguments.output.resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
