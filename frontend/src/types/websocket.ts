// WebSocket message types (not covered by OpenAPI schema).

export interface WsStatusData {
  best_score?: number;
  error?: string;
}

export interface WsStatusUpdate {
  type: "job_status_update";
  status: "running" | "completed" | "error";
  data: WsStatusData;
  seq?: number;
}

export interface WsLogMessage {
  type: "task_log_message";
  message: string;
  seq?: number;
}

export type WsMessage = WsStatusUpdate | WsLogMessage;
