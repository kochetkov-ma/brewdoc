# Supplemental standalone notices

These files supplement each bundle's installed distribution and interpreter license trees. They cover native source dependencies whose notices are absent from wheel metadata, including calamine's Rust graph, Lexbor and QuickJS.

provenance.json records exact source versions, archive checksums or Git revisions, source members and retained file hashes. License text is unchanged. File records identify added final newlines and the single CRLF-to-LF conversion.

The openssl directories retain exact README copyright sections for interpreter OpenSSL 3.5.9 and cryptography OpenSSL 4.0.3. Provenance identifies each immutable upstream source and extraction. Both versions use the existing Apache license text; neither upstream tree supplies a NOTICE file.

Bundle builders verify file hashes before copying component directories. They also retain the actual target's CPython, OpenSSL, PDFium and other collected distribution licenses. This source inventory does not establish which libraries entered a particular binary.

Bundle builders must exclude the selectolax.parser extension and parser.* source/stub files from the Lexbor-only bundle. The pyo3-file directory retains its actual author/license declaration and the declared Apache-2.0 alternative's text; upstream supplies no separate LICENSE file. Independent review owns acceptance of that attribution.
