import { type Ref, getCurrentInstance, onUnmounted, ref } from "vue";

import { useAuthStore } from "@/stores/auth";

const MAX_RECONNECT_ATTEMPTS = 10;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;
const MAX_MESSAGES = 500;

export type ConnectionState = "disconnected" | "connecting" | "connected" | "reconnecting";

export function useWebSocket<T = unknown>(path: string) {
  const messages: Ref<T[]> = ref([]);
  const isConnected = ref(false);
  const connectionState = ref<ConnectionState>("disconnected");
  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let intentionalClose = false;
  let lastSeq = -1;

  function buildUrl(): string {
    const auth = useAuthStore();
    const wsBase = import.meta.env.VITE_WS_BASE_URL;
    const base = wsBase
      ? `${wsBase}${path}`
      : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${path}`;
    if (auth.accessToken) {
      return `${base}?token=${encodeURIComponent(auth.accessToken)}`;
    }
    return base;
  }

  function scheduleReconnect() {
    if (intentionalClose || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      connectionState.value = "disconnected";
      return;
    }
    connectionState.value = "reconnecting";
    const delay = Math.min(BASE_DELAY_MS * Math.pow(2, reconnectAttempts), MAX_DELAY_MS);
    reconnectAttempts++;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    intentionalClose = false;
    connectionState.value = reconnectAttempts > 0 ? "reconnecting" : "connecting";
    lastSeq = -1;
    ws = new WebSocket(buildUrl());

    ws.onopen = () => {
      isConnected.value = true;
      connectionState.value = "connected";
      reconnectAttempts = 0;
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      // Respond to server heartbeat pings
      if (data.type === "ping") {
        send({ type: "pong" });
        return;
      }
      // Sequence number tracking
      if (typeof data.seq === "number") {
        if (data.seq <= lastSeq) {
          console.warn(`[useWebSocket] Duplicate message: seq=${data.seq}, lastSeq=${lastSeq}. Discarding.`);
          return;
        }
        if (data.seq > lastSeq + 1) {
          console.warn(`[useWebSocket] Sequence gap: expected=${lastSeq + 1}, received=${data.seq}.`);
        }
        lastSeq = data.seq;
      }
      messages.value.push(data as T);
      if (messages.value.length > MAX_MESSAGES) {
        messages.value = messages.value.slice(-MAX_MESSAGES);
      }
    };

    ws.onclose = () => {
      isConnected.value = false;
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = () => {
      isConnected.value = false;
    };
  }

  function disconnect() {
    intentionalClose = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    reconnectAttempts = 0;
    connectionState.value = "disconnected";
  }

  function send(data: unknown) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  // Allow using this composable in plain unit tests (outside component setup).
  if (getCurrentInstance()) {
    onUnmounted(() => {
      disconnect();
    });
  }

  return { messages, isConnected, connectionState, connect, disconnect, send };
}
