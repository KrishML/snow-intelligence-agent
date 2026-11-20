import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { apiJson } from "../api/client";
import { useUi } from "../store";
import { chatReducer, initialChatState, type ServerEvent } from "./reducer";

type Connection = "idle" | "connecting" | "open" | "closed";

export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [connection, setConnection] = useState<Connection>("idle");
  const socket = useRef<WebSocket | null>(null);
  const sessionId = useRef<string | null>(null);

  const connect = useCallback(async () => {
    setConnection("connecting");
    try {
      const created = await apiJson<{ session_id: string }>("/api/chat/session", {
        method: "POST",
        body: JSON.stringify({}),
      });
      sessionId.current = created.session_id;
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(
        `${scheme}://${window.location.host}/api/chat/session/${created.session_id}/messages`,
      );
      ws.onopen = () => {
        // Browsers cannot set WebSocket headers, so authenticate with the first frame.
        ws.send(JSON.stringify({ type: "auth", token: useUi.getState().token }));
        setConnection("open");
      };
      ws.onmessage = (event) => dispatch(JSON.parse(event.data) as ServerEvent);
      ws.onclose = () => {
        setConnection("closed");
        dispatch({ type: "disconnected", reason: "Chat connection closed." });
      };
      socket.current = ws;
    } catch (error) {
      setConnection("closed");
      dispatch({
        type: "disconnected",
        reason: error instanceof Error ? error.message : "Could not start chat.",
      });
    }
  }, []);

  const send = useCallback((content: string) => {
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    dispatch({ type: "user_message", content });
    ws.send(JSON.stringify({ content }));
  }, []);

  const end = useCallback(async () => {
    const id = sessionId.current;
    socket.current?.close();
    socket.current = null;
    sessionId.current = null;
    if (id) await apiJson(`/api/chat/session/${id}/end`, { method: "POST" }).catch(() => undefined);
  }, []);

  useEffect(
    () => () => {
      socket.current?.close();
    },
    [],
  );

  return { state, connection, connect, send, end };
}
