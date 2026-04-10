# Auto Research Phase 2 ALCF Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real OpenSSH-based Polaris bridge to `auto-research` with `attach`, `check`, `status`, and `detach` CLI commands plus deterministic tests.

**Architecture:** Keep the bridge narrow. `settings.py` loads typed bridge config, `schemas.py` defines bridge result types, `bridge/ssh_master.py` builds and runs `ssh` control-master commands, `bridge/health.py` maps raw command results into bridge states, and `cli.py` remains a thin layer over a bridge service. Control-path filesystem checks are optional: only inspect a socket path when the configured path is static, otherwise rely on `ssh -O check` result patterns.

**Tech Stack:** Python 3.11+, Typer, PyYAML, subprocess, pytest

---

### Task 1: Extend Settings For Polaris Bridge Config

**Files:**
- Modify: `conf/polaris.yaml`
- Modify: `src/autoresearch/settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing bridge settings test**

```python
from pathlib import Path

from autoresearch.settings import load_settings


def test_load_settings_reads_bridge_config(tmp_path: Path) -> None:
    repo_root = tmp_path
    conf_dir = repo_root / "conf"
    conf_dir.mkdir()
    (conf_dir / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )
    (conf_dir / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n",
        encoding="utf-8",
    )

    settings = load_settings(repo_root=repo_root)

    assert settings.bridge.alias == "polaris-relay"
    assert settings.bridge.host == "polaris-login-04.hsn.cm.polaris.alcf.anl.gov"
    assert settings.bridge.user == "zzq"
    assert settings.bridge.control_path == "~/.ssh/cm-%C"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_settings.py::test_load_settings_reads_bridge_config -v`
Expected: FAIL because `Settings` has no `bridge` field yet

- [ ] **Step 3: Write the minimal bridge settings implementation**

`conf/polaris.yaml`

```yaml
bridge:
  alias: polaris-relay
  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov
  user: ALCF_USERNAME
  control_path: ~/.ssh/cm-%C
  server_alive_interval: 60
  server_alive_count_max: 3
  connect_timeout: 15
```

`src/autoresearch/settings.py`

```python
@dataclass(frozen=True)
class BridgeSettings:
    alias: str
    host: str
    user: str
    control_path: str
    server_alive_interval: int
    server_alive_count_max: int
    connect_timeout: int


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str
    bridge: BridgeSettings


def load_settings(repo_root: Path | None = None) -> Settings:
    resolved_root = resolve_repo_root(repo_root=repo_root)
    app_config = yaml.safe_load((resolved_root / "conf" / "app.yaml").read_text(encoding="utf-8"))
    bridge_config = yaml.safe_load((resolved_root / "conf" / "polaris.yaml").read_text(encoding="utf-8"))
    state_dir = _resolve_path(resolved_root, app_config["paths"]["state_dir"])
    cache_dir = _resolve_path(resolved_root, app_config["paths"]["cache_dir"])
    logs_dir = _resolve_path(resolved_root, app_config["paths"]["logs_dir"])
    db_path = _resolve_path(resolved_root, app_config["paths"]["db_path"])
    return Settings(
        app_name=app_config["app_name"],
        paths=AppPaths(
            repo_root=resolved_root,
            state_dir=state_dir,
            cache_dir=cache_dir,
            logs_dir=logs_dir,
            db_path=db_path,
        ),
        remote_root=app_config["remote"]["root"],
        bridge=BridgeSettings(
            alias=bridge_config["bridge"]["alias"],
            host=bridge_config["bridge"]["host"],
            user=bridge_config["bridge"]["user"],
            control_path=bridge_config["bridge"]["control_path"],
            server_alive_interval=bridge_config["bridge"]["server_alive_interval"],
            server_alive_count_max=bridge_config["bridge"]["server_alive_count_max"],
            connect_timeout=bridge_config["bridge"]["connect_timeout"],
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_settings.py::test_load_settings_reads_bridge_config -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add conf/polaris.yaml src/autoresearch/settings.py tests/test_settings.py
git commit -m "feat: add typed bridge settings"
```

### Task 2: Add Bridge Result Schemas And Health Mapping

**Files:**
- Modify: `src/autoresearch/schemas.py`
- Create: `src/autoresearch/bridge/__init__.py`
- Create: `src/autoresearch/bridge/health.py`
- Create: `tests/test_bridge.py`

- [ ] **Step 1: Write the failing bridge health tests**

```python
from autoresearch.bridge.health import classify_bridge_status
from autoresearch.schemas import CommandResult


def test_classify_bridge_status_reports_attached() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=0,
        stdout="Master running",
        stderr="",
        duration_seconds=0.12,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.state == "ATTACHED"


def test_classify_bridge_status_reports_detached_for_no_master() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="Control socket connect(/tmp/cm): No such file or directory",
        duration_seconds=0.07,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=None)

    assert status.state == "DETACHED"


def test_classify_bridge_status_reports_stale_for_failed_check_with_socket() -> None:
    result = CommandResult(
        args=("ssh", "-O", "check", "polaris-relay"),
        returncode=255,
        stdout="",
        stderr="mux_client_request_session: read from master failed: Broken pipe",
        duration_seconds=0.09,
    )

    status = classify_bridge_status(alias="polaris-relay", check_result=result, control_path_exists=True)

    assert status.state == "STALE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_bridge.py::test_classify_bridge_status_reports_attached -v`
Expected: FAIL because bridge schemas and health module do not exist yet

- [ ] **Step 3: Write the minimal schemas and health classifier**

`src/autoresearch/schemas.py`

```python
@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class BridgeStatusResult:
    alias: str
    state: str
    explanation: str
    command_result: CommandResult | None = None
    control_path_exists: bool | None = None
```

`src/autoresearch/bridge/health.py`

```python
from autoresearch.schemas import BridgeStatusResult, CommandResult


DETACHED_PATTERNS = (
    "no such file or directory",
    "no master running",
    "control socket connect",
)


def classify_bridge_status(
    alias: str,
    check_result: CommandResult,
    control_path_exists: bool | None,
) -> BridgeStatusResult:
    combined = f"{check_result.stdout}\n{check_result.stderr}".lower()
    if check_result.returncode == 0:
        return BridgeStatusResult(
            alias=alias,
            state="ATTACHED",
            explanation="OpenSSH control master is healthy.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    if control_path_exists:
        return BridgeStatusResult(
            alias=alias,
            state="STALE",
            explanation="Control socket exists but the master check failed.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    if any(pattern in combined for pattern in DETACHED_PATTERNS):
        return BridgeStatusResult(
            alias=alias,
            state="DETACHED",
            explanation="No active OpenSSH control master is attached.",
            command_result=check_result,
            control_path_exists=control_path_exists,
        )
    return BridgeStatusResult(
        alias=alias,
        state="STALE",
        explanation="Bridge state is abnormal and requires operator attention.",
        command_result=check_result,
        control_path_exists=control_path_exists,
    )
```

`src/autoresearch/bridge/__init__.py`

```python
from autoresearch.bridge.health import classify_bridge_status

__all__ = ["classify_bridge_status"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/schemas.py src/autoresearch/bridge/__init__.py src/autoresearch/bridge/health.py tests/test_bridge.py
git commit -m "feat: add bridge health classification"
```

### Task 3: Add SSH Master Command Construction And Runner

**Files:**
- Create: `src/autoresearch/bridge/ssh_master.py`
- Modify: `tests/test_bridge.py`

- [ ] **Step 1: Write the failing ssh master tests**

```python
from autoresearch.bridge.ssh_master import SSHMasterClient
from autoresearch.settings import BridgeSettings
from autoresearch.schemas import CommandResult


def test_attach_uses_master_background_flags() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(args: tuple[str, ...]) -> CommandResult:
        calls.append(args)
        return CommandResult(args=args, returncode=0, stdout="", stderr="", duration_seconds=0.01)

    client = SSHMasterClient(
        settings=BridgeSettings(
            alias="polaris-relay",
            host="host",
            user="user",
            control_path="~/.ssh/cm-%C",
            server_alive_interval=60,
            server_alive_count_max=3,
            connect_timeout=15,
        ),
        runner=fake_runner,
    )

    client.attach()

    assert calls == [("ssh", "-MNf", "polaris-relay")]


def test_check_uses_mux_check_operation() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(args: tuple[str, ...]) -> CommandResult:
        calls.append(args)
        return CommandResult(args=args, returncode=255, stdout="", stderr="", duration_seconds=0.01)

    client = SSHMasterClient(
        settings=BridgeSettings(
            alias="polaris-relay",
            host="host",
            user="user",
            control_path="~/.ssh/cm-%C",
            server_alive_interval=60,
            server_alive_count_max=3,
            connect_timeout=15,
        ),
        runner=fake_runner,
    )

    client.check()

    assert calls == [("ssh", "-O", "check", "polaris-relay")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_bridge.py::test_attach_uses_master_background_flags -v`
Expected: FAIL because `SSHMasterClient` does not exist yet

- [ ] **Step 3: Write the minimal ssh master client**

`src/autoresearch/bridge/ssh_master.py`

```python
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import time

from autoresearch.schemas import BridgeStatusResult, CommandResult
from autoresearch.settings import BridgeSettings
from autoresearch.bridge.health import classify_bridge_status


CommandRunner = Callable[[tuple[str, ...]], CommandResult]


def run_command(args: tuple[str, ...]) -> CommandResult:
    started = time.perf_counter()
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=time.perf_counter() - started,
    )


@dataclass
class SSHMasterClient:
    settings: BridgeSettings
    runner: CommandRunner = run_command

    def attach(self) -> CommandResult:
        return self.runner(("ssh", "-MNf", self.settings.alias))

    def check(self) -> CommandResult:
        return self.runner(("ssh", "-O", "check", self.settings.alias))

    def detach(self) -> CommandResult:
        return self.runner(("ssh", "-O", "exit", self.settings.alias))

    def status(self) -> BridgeStatusResult:
        check_result = self.check()
        return classify_bridge_status(
            alias=self.settings.alias,
            check_result=check_result,
            control_path_exists=self._control_path_exists(),
        )

    def _control_path_exists(self) -> bool | None:
        raw = self.settings.control_path
        if "%" in raw:
            return None
        return Path(os.path.expanduser(raw)).exists()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/bridge/ssh_master.py tests/test_bridge.py
git commit -m "feat: add ssh master bridge client"
```

### Task 4: Wire Bridge Commands Into The CLI

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing bridge CLI tests**

```python
from pathlib import Path

from typer.testing import CliRunner

from autoresearch.cli import app
from autoresearch.schemas import BridgeStatusResult, CommandResult


runner = CliRunner()


def test_bridge_status_prints_normalized_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf" / "app.yaml").write_text(
        "app_name: auto-research\n"
        "paths:\n"
        "  state_dir: state\n"
        "  cache_dir: cache\n"
        "  logs_dir: logs\n"
        "  db_path: state/autoresearch.db\n"
        "remote:\n"
        "  root: /eagle/lc-mpi/Zhiqing/auto-research\n",
        encoding="utf-8",
    )
    (tmp_path / "conf" / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: host\n"
        "  user: user\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n",
        encoding="utf-8",
    )

    class FakeBridgeService:
        def status(self) -> BridgeStatusResult:
            return BridgeStatusResult(
                alias="polaris-relay",
                state="DETACHED",
                explanation="No active OpenSSH control master is attached.",
                command_result=CommandResult(
                    args=("ssh", "-O", "check", "polaris-relay"),
                    returncode=255,
                    stdout="",
                    stderr="Control socket connect(/tmp/cm): No such file or directory",
                    duration_seconds=0.01,
                ),
                control_path_exists=None,
            )

    monkeypatch.setattr("autoresearch.cli.build_bridge_service", lambda settings: FakeBridgeService())

    result = runner.invoke(app, ["bridge", "status"])

    assert result.exit_code == 0
    assert "DETACHED" in result.stdout
    assert "polaris-relay" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_bridge_status_prints_normalized_state -v`
Expected: FAIL because `bridge` commands and `build_bridge_service` do not exist yet

- [ ] **Step 3: Write the minimal bridge CLI wiring**

`src/autoresearch/cli.py`

```python
from autoresearch.bridge.ssh_master import SSHMasterClient


bridge_app = typer.Typer(help="ALCF bridge commands.")
app.add_typer(bridge_app, name="bridge")


def build_bridge_service(settings):
    return SSHMasterClient(settings.bridge)


def _echo_bridge_status(status) -> None:
    typer.echo(f"{status.alias}\t{status.state}\t{status.explanation}")


@bridge_app.command("attach")
def bridge_attach() -> None:
    settings = load_settings()
    service = build_bridge_service(settings)
    result = service.attach()
    if result.returncode == 0:
        typer.echo(f"{settings.bridge.alias}\tATTACHED\tSSH control master attached.")
        return
    typer.echo(result.stderr or result.stdout)
    raise typer.Exit(result.returncode)


@bridge_app.command("check")
def bridge_check() -> None:
    settings = load_settings()
    service = build_bridge_service(settings)
    status = service.status()
    _echo_bridge_status(status)
    if status.state == "ATTACHED":
        return
    raise typer.Exit(1)


@bridge_app.command("status")
def bridge_status() -> None:
    settings = load_settings()
    service = build_bridge_service(settings)
    _echo_bridge_status(service.status())


@bridge_app.command("detach")
def bridge_detach() -> None:
    settings = load_settings()
    service = build_bridge_service(settings)
    result = service.detach()
    if result.returncode == 0:
        typer.echo(f"{settings.bridge.alias}\tDETACHED\tSSH control master exited.")
        return
    status = service.status()
    _echo_bridge_status(status)
    if status.state == "DETACHED":
        return
    raise typer.Exit(result.returncode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py::test_bridge_status_prints_normalized_state -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/cli.py tests/test_cli.py
git commit -m "feat: add bridge cli commands"
```

### Task 5: Document Bridge Usage And Failure Modes

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `README.md`

- [ ] **Step 1: Write the failing docs smoke test**

```python
from pathlib import Path


def test_bridge_docs_are_present() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    architecture = (repo_root / "docs" / "architecture.md").read_text(encoding="utf-8")
    runbook = (repo_root / "docs" / "runbook.md").read_text(encoding="utf-8")

    assert "bridge" in architecture.lower()
    assert "autoresearch bridge attach" in runbook
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_bridge_docs_are_present -v`
Expected: FAIL because bridge docs text is not present yet

- [ ] **Step 3: Write the minimal docs**

`docs/architecture.md`

```markdown
# Architecture

Phase 0 + 1 implement the local control-plane foundation only: config loading, SQLite bootstrap, and run registry CLI.

## Phase 2 bridge

The ALCF bridge is a narrow OpenSSH control-master layer. It manages `attach`, `check`, `status`, and `detach` only. It does not submit jobs or run arbitrary remote commands.
```

`docs/runbook.md`

```markdown
# Runbook

## Local bootstrap

1. Create a virtual environment.
2. Install with `pip install -e .[dev]`.
3. Initialize the database.
4. Create and list runs through the CLI.

## Bridge usage

Run `autoresearch bridge attach` to establish the control-master connection.
Run `autoresearch bridge check` to probe the mux health directly.
Run `autoresearch bridge status` for a normalized bridge state summary.
Run `autoresearch bridge detach` to exit the master connection cleanly.
```

`README.md`

```markdown
## Bridge commands

- `python -m autoresearch.cli bridge attach`
- `python -m autoresearch.cli bridge check`
- `python -m autoresearch.cli bridge status`
- `python -m autoresearch.cli bridge detach`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py::test_bridge_docs_are_present -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture.md docs/runbook.md tests/test_cli.py
git commit -m "docs: add bridge usage guidance"
```

### Task 6: Final Verification Sweep

**Files:**
- Modify: any files required only if verification reveals defects

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all tests PASS

- [ ] **Step 2: Run bridge CLI help**

Run: `PYTHONPATH=src .venv/bin/python -m autoresearch.cli bridge --help`
Expected: help text shows `attach`, `check`, `status`, and `detach`

- [ ] **Step 3: Check repository status**

Run: `git status --short --branch`
Expected: clean working tree on the feature branch

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "test: verify phase2 bridge implementation"
```

## Self-Review

- Spec coverage: this plan covers bridge config loading, typed result schemas, health classification, ssh command construction, bridge CLI commands, and bridge docs. It intentionally excludes PBS, file transfer, and generic remote execution.
- Placeholder scan: no `TODO`, `TBD`, or deferred implementation markers remain.
- Type consistency: the plan uses `BridgeSettings`, `CommandResult`, `BridgeStatusResult`, and `SSHMasterClient` consistently across settings, health classification, CLI wiring, and tests.
