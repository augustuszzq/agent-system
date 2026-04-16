from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shlex
from typing import Protocol

from autoresearch.bridge.remote_exec import RemoteBridgeError, ensure_remote_path_within_root
from autoresearch.executor.pbs import build_qstat_command, parse_qstat_json
from autoresearch.paths import AppPaths, incident_snapshot_dir, incident_state_dir
from autoresearch.runs.registry import JobRecord
from autoresearch.schemas import BridgeStatusResult, IncidentFetchResult, IncidentSnapshotRef


class IncidentFetchError(RuntimeError):
    pass


class _BridgeClient(Protocol):
    def status(self) -> BridgeStatusResult: ...

    def exec(self, command: str): ...


def collect_incident_evidence(
    paths: AppPaths,
    job_record: JobRecord,
    bridge_client: _BridgeClient,
    remote_root: str,
) -> IncidentFetchResult:
    latest_snapshot = _latest_snapshot(paths, job_record.job_id)

    bridge_status = bridge_client.status()
    if bridge_status.state == "ATTACHED":
        try:
            return _fetch_live_snapshot(
                paths=paths,
                job_record=job_record,
                bridge_client=bridge_client,
                remote_root=remote_root,
            )
        except IncidentFetchError:
            if latest_snapshot is not None:
                return IncidentFetchResult(
                    source="local-fallback",
                    snapshot=latest_snapshot,
                    previous_snapshot=_previous_snapshot(paths, job_record.job_id, latest_snapshot),
                )
            raise

    if latest_snapshot is not None:
        return IncidentFetchResult(
            source="local-fallback",
            snapshot=latest_snapshot,
            previous_snapshot=_previous_snapshot(paths, job_record.job_id, latest_snapshot),
        )

    raise IncidentFetchError("No incident evidence available from live fetch or local snapshots")


def _fetch_live_snapshot(
    *,
    paths: AppPaths,
    job_record: JobRecord,
    bridge_client: _BridgeClient,
    remote_root: str,
) -> IncidentFetchResult:
    if not job_record.pbs_job_id:
        raise IncidentFetchError(f"job {job_record.job_id} has no PBS job id")

    scan_time = _scan_time_now()
    previous_snapshot = _latest_snapshot(paths, job_record.job_id)
    scan_time = _allocate_snapshot_scan_time(paths, job_record.job_id, scan_time)
    snapshot_dir = incident_snapshot_dir(paths, job_record.job_id, scan_time)

    qstat_command = shlex.join(build_qstat_command(job_record.pbs_job_id))
    qstat_result = bridge_client.exec(qstat_command)
    if qstat_result.returncode != 0:
        raise IncidentFetchError(qstat_result.stderr.strip() or "qstat fetch failed")

    try:
        qstat = parse_qstat_json(qstat_result.stdout)
    except (ValueError, TypeError) as exc:
        raise IncidentFetchError("qstat fetch returned invalid job data") from exc

    stdout_path = _preferred_remote_path(qstat.stdout_path, job_record.stdout_path, remote_root)
    stderr_path = _preferred_remote_path(qstat.stderr_path, job_record.stderr_path, remote_root)
    if stdout_path is None and stderr_path is None:
        raise IncidentFetchError(f"job {job_record.job_id} has no usable stdout/stderr paths")

    stdout_tail, stdout_available = _tail_remote_path(
        bridge_client,
        stdout_path,
        "stdout",
    )
    stderr_tail, stderr_available = _tail_remote_path(
        bridge_client,
        stderr_path,
        "stderr",
    )
    if not stdout_available and not stderr_available:
        raise IncidentFetchError(f"job {job_record.job_id} has no usable stdout/stderr evidence")

    qstat_json_path = snapshot_dir / "qstat.json"
    stdout_tail_path = snapshot_dir / "stdout.tail.log"
    stderr_tail_path = snapshot_dir / "stderr.tail.log"
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        qstat_json_path.write_text(qstat_result.stdout, encoding="utf-8")
        stdout_tail_path.write_text(stdout_tail, encoding="utf-8")
        stderr_tail_path.write_text(stderr_tail, encoding="utf-8")
    except OSError as exc:
        raise IncidentFetchError(f"snapshot persistence failed: {snapshot_dir}") from exc

    return IncidentFetchResult(
        source="live",
        snapshot=IncidentSnapshotRef(
            scan_time=scan_time,
            snapshot_dir=snapshot_dir,
            qstat_json_path=qstat_json_path,
            stdout_tail_path=stdout_tail_path,
            stderr_tail_path=stderr_tail_path,
        ),
        previous_snapshot=previous_snapshot,
    )


def _tail_remote_path(
    bridge_client: _BridgeClient,
    remote_path: str | None,
    label: str,
) -> tuple[str, bool]:
    if remote_path is None or not remote_path.strip():
        return "", False

    tail_command = f"tail -n 200 {shlex.quote(remote_path)}"
    result = bridge_client.exec(tail_command)
    if result.returncode != 0:
        return "", False
    return result.stdout, True


def _scan_time_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _preferred_remote_path(
    primary: str | None,
    fallback: str | None,
    remote_root: str,
) -> str | None:
    for candidate in (primary, fallback):
        normalized = _validated_remote_path(candidate, remote_root)
        if normalized is not None:
            return normalized
    return None


def _validated_remote_path(candidate: str | None, remote_root: str) -> str | None:
    if candidate is None or not candidate.strip():
        return None
    try:
        return ensure_remote_path_within_root(candidate, remote_root)
    except RemoteBridgeError:
        return None


def _allocate_snapshot_scan_time(paths: AppPaths, job_id: str, base_scan_time: str) -> str:
    root = incident_state_dir(paths, job_id)
    candidate = base_scan_time
    suffix = 1
    while incident_snapshot_dir(paths, job_id, candidate).exists():
        candidate = f"{base_scan_time}--{suffix:04d}"
        suffix += 1
    return candidate


def _latest_snapshot(paths: AppPaths, job_id: str) -> IncidentSnapshotRef | None:
    return _find_snapshot(paths, job_id)


def _previous_snapshot(
    paths: AppPaths,
    job_id: str,
    current_snapshot: IncidentSnapshotRef,
) -> IncidentSnapshotRef | None:
    return _find_snapshot(paths, job_id, before_scan_time=current_snapshot.scan_time)


def _find_snapshot(
    paths: AppPaths,
    job_id: str,
    *,
    before_scan_time: str | None = None,
) -> IncidentSnapshotRef | None:
    root = incident_state_dir(paths, job_id)
    if not root.exists():
        return None

    candidates: list[IncidentSnapshotRef] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if before_scan_time is not None and child.name >= before_scan_time:
            continue
        snapshot = _snapshot_ref_from_dir(child)
        if snapshot is not None:
            candidates.append(snapshot)

    if not candidates:
        return None
    return max(candidates, key=lambda snapshot: snapshot.scan_time)


def _snapshot_ref_from_dir(snapshot_dir: Path) -> IncidentSnapshotRef | None:
    qstat_json_path = snapshot_dir / "qstat.json"
    stdout_tail_path = snapshot_dir / "stdout.tail.log"
    stderr_tail_path = snapshot_dir / "stderr.tail.log"
    if not qstat_json_path.exists() or not stdout_tail_path.exists() or not stderr_tail_path.exists():
        return None
    return IncidentSnapshotRef(
        scan_time=snapshot_dir.name,
        snapshot_dir=snapshot_dir,
        qstat_json_path=qstat_json_path,
        stdout_tail_path=stdout_tail_path,
        stderr_tail_path=stderr_tail_path,
    )
