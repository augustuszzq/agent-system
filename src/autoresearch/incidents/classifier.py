from __future__ import annotations

from hashlib import sha256
import re
from typing import Iterable, Sequence

from autoresearch.schemas import ClassifiedIncident, NormalizedIncidentInput


_FILESYSTEM_UNAVAILABLE_PATTERNS = (
    "filesystem unavailable",
    "filesystems unavailable",
    "eagle unavailable",
    "eagle is unavailable",
)
_OOM_PATTERNS = (
    "out of memory",
    "cuda out of memory",
    "cublas_status_alloc_failed",
    "oom-kill",
)
_OOM_CONTEXT_PATTERNS = (
    "memory pressure",
    "memory exhausted",
    "memory exhaustion",
    "memory allocation failed",
    "gpu memory pressure",
    "cuda memory",
    "cuda oom",
)
_WALLTIME_PATTERNS = (
    "walltime",
    "exceeded limit",
    "time limit",
)
_IMPORT_ERROR_PATTERNS = (
    "modulenotfounderror",
    "importerror",
    "no module named",
)
_PATH_ERROR_PATTERNS = (
    "no such file or directory",
    "cannot open",
    "cannot cd",
    "can't open file",
)
_RUNNING_LIKE_STATES = {"R", "E", "S"}


def classify_incident(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    evidence = _collect_evidence(incident)
    if not evidence:
        return None

    filesystem = _match_filesystem_unavailable(incident)
    if filesystem is not None:
        return filesystem

    oom = _match_resource_oom(incident)
    if oom is not None:
        return oom

    walltime = _match_resource_walltime(incident)
    if walltime is not None:
        return walltime

    import_error = _match_import_error(incident)
    if import_error is not None:
        return import_error

    path_error = _match_path_error(incident)
    if path_error is not None:
        return path_error

    nccl = _match_nccl_failure(incident)
    if nccl is not None:
        return nccl

    mpi = _match_mpi_bootstrap(incident)
    if mpi is not None:
        return mpi

    no_heartbeat = _match_no_heartbeat(incident)
    if no_heartbeat is not None:
        return no_heartbeat

    return _classify_unknown(incident)


def _match_filesystem_unavailable(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    comment = _normalize_text(incident.comment)
    if not comment or not _contains_any(comment, _FILESYSTEM_UNAVAILABLE_PATTERNS):
        return None
    return ClassifiedIncident(
        category="FILESYSTEM_UNAVAILABLE",
        severity="CRITICAL",
        fingerprint=_normalize_rule_fingerprint(comment),
        matched_lines=(comment,),
        rule_name="filesystem_unavailable",
    )


def _match_resource_oom(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    lines = tuple(
        _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail, incident.comment or "")
    )
    line = _first_matching_line(lines, _OOM_PATTERNS)
    if line is None:
        killed_index = _first_killed_line_index(lines)
        if killed_index is None:
            return None
        nearby_context = _lines_within_radius(lines, killed_index, radius=1)
        if _contains_any(" ".join(nearby_context), _WALLTIME_PATTERNS):
            return None
        if not _contains_any(" ".join(nearby_context), _OOM_PATTERNS[:-1] + _OOM_CONTEXT_PATTERNS):
            return None
        line = lines[killed_index]
    return ClassifiedIncident(
        category="RESOURCE_OOM",
        severity="CRITICAL",
        fingerprint=_normalize_rule_fingerprint(line),
        matched_lines=(line,),
        rule_name="resource_oom",
    )


def _match_resource_walltime(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    line = _first_matching_line(
        _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail, incident.comment or ""),
        _WALLTIME_PATTERNS,
    )
    if line is None:
        return None
    return ClassifiedIncident(
        category="RESOURCE_WALLTIME",
        severity="HIGH",
        fingerprint=_normalize_rule_fingerprint(line),
        matched_lines=(line,),
        rule_name="resource_walltime",
    )


def _match_import_error(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    line = _first_matching_line(
        _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail),
        _IMPORT_ERROR_PATTERNS,
    )
    if line is None:
        return None

    fingerprint = _normalize_import_fingerprint(line)
    return ClassifiedIncident(
        category="ENV_IMPORT_ERROR",
        severity="HIGH",
        fingerprint=fingerprint,
        matched_lines=(line,),
        rule_name="env_import_error",
    )


def _match_path_error(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    line = _first_matching_line(
        _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail),
        _PATH_ERROR_PATTERNS,
    )
    if line is None:
        return None
    return ClassifiedIncident(
        category="ENV_PATH_ERROR",
        severity="HIGH",
        fingerprint=_normalize_rule_fingerprint(line),
        matched_lines=(line,),
        rule_name="env_path_error",
    )


def _match_nccl_failure(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    for line in _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail):
        if not _is_nccl_failure_line(line):
            continue
        normalized = _normalize_rule_fingerprint(line)
        return ClassifiedIncident(
            category="NCCL_FAILURE",
            severity="CRITICAL",
            fingerprint=normalized,
            matched_lines=(line,),
            rule_name="nccl_failure",
        )
    return None


def _match_mpi_bootstrap(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    for line in _iter_nonempty_lines(incident.stdout_tail, incident.stderr_tail):
        if not _is_mpi_bootstrap_line(line):
            continue
        normalized = _normalize_rule_fingerprint(line)
        return ClassifiedIncident(
            category="MPI_BOOTSTRAP",
            severity="CRITICAL",
            fingerprint=normalized,
            matched_lines=(line,),
            rule_name="mpi_bootstrap",
        )
    return None


def _is_mpi_bootstrap_line(line: str) -> bool:
    if "pmi server not found" in line:
        return True
    if "mpi_init" in line:
        return _contains_any(line, ("failed", "fatal", "abort", "not found"))
    if "launcher" in line:
        return _contains_any(line, ("failed", "fatal", "abort", "error", "not found"))
    if "bootstrap" not in line:
        return False
    return _contains_any(line, ("failed", "fatal", "not found", "abort"))


def _is_nccl_failure_line(line: str) -> bool:
    if "nccl" not in line:
        return False
    if "unhandled cuda error" in line:
        return True
    if _contains_any(line, ("failed", "fatal", "abort", "error in")):
        return True
    if "watchdog" in line and "timeout" in line:
        return True
    if "collective operation timeout" in line:
        return True
    return "warn" in line and _contains_any(
        line,
        ("connection closed by remote peer",),
    )


def _match_no_heartbeat(incident: NormalizedIncidentInput) -> ClassifiedIncident | None:
    if incident.job_state.strip().upper() not in _RUNNING_LIKE_STATES:
        return None
    if not _has_meaningful_output(incident.stdout_tail, incident.stderr_tail):
        return None
    if not incident.current_log_tail_hash or not incident.previous_log_tail_hash:
        return None
    if incident.current_log_tail_hash != incident.previous_log_tail_hash:
        return None

    fingerprint = "no-heartbeat"
    return ClassifiedIncident(
        category="NO_HEARTBEAT",
        severity="HIGH",
        fingerprint=fingerprint,
        matched_lines=tuple(
            line
            for line in (
                _first_nonempty_line(incident.stdout_tail),
                _first_nonempty_line(incident.stderr_tail),
            )
            if line is not None
        ),
        rule_name="no_heartbeat",
    )


def _classify_unknown(incident: NormalizedIncidentInput) -> ClassifiedIncident:
    stderr_lines = tuple(_iter_nonempty_lines(incident.stderr_tail))
    fingerprint_source = "\n".join(
        part
        for part in (
            _normalize_text(incident.comment),
            "\n".join(stderr_lines[:3]),
        )
        if part
    )
    fingerprint = sha256(fingerprint_source.encode("utf-8")).hexdigest()
    matched_lines = tuple(
        line
        for line in (
            _normalize_text(incident.comment) if incident.comment else None,
            *stderr_lines[:3],
        )
        if line
    )
    return ClassifiedIncident(
        category="UNKNOWN",
        severity="MEDIUM",
        fingerprint=fingerprint,
        matched_lines=matched_lines,
        rule_name="unknown",
    )


def _collect_evidence(incident: NormalizedIncidentInput) -> tuple[str, ...]:
    return tuple(
        line
        for line in (
            _normalize_text(incident.comment) if incident.comment else None,
            _normalize_text(incident.stdout_tail) if incident.stdout_tail else None,
            _normalize_text(incident.stderr_tail) if incident.stderr_tail else None,
        )
        if line
    )


def _iter_nonempty_lines(*chunks: str) -> Iterable[str]:
    for chunk in chunks:
        if not chunk:
            continue
        for line in chunk.splitlines():
            normalized = _normalize_text(line)
            if normalized:
                yield normalized


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        normalized = _normalize_text(line)
        if normalized:
            return normalized
    return None


def _first_matching_line(lines: Iterable[str], patterns: Sequence[str]) -> str | None:
    for line in lines:
        if _contains_any(line, patterns):
            return line
    return None


def _contains_any(text: str, patterns: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in patterns)


def _first_killed_line_index(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if "killed" in line:
            return index
    return None


def _lines_within_radius(lines: Sequence[str], index: int, *, radius: int) -> tuple[str, ...]:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return tuple(lines[start:end])


def _has_meaningful_output(*chunks: str) -> bool:
    return any(line for chunk in chunks if chunk for line in chunk.splitlines() if line.strip())


def _normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.lower()


def _normalize_rule_fingerprint(value: str) -> str:
    normalized = _normalize_text(value)
    normalized = _strip_trivial_prefix(normalized)
    return normalized


def _strip_trivial_prefix(value: str) -> str:
    stripped = value
    patterns = (
        r"^\[[^\]]+\]\s*",
        r"^\d{4}-\d{2}-\d{2}[ tT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:z|[+-]\d{2}:?\d{2})?\s+",
        r"^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+",
        r"^[a-z]{3}\s+[a-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\S+\s+",
    )
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            updated = re.sub(pattern, "", stripped, count=1)
            if updated != stripped:
                stripped = updated
                changed = True
    return stripped


def _normalize_import_fingerprint(line: str) -> str:
    lower_line = _normalize_rule_fingerprint(line)

    module_match = re.search(
        r"(?:no module named|cannot import name|module not found)\s+['\"]?([a-zA-Z0-9_.-]+)['\"]?",
        lower_line,
    )
    if module_match:
        return f"no module named {module_match.group(1)}"

    import_match = re.search(r"importerror:.*?['\"]([a-zA-Z0-9_.-]+)['\"]", lower_line)
    if import_match:
        return f"no module named {import_match.group(1)}"

    return lower_line
