/**
 * This file was auto-generated by openapi-typescript.
 * Do not make direct changes to the file.
 */

export interface paths {
  "/api/evaluation_config/": {
    get: operations["evaluation_config_list"];
    post: operations["evaluation_config_create"];
  };
  "/api/evaluation_config/{id}/": {
    get: operations["evaluation_config_retrieve"];
    put: operations["evaluation_config_update"];
    delete: operations["evaluation_config_destroy"];
    patch: operations["evaluation_config_partial_update"];
  };
  "/api/model_configuration/": {
    get: operations["model_configuration_list"];
    post: operations["model_configuration_create"];
  };
  "/api/model_configuration/{id}/": {
    get: operations["model_configuration_retrieve"];
    put: operations["model_configuration_update"];
    delete: operations["model_configuration_destroy"];
    patch: operations["model_configuration_partial_update"];
  };
  "/api/parameter_tuning_job/": {
    get: operations["parameter_tuning_job_list"];
    post: operations["parameter_tuning_job_create"];
  };
  "/api/parameter_tuning_job/{id}/": {
    get: operations["parameter_tuning_job_retrieve"];
    put: operations["parameter_tuning_job_update"];
    delete: operations["parameter_tuning_job_destroy"];
    patch: operations["parameter_tuning_job_partial_update"];
  };
  "/api/project/": {
    get: operations["project_list"];
    post: operations["project_create"];
  };
  "/api/project/{id}/": {
    get: operations["project_retrieve"];
    put: operations["project_update"];
    delete: operations["project_destroy"];
    patch: operations["project_partial_update"];
  };
  "/api/split_config/": {
    get: operations["split_config_list"];
    post: operations["split_config_create"];
  };
  "/api/split_config/{id}/": {
    get: operations["split_config_retrieve"];
    put: operations["split_config_update"];
    delete: operations["split_config_destroy"];
    patch: operations["split_config_partial_update"];
  };
  "/api/task_log/": {
    get: operations["task_log_list"];
  };
  "/api/task_log/{id}/": {
    get: operations["task_log_retrieve"];
  };
  "/api/token/": {
    /**
     * Takes a set of user credentials and returns an access and refresh JSON web
     * token pair to prove the authentication of those credentials.
     */
    post: operations["token_create"];
  };
  "/api/token/refresh/": {
    /**
     * Takes a refresh type JSON web token and returns an access type JSON web
     * token if the refresh token is valid.
     */
    post: operations["token_refresh_create"];
  };
  "/api/trained_model/": {
    get: operations["trained_model_list"];
    post: operations["trained_model_create"];
  };
  "/api/trained_model/{id}/": {
    get: operations["trained_model_retrieve"];
    put: operations["trained_model_update"];
    delete: operations["trained_model_destroy"];
    patch: operations["trained_model_partial_update"];
  };
  "/api/training_data/": {
    get: operations["training_data_list"];
    post: operations["training_data_create"];
  };
  "/api/training_data/{id}/": {
    get: operations["training_data_retrieve"];
    put: operations["training_data_update"];
    delete: operations["training_data_destroy"];
    patch: operations["training_data_partial_update"];
  };
}

export interface components {
  schemas: {
    EvaluationConfig: {
      id: number;
      name?: string | null;
      cutoff?: number;
      target_metric?: components["schemas"]["TargetMetricEnum"];
    };
    ModelConfiguration: {
      id: number;
      name?: string | null;
      recommender_class_name: string;
      parameters_json: string;
      ins_datetime: string;
      upd_datetime: string;
      project: number;
    };
    ParameterTuningJob: {
      id: number;
      name?: string | null;
      n_tasks_parallel?: number;
      n_trials?: number;
      memory_budget?: number;
      timeout_overall?: number | null;
      timeout_singlestep?: number | null;
      random_seed?: number | null;
      ins_datetime: string;
      upd_datetime: string;
      data: number;
      split?: number | null;
      evaluation: number;
      best_config?: number | null;
      tuned_model?: number | null;
    };
    PatchedEvaluationConfig: {
      id?: number;
      name?: string | null;
      cutoff?: number;
      target_metric?: components["schemas"]["TargetMetricEnum"];
    };
    PatchedModelConfiguration: {
      id?: number;
      name?: string | null;
      recommender_class_name?: string;
      parameters_json?: string;
      ins_datetime?: string;
      upd_datetime?: string;
      project?: number;
    };
    PatchedParameterTuningJob: {
      id?: number;
      name?: string | null;
      n_tasks_parallel?: number;
      n_trials?: number;
      memory_budget?: number;
      timeout_overall?: number | null;
      timeout_singlestep?: number | null;
      random_seed?: number | null;
      ins_datetime?: string;
      upd_datetime?: string;
      data?: number;
      split?: number | null;
      evaluation?: number;
      best_config?: number | null;
      tuned_model?: number | null;
    };
    PatchedProject: {
      id?: number;
      name?: string;
      user_column?: string;
      item_column?: string;
      time_column?: string | null;
      ins_datetime?: string;
      upd_datetime?: string;
    };
    PatchedSplitConfig: {
      id?: number;
      name?: string | null;
      scheme?: components["schemas"]["SchemeEnum"];
      heldout_ratio?: number;
      n_heldout?: number | null;
      test_user_ratio?: number;
      n_test_users?: number | null;
      random_seed?: number;
    };
    PatchedTrainedModel: {
      id?: number;
      name?: string | null;
      model_path?: string | null;
      ins_datetime?: string;
      upd_datetime?: string;
      configuration?: number;
      data_loc?: number;
    };
    PatchedTrainingData: {
      id?: number;
      upload_path?: string;
      ins_datetime?: string;
      upd_datetime?: string;
      project?: number;
    };
    Project: {
      id: number;
      name: string;
      user_column: string;
      item_column: string;
      time_column?: string | null;
      ins_datetime: string;
      upd_datetime: string;
    };
    SchemeEnum: "RG" | "TG" | "TU";
    SplitConfig: {
      id: number;
      name?: string | null;
      scheme?: components["schemas"]["SchemeEnum"];
      heldout_ratio?: number;
      n_heldout?: number | null;
      test_user_ratio?: number;
      n_test_users?: number | null;
      random_seed?: number;
    };
    TargetMetricEnum: "ndcg" | "map" | "recall" | "hit";
    TaskLog: {
      id: number;
      contents?: string;
      ins_datetime: string;
      task: number;
    };
    TokenObtainPair: {
      username: string;
      password: string;
      access: string;
      refresh: string;
    };
    TokenRefresh: {
      access: string;
      refresh: string;
    };
    TrainedModel: {
      id: number;
      name?: string | null;
      model_path?: string | null;
      ins_datetime: string;
      upd_datetime: string;
      configuration: number;
      data_loc: number;
    };
    TrainingData: {
      id: number;
      upload_path: string;
      ins_datetime: string;
      upd_datetime: string;
      project: number;
    };
  };
}

export interface operations {
  evaluation_config_list: {
    parameters: {
      query: {
        id?: number;
        name?: string;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["EvaluationConfig"][];
        };
      };
    };
  };
  evaluation_config_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["EvaluationConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EvaluationConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["EvaluationConfig"];
        "multipart/form-data": components["schemas"]["EvaluationConfig"];
      };
    };
  };
  evaluation_config_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this evaluation config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["EvaluationConfig"];
        };
      };
    };
  };
  evaluation_config_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this evaluation config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["EvaluationConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EvaluationConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["EvaluationConfig"];
        "multipart/form-data": components["schemas"]["EvaluationConfig"];
      };
    };
  };
  evaluation_config_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this evaluation config. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  evaluation_config_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this evaluation config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["EvaluationConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedEvaluationConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedEvaluationConfig"];
        "multipart/form-data": components["schemas"]["PatchedEvaluationConfig"];
      };
    };
  };
  model_configuration_list: {
    parameters: {
      query: {
        id?: number;
        project?: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ModelConfiguration"][];
        };
      };
    };
  };
  model_configuration_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["ModelConfiguration"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ModelConfiguration"];
        "application/x-www-form-urlencoded": components["schemas"]["ModelConfiguration"];
        "multipart/form-data": components["schemas"]["ModelConfiguration"];
      };
    };
  };
  model_configuration_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this model configuration. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ModelConfiguration"];
        };
      };
    };
  };
  model_configuration_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this model configuration. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ModelConfiguration"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ModelConfiguration"];
        "application/x-www-form-urlencoded": components["schemas"]["ModelConfiguration"];
        "multipart/form-data": components["schemas"]["ModelConfiguration"];
      };
    };
  };
  model_configuration_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this model configuration. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  model_configuration_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this model configuration. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ModelConfiguration"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedModelConfiguration"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedModelConfiguration"];
        "multipart/form-data": components["schemas"]["PatchedModelConfiguration"];
      };
    };
  };
  parameter_tuning_job_list: {
    parameters: {
      query: {
        data__project?: number;
        id?: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ParameterTuningJob"][];
        };
      };
    };
  };
  parameter_tuning_job_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["ParameterTuningJob"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ParameterTuningJob"];
        "application/x-www-form-urlencoded": components["schemas"]["ParameterTuningJob"];
        "multipart/form-data": components["schemas"]["ParameterTuningJob"];
      };
    };
  };
  parameter_tuning_job_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this parameter tuning job. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ParameterTuningJob"];
        };
      };
    };
  };
  parameter_tuning_job_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this parameter tuning job. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ParameterTuningJob"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ParameterTuningJob"];
        "application/x-www-form-urlencoded": components["schemas"]["ParameterTuningJob"];
        "multipart/form-data": components["schemas"]["ParameterTuningJob"];
      };
    };
  };
  parameter_tuning_job_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this parameter tuning job. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  parameter_tuning_job_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this parameter tuning job. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["ParameterTuningJob"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedParameterTuningJob"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedParameterTuningJob"];
        "multipart/form-data": components["schemas"]["PatchedParameterTuningJob"];
      };
    };
  };
  project_list: {
    parameters: {
      query: {
        id?: number;
        name?: string;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["Project"][];
        };
      };
    };
  };
  project_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["Project"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Project"];
        "application/x-www-form-urlencoded": components["schemas"]["Project"];
        "multipart/form-data": components["schemas"]["Project"];
      };
    };
  };
  project_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this project. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["Project"];
        };
      };
    };
  };
  project_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this project. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["Project"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Project"];
        "application/x-www-form-urlencoded": components["schemas"]["Project"];
        "multipart/form-data": components["schemas"]["Project"];
      };
    };
  };
  project_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this project. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  project_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this project. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["Project"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedProject"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedProject"];
        "multipart/form-data": components["schemas"]["PatchedProject"];
      };
    };
  };
  split_config_list: {
    parameters: {
      query: {
        id?: number;
        name?: string;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["SplitConfig"][];
        };
      };
    };
  };
  split_config_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["SplitConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SplitConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["SplitConfig"];
        "multipart/form-data": components["schemas"]["SplitConfig"];
      };
    };
  };
  split_config_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this split config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["SplitConfig"];
        };
      };
    };
  };
  split_config_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this split config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["SplitConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SplitConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["SplitConfig"];
        "multipart/form-data": components["schemas"]["SplitConfig"];
      };
    };
  };
  split_config_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this split config. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  split_config_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this split config. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["SplitConfig"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedSplitConfig"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedSplitConfig"];
        "multipart/form-data": components["schemas"]["PatchedSplitConfig"];
      };
    };
  };
  task_log_list: {
    parameters: {
      query: {
        id?: number;
        task__taskandparameterjoblink__job?: number;
        task__taskandtrainedmodellink__model?: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TaskLog"][];
        };
      };
    };
  };
  task_log_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this task log. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TaskLog"];
        };
      };
    };
  };
  /**
   * Takes a set of user credentials and returns an access and refresh JSON web
   * token pair to prove the authentication of those credentials.
   */
  token_create: {
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TokenObtainPair"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TokenObtainPair"];
        "application/x-www-form-urlencoded": components["schemas"]["TokenObtainPair"];
        "multipart/form-data": components["schemas"]["TokenObtainPair"];
      };
    };
  };
  /**
   * Takes a refresh type JSON web token and returns an access type JSON web
   * token if the refresh token is valid.
   */
  token_refresh_create: {
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TokenRefresh"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TokenRefresh"];
        "application/x-www-form-urlencoded": components["schemas"]["TokenRefresh"];
        "multipart/form-data": components["schemas"]["TokenRefresh"];
      };
    };
  };
  trained_model_list: {
    parameters: {
      query: {
        data_loc?: number;
        data_loc__project?: number;
        id?: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainedModel"][];
        };
      };
    };
  };
  trained_model_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["TrainedModel"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TrainedModel"];
        "application/x-www-form-urlencoded": components["schemas"]["TrainedModel"];
        "multipart/form-data": components["schemas"]["TrainedModel"];
      };
    };
  };
  trained_model_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this trained model. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainedModel"];
        };
      };
    };
  };
  trained_model_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this trained model. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainedModel"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TrainedModel"];
        "application/x-www-form-urlencoded": components["schemas"]["TrainedModel"];
        "multipart/form-data": components["schemas"]["TrainedModel"];
      };
    };
  };
  trained_model_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this trained model. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  trained_model_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this trained model. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainedModel"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedTrainedModel"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedTrainedModel"];
        "multipart/form-data": components["schemas"]["PatchedTrainedModel"];
      };
    };
  };
  training_data_list: {
    parameters: {
      query: {
        id?: number;
        project?: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainingData"][];
        };
      };
    };
  };
  training_data_create: {
    responses: {
      201: {
        content: {
          "application/json": components["schemas"]["TrainingData"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TrainingData"];
        "application/x-www-form-urlencoded": components["schemas"]["TrainingData"];
        "multipart/form-data": components["schemas"]["TrainingData"];
      };
    };
  };
  training_data_retrieve: {
    parameters: {
      path: {
        /** A unique integer value identifying this training data. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainingData"];
        };
      };
    };
  };
  training_data_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this training data. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainingData"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TrainingData"];
        "application/x-www-form-urlencoded": components["schemas"]["TrainingData"];
        "multipart/form-data": components["schemas"]["TrainingData"];
      };
    };
  };
  training_data_destroy: {
    parameters: {
      path: {
        /** A unique integer value identifying this training data. */
        id: number;
      };
    };
    responses: {
      /** No response body */
      204: never;
    };
  };
  training_data_partial_update: {
    parameters: {
      path: {
        /** A unique integer value identifying this training data. */
        id: number;
      };
    };
    responses: {
      200: {
        content: {
          "application/json": components["schemas"]["TrainingData"];
        };
      };
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PatchedTrainingData"];
        "application/x-www-form-urlencoded": components["schemas"]["PatchedTrainingData"];
        "multipart/form-data": components["schemas"]["PatchedTrainingData"];
      };
    };
  };
}
