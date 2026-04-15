# Phase 3B Remote Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real, operator-visible Polaris probe workflow by extending the bridge with remote exec/copy primitives, bootstrapping the managed Eagle root, and submitting/polling one fixed built-in probe job.

**Architecture:** Keep the Phase 3B path narrow. `bridge/ssh_master.py` owns raw OpenSSH command construction, `bridge/remote_exec.py` and `bridge/remote_fs.py` enforce Phase 3B safety rules, `executor/polaris.py` and `executor/pbs.py` own probe request building plus scheduler command/parsing logic, and `cli.py` stays a thin shell that composes those services. The first real remote submission path is the built-in probe only; there is no generalized job submission in this phase.

**Tech Stack:** Python 3.11+, Typer, SQLite, pytest, OpenSSH (`ssh`/`scp`), fixture-driven scheduler parsing

---

## File Map

- Modify: `conf/polaris.yaml`
- Modify: `src/autoresearch/settings.py`
- Modify: `src/autoresearch/bridge/ssh_master.py`
- Create: `src/autoresearch/bridge/remote_exec.py`
- Create: `src/autoresearch/bridge/remote_fs.py`
- Modify: `src/autoresearch/executor/polaris.py`
- Modify: `src/autoresearch/executor/pbs.py`
- Modify: `src/autoresearch/runs/registry.py`
- Modify: `src/autoresearch/cli.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`
- Modify: `tests/test_settings.py`
- Modify: `tests/test_bridge.py`
- Create: `tests/test_remote_bootstrap.py`
- Create: `tests/test_probe_flow.py`
- Modify: `tests/test_cli.py`

## Task 1: Extend Phase 3B Settings And Typed Configuration

**Files:**
- Modify: `conf/polaris.yaml`
- Modify: `src/autoresearch/settings.py`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: Write failing settings tests for probe defaults**

Add these tests to `tests/test_settings.py`:

```python
from pathlib import Path

from autoresearch.settings import load_settings


def _write_phase3b_bridge_config(conf_dir: Path) -> None:
    (conf_dir / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n"
        "probe:\n"
        "  project: demo\n"
        "  queue: debug\n"
        "  walltime: 00:10:00\n",
        encoding="utf-8",
    )


def test_load_settings_reads_probe_defaults(tmp_path: Path) -> None:
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
    _write_phase3b_bridge_config(conf_dir)

    settings = load_settings(repo_root=repo_root)

    assert settings.probe.project == "demo"
    assert settings.probe.queue == "debug"
    assert settings.probe.walltime == "00:10:00"
```

- [ ] **Step 2: Run the settings tests to verify failure**

Run:

```bash
pytest tests/test_settings.py -q
```

Expected: `AttributeError` or missing-key failures because `Settings` does not expose `probe` yet.

- [ ] **Step 3: Add typed probe settings**

Update `src/autoresearch/settings.py`:

```python
@dataclass(frozen=True)
class ProbeSettings:
    project: str
    queue: str
    walltime: str


@dataclass(frozen=True)
class Settings:
    app_name: str
    paths: AppPaths
    remote_root: str
    bridge: BridgeSettings
    probe: ProbeSettings
```

Extend `load_settings()` so it reads:

```python
probe=ProbeSettings(
    project=bridge_config["probe"]["project"],
    queue=bridge_config["probe"]["queue"],
    walltime=bridge_config["probe"]["walltime"],
),
```

Update `conf/polaris.yaml` to include:

```yaml
probe:
  project: ALCF_PROJECT
  queue: debug
  walltime: 00:10:00
```

- [ ] **Step 4: Re-run settings tests**

Run:

```bash
pytest tests/test_settings.py -q
```

Expected: probe settings tests pass.

- [ ] **Step 5: Commit**

```bash
git add conf/polaris.yaml src/autoresearch/settings.py tests/test_settings.py
git commit -m "feat: add phase3b probe settings"
```

## Task 2: Add Bridge Exec And File-Copy Primitives

**Files:**
- Modify: `src/autoresearch/bridge/ssh_master.py`
- Modify: `tests/test_bridge.py`

- [ ] **Step 1: Write failing bridge primitive tests**

Add these tests to `tests/test_bridge.py`:

```python
def test_exec_uses_remote_ssh_command() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_runner(args: tuple[str, ...]) -> CommandResult:
        calls.append(args)
        return CommandResult(args=args, returncode=0, stdout="ok\n", stderr="", duration_seconds=0.01)

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

    result = client.exec("pwd")

    assert result.returncode == 0
    assert calls == [("ssh", "polaris-relay", "pwd")]


def test_copy_to_uses_scp_with_alias_destination() -> None:
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

    client.copy_to("/tmp/local.txt", "/eagle/demo/local.txt")

    assert calls == [("scp", "/tmp/local.txt", "polaris-relay:/eagle/demo/local.txt")]


def test_copy_from_uses_scp_with_alias_source() -> None:
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

    client.copy_from("/eagle/demo/remote.txt", "/tmp/local.txt")

    assert calls == [("scp", "polaris-relay:/eagle/demo/remote.txt", "/tmp/local.txt")]
```

- [ ] **Step 2: Run bridge tests to verify failure**

Run:

```bash
pytest tests/test_bridge.py -q
```

Expected: missing-method failures for `exec`, `copy_to`, and `copy_from`.

- [ ] **Step 3: Implement the bridge primitives**

Add these methods to `SSHMasterClient` in `src/autoresearch/bridge/ssh_master.py`:

```python
    def exec(self, remote_command: str) -> CommandResult:
        return self.runner(("ssh", self.settings.alias, remote_command))

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult:
        return self.runner(("scp", local_path, f"{self.settings.alias}:{remote_path}"))

    def copy_from(self, remote_path: str, local_path: str) -> CommandResult:
        return self.runner(("scp", f"{self.settings.alias}:{remote_path}", local_path))
```

- [ ] **Step 4: Re-run bridge tests**

Run:

```bash
pytest tests/test_bridge.py -q
```

Expected: the new bridge primitive tests pass with the existing bridge tests.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/bridge/ssh_master.py tests/test_bridge.py
git commit -m "feat: add bridge exec and copy primitives"
```

## Task 3: Add Safe Remote Exec And Remote Filesystem Bootstrap Services

**Files:**
- Create: `src/autoresearch/bridge/remote_exec.py`
- Create: `src/autoresearch/bridge/remote_fs.py`
- Create: `tests/test_remote_bootstrap.py`

- [ ] **Step 1: Write failing tests for safe remote exec and bootstrap planning**

Create `tests/test_remote_bootstrap.py`:

```python
from pathlib import Path

import pytest

from autoresearch.bridge.remote_exec import (
    RemoteBridgeError,
    copy_to_remote,
    ensure_remote_path_within_root,
    execute_remote_command,
)
from autoresearch.bridge.remote_fs import build_bootstrap_files, build_bootstrap_mkdir_command
from autoresearch.schemas import BridgeStatusResult, CommandResult


class FakeBridgeClient:
    def __init__(self, state: str = "ATTACHED") -> None:
        self._state = state
        self.exec_calls: list[str] = []
        self.copy_to_calls: list[tuple[str, str]] = []
        self.copy_from_calls: list[tuple[str, str]] = []

    def status(self) -> BridgeStatusResult:
        return BridgeStatusResult(
            alias="polaris-relay",
            state=self._state,  # type: ignore[arg-type]
            explanation="test",
            command_result=None,
            control_path_exists=None,
        )

    def exec(self, remote_command: str) -> CommandResult:
        self.exec_calls.append(remote_command)
        return CommandResult(args=("ssh", "polaris-relay", remote_command), returncode=0, stdout="", stderr="", duration_seconds=0.01)

    def copy_to(self, local_path: str, remote_path: str) -> CommandResult:
        self.copy_to_calls.append((local_path, remote_path))
        return CommandResult(args=("scp", local_path, f"polaris-relay:{remote_path}"), returncode=0, stdout="", stderr="", duration_seconds=0.01)


def test_execute_remote_command_requires_attached_bridge() -> None:
    client = FakeBridgeClient(state="DETACHED")

    with pytest.raises(RemoteBridgeError, match="bridge must be ATTACHED"):
        execute_remote_command(client, "pwd")


def test_ensure_remote_path_within_root_rejects_outside_paths() -> None:
    with pytest.raises(ValueError, match="remote path must stay under remote_root"):
        ensure_remote_path_within_root("/tmp/outside.txt", "/eagle/demo")


def test_copy_to_remote_enforces_remote_root_prefix(tmp_path: Path) -> None:
    client = FakeBridgeClient()
    local_file = tmp_path / "probe.sh"
    local_file.write_text("echo hi\n", encoding="utf-8")

    copy_to_remote(client, local_file, "/eagle/demo/jobs/probe/entrypoint.sh", "/eagle/demo")

    assert client.copy_to_calls == [
        (str(local_file), "/eagle/demo/jobs/probe/entrypoint.sh")
    ]


def test_build_bootstrap_mkdir_command_creates_required_directories() -> None:
    command = build_bootstrap_mkdir_command("/eagle/demo")

    assert command == (
        "mkdir -p "
        "/eagle/demo "
        "/eagle/demo/jobs "
        "/eagle/demo/jobs/probe "
        "/eagle/demo/runs "
        "/eagle/demo/manifests"
    )


def test_build_bootstrap_files_returns_managed_paths() -> None:
    files = build_bootstrap_files("/eagle/demo")

    assert "/eagle/demo/README.remote.md" in files
    assert "/eagle/demo/jobs/probe/entrypoint.sh" in files
```

- [ ] **Step 2: Run the new tests to verify failure**

Run:

```bash
pytest tests/test_remote_bootstrap.py -q
```

Expected: import failures because `remote_exec.py` and `remote_fs.py` do not exist yet.

- [ ] **Step 3: Implement safe remote exec helpers**

Create `src/autoresearch/bridge/remote_exec.py` with:

```python
from __future__ import annotations

from pathlib import Path

from autoresearch.bridge.ssh_master import SSHMasterClient
from autoresearch.schemas import CommandResult


class RemoteBridgeError(RuntimeError):
    pass


def ensure_bridge_attached(client: SSHMasterClient) -> None:
    status = client.status()
    if status.state != "ATTACHED":
        raise RemoteBridgeError("bridge must be ATTACHED")


def ensure_remote_path_within_root(remote_path: str, remote_root: str) -> str:
    normalized_root = remote_root.rstrip("/")
    normalized_path = remote_path.rstrip("/")
    if normalized_path != normalized_root and not normalized_path.startswith(normalized_root + "/"):
        raise ValueError("remote path must stay under remote_root")
    return remote_path


def execute_remote_command(client: SSHMasterClient, remote_command: str) -> CommandResult:
    ensure_bridge_attached(client)
    return client.exec(remote_command)


def copy_to_remote(
    client: SSHMasterClient,
    local_path: Path,
    remote_path: str,
    remote_root: str,
) -> CommandResult:
    ensure_bridge_attached(client)
    ensure_remote_path_within_root(remote_path, remote_root)
    return client.copy_to(str(local_path), remote_path)


def copy_from_remote(
    client: SSHMasterClient,
    remote_path: str,
    local_path: Path,
    remote_root: str,
) -> CommandResult:
    ensure_bridge_attached(client)
    ensure_remote_path_within_root(remote_path, remote_root)
    return client.copy_from(remote_path, str(local_path))
```

- [ ] **Step 4: Implement bootstrap planning helpers**

Create `src/autoresearch/bridge/remote_fs.py` with:

```python
from __future__ import annotations


def build_bootstrap_mkdir_command(remote_root: str) -> str:
    return (
        "mkdir -p "
        f"{remote_root} "
        f"{remote_root}/jobs "
        f"{remote_root}/jobs/probe "
        f"{remote_root}/runs "
        f"{remote_root}/manifests"
    )


def build_bootstrap_files(remote_root: str) -> dict[str, str]:
    return {
        f"{remote_root}/README.remote.md": (
            "# Auto Research Remote Root\n\n"
            "This directory is managed by the lab-server control plane.\n"
        ),
        f"{remote_root}/jobs/probe/entrypoint.sh": (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            'RUN_DIR="${RUN_DIR:?}"\n'
            'mkdir -p "$RUN_DIR"\n'
            'echo "start $(date -Is)" | tee -a "$RUN_DIR/probe.log"\n'
            "sleep 5\n"
            'echo "heartbeat 1" | tee -a "$RUN_DIR/probe.log"\n'
            "sleep 5\n"
            'echo "done $(date -Is)" | tee -a "$RUN_DIR/probe.log"\n'
        ),
    }
```

- [ ] **Step 5: Re-run the remote bootstrap tests**

Run:

```bash
pytest tests/test_remote_bootstrap.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/autoresearch/bridge/remote_exec.py src/autoresearch/bridge/remote_fs.py tests/test_remote_bootstrap.py
git commit -m "feat: add remote bootstrap services"
```

## Task 4: Add Probe Request Defaults And Real Scheduler Command Helpers

**Files:**
- Modify: `src/autoresearch/executor/polaris.py`
- Modify: `src/autoresearch/executor/pbs.py`
- Create: `tests/test_probe_flow.py`

- [ ] **Step 1: Write failing probe-helper tests**

Create `tests/test_probe_flow.py`:

```python
from autoresearch.executor.pbs import build_qstat_command, build_qsub_command
from autoresearch.executor.polaris import build_probe_job_request
from autoresearch.settings import ProbeSettings


def test_build_probe_job_request_uses_probe_defaults() -> None:
    request = build_probe_job_request(
        run_id="run_probe",
        probe=ProbeSettings(project="demo", queue="debug", walltime="00:10:00"),
        remote_root="/eagle/demo",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
    )

    assert request.project == "demo"
    assert request.queue == "debug"
    assert request.walltime == "00:10:00"
    assert request.submit_script_path == "/eagle/demo/jobs/run_probe/submit.pbs"


def test_build_probe_job_request_allows_cli_overrides() -> None:
    request = build_probe_job_request(
        run_id="run_probe",
        probe=ProbeSettings(project="demo", queue="debug", walltime="00:10:00"),
        remote_root="/eagle/demo",
        entrypoint_path="/eagle/demo/jobs/probe/entrypoint.sh",
        queue="prod",
        walltime="00:20:00",
    )

    assert request.queue == "prod"
    assert request.walltime == "00:20:00"


def test_build_qsub_command_targets_submit_script() -> None:
    assert build_qsub_command("/eagle/demo/jobs/run_probe/submit.pbs") == (
        "qsub",
        "/eagle/demo/jobs/run_probe/submit.pbs",
    )


def test_build_qstat_command_requests_json() -> None:
    assert build_qstat_command("123.polaris") == (
        "qstat",
        "-fF",
        "JSON",
        "123.polaris",
    )
```

- [ ] **Step 2: Run probe-helper tests to verify failure**

Run:

```bash
pytest tests/test_probe_flow.py -q
```

Expected: import failures for missing probe helper functions.

- [ ] **Step 3: Add probe request builder**

Add this helper to `src/autoresearch/executor/polaris.py`:

```python
from autoresearch.settings import ProbeSettings


def build_probe_job_request(
    *,
    run_id: str,
    probe: ProbeSettings,
    remote_root: str,
    entrypoint_path: str,
    queue: str | None = None,
    walltime: str | None = None,
    project: str | None = None,
) -> PolarisJobRequest:
    return build_polaris_job_request(
        run_id=run_id,
        project=project or probe.project,
        queue=queue or probe.queue,
        walltime=walltime or probe.walltime,
        entrypoint_path=entrypoint_path,
        remote_root=remote_root,
    )
```

- [ ] **Step 4: Add real scheduler command helpers**

Add these helpers to `src/autoresearch/executor/pbs.py`:

```python
def build_qsub_command(submit_script_path: str) -> tuple[str, ...]:
    return ("qsub", submit_script_path)


def build_qstat_command(pbs_job_id: str) -> tuple[str, ...]:
    return ("qstat", "-fF", "JSON", pbs_job_id)
```

- [ ] **Step 5: Re-run probe-helper tests**

Run:

```bash
pytest tests/test_probe_flow.py -q
```

Expected: probe-helper tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/autoresearch/executor/polaris.py src/autoresearch/executor/pbs.py tests/test_probe_flow.py
git commit -m "feat: add probe job request helpers"
```

## Task 5: Extend The Registry For Probe Submission And Polling

**Files:**
- Modify: `src/autoresearch/runs/registry.py`
- Modify: `tests/test_registry.py`

- [ ] **Step 1: Write failing registry tests**

Add these tests to `tests/test_registry.py`:

```python
def test_get_job_returns_existing_job(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    record = registry.create_job(
        run_id="run_demo",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
    )

    loaded = registry.get_job(record.job_id)

    assert loaded.job_id == record.job_id
    assert loaded.state == "DRAFT"


def test_mark_job_submitted_sets_scheduler_identifier(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    record = registry.create_job(
        run_id="run_demo",
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
    )

    updated = registry.mark_job_submitted(record.job_id, "123.polaris")

    assert updated.state == "SUBMITTED"
    assert updated.pbs_job_id == "123.polaris"
```

- [ ] **Step 2: Run registry tests to verify failure**

Run:

```bash
pytest tests/test_registry.py -q
```

Expected: missing-method failures for `get_job` and `mark_job_submitted`.

- [ ] **Step 3: Add focused registry helpers**

Add these methods to `RunRegistry`:

```python
    def get_job(self, job_id: str) -> JobRecord:
        with connect_db(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT job_id, run_id, backend, pbs_job_id, queue, walltime,
                       filesystems, select_expr, place_expr, exec_host, state,
                       submit_script_path, stdout_path, stderr_path,
                       created_at, updated_at
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")
        return self._row_to_job_record(row)

    def mark_job_submitted(self, job_id: str, pbs_job_id: str) -> JobRecord:
        return self.update_job_state(job_id=job_id, state="SUBMITTED", pbs_job_id=pbs_job_id)
```

- [ ] **Step 4: Re-run registry tests**

Run:

```bash
pytest tests/test_registry.py -q
```

Expected: the new registry tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/runs/registry.py tests/test_registry.py
git commit -m "feat: add probe registry helpers"
```

## Task 6: Add CLI For Bridge Exec, Copy, And Remote Bootstrap

**Files:**
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_remote_bootstrap.py`

- [ ] **Step 1: Write failing CLI tests**

Add these tests to `tests/test_cli.py`:

```python
def test_bridge_exec_uses_bridge_service(monkeypatch) -> None:
    class FakeExecBridge(FakeBridgeService):
        def exec(self, remote_command: str) -> CommandResult:
            self.calls.append(f"exec:{remote_command}")
            return CommandResult(
                args=("ssh", "polaris-relay", remote_command),
                returncode=0,
                stdout="/eagle/demo\n",
                stderr="",
                duration_seconds=0.01,
            )

    fake_service = FakeExecBridge()
    monkeypatch.setattr(cli_module, "build_bridge_service", lambda: fake_service)

    result = runner.invoke(app, ["bridge", "exec", "--", "pwd"])

    assert result.exit_code == 0
    assert fake_service.calls == ["exec:pwd"]
    assert "/eagle/demo" in result.stdout


def test_remote_bootstrap_invokes_bootstrap_service(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_bootstrap(*, force: bool) -> None:
        calls.append(force)

    monkeypatch.setattr(cli_module, "run_remote_bootstrap", fake_bootstrap)

    result = runner.invoke(app, ["remote", "bootstrap", "--force"])

    assert result.exit_code == 0
    assert calls == [True]
```

- [ ] **Step 2: Run CLI tests to verify failure**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: unknown-command or missing-function failures for `bridge exec` and `remote bootstrap`.

- [ ] **Step 3: Add CLI surface for bridge and remote bootstrap**

Update `src/autoresearch/cli.py`:

```python
remote_app = typer.Typer(help="Remote environment commands.")
app.add_typer(remote_app, name="remote")


def run_remote_bootstrap(*, force: bool) -> None:
    settings = load_settings()
    client = build_bridge_service()
    result = execute_remote_command(
        client,
        build_bootstrap_mkdir_command(settings.remote_root),
    )
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)


@bridge_app.command("exec")
def exec_bridge(remote_command: str = typer.Argument(...)) -> None:
    service = build_bridge_service()
    result = execute_remote_command(service, remote_command)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)
    if result.stdout:
        typer.echo(result.stdout, nl=False)


@bridge_app.command("copy-to")
def bridge_copy_to(
    src: str = typer.Option(..., "--src"),
    dst: str = typer.Option(..., "--dst"),
) -> None:
    settings = load_settings()
    result = copy_to_remote(build_bridge_service(), Path(src), dst, settings.remote_root)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)


@bridge_app.command("copy-from")
def bridge_copy_from(
    src: str = typer.Option(..., "--src"),
    dst: str = typer.Option(..., "--dst"),
) -> None:
    settings = load_settings()
    result = copy_from_remote(build_bridge_service(), src, Path(dst), settings.remote_root)
    if result.returncode != 0:
        _echo_failed_command(result)
        raise typer.Exit(code=result.returncode)


@remote_app.command("bootstrap")
def remote_bootstrap(force: bool = typer.Option(False, "--force")) -> None:
    run_remote_bootstrap(force=force)
    typer.echo("Remote bootstrap completed.")
```

- [ ] **Step 4: Re-run CLI tests**

Run:

```bash
pytest tests/test_cli.py tests/test_remote_bootstrap.py -q
```

Expected: the new CLI command tests pass, even though bootstrap file upload behavior is not finished yet.

- [ ] **Step 5: Commit**

```bash
git add src/autoresearch/cli.py tests/test_cli.py tests/test_remote_bootstrap.py
git commit -m "feat: add remote bootstrap cli"
```

## Task 7: Complete Remote Bootstrap And Add Probe Submission/Poll CLI

**Files:**
- Modify: `src/autoresearch/bridge/remote_fs.py`
- Modify: `src/autoresearch/cli.py`
- Modify: `tests/test_remote_bootstrap.py`
- Modify: `tests/test_probe_flow.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing probe-submission tests**

Extend `tests/test_probe_flow.py`:

```python
from pathlib import Path

from autoresearch.bridge.remote_fs import build_bootstrap_files
from autoresearch.db import init_db
from autoresearch.runs.registry import RunRegistry
from autoresearch.schemas import CommandResult, QstatParseResult, QsubParseResult, RunCreateRequest


def test_build_bootstrap_files_contains_probe_entrypoint() -> None:
    files = build_bootstrap_files("/eagle/demo")
    assert files["/eagle/demo/jobs/probe/entrypoint.sh"].startswith("#!/bin/bash")


def test_submit_probe_persists_submitted_job(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "autoresearch.db"
    init_db(db_path)
    registry = RunRegistry(db_path)
    run = registry.create_run(RunCreateRequest(run_kind="probe", project="demo"))
    job = registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue="debug",
        walltime="00:10:00",
        filesystems="eagle",
        select_expr="1:system=polaris",
        place_expr="scatter",
    )

    updated = registry.mark_job_submitted(job.job_id, "123.polaris")

    assert updated.state == "SUBMITTED"
    assert updated.pbs_job_id == "123.polaris"
```

Add CLI-level tests to `tests/test_cli.py`:

```python
def test_job_submit_probe_creates_and_submits_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_DB", str(tmp_path / "state" / "autoresearch.db"))
    monkeypatch.setenv("AUTORESEARCH_REPO_ROOT", str(tmp_path))
    _write_repo_config(tmp_path)
    (tmp_path / "conf" / "polaris.yaml").write_text(
        "bridge:\n"
        "  alias: polaris-relay\n"
        "  host: polaris-login-04.hsn.cm.polaris.alcf.anl.gov\n"
        "  user: zzq\n"
        "  control_path: ~/.ssh/cm-%C\n"
        "  server_alive_interval: 60\n"
        "  server_alive_count_max: 3\n"
        "  connect_timeout: 15\n"
        "probe:\n"
        "  project: demo\n"
        "  queue: debug\n"
        "  walltime: 00:10:00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli_module,
        "submit_probe_job",
        lambda **kwargs: ("run_probe", "job_probe", "123.polaris"),
    )

    result = runner.invoke(app, ["job", "submit-probe"])

    assert result.exit_code == 0
    assert "run_probe" in result.stdout
    assert "job_probe" in result.stdout
    assert "123.polaris" in result.stdout


def test_job_poll_prints_updated_state(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "poll_probe_job",
        lambda job_id: ("RUNNING", "123.polaris"),
    )

    result = runner.invoke(app, ["job", "poll", "--job-id", "job_probe"])

    assert result.exit_code == 0
    assert "job_probe" in result.stdout
    assert "RUNNING" in result.stdout
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_probe_flow.py tests/test_cli.py -q
```

Expected: missing helper and unknown-command failures for `submit-probe` and `poll`.

- [ ] **Step 3: Finish remote bootstrap**

Extend `src/autoresearch/bridge/remote_fs.py` with:

```python
from pathlib import Path
import shlex
import tempfile

from autoresearch.bridge.remote_exec import copy_to_remote, execute_remote_command
from autoresearch.bridge.ssh_master import SSHMasterClient
from autoresearch.schemas import CommandResult


def _raise_on_failure(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "remote command failed")


def bootstrap_remote_root(
    client: SSHMasterClient,
    remote_root: str,
    *,
    force: bool,
) -> None:
    _raise_on_failure(execute_remote_command(client, build_bootstrap_mkdir_command(remote_root)))
    for remote_path, content in build_bootstrap_files(remote_root).items():
        if not force:
            check_result = execute_remote_command(
                client,
                f"test -f {shlex.quote(remote_path)}",
            )
            if check_result.returncode == 0:
                continue
            if check_result.returncode != 1:
                _raise_on_failure(check_result)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        try:
            _raise_on_failure(copy_to_remote(client, temp_path, remote_path, remote_root))
        finally:
            temp_path.unlink(missing_ok=True)
```

- create directories first
- upload only managed files
- respect `force`
- never delete remote content

- [ ] **Step 4: Add probe orchestration helpers and CLI wiring**

Add these thin orchestration helpers to `src/autoresearch/cli.py`:

```python
import shlex
import tempfile
from pathlib import Path


def submit_probe_job(
    *,
    project: str | None = None,
    queue: str | None = None,
    walltime: str | None = None,
) -> tuple[str, str, str]:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    run = registry.create_run(RunCreateRequest(run_kind="probe", project=project or settings.probe.project))
    request = build_probe_job_request(
        run_id=run.run_id,
        probe=settings.probe,
        remote_root=settings.remote_root,
        entrypoint_path=f"{settings.remote_root}/jobs/probe/entrypoint.sh",
        project=project,
        queue=queue,
        walltime=walltime,
    )
    job = registry.create_job(
        run_id=run.run_id,
        backend="pbs",
        queue=request.queue,
        walltime=request.walltime,
        filesystems=request.filesystems,
        select_expr=request.select_expr,
        place_expr=request.place_expr,
        submit_script_path=request.submit_script_path,
        stdout_path=request.stdout_path,
        stderr_path=request.stderr_path,
    )
    rendered = render_pbs_script(request)
    client = build_bridge_service()
    bootstrap_remote_root(client, settings.remote_root, force=False)
    if request.submit_script_path is None:
        raise ValueError("submit_script_path must be set")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(rendered.script_text)
        temp_submit_path = Path(handle.name)
    try:
        copy_result = copy_to_remote(
            client,
            temp_submit_path,
            request.submit_script_path,
            settings.remote_root,
        )
        if copy_result.returncode != 0:
            raise RuntimeError(copy_result.stderr or copy_result.stdout or "submit script upload failed")
    finally:
        temp_submit_path.unlink(missing_ok=True)
    qsub_result = execute_remote_command(
        client,
        shlex.join(build_qsub_command(request.submit_script_path)),
    )
    if qsub_result.returncode != 0:
        raise RuntimeError(qsub_result.stderr or qsub_result.stdout or "qsub failed")
    parsed = parse_qsub_output(qsub_result.stdout)
    updated = registry.mark_job_submitted(job.job_id, parsed.pbs_job_id)
    return run.run_id, updated.job_id, parsed.pbs_job_id


def poll_probe_job(job_id: str) -> tuple[str, str]:
    settings = load_settings()
    registry = RunRegistry(settings.paths.db_path)
    job = registry.get_job(job_id)
    if not job.pbs_job_id:
        raise ValueError("job has no pbs_job_id")
    client = build_bridge_service()
    qstat_result = execute_remote_command(
        client,
        shlex.join(build_qstat_command(job.pbs_job_id)),
    )
    if qstat_result.returncode != 0:
        raise RuntimeError(qstat_result.stderr or qstat_result.stdout or "qstat failed")
    parsed = parse_qstat_json(qstat_result.stdout)
    state_map = {"Q": "QUEUED", "R": "RUNNING", "F": "SUCCEEDED"}
    updated = registry.update_job_state(
        job.job_id,
        state=state_map.get(parsed.state, parsed.state),
        pbs_job_id=parsed.pbs_job_id,
        exec_host=parsed.exec_host,
    )
    return updated.state, parsed.pbs_job_id
```

Wire the commands:

```python
@job_app.command("submit-probe")
def job_submit_probe(
    project: str | None = typer.Option(None, "--project"),
    queue: str | None = typer.Option(None, "--queue"),
    walltime: str | None = typer.Option(None, "--walltime"),
) -> None:
    run_id, job_id, pbs_job_id = submit_probe_job(
        project=project,
        queue=queue,
        walltime=walltime,
    )
    typer.echo(f"{run_id}\t{job_id}\t{pbs_job_id}")


@job_app.command("poll")
def job_poll(job_id: str = typer.Option(..., "--job-id")) -> None:
    state, pbs_job_id = poll_probe_job(job_id)
    typer.echo(f"{job_id}\t{pbs_job_id}\t{state}")
```

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
pytest tests/test_cli.py tests/test_probe_flow.py tests/test_remote_bootstrap.py -q
```

Expected: new Phase 3B CLI and bootstrap tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/autoresearch/bridge/remote_fs.py src/autoresearch/cli.py tests/test_remote_bootstrap.py tests/test_probe_flow.py tests/test_cli.py
git commit -m "feat: add probe submission cli"
```

## Task 8: Update Docs And Run Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runbook.md`

- [ ] **Step 1: Update architecture docs**

Add a Phase 3B section to `docs/architecture.md` describing:

```markdown
## Remote probe workflow

Phase 3B extends the local PBS executor with:

- bridge-level `exec`, `copy_to`, and `copy_from`
- remote bootstrap of the managed Eagle root
- one built-in probe submission path
- real `qsub` and `qstat` integration for the probe

This remains a narrow operational loop. Arbitrary remote entrypoints and generalized job submission are still out of scope.
```

- [ ] **Step 2: Update runbook**

Add operator-facing commands to `docs/runbook.md`:

````markdown
## Remote bootstrap and probe commands

```bash
python -m autoresearch.cli bridge exec -- "pwd"
python -m autoresearch.cli bridge copy-to --src local.txt --dst <remote_root>/tmp/local.txt
python -m autoresearch.cli bridge copy-from --src <remote_root>/runs/<run_id>/probe.log --dst /tmp/probe.log
python -m autoresearch.cli remote bootstrap
python -m autoresearch.cli job submit-probe
python -m autoresearch.cli job poll --job-id <job_id>
```

`job submit-probe` is the first real remote submission path in the project. It only submits the built-in probe.
````

- [ ] **Step 3: Run the full test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Verify CLI help**

Run:

```bash
PYTHONPATH=src python -m autoresearch.cli --help
PYTHONPATH=src python -m autoresearch.cli bridge --help
PYTHONPATH=src python -m autoresearch.cli remote --help
PYTHONPATH=src python -m autoresearch.cli job --help
```

Expected: `bridge exec`, `bridge copy-to`, `bridge copy-from`, `remote bootstrap`, `job submit-probe`, and `job poll` all appear in help output.

- [ ] **Step 5: Commit docs**

```bash
git add docs/architecture.md docs/runbook.md
git commit -m "docs: add phase3b probe workflow docs"
```

- [ ] **Step 6: Manual acceptance checklist**

Run these commands against a real attached bridge after the code is merged:

```bash
python -m autoresearch.cli bridge status
python -m autoresearch.cli remote bootstrap
python -m autoresearch.cli job submit-probe
python -m autoresearch.cli job poll --job-id <job_id>
python -m autoresearch.cli job list
```

Expected operational outcome:

- bridge is `ATTACHED`
- remote bootstrap completes without deleting remote files
- probe returns a real PBS job id
- polling reaches a terminal state
- stdout/stderr land under the configured `remote_root`
- local `job list` reflects the real scheduler-backed state

## Self-Review

- Spec coverage:
  - bridge `exec/copy_to/copy_from`: Tasks 2 and 6
  - remote bootstrap with `--force`: Tasks 3 and 7
  - built-in probe only: Tasks 4 and 7
  - config defaults plus CLI overrides: Tasks 1 and 7
  - real `qsub` and `qstat` path: Tasks 4 and 7
  - docs and manual acceptance: Task 8
- Placeholder scan:
  - no `TODO`, `TBD`, or “implement later” markers remain
- Type consistency:
  - `ProbeSettings`, `build_probe_job_request`, `build_qsub_command`, `build_qstat_command`, `submit_probe_job`, and `poll_probe_job` are named consistently across tasks
