export interface ApiKey {
  id: number
  project: number
  name: string
  key_prefix: string
  scopes: string[]
  is_active: boolean
  expires_at: string | null
  last_used_at: string | null
  ins_datetime: string
}

export interface ApiKeyCreateResponse extends ApiKey {
  key: string
}

export interface RetrainingSchedule {
  id: number
  project: number
  is_enabled: boolean
  cron_expression: string
  training_data: number | null
  model_configuration: number | null
  retune: boolean
  split_config: number | null
  evaluation_config: number | null
  max_retries: number
  notify_on_failure: boolean
  last_run_at: string | null
  last_run_status: string | null
  next_run_at: string | null
  auto_deploy: boolean
  ins_datetime: string
  updated_at: string
}

export interface RetrainingRun {
  id: number
  schedule: number
  status: string
  trained_model: number | null
  tuning_job: number | null
  error_message: string
  ins_datetime: string
  completed_at: string | null
  data_rows_at_trigger: number | null
}

export interface DeploymentSlot {
  id: number
  project: number
  name: string
  trained_model: number
  weight: number
  is_active: boolean
  ins_datetime: string
  updated_at: string
}

export interface ABTest {
  id: number
  project: number
  name: string
  status: "DRAFT" | "RUNNING" | "COMPLETED" | "CANCELLED"
  control_slot: number
  variant_slot: number
  target_metric_name: string
  min_sample_size: number
  confidence_level: number
  started_at: string | null
  ended_at: string | null
  winner_slot: number | null
  ins_datetime: string
  updated_at: string
}

export interface ABTestResult {
  control_impressions: number
  control_conversions: number
  control_rate: number
  variant_impressions: number
  variant_conversions: number
  variant_rate: number
  z_score: number
  p_value: number
  significant: boolean
  lift: number
  confidence_interval: [number, number]
}
