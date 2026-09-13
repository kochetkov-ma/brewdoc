# Full-run results

Reviewed full-run bundles are versioned on the `benchmark` branch. Each row
links to the summary, actual Markdown, portable capture/receipt records,
source annotations and measured context for one completed run.

| Run | Measured tool and source | Coverage | Content quality | Verdict |
|---|---|---|---|---|
| [20260913-133006](20260913-133006/README.md) | brewdoc 0.1.0; [56102bd](https://github.com/kochetkov-ma/brewdoc/commit/56102bd20e0e05d9fea104c88d241f7cc682c291) | 31 public inputs; 2 passes; 62 captures | 8 success, 15 warning, 8 failure | Local capture/review gate complete; PDF fidelity fails |

The measured revision is `56102bd20e0e05d9fea104c88d241f7cc682c291`, taken
from the frozen run context. The publication commit identifies the shared bundle,
not a newly measured converter. [context.json](20260913-133006/context.json)
records source fingerprints, input identity, versions and commands;
[results.json](20260913-133006/results.json) declares the full-run scope;
[quality.json](20260913-133006/quality.json) retains all judgments.

## Storage and selection

Immutable raw stdout, stderr, capture records and outputs remain in the local
`.codex/reports/<UTC>_baseline-results/` tree. That is the authoritative raw
record. A selected full run is shared here as a reviewed portable bundle;
see its [schema and transformations](20260913-133006/README.md).

Public Markdown is byte-identical to the captured output. Receipt copies record
output-path normalization and any exact host-path replacement in
`receipt_transformations`. Public context removes host-specific absolute paths
while retaining enough source/version/input/command detail to repeat the run.
It contains no secret or environment-variable dump. Portable receipt bytes are
not presented as untouched raw stdout.

Publish only an explicitly selected completed full run after checking every
input in every pass, recorded failures, provenance, output integrity and
source-based quality review. Quality failures are valid results and stay visible.
Partial, smoke and debug runs remain local; ordinary test runs do not add rows
or trigger publication. Commit/push authority follows the user's active request
and session scope. See the [measurement and sharing method](../METHODOLOGY.md).

