# Releasing

Releases are published to PyPI by CI. Nothing is built or uploaded from a
developer machine.

## Process

1. Bump `version` in `pyproject.toml` and add a `CHANGELOG.md` entry for the new
   version.
2. Commit as `Bump version to X.Y.Z`.
3. Tag that commit `vX.Y.Z` and push the branch and the tag.
4. Create a GitHub release for the tag — for example:

       gh release create vX.Y.Z --title vX.Y.Z --notes "..."

   This is the publish step; everything after it is automatic.

## What CI does

Publishing a GitHub release triggers `.github/workflows/publish.yml`. It
confirms the Tests workflow passed for that commit (re-running the suite if
there is no prior successful run), builds the sdist and wheel, and uploads them
with `pypa/gh-action-pypi-publish`.

Upload uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/)
over OIDC, scoped to the `pypi` environment. There is no API token to hold, and
no credential that would let a local upload succeed.

## Do not publish locally

Do not run `python -m build`, `pyproject-build`, or `twine upload` as part of a
release. A hand-built artifact skips the test gate and breaks the
trusted-publishing provenance chain, and a PyPI version number can never be
reused once uploaded — a mistaken upload cannot be corrected, only followed by
another version.

Local builds also leave artifacts in `dist/`, which is not cleaned between
builds; a later `twine upload dist/*` would sweep up whatever is sitting there.
If you build locally for some other reason, delete the artifacts afterward.
