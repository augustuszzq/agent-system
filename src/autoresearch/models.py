RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY,
  run_kind TEXT NOT NULL,
  project TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  status TEXT NOT NULL,
  git_commit TEXT,
  git_dirty INTEGER NOT NULL DEFAULT 0,
  local_cmd TEXT,
  remote_cmd TEXT,
  working_dir TEXT,
  notes TEXT
)
"""

JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  backend TEXT NOT NULL,
  pbs_job_id TEXT,
  queue TEXT,
  walltime TEXT,
  filesystems TEXT,
  select_expr TEXT,
  place_expr TEXT,
  exec_host TEXT,
  state TEXT NOT NULL,
  submit_script_path TEXT,
  stdout_path TEXT,
  stderr_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

INCIDENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS incidents(
  incident_id TEXT PRIMARY KEY,
  run_id TEXT,
  job_id TEXT,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  fingerprint TEXT,
  evidence_json TEXT NOT NULL,
  auto_action TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
)
"""

DECISIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decisions(
  decision_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  rationale TEXT,
  actor TEXT NOT NULL
)
"""

RETRY_REQUESTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS retry_requests(
  retry_request_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  source_run_id TEXT,
  source_job_id TEXT,
  source_pbs_job_id TEXT,
  requested_action TEXT NOT NULL,
  approval_status TEXT NOT NULL,
  execution_status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL,
  approved_by TEXT,
  approval_reason TEXT,
  last_error TEXT,
  result_run_id TEXT,
  result_job_id TEXT,
  result_pbs_job_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  executed_at TEXT
)
"""
