"""Preserve native descriptor ownership across Python allocation failures."""

from collections import Counter
import json
from pathlib import Path
import sys

import pytest

from brewdoc import htmljs
from brewdoc._urljs import native as native_module


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Native ownership proofs require isolated Linux.')

SOURCE = '<!DOCTYPE html><html><head></head><body><p safe="yes">Ordinary article</p></body></html>'


@pytest.fixture
def healthy_native():
    """Boot one ordinary document with installed DOM and private snapshot controls."""
    worker = Path(htmljs.__file__).with_name('_vendor').joinpath('linkedom-worker.js').read_text()
    bundle, marker, suffix = worker.rpartition('\nexport {')
    assert (marker, suffix.endswith('};\n')) == ('\nexport {', True), 'The fixture must load the real installed DOM export boundary.'
    glue = Path(htmljs.__file__).with_name('_urljs').joinpath('glue.js').read_text()
    with native_module.Context({}.__getitem__) as native:
        native.eval('(()=>{' + bundle + '\n' + glue + '\n})();', 'ownership-bootstrap')
        native.save(['__smallParseStart', '__smallParseEnd', '__smallBoot', '__smallSnapshot',
                     '__smallRawRoot', '__smallRawCurrent', '__smallRawAdvance',
                     '__smallRawKey', '__smallRawDependency'])
        assert native.call('__smallParseStart', SOURCE) == '0', 'The ordinary input must finish its initial parse without a checkpoint.'
        assert native.call('__smallParseEnd') == '1', 'The ordinary document must finish parsing before boot.'
        native.call('__smallBoot', 'https://fixture.test/', json.dumps({'href': 'https://fixture.test/'}))
        native.eval('document.doctype.name="html";globalThis.__ownedParagraph=()=>document.querySelector("p");', 'ordinary-document')
        native.save(['__ownedParagraph'])
        yield native


def _signature(descriptor):
    """Identify acquired native fields without copying or converting their values."""
    return descriptor.flags, tuple((getattr(descriptor, field).tag, getattr(descriptor, field).u.pointer)
                                   for field in ('value', 'getter', 'setter'))


def _reject_packing(monkeypatch):
    """Fail the Python byte allocation after the native descriptor is acquired."""
    def reject(descriptor):
        """Raise the original allocation failure without transferring ownership."""
        raise MemoryError('synthetic descriptor allocation failure')

    monkeypatch.setattr(native_module, 'bytes', reject, raising=False)
    return []


class _RejectingRecords(list):
    """Reject journal growth after packing has completed."""

    def append(self, record):
        """Leave the journal empty when its allocation fails."""
        raise MemoryError('synthetic descriptor allocation failure')


def _reject_append(monkeypatch):
    """Keep ordinary packing and fail only the journal ownership transfer."""
    return _RejectingRecords()


@pytest.mark.parametrize('reject', [_reject_packing, _reject_append], ids=['packing-allocation', 'journal-allocation'])
def test_descriptor_allocation_failure_releases_the_original_owned_fields_once(healthy_native, monkeypatch, reject):
    # GIVEN a real document reference owned by an ordinary paragraph descriptor.
    native = healthy_native
    with native:
        paragraph = native.call_raw('__ownedParagraph')
        try:
            descriptor = native.own_descriptor(paragraph, 'ownerDocument')
            assert descriptor is not None, 'The ordinary paragraph must expose its owned document descriptor.'
            assert (descriptor.value.tag, descriptor.getter.tag, descriptor.setter.tag) == (-1, 3, 3), 'The real descriptor must own one document object and no accessor references.'
            acquired = _signature(descriptor)
            freed = []
            original_free = native.free_descriptor

            def free(value):
                """Observe each actual native release before its fields are cleared."""
                freed.append(_signature(value))
                original_free(value)

            monkeypatch.setattr(native, 'free_descriptor', free)
            records = reject(monkeypatch)
            # WHEN Python cannot pack or append the acquired native descriptor.
            with pytest.raises(MemoryError, match='^synthetic descriptor allocation failure$'):
                native._retain_descriptor(descriptor, records)
            # THEN the original fields are freed once and no incomplete ownership entry remains.
            assert (records, freed, tuple(getattr(descriptor, field).tag for field in ('value', 'getter', 'setter'))) == (
                [], [acquired], (3, 3, 3),
            ), 'Allocation failure must release exactly the acquired native fields and clear their handles.'
        finally:
            native.lib.JS_FreeValue(native.context, paragraph)
    assert (native.context, native.runtime, native.allocated_bytes, native.allocations, native.handles) == (
        None, None, 0, {}, {},
    ), 'A failed ownership transfer must leave no native allocations or saved handles after close.'


def test_ordinary_snapshot_retains_every_descriptor_until_serialization_and_frees_it_once(healthy_native, monkeypatch):
    # GIVEN an ordinary document and observations of real ownership transfers and releases.
    native = healthy_native
    document = native.call_raw('__smallRawRoot')
    document_descriptor = (7, ((-1, document.u.pointer), (3, None), (3, None)))
    native.lib.JS_FreeValue(native.context, document)
    acquired, freed, serialization = [], [], []
    original_retain, original_free, original_call = native._retain_descriptor, native.free_descriptor, native.call

    def retain(descriptor, records):
        """Record successful ownership transfers without altering their lifetime."""
        original_retain(descriptor, records)
        acquired.append(_signature(descriptor))

    def free(descriptor):
        """Record actual releases, including reconstructed cleanup descriptors."""
        freed.append(_signature(descriptor))
        original_free(descriptor)

    def serialize(*arguments):
        """Observe ownership while the real saved serializer executes."""
        serialization.append((len(acquired), len(freed)))
        return original_call(*arguments)

    monkeypatch.setattr(native, '_retain_descriptor', retain)
    monkeypatch.setattr(native, 'free_descriptor', free)
    monkeypatch.setattr(native, 'call', serialize)
    # WHEN the unchanged native serializer publishes the healthy document.
    with native:
        snapshot = native.snapshot()
    # THEN all fields survive serialization and every acquired descriptor is released once.
    assert document_descriptor in acquired, 'The snapshot must acquire the real ordinary ownerDocument reference before publishing HTML.'
    assert (snapshot, serialization, Counter(freed)) == (
        SOURCE, [(len(acquired), 0)], Counter(acquired),
    ), 'The exact healthy HTML must survive with no early, missing or duplicate descriptor release.'
    assert (native.context, native.runtime, native.allocated_bytes, native.allocations, native.handles) == (
        None, None, 0, {}, {},
    ), 'Successful snapshot cleanup must release native allocations and saved handles completely.'
