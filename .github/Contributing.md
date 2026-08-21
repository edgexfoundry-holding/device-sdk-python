# Contributing Guide

## Commits

All PRs must be squash merged via GitHub, using the conventional commit message format that is
automatically imported from the PR title.

Conventional commit messages must be prefixed with one of the following:

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only changes
- `style:` - Changes that do not affect the meaning of the code (white-space, formatting, etc.)
- `refactor:` - A code change that neither fixes a bug nor adds a feature
- `perf:` - A code change that improves performance
- `test:` - Adding missing tests or correcting existing tests
- `chore:` - Changes to the build process or auxiliary tools and libraries

The commit message must contain `Closes #<issue number>` when there is a related issue.

## Pull Requests

- All PRs must be opened against the `main` branch.
- PR title must follow the conventional commit message format described above.
- New features and bug fixes must be covered by unit tests (pytest).
- Do not introduce new dependencies without justification; see the
  [vetting process for 3rd party dependencies](https://wiki.edgexfoundry.org/display/FA/Vetting+Process+for+3rd+Party+Dependencies).

## Development Setup

```bash
git clone https://github.com/edgexfoundry/device-sdk-python
cd device-sdk-python
pip install -e ".[dev]"
```

## Running Tests

```bash
python -m pytest tests/ -q
```

## Code Style

- Python 3.10+ is required.
- Follow the naming and import conventions in [DEVELOPMENT.md](../DEVELOPMENT.md).
- Every public function/class requires a docstring that references the corresponding
  Go SDK source location (see the docstring template in DEVELOPMENT.md).
