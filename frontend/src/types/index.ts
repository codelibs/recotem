// Frontend type definitions for the Recotem API.
//
// When the backend is running, auto-generate types from OpenAPI schema:
//   npm run generate:types
//
// Generated types land in src/api/generated-types.ts.
// This file re-exports the canonical types used throughout the app.
// WebSocket message types live in ./websocket.ts (not in OpenAPI).

export interface Project {
  id: number;
  name: string;
  user_column: string;
  item_column: string;
  time_column: string | null;
  owner: number | null;
  ins_datetime: string;
}

export interface TrainingData {
  id: number;
  project: number;
  file: string;
  ins_datetime: string;
  basename: string;
  filesize: number;
}

export interface ItemMetaData {
  id: number;
  project: number;
  file: string;
  valid_columns_list_json: string | null;
  ins_datetime: string;
  basename: string;
  filesize: number;
}

export interface SplitConfig {
  id: number;
  name: string | null;
  created_by: number | null;
  scheme: "RG" | "TG" | "TU";
  heldout_ratio: number;
  n_heldout: number | null;
  test_user_ratio: number;
  n_test_users: number | null;
  random_seed: number;
  ins_datetime: string;
}

export interface EvaluationConfig {
  id: number;
  name: string | null;
  cutoff: number;
  created_by: number | null;
  target_metric: "ndcg" | "map" | "recall" | "hit";
  ins_datetime: string;
}

export interface ModelConfiguration {
  id: number;
  name: string | null;
  project: number;
  recommender_class_name: string;
  parameters_json: string;
  tuning_job: number | null;
  ins_datetime: string;
}

export interface TrainedModel {
  id: number;
  configuration: number;
  data_loc: number;
  irspack_version: string | null;
  file: string;
  ins_datetime: string;
  basename: string;
  filesize: number;
}

export interface TaskResult {
  task_id: string;
  status: string;
  date_created: string | null;
  date_done: string | null;
  traceback: string | null;
}

export interface TaskLink {
  task: TaskResult;
}

export const JOB_STATUS = {
  PENDING: "PENDING",
  RUNNING: "RUNNING",
  COMPLETED: "COMPLETED",
  FAILED: "FAILED",
} as const;

export type TuningJobStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface ParameterTuningJob {
  id: number;
  data: number;
  split: number;
  evaluation: number;
  status: TuningJobStatus;
  n_tasks_parallel: number;
  n_trials: number;
  memory_budget: number;
  timeout_overall: number | null;
  timeout_singlestep: number | null;
  random_seed: number | null;
  tried_algorithms_json: string | null;
  irspack_version: string | null;
  train_after_tuning: boolean;
  tuned_model: number | null;
  best_config: number | null;
  best_score: number | null;
  task_links: TaskLink[];
  ins_datetime: string;
}

export interface TaskLog {
  id: number;
  task: number;
  contents: string;
  ins_datetime: string;
}

export interface ProjectSummary {
  n_data: number;
  n_complete_jobs: number;
  n_models: number;
  ins_datetime: string;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Recommendation {
  item_id: string;
  score: number;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}

export interface User {
  pk: number;
  username: string;
  email: string;
}

/** Standard DRF error response body. */
export interface ApiErrorResponse {
  detail?: string;
  [field: string]: string | string[] | undefined;
}

/** Classified API error types for granular error handling. */
export type ApiErrorKind =
  | "validation"      // 400 — field-level validation errors
  | "unauthorized"    // 401 — auth failure
  | "forbidden"       // 403 — permission denied
  | "not_found"       // 404 — resource not found
  | "rate_limited"    // 429 — throttled
  | "server_error"    // 5xx — backend failure
  | "network_error"   // no response — connectivity issue
  | "timeout"         // request timed out
  | "unknown";        // anything else

export interface ClassifiedApiError {
  kind: ApiErrorKind;
  status: number | null;
  message: string;
  fieldErrors?: Record<string, string[]>;
  raw?: unknown;
}

// Re-export WebSocket types for convenience
export type { WsStatusUpdate, WsStatusData, WsLogMessage, WsMessage } from "./websocket";
