import { onUnmounted, ref } from "vue";

const MAX_RECONNECT_ATTEMPTS = 10;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

export function useWebSocket(path: string) {
  const messages = ref<any[]>([]);
  const isConnected = ref(false);
  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let intentionalClose = false;

  function buildUrl(): string {
    const wsBase = import.meta.env.VITE_WS_BASE_URL;
    if (wsBase) {
      return `${wsBase}${path}`;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${path}`;
  }

  function scheduleReconnect() {
    if (intentionalClose || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      return;
    }
    const delay = Math.min(BASE_DELAY_MS * Math.pow(2, reconnectAttempts), MAX_DELAY_MS);
    reconnectAttempts++;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    intentionalClose = false;
    ws = new WebSocket(buildUrl());

    ws.onopen = () => {
      isConnected.value = true;
      reconnectAttempts = 0;
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      messages.value.push(data);
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
  }

  function send(data: any) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  onUnmounted(() => {
    disconnect();
  });

  return { messages, isConnected, connect, disconnect, send };
}
