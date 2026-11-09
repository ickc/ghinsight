# Repository notes for future agents

- Use `uv` for PyPI packaging and release checks in this repository.
- The repository also defines a `pixi` environment in `pyproject.toml`; keep
  package runtime dependencies mirrored in `[project.dependencies]` and
  `[tool.pixi.dependencies]` so uv resolves from PyPI while pixi resolves from
  conda-forge.
- Prefer commands of the form:

```bash
uv run python ...
uv run ghinsight ...
pixi run check
```

- Do not run bare `python` for project tasks unless the user explicitly asks for
  it.
- Keep this package organization-agnostic. Organization names, private data
  paths, billing assumptions, and one-off repository quirks should live in a
  consuming repository's config, not in this library.

## Architecture (post-refactor)

- Three functional stages: `fetch` (network) → `dataset` (the canonical tidy
  IR, `ActionsDataset`) → analysis (`metrics`, `analysis`, `charts`, `report`).
- The intermediate representation is a tidy per-job table, not GitHub's
  aggregate CSVs. CSVs are an export (`csv_export`) for `compare` only.
- Prefer the functional style already in place: frozen dataclasses, pure
  functions, type hints on signatures, declarative specs over imperative loops.
- Derived measures (billed minutes, durations, OS) are defined once, as
  properties on the `Job`/`Run` records in `domain.py`.

## Testing & docs

- `uv run pytest` runs the offline suite against `tests/synthetic.py` (no token
  or network needed). Add fixtures there rather than depending on a live org.
- `uv run --group docs sphinx-build -b html -W docs docs/_build/html` builds the
  docs (AutoAPI + MyST-NB tutorials); CI builds with `-W`, so keep it warning-free.
