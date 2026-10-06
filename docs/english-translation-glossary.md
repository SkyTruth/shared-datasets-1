# English-only translation glossaries

Use `scripts/feature_metadata_english_glossary.py` when missing translation
workbooks contain large numbers of place names or identifiers. This offline
tool asks TypeSafe's Jev to classify distinct words in their label context,
then exports reviewed English phrases once per target language. For example,
`890. ACHANAKMAR TIGER RESERVE` contributes `tiger reserve`; its number and
specific place name are retained locally.

This is label preparation, not general prose translation or a production
runtime dependency. Jev classifies; an agent or a translation provider translates
the resulting bounded glossary. No paid calls run in ordinary tests. Existing translations, metadata
CSVs, canonical sidecars, geometry and GCS objects are never changed by this
tool.

## Credentials

The CLI reads only `TYPESAFE_API_KEY`. Keep the secret outside Git. One local
setup is a user-readable-only file at `~/.config/typesafe/api-key`:

```bash
export TYPESAFE_API_KEY="$(cat ~/.config/typesafe/api-key)"
```

The configured TypeSafe key is named **English Translatable** in the provider's
interface. That display name is useful for troubleshooting and rotation; the
program still reads the environment variable above.

For GitHub Actions, store it as the repository or environment secret
`TYPESAFE_API_KEY` and inject it only into an explicitly invoked classification
step with `env: TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}`. Do not put a
key in a workflow, configuration file, CLI argument, log, or test fixture.
GitHub Secrets do not make the key available to a local terminal. This tool
does not add a scheduled workflow or spend credits on every push.

The model is pinned to `jev-1.13.0` at the official TypeSafe API endpoint.
See [TypeSafe's API contract](https://docs.typesafe.ai/api) for request/response
shapes, [TypeSafe models](https://docs.typesafe.ai/models) for current pricing and
limits and [GitHub Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
for credential setup.

## Classify, review, export

Start with original, uncompleted `hash,text` workbooks. Do not submit translated
workbooks or work already completed successfully. Work under the standard
temporary workspace; the source workbooks must remain available through import.

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/feature_metadata_english_glossary.py classify \
  --input es="$WORK_ROOT/source-es.xlsx" \
  --input fr="$WORK_ROOT/source-fr.xlsx" \
  --output-dir "$WORK_ROOT/english-review"
```

Distinct words share up to three representative label contexts. Jev answers
128 questions per request, with each word and its examples inside its own
question. Before changing that request format, check a full-size batch against
known English descriptors and misleading names. Successful calls are cached by
the exact model, instructions and state, so rerunning identical work does not pay
again.
`--max-requests` limits new calls per invocation (default 1,000). A provider
failure stops the run and retains completed batches for resumption. The report
records successful input-token usage, including reused cached responses; the
request limit is not a dollar limit. Failure messages count attempted new calls
and report discarded-response token usage when available. Failed-call billing
can be unknown; consult the provider's usage records rather than counting only
the final classification report.

Review `classification.json`, especially words that also occur as names.
Do not equate a dictionary match or model probability with linguistic review.
Create `reviewed-terms.json` containing the SHA-256 of that exact report and
the lowercase terms approved for this corpus:

```json
{
  "classification_sha256": "<64-character SHA-256 of classification.json>",
  "terms": ["division", "forest", "reserve", "tiger"]
}
```

An agent can perform this bounded review; it is not a requirement for a new
human approval. Uncertain words should remain unchanged. Add words only after
checking their examples; this list is a corpus-specific decision, not a global
English dictionary. Word-level decisions based on sampled contexts can miss
rare name collisions, so inspect exported phrases and reconstructed labels.

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/feature_metadata_english_glossary.py export \
  --classification "$WORK_ROOT/english-review/classification.json" \
  --approved-terms "$WORK_ROOT/reviewed-terms.json" \
  --output-dir "$WORK_ROOT/english-glossary"
```

The output contains one small `hash,text` workbook per nonempty target and a
local manifest recording exact source hashes, targets and character spans.
Adjacent approved words are kept together. Whitespace and case variants share
one phrase; identifiers, non-English terms and names are omitted from the
translation workload. Numbers, punctuation and excluded text remain literals.
The original input workbooks are never overwritten. Preserve the manifest and
originals locally. An agent may translate a small glossary directly by writing
returned `hash,text` workbooks with the shared document helper; preserve every
hash and review domain meanings in context. For a larger workload, send only the
glossary workbooks to the chosen provider. In Google Translate, select English
as the source and the file's target language as the destination. Reconsider the
method after reduction rather than handing a small glossary back to the user.

## Reconstruct and complete the existing workflow

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/feature_metadata_english_glossary.py reconstruct \
  --manifest "$WORK_ROOT/english-glossary/glossary-manifest.json" \
  --translated es="$WORK_ROOT/returned-es.xlsx" \
  --translated fr="$WORK_ROOT/returned-fr.xlsx" \
  --provenance agent-glossary \
  --output-dir "$WORK_ROOT/reconstructed"
```

Returned rows may be reordered, but every exported hash must survive exactly
once. The existing document helper validates complete hash membership before
reconstruction. Review content as well: structurally valid or unchanged output
does not by itself establish successful translation. Translate intact phrases;
do not translate hashes or repair them by row position.

`reconstructed-{target}.jsonl` associates each reconstructed label with its
original source-value hash and records the supplied translation provenance and
`review_state=machine_translated`. Use `--provenance google-document-translation`
when that is the actual method. Agent review is not human language review. It includes
only labels with reviewed English spans. These files are local intermediate
artifacts, not translation CSVs or publishable sidecars. Match them to current
missing feature/field/locale/hash keys using the existing missing-only process,
preserving every successful existing row. Source-only labels and uncertain
terms remain explicit exclusions/source fallbacks; never count them as
completed machine translations. Phrase substitution preserves label order,
so it is not suitable for grammatical translation of whole prose sentences.

After content validation and missing-only import, use the existing localization
and reviewed dataset publication workflow. Build affected sidecars once for the
accepted input revision. This tool neither authorizes publication nor changes
maintained field/locale contracts.
