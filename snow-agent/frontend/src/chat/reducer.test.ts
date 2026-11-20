import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState } from "./reducer";

describe("chatReducer", () => {
  it("streams tokens into one assistant message", () => {
    let state = chatReducer(initialChatState, { type: "user_message", content: "hi" });
    expect(state.busy).toBe(true);
    state = chatReducer(state, { type: "token", content: "Hel" });
    state = chatReducer(state, { type: "token", content: "lo" });
    state = chatReducer(state, { type: "message_complete" });
    expect(state.messages.map((m) => [m.role, m.content])).toEqual([
      ["user", "hi"],
      ["assistant", "Hello"],
    ]);
    expect(state.busy).toBe(false);
  });

  it("starts a new assistant message after a user turn", () => {
    let state = chatReducer(initialChatState, { type: "token", content: "a" });
    state = chatReducer(state, { type: "user_message", content: "q" });
    state = chatReducer(state, { type: "token", content: "b" });
    expect(state.messages.map((m) => m.content)).toEqual(["a", "q", "b"]);
  });

  it("shows tool status until the next token", () => {
    let state = chatReducer(initialChatState, { type: "tool_call", name: "fetch_live_snow" });
    expect(state.toolStatus).toBe("fetch_live_snow");
    state = chatReducer(state, { type: "token", content: "x" });
    expect(state.toolStatus).toBeNull();
  });

  it("surfaces errors and disconnects and clears busy", () => {
    let state = chatReducer(initialChatState, { type: "user_message", content: "hi" });
    state = chatReducer(state, { type: "error", detail: "boom" });
    expect(state).toMatchObject({ busy: false, error: "boom" });
    state = chatReducer(state, { type: "disconnected", reason: "closed" });
    expect(state.error).toBe("closed");
  });

  it("stores the plan card", () => {
    const plan = { id: "p1" } as never;
    expect(chatReducer(initialChatState, { type: "plan_card", plan }).plan).toBe(plan);
  });
});
