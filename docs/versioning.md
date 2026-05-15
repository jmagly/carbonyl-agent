# Versioning

`carbonyl-agent` uses **CalVer** — `YYYY.M.PATCH` — matching the convention used in [`aiwg`](https://github.com/jmagly/aiwg), `fortemi`, `sysops`, and other Roctinam repos.

## Format

```
YYYY.M.PATCH
```

| Segment | Meaning | Example |
|---------|---------|---------|
| `YYYY` | Four-digit year of the release cut | `2026` |
| `M` | Month of the release cut, **no leading zero** | `5` (May), `12` (December) |
| `PATCH` | Patch counter within that month, starting at `0`, **no leading zero** | `0`, `1`, `15` |

No prerelease suffixes (`aN`/`bN`/`rcN`). Each tag is the current state of `main` at that calendar moment. Quality gating happens through CI and changelog, not version metadata.

### Examples

| Version | Meaning |
|---------|---------|
| `2026.5.0` | First release in May 2026 |
| `2026.5.1` | Second release in May 2026 |
| `2026.12.0` | First release in December 2026 |

### Forbidden forms

- `2026.05.0` — leading zero in month, **rejected by npm and PEP 440 strict parsers**
- `2026.5.05` — leading zero in patch
- `26.5.0` — two-digit year
- `2026.5` — missing patch segment

## Tag format

Git tags use a leading `v`:

```
v2026.5.0
v2026.5.1
```

The release workflows (`.gitea/workflows/release.yml`, `.github/workflows/release.yml`) trigger on `v*` and validate that the tag matches `pyproject.toml`'s `version` field.

## Why CalVer

- **Calendar-driven cadence.** Releases happen when the work is ready, not on a marketing schedule. CalVer reflects that honestly.
- **No SemVer compatibility contract overhead** for a rolling alpha → beta → stable lifecycle. The compatibility surface is documented in CHANGELOG, not encoded in the version number.
- **Fleet alignment.** `aiwg` and related Roctinam repos all use the same scheme — version numbers across repos sort consistently, and the release tooling is shared.

## PEP 440 compliance

PEP 440 (Python packaging) accepts CalVer without modification. `2026.5.0` parses as a normal `MAJOR.MINOR.PATCH` release. PyPI accepts it.

## CHANGELOG convention

CHANGELOG entries follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):

```markdown
## [Unreleased]

## [2026.5.0] - 2026-05-15
```

The version in `[brackets]` matches `pyproject.toml`. The ISO date is the release date.

## Patch numbering within a month

Patch starts at `0` for the first release of the month and increments. A `0` patch is normal — it does not imply "major release". The next release after `2026.5.0` is `2026.5.1`; the next release in June is `2026.6.0` regardless of whether May had patches.

## See also

- [PEP 440 — Version Identification](https://peps.python.org/pep-0440/)
- [CalVer specification](https://calver.org/)
- [AIWG versioning rule](https://github.com/jmagly/aiwg/blob/main/agentic/code/frameworks/sdlc-complete/rules/versioning.md) — canonical reference for the Roctinam-fleet pattern
