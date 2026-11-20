import type { Plan } from "../api/types";

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
}

export interface ChatState {
  messages: ChatMessage[];
  busy: boolean;
  error: string | null;
  plan: Plan | null;
  toolStatus: string | null;
}

export type ServerEvent =
  | { type: "token"; content: string }
  | { type: "message_complete" }
  | { type: "error"; detail: string }
  | { type: "tool_call"; name: string }
  | { type: "plan_card"; plan: Plan };

export type ChatAction =
  | { type: "user_message"; content: string }
  | { type: "disconnected"; reason: string }
  | ServerEvent;

export const initialChatState: ChatState = {
  messages: [],
  busy: false,
  error: null,
  plan: null,
  toolStatus: null,
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "user_message":
      return {
        ...state,
        busy: true,
        error: null,
        messages: [
          ...state.messages,
          { id: state.messages.length, role: "user", content: action.content },
        ],
      };
    case "token": {
      const last = state.messages[state.messages.length - 1];
      const messages =
        last?.role === "assistant"
          ? [...state.messages.slice(0, -1), { ...last, content: last.content + action.content }]
          : [...state.messages, { id: state.messages.length, role: "assistant" as const, content: action.content }];
      return { ...state, messages, toolStatus: null };
    }
    case "tool_call":
      return { ...state, toolStatus: action.name };
    case "message_complete":
      return { ...state, busy: false, toolStatus: null };
    case "plan_card":
      return { ...state, plan: action.plan };
    case "error":
      return { ...state, busy: false, toolStatus: null, error: action.detail };
    case "disconnected":
      return { ...state, busy: false, toolStatus: null, error: action.reason };
  }
}
