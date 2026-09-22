# Capture and review a baseline

Run from the repository checkout with its prepared Python 3.12 environment.
Use [corpus.json](corpus.json) and [capture_results.py](capture_results.py).
Do not install or discover tools during measurement. The corpus contains 41
inputs: 11 `core` documents and 30 `feature_probe` documents. Select all 41;
report the groups separately. These inputs are also test fixtures, not a
held-out evaluation set.

## Freeze the context

Before conversion, record the following in `context/`:

- Copy the corpus index, provenance and this method. Record their SHA-256 hashes.
  Verify each selected input's hash and size against the index. Retain a sorted
  list of input IDs, paths, formats, groups and hashes.
- Record checkout path, Git revision, branch and dirty status. Save the scoped
  tracked diff, including binary changes, and its hash. Hash the actual files
  under `src/brewdoc/`, the capture helper, `pyproject.toml` and `uv.lock`, including
  untracked files in these scopes. A revision alone does not identify dirty code.
- Record the Python version and executable, imported brewdoc version and module
  path, installed package names/versions, OS and machine architecture. Record
  working directory, capture arguments, timeout and pass order. Use an explicit
  version label matching the imported tool. Never save environment variables,
  credentials, authentication files or unrelated private working changes.

Do not edit conversion code, dependencies or inputs during the run. Recheck the
same source and input fingerprints afterward and retain that verification.
Concurrent task notes do not change the measured tool, but record any change to
a measured file. Such a change prevents treating both passes as one baseline.

## Allocate a new result tree

Create a unique UTC name; refuse an existing root. Capture immutable raw results locally:

```text
.codex/reports/<UTC>_baseline-results/
  context/
  pass-1/<input-id>/
  pass-2/<input-id>/
  reviews/
  summary/
```

`context/` identifies the run. Each `pass-N/<input-id>/` is created only by the
capture helper and contains `result.json`, `stdout.bin`, `stderr.bin` and
`artifacts/`. Keep their actual bytes unchanged. Put source review, decoded
receipts, normalized comparisons and aggregate tables in `reviews/` or `summary/`.
Do not write derived files into a completed capture directory.

For example, from the checkout:

```sh
baseline_dir="$(pwd)/.codex/reports/$(date -u +%Y%m%dT%H%M%SZ)_baseline-results"
mkdir -p .codex/reports
mkdir "$baseline_dir" || exit 2
mkdir "$baseline_dir/context" "$baseline_dir/pass-1" "$baseline_dir/pass-2" \
  "$baseline_dir/reviews" "$baseline_dir/summary"
```

Record the chosen directory in the task index. A collision requires another
name. On interruption, retain the incomplete attempt and its exit information;
use a new capture directory for recovery and record the replacement relationship.
Never overwrite, silently skip or repair an existing observation.

For the frozen input selection:

```sh
.venv/bin/python - "$baseline_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

context = Path(sys.argv[1]) / "context"
index_bytes = Path("benchmarks/corpus.json").read_bytes()
documents = json.loads(index_bytes)["documents"]
rows = []
for document in sorted(documents, key=lambda item: item["id"]):
    data = Path(document["path"]).read_bytes()
    if (hashlib.sha256(data).hexdigest(), len(data)) != (document["sha256"], document["size_bytes"]):
        raise SystemExit(f"Input changed: {document['id']}")
    rows.append(f"{document['id']}\t{document['path']}\n")
with (context / "corpus.json").open("xb") as output:
    output.write(index_bytes)
with (context / "selection.tsv").open("x", encoding="utf-8") as output:
    output.writelines(rows)
PY
```

This verifies input identity. Complete the remaining context records described
above before starting conversion.

## Run two serial passes

Use one fixed command and a 300-second timeout for every input. Complete pass 1
in sorted ID order, then repeat that order for pass 2. Start no parallel capture
processes. Every selected input needs an entry in each pass, including refusals,
timeouts and capture failures. Record the helper exit code separately from the
tool's `returncode`. Continue to the next input after a recorded failure.

This single-input command uses the same interface as the full run:

```sh
tool_version=$(.venv/bin/python -c 'import brewdoc; print(brewdoc.__version__)')
.venv/bin/python benchmarks/capture_results.py \
  --input tests/fixtures/xlsx/calamine-date.xlsx \
  --output-dir "$baseline_dir/pass-1/xlsx-calamine-date" \
  --tool brewdoc --tool-version "$tool_version" --timeout 300 \
  -- .venv/bin/python -m brewdoc.cli '{input}' --out '{output_dir}/document.md'
```

For the complete run, use the frozen `context/selection.tsv` with two columns,
ID and repository-relative input path, sorted by ID and without a header. The
following shell loop repeats the same explicit command; it adds no runner or
tool discovery. Use it instead of the single-input example on the same root.

```sh
tool_version=$(.venv/bin/python -c 'import brewdoc; print(brewdoc.__version__)')
for pass in 1 2; do
  while IFS="$(printf '\t')" read -r input_id input_path; do
    capture_exit=0
    .venv/bin/python benchmarks/capture_results.py \
      --input "$input_path" --output-dir "$baseline_dir/pass-$pass/$input_id" \
      --tool brewdoc --tool-version "$tool_version" --timeout 300 \
      -- .venv/bin/python -m brewdoc.cli '{input}' --out '{output_dir}/document.md' \
      || capture_exit=$?
    printf '%s\t%s\t%s\n' "$pass" "$input_id" "$capture_exit" \
      >> "$baseline_dir/summary/helper-exits.tsv"
  done < "$baseline_dir/context/selection.tsv"
done
```

Create `selection.tsv` from the frozen index after verifying its input hashes.
Preserve the exact command and selection with the run, including any departure
from this protocol. If `result.json` cannot be written, keep the helper exit and
available files, and explain the missing record in the summary.

`elapsed_seconds` measures wall time around subprocess invocation, including
interpreter startup, imports, rendering, output writes and termination cleanup.
It excludes input hashing and final artifact inventory. It is neither CPU time
nor extraction-only time. Report seconds for both passes and their totals.
Ordered passes can differ because of filesystem caches and system load; do not
label them cold/warm measurements or infer a speed improvement from two values.

## Compare observations

First check all 82 capture locations, helper exits, tool statuses and
`capture_errors`. A command exit of zero does not override incomplete capture.
Verify hashes of saved artifacts before review. Keep scan refusals and other
failures visible in the result table.

Compare emitted Markdown as exact bytes. For each emitted receipt, decode the
raw stdout and require exactly one JSON receipt line with `--out`. Compare the
parsed receipt objects after changing only `out` to `artifacts/document.md`
in a derived copy, and only when its original value equals that capture's
expanded output argument. Leave `out: null` unchanged. Preserve `reason`,
`source`, counts, dropped-content fields and every other value. Do not strip
arbitrary paths or normalize Markdown. Retain the raw stdout, decoded receipt,
normalized copy and comparison result.

Report absent Markdown as not applicable when both runs refuse the input;
absence is not successful deterministic rendering. Missing or malformed
receipts, differing Markdown, differing normalized receipt fields and changed
statuses require explicit findings. Matching bytes establish repeatability
for these two runs, not content quality or correctness on other documents.

## Review source content and report results

Review every input against its emitted Markdown or refusal. Use PDF page numbers, workbook sheet
names and cell/range coordinates, DOCX main-story blocks and chapter labels, or PPTX slide numbers
and package objects. Check source text, table headers and values, reading order, empty areas, dates,
merged regions, notes and pictures where relevant. Record exactly which source locations were
inspected; sampled evidence does not establish complete fidelity across uninspected content.
Do not invent expected Markdown or edit observed output to match a judgment.

Assign a separate quality judgment with a concrete rationale:

- `success`: inspected content retains the meaningful text and values without
  a material issue in the stated review scope.
- `warning`: output has a bounded limitation or uncertain fidelity, or an
  explicit expected refusal such as an image-only PDF without OCR.
- `failure`: missing or changed meaningful content makes the result unusable
  for the stated purpose, or an unexpected execution/capture failure prevents
  evaluating the output.

In `summary/README.md`, provide one readable row per input: ID/group, both
execution outcomes and timings, Markdown/receipt repeatability, quality judgment,
reason, source evidence and links. Link each pass's capture record, actual
Markdown when emitted, raw stdout and decoded receipt; link detailed reviews
separately. Use relative links and retain source/provenance references when
sharing a result tree. Clearly mark missing artifacts instead of linking them.

Summarize coverage, successes, warnings and failures by group, with material
limitations and unresolved issues. State whether brewdoc handled the inspected
cases usefully, without equating exit zero or matching hashes with acceptance.
Keep this local baseline distinct from unit tests, CI, packaging and release
evidence. Later comparisons need the same frozen inputs, command, environment
description and separate new result tree.

## Share a completed full run

Keep two storage layers. The local `.codex/reports/<UTC>_baseline-results/` tree
is the authoritative raw evidence and remains unchanged. A reviewed, portable
bundle in `benchmarks/results/<run-id>/` is versioned on the `benchmark` branch;
the [full-run registry](results/README.md) links its summary and actual outputs.

Select a completed full run explicitly for sharing. It must account for all 41
inputs in every pass, including refusals and failures, and retain provenance,
source identity, output-integrity checks and source-based quality review.
Quality failures belong in the bundle; a green quality verdict is not required.
Partial, smoke and debug runs remain local. Tests do not automatically publish
results or append registry entries. Git authority comes from the user's active
request and session scope, not from this method.

Preserve emitted Markdown byte for byte. Public receipt copies use the bundle's
declared normalization schema: record `out` changes and any exact host-path
replacement, including paths inside refusal reasons, as explicit transformations.
Retain observed counts, statuses, timings and quality failures. Remove host-specific
absolute paths from public context; identify commands, tool/dependency versions,
measured revision, source fingerprints and input hashes without environment or
credential dumps. Use repository-relative links that work on GitHub.

Validate the portable bundle against local raw evidence before the authorized
commit and push. Include its declared scope, all pass records, transformation
rules and checksums. Add one registry row for that selected completed run;
future runs get new directories. Publication does not alter the measured source
revision or turn a failed content-quality result into acceptance.
