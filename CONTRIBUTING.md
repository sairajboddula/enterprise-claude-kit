# Contributing to enterprise-claude-kit

Thank you for taking the time to contribute. This document explains how to get from zero to a merged pull request as quickly as possible.

---

## Table of Contents

1. [Dev environment setup](#1-dev-environment-setup)
2. [Running the test suite](#2-running-the-test-suite)
3. [Code style](#3-code-style)
4. [Project structure](#4-project-structure)
5. [PR guidelines](#5-pr-guidelines)
6. [Reporting issues](#6-reporting-issues)
7. [Release process](#7-release-process)

---

## 1. Dev environment setup

**Requirements:** Python 3.11 or 3.12, Git.

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/<your-handle>/enterprise-claude-kit.git
cd enterprise-claude-kit

# 2. Create an isolated virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install the package in editable mode with all dev dependencies
pip install -e ".[dev]"

# 4. Copy the example env file and add your API key
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY=sk-ant-…

# 5. Verify everything works
python -m pytest --tb=short -q
```

You should see `66 passed` (or more, if tests have been added). If anything fails before you have made any changes, open an issue — that is a bug in the project setup, not your environment.

### Optional: pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

This installs git hooks that run `ruff` and `mypy` automatically before each commit, so you catch issues before pushing.

---

## 2. Running the test suite

### All tests

```bash
python -m pytest
```

### Specific module

```bash
python -m pytest tests/test_governance.py -v
```

### With coverage report

```bash
python -m pytest --cov=enterprise_claude --cov-report=term-missing
```

### Skip tests that require an API key

Tests that make real Anthropic API calls are decorated with `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), ...)`. They run when the key is present and are silently skipped otherwise — so the full suite is always runnable offline.

### mypy

```bash
python -m mypy enterprise_claude/ --python-version 3.12 --ignore-missing-imports
```

Expected output: `Success: no issues found in 8 source files`.

### ruff

```bash
python -m ruff check .          # lint
python -m ruff check . --fix   # auto-fix safe issues
```

Expected output: `All checks passed!`

### Run everything at once (mirrors CI)

```bash
python -m ruff check . && \
python -m mypy enterprise_claude/ --python-version 3.12 --ignore-missing-imports && \
python -m pytest --tb=short -q
```

All three must exit `0` before you open a PR.

---

## 3. Code style

The project enforces style mechanically so humans don't have to argue about it.

### Formatting and lint — ruff

Configuration lives in `pyproject.toml` (`[tool.ruff]`). Key rules:

| Rule group | What it enforces |
|------------|------------------|
| `E`, `W`   | PEP 8 whitespace and style |
| `F`        | pyflakes — unused imports, undefined names |
| `I`        | isort import ordering |
| `UP`       | pyupgrade — prefer modern syntax (`X \| Y`, `datetime.UTC`) |
| `RUF`      | Ruff-specific rules (`__all__` ordering, etc.) |
| `S`        | bandit — common security anti-patterns |
| `ASYNC`    | asyncio correctness |
| `BLE`      | ban bare `except Exception` without justification |
| `SIM`      | simplification — collapse nested `with`, redundant conditions |

If a rule fires on code that is deliberately non-standard, suppress it with an inline comment **and a reason**:

```python
with open(path, "w") as fh:  # noqa: ASYNC230 — intentional blocking write; no trio dep
```

Bare `# noqa` without a code is not accepted.

### Type annotations — mypy

- All public functions and methods must be fully annotated (parameters + return type).
- Use built-in generic syntax (`list[str]`, `dict[str, Any]`, `X | Y`) — not `List`, `Dict`, `Optional`, `Union`.
- `Any` is allowed only where a third-party type is genuinely unknown. Add a comment explaining why.
- `# type: ignore` requires a narrowing code (`# type: ignore[assignment]`) and a one-line comment.

### General style conventions

```python
# ✅ Good — explicit, no ambiguity
async def get_cost_summary(
    self,
    start_date: datetime,
    end_date: datetime,
) -> CostSummary:
    ...

# ❌ Avoid — implicit return type, no annotation on param
async def get_cost_summary(self, start, end):
    ...
```

- **Line length:** 100 characters (enforced by ruff).
- **Docstrings:** Google style for public classes and functions. One-liners are fine for simple helpers.
- **Async:** All I/O must be async. Never call blocking functions (file open, `requests`, `time.sleep`) inside an `async def` without `# noqa: ASYNC230` justification.
- **Pydantic models:** Use `model_config = ConfigDict(...)` — not the deprecated `class Config` inner class.
- **Exceptions:** Raise specific subclasses of `EnterpriseClaudeError`. Never raise bare `Exception` or `RuntimeError` from library code.
- **Logging:** Use the module-level `logger = logging.getLogger(__name__)` pattern. No `print()` in library code (examples and CLI are exempt).

---

## 4. Project structure

```
enterprise_claude/        ← library source (the pip-installable package)
│
├── __init__.py           ← public API re-exports + exception hierarchy
├── governance.py         ← GovernanceLayer, GovernanceConfig, GovernanceResult
├── orchestrator.py       ← AgentOrchestrator, Agent
├── token_monitor.py      ← TokenMonitor, MonitorConfig, BudgetStatus, CostSummary
├── audit.py              ← AuditLogger, AuditEvent, AuditFilter
├── adoption_tracker.py   ← AdoptionTracker, Wave, WaveProgress, Persona
├── mcp_connectors.py     ← MCPConnectorRegistry, ConnectorConfig, AuthType
└── cli.py                ← `ecl` Typer CLI

tests/                    ← pytest suite (mirrors the source structure)
├── test_governance.py
├── test_orchestrator.py
└── test_token_monitor.py

examples/                 ← runnable demos (require ANTHROPIC_API_KEY)
├── basic_governed_agent.py
├── clinical_trial_agent.py
└── sdlc_accelerator.py   ← offline, no API key needed

.github/workflows/
├── ci.yml                ← lint + type-check + test on every push/PR
└── publish.yml           ← build + publish on version tags
```

### Where to add new code

| What you're adding | Where it goes |
|--------------------|---------------|
| New governance policy | `governance.py` — extend `GovernanceLayer` or add a hook |
| New cost metric | `token_monitor.py` — extend `CostSummary` or `MonitorConfig` |
| New audit query | `audit.py` — extend `AuditFilter` |
| New wave feature | `adoption_tracker.py` |
| New MCP connector | `mcp_connectors.py` — add to `_BUILTIN_CONNECTORS` dict |
| New CLI command | `cli.py` — add a Typer command group |
| New public class/function | Re-export from `__init__.py` and add to `__all__` |

---

## 5. PR guidelines

### Before you open a PR

- [ ] `python -m ruff check .` → clean
- [ ] `python -m mypy enterprise_claude/ --python-version 3.12 --ignore-missing-imports` → clean
- [ ] `python -m pytest --tb=short -q` → all passing
- [ ] New behaviour has tests; bug fixes have a regression test
- [ ] Docstrings updated for any changed public API
- [ ] `CHANGELOG.md` entry added under `## Unreleased` (if it exists)

### PR title format

Use [Conventional Commits](https://www.conventionalcommits.org/) style:

```
feat: add PII redaction to audit event payloads
fix: handle None return from aiosqlite fetchone()
docs: add GxP mode example to README
chore: bump ruff to 0.5
test: add regression for WaveGateError threshold edge case
```

The type prefix (`feat`, `fix`, `docs`, `chore`, `test`, `refactor`, `perf`) is used to auto-generate the GitHub Release changelog, so please be precise.

### PR description

Use this template:

```markdown
## What and why

<!-- One paragraph. What does this PR do, and why is it needed? -->

## How

<!-- Brief description of the implementation approach. -->

## Testing

<!-- How did you verify this works? Which test(s) cover it? -->

## Breaking changes

<!-- None / list any API changes that require a major version bump. -->
```

### Review process

- All PRs require at least one approving review before merge.
- CI must be green (ruff + mypy + pytest on both Python 3.11 and 3.12).
- Prefer small, focused PRs over large ones — they review faster and are easier to revert if something goes wrong.
- Squash-merge is the default strategy; your commit history within the branch does not need to be clean.

---

## 6. Reporting issues

### Bug reports

Please include:

1. **Python version** (`python --version`)
2. **Package version** (`pip show enterprise-claude-kit`)
3. **Minimal reproducible example** — the shortest code that triggers the bug
4. **Full traceback** — paste it, don't summarise it
5. **Expected vs. actual behaviour**

### Feature requests

Open an issue tagged `enhancement`. Describe:

- The problem you're trying to solve (not just the solution you want)
- Any workaround you're currently using
- Whether you'd like to implement it yourself

For significant API changes, discuss in an issue **before** opening a PR — it saves everyone time if the approach needs rethinking.

---

## 7. Release process

Releases are made by maintainers. The process is:

```bash
# 1. Update version in pyproject.toml
#    version = "0.2.0"

# 2. Update CHANGELOG.md — move "Unreleased" items under "## v0.2.0 (YYYY-MM-DD)"

# 3. Commit
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release v0.2.0"

# 4. Tag — this triggers the publish workflow
git tag v0.2.0
git push origin main --tags
```

The `publish.yml` workflow then:
1. Runs the full CI suite
2. Verifies the git tag matches `pyproject.toml`
3. Builds the wheel and sdist with hatchling
4. Publishes to PyPI via OIDC trusted publisher
5. Creates a GitHub Release with auto-generated notes

**Do not push version tags without a passing CI run.** The workflow will fail at step 1 and leave a broken tag in the repo.

---

## Code of Conduct

Be kind. Be direct. Critique code, not people. We are all here to build something useful.
