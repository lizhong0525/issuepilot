# IssuePilot

Maintainer-assist triage for small public GitHub repositories: it reads open
issues, flags possible duplicates, bug reports missing reproduction details,
and likely labels — then hands you a review queue where **every suggestion
carries evidence and a confidence level, and nothing is ever posted, closed,
or labeled automatically.**

## Quickstart

```bash
python -m issuepilot demo
```

That's it. No dependencies, no API keys, no network — the demo runs the full
pipeline over the bundled fixtures and prints the review queue.

## Demo walkthrough

`fixtures/issues.json` contains 12 synthetic issues from a fictional CLI tool
(`weathercli`), including a 3-issue duplicate cluster (same startup crash,
same `KeyError: 'api_key'`) and two CSV issues that *sound* alike but are
different bugs — on purpose.

```bash
# 1. Run the end-to-end demo (writes demo_state.json)
python -m issuepilot demo

# 2. Inspect the queue again later, summaries only
python -m issuepilot review --state demo_state.json --brief

# 3. Act on a suggestion — approving only prints the manual steps
python -m issuepilot approve duplicate-980af815 --state demo_state.json

# 4. Permanently ignore a suggestion you disagree with
python -m issuepilot ignore duplicate-22a7355f --state demo_state.json
```

Ignored suggestions never resurface, even after re-running `analyze`.

Against a real repository (read-only, pagination + rate-limit handling +
ETag caching included):

```bash
python -m issuepilot fetch OWNER REPO --out issues.json            # unauthenticated is fine for small repos
python -m issuepilot analyze --issues issues.json
python -m issuepilot review
```

`--token` accepts a read-only token if you hit the unauthenticated rate
limit. The token is used for the request only and is never stored.

## Features

- **Duplicate candidates** — pairwise similarity over titles and bodies.
  A pair is surfaced only when it shares enough distinctive terms *or* a
  strong overlap of error/identifier tokens (`KeyError`, `api_key`,
  `config.yaml`, version numbers). Similar wording alone is never enough,
  can never reach high confidence, and the suggested action always says to
  read both issues first.
- **Missing reproduction details** — bug-looking reports are checked for
  steps to reproduce, expected/actual behavior, and environment/version info;
  the suggestion lists exactly what is missing and what is present.
- **Label suggestions** — keyword rules for `bug`, `enhancement`,
  `documentation`, `performance`, `question`, with the matched patterns shown
  as evidence. Labels already on the issue are not re-suggested.
- **Review queue** — persistent JSON state; suggestions are `pending`,
  `approved`, or `ignored`. Every suggestion shows a preview of the proposed
  action. v1 performs **no** writes on GitHub: approving prints manual steps.
- **GitHub client** — read-only GETs, `Link`-header pagination (with a
  `--max-pages` guard), rate-limit retries honoring `Retry-After` /
  `X-RateLimit-Reset`, and an ETag/`If-None-Match` file cache so re-runs are
  cheap.

## Privacy note

IssuePilot is read-only and local-first. It only ever sends GET requests to
`api.github.com`; issue data, the HTTP cache (`issuepilot_cache.json`), and
the review queue (`issuepilot_state.json`) stay on your disk. There is no
telemetry, no third-party service, and your token (if you pass one) is never
written to disk. Delete the two JSON files to remove all local state.

## Known limitations

- English-language keyword rules; similarity is token-based (no embeddings),
  so paraphrased duplicates with no shared identifiers may be missed.
- Duplicate detection is pairwise (O(n²)) — fine for small repos, not for
  thousands of open issues.
- v1 cannot post comments, apply labels, or close issues — by design.
- Fetching requires network access to GitHub; everything else is offline.

## Roadmap

1. Optional one-command application of approved actions (comments/labels)
   with a per-action dry-run diff, still defaulting to preview-only.
2. Configurable label rules and repro-check sections via a config file.
3. Incremental analysis (only new/changed issues) using the ETag cache.

## Development

Pure Python 3.9+ standard library. Run the tests (fully offline, mocked
GitHub transport):

```bash
python -m unittest discover -v
```

## License

MIT — see [LICENSE](LICENSE).
