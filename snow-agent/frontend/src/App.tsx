import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import { Dashboard } from "./components/Dashboard";
import { LoginGate } from "./components/LoginGate";
import { useUi } from "./store";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

export default function App() {
  const token = useUi((s) => s.token);
  return (
    <QueryClientProvider client={queryClient}>
      {token ? <Dashboard /> : <LoginGate />}
    </QueryClientProvider>
  );
}
