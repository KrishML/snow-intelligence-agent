import { useState } from "react";
import { useUi } from "../store";

export function LoginGate() {
  const setToken = useUi((s) => s.setToken);
  const [value, setValue] = useState("");

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold">Snow Intelligence Agent</h1>
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) setToken(value.trim());
        }}
      >
        <label className="text-sm font-medium" htmlFor="token">
          API token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="rounded border border-slate-300 px-3 py-2"
        />
        <button className="rounded bg-sky-700 px-3 py-2 font-medium text-white hover:bg-sky-800">
          Sign in
        </button>
      </form>
    </main>
  );
}
