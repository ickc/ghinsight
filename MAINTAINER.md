# Maintainer Guide

This package is intended to be published to PyPI as `ghinsight`.

## Local Release Checks

Use `uv` from the repository root:

```bash
uv sync
uv build
uv run twine check dist/*
```

Also check the pixi environment after dependency changes:

```bash
pixi lock
pixi run check
pixi run help
```

Inspect the contents before uploading:

```bash
tar -tzf dist/ghinsight-*.tar.gz | sort | head -50
unzip -l dist/ghinsight-*.whl | sort | head -50
```

## First PyPI Release

1. Confirm the package name in `pyproject.toml` is available on PyPI. If
   `ghinsight` is already taken, rename the project before the first
   release.
2. Confirm `README.md`, `LICENSE`, project URLs, dependencies, and the console
   script metadata are correct.
3. Create a PyPI account if needed and enable 2FA.
4. Configure a PyPI Trusted Publisher for GitHub Actions.
   - Project name: `ghinsight`
   - Owner: the GitHub repository owner
   - Repository: the GitHub repository name
   - Workflow filename: `release.yml`
   - Environment: `pypi`
5. Push the repository to GitHub.
6. Create a GitHub Release for the version in `pyproject.toml`.
7. The `release.yml` workflow will build the sdist/wheel with `uv build` and
   publish with `pypa/gh-action-pypi-publish`.

Trusted Publishing avoids storing a long-lived PyPI token in GitHub secrets.
The workflow needs `permissions: id-token: write`, and the `pypi` environment
should match the environment configured on PyPI.

## Ongoing Releases

1. Update `version` in `pyproject.toml`.
2. Update the README or changelog if user-facing behavior changed.
3. If runtime dependencies changed, update both `[project.dependencies]` and
   `[tool.pixi.dependencies]` in `pyproject.toml`, then regenerate `uv.lock`
   and `pixi.lock`.
4. Run local checks:

```bash
uv sync

uv build
uv run twine check dist/*
pixi lock
pixi run check
```

5. Commit the version/docs changes.
6. Tag or create a GitHub Release for the same version, for example `v0.1.1`.
7. Confirm the GitHub Actions release workflow publishes successfully.
8. Verify the package page and install path:

```bash
uv tool install --force ghinsight
ghinsight --help
```

## Useful References

- PyPI Trusted Publishing:
  https://docs.pypi.org/trusted-publishers/
- Publishing with a Trusted Publisher:
  https://docs.pypi.org/trusted-publishers/using-a-publisher/
- Python Packaging User Guide for GitHub Actions publishing:
  https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
