import { useEffect, useRef, useState } from "react";
import { useChat } from "../chat/useChat";
import { PlanCard } from "./PlanCard";

export function ChatPanel() {
  const { state, connection, connect, send, end } = useChat();
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void connect();
    return () => {
      void end();
    };
  }, [connect, end]);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [state.messages, state.plan]);

  const ready = connection === "open" && !state.busy;

  return (
    <section aria-label="Chat" className="flex h-full min-h-[24rem] flex-col rounded-lg border border-slate-200 bg-white">
      <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
        {state.messages.length === 0 && connection === "open" && (
          <p className="text-sm text-slate-500">
            Ask about snow conditions, flights, or request a plan for one of the top resorts.
          </p>
        )}
        {state.messages.map((message) => (
          <p
            key={message.id}
            className={`whitespace-pre-wrap rounded px-3 py-2 text-sm ${
              message.role === "user" ? "ml-8 bg-sky-700 text-white" : "mr-8 bg-slate-100"
            }`}
          >
            {message.content}
          </p>
        ))}
        {state.toolStatus && <p className="text-xs text-slate-500">Using {state.toolStatus}…</p>}
        {state.busy && !state.toolStatus && <p className="text-xs text-slate-500">Thinking…</p>}
        {state.plan && <PlanCard plan={state.plan} />}
        {connection === "connecting" && <p className="text-sm text-slate-500">Connecting…</p>}
        {state.error && (
          <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-800">
            {state.error}{" "}
            {connection === "closed" && (
              <button className="underline" onClick={() => void connect()}>
                Reconnect
              </button>
            )}
          </p>
        )}
        <div ref={bottom} />
      </div>
      <form
        className="flex gap-2 border-t border-slate-200 p-3"
        onSubmit={(event) => {
          event.preventDefault();
          const content = draft.trim();
          if (content && ready) {
            send(content);
            setDraft("");
          }
        }}
      >
        <input
          aria-label="Message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={connection !== "open"}
          placeholder="Ask the agent…"
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          disabled={!ready || !draft.trim()}
          className="rounded bg-sky-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </section>
  );
}
