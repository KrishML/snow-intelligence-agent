import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UiState {
  token: string;
  setToken: (token: string) => void;
  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      token: "",
      setToken: (token) => set({ token }),
      chatOpen: false,
      setChatOpen: (chatOpen) => set({ chatOpen }),
    }),
    { name: "snow-agent-ui", partialize: (state) => ({ token: state.token }) },
  ),
);
