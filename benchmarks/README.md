# PDF and Excel corpus

[corpus.json](corpus.json) indexes 31 existing files (4,227,297 bytes) in
[tests/fixtures](../tests/fixtures). Run examples from a repository checkout.
Fixtures and benchmark tools are not installed by the brewdoc wheel.

Browse the [file catalog](CATALOG.md) for each document's title, topics, pages or
sheets, and table/chart/cell features. All 31 inputs are public Internet files;
none were generated for this corpus. The [annotation and provenance contract](annotations/README.md)
explains source evidence, download verification and remaining unknowns.

For a complete run, follow the [baseline methodology](METHODOLOGY.md): freeze
the context, capture all 31 inputs twice, compare observations and review source
content. Immutable raw results live in `.codex/reports/<UTC>_baseline-results/`.
Selected completed full runs have reviewed portable bundles in
`benchmarks/results/<run-id>/`, versioned on the `benchmark` branch. Browse the
[full-run registry](results/README.md) for summaries and actual outputs.

The `core` group contains 9 government/research PDFs and 2 Census workbooks.
The `feature_probe` group contains 20 upstream Calamine parser examples for XLS,
XLSX, XLSM, XLSB and ODS. Keep group results separate. Every input already appears
in the test suite; this is a reusable comparison set, not a held-out quality set.

## Find an input

Each document has a stable `id`, title, topics, language, repository-relative
`path`, `provenance_kind`, original `source_url`, observed `download_url`,
`source_verification`, license links, local `sha256` and `size_bytes`, format,
variant, group, origin kind, searchable features and `annotation_path`.
The root `schema_version` is 2; `gaps` lists missing coverage. IDs do not change
when a file moves. Public origin and feature-probe selection are separate fields.

For example, list the ODS probes with Python:

```sh
python3 - <<'PY'
import json
from pathlib import Path

corpus = json.loads(Path("benchmarks/corpus.json").read_text())
for document in corpus["documents"]:
    if document["format"] == "ods":
        print(document["id"], document["path"], ", ".join(document["features"]))
PY
```

Features describe source content and observed cell types, not successful
conversion. The scan has five image-only pages; brewdoc refuses it because it
has no text layer. An empty workbook and an unreadable document are different
cases. Preserve the actual outcome when comparing tools.

[Fixture provenance](../tests/fixtures/SOURCES.md) owns source URLs, licenses and
input hashes. Some older shape descriptions there are inaccurate: the ODS
`special_cells` file contains whitespace strings; XLSX `date` contains dates
and a duration; ODS `date` contains date, datetime and time cells. ODS
`any_sheets` names do not imply Excel chart or VeryHidden semantics. The index
uses inspected features. Hashes identify the stored bytes; changing upstream
URLs do not authorize automatic replacement.

## Capture one command

[Capture helper](capture_results.py) runs one explicit command and retains its
outputs. Prepare brewdoc using the project's development setup, then choose a
new result directory:

```sh
.venv/bin/python benchmarks/capture_results.py \
  --input tests/fixtures/xlsx/calamine-date.xlsx \
  --output-dir /tmp/brewdoc-date-run-001 \
  --tool brewdoc --tool-version 0.1.0 --timeout 60 \
  -- .venv/bin/brewdoc '{input}' --out '{output_dir}/document.md'
```

The result directory must not exist. Choose another name for each run.
`{input}` becomes the absolute input path; `{output_dir}` becomes the new
result directory's `artifacts/` subdirectory. Quote both placeholders in shell
commands. For another tool, supply its already installed executable, exact
version label and arguments after `--`. The helper does not discover or install
tools, invoke a shell, or choose comparison runs.

Each result contains `result.json`, raw `stdout.bin` and `stderr.bin`, and
`artifacts/`. JSON records the declared tool/version, input hash and size,
original and expanded command arguments, working directory, start time, elapsed
subprocess time, timeout, status, return code and output paths/hashes. Tool
versions are supplied by the caller; record the version actually used. Command
arguments preserve options. Only files under `artifacts/` are inventoried;
outputs written elsewhere are not discovered. Symlinks and special files are
identified without hashing their targets.

Status can be `success`, `nonzero_exit`, `signal`, `timeout`, `spawn_error`
or `interrupted`. The helper exits 0 for a successful command, 1 for a captured
command failure, and 2 for usage or capture I/O errors. `capture_errors` records
incomplete stream hashing or artifact inventory while retaining the command's
status and readable evidence. Unreadable stream records can be null; an unwritable
result directory can prevent `result.json` from being saved.
No timeout applies unless supplied. On POSIX, timeout stops the command's process
group; elsewhere it stops the direct child. Run foreground commands that finish
before their output is inspected. The working directory and environment are
inherited; stdin is disabled.

Keep raw captures local. Partial, smoke and debug runs stay local; only explicitly
selected completed full runs enter the [shared registry](results/README.md).
Its bundles preserve Markdown bytes and document receipt/path transformations.
Captured Markdown is an observation, not an authored expected output. Timing,
exit status and matching hashes do not establish semantic quality. Compare the
actual output against source pages/cells and retain review notes beside the run.
The fixture receipt snapshots and synthetic unit assertions remain separate.

## Attribution

The original fixture bytes are reused without modification by this corpus.
Keep the source and license links with redistributed inputs. Calamine examples
retain the [MIT notice](LICENSES/calamine-MIT.txt), copyright 2016 Johann Tuffe.
Government documents retain their individual public-domain bases in the index.

The three research papers are licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/):

- Hugo Touvron and coauthors, [LLaMA: Open and Efficient Foundation Language Models](https://arxiv.org/abs/2302.13971v1), arXiv 2302.13971v1.
- Rafael Rafailov, Archit Sharma, Eric Mitchell, Stefano Ermon, Christopher D. Manning and Chelsea Finn, [Direct Preference Optimization: Your Language Model is Secretly a Reward Model](https://arxiv.org/abs/2305.18290v3), arXiv 2305.18290v3.
- Dirk Groeneveld and coauthors, [OLMo: Accelerating the Science of Language Models](https://arxiv.org/abs/2402.00838v4), arXiv 2402.00838v4.

Full author lists and version records are available at the linked source pages
and in the unchanged PDFs. Conversions produce separate derived artifacts;
retain attribution and describe those changes when sharing them.
