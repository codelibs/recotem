// Centralized API endpoint paths to eliminate magic strings.
// All paths are relative to the API_BASE_URL configured in config.ts.

export const ENDPOINTS = {
  // Projects
  PROJECT: "/project/",
  PROJECT_SUMMARY: (id: number) => `/project_summary/${id}/`,

  // Training data
  TRAINING_DATA: "/training_data/",
  TRAINING_DATA_DETAIL: (id: number) => `/training_data/${id}/`,
  TRAINING_DATA_PREVIEW: (id: number) => `/training_data/${id}/preview/`,

  // Tuning
  PARAMETER_TUNING_JOB: "/parameter_tuning_job/",
  PARAMETER_TUNING_JOB_DETAIL: (id: number) => `/parameter_tuning_job/${id}/`,

  // Models
  TRAINED_MODEL: "/trained_model/",
  TRAINED_MODEL_DETAIL: (id: number) => `/trained_model/${id}/`,
  TRAINED_MODEL_RECOMMENDATION: (id: number) => `/trained_model/${id}/recommendation/`,

  // Configuration
  MODEL_CONFIGURATION: "/model_configuration/",
  MODEL_CONFIGURATION_DETAIL: (id: number) => `/model_configuration/${id}/`,
  SPLIT_CONFIG: "/split_config/",
  EVALUATION_CONFIG: "/evaluation_config/",

  // Task logs
  TASK_LOG: "/task_log/",

  // Auth
  AUTH_LOGIN: "/auth/login/",
  AUTH_TOKEN_REFRESH: "/auth/token/refresh/",
  AUTH_LOGOUT: "/auth/logout/",
  AUTH_USER: "/auth/user/",

  // Health
  PING: "/ping/",
} as const;
