import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "./client";
import type { City, Plan, Prefs, PrefsPatch, Weekly } from "./types";

export const usePrefs = () =>
  useQuery({ queryKey: ["prefs"], queryFn: () => apiJson<Prefs>("/api/user/prefs") });

export const useCities = () =>
  useQuery({
    queryKey: ["cities"],
    queryFn: () => apiJson<City[]>("/api/user/cities"),
    staleTime: Infinity,
  });

export const useWeekly = () =>
  useQuery({
    queryKey: ["dashboard", "weekly"],
    queryFn: () => apiJson<Weekly>("/api/dashboard/weekly"),
  });

export const usePlans = () =>
  useQuery({ queryKey: ["plans"], queryFn: () => apiJson<Plan[]>("/api/plans") });

export function usePatchPrefs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: PrefsPatch) =>
      apiJson<Prefs>("/api/user/prefs", { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: (prefs) => {
      queryClient.setQueryData(["prefs"], prefs);
      // Mode, city and budget all change what the dashboard shows.
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useCancelPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (planId: string) => apiJson<Plan>(`/api/plans/${planId}`, { method: "DELETE" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["plans"] }),
  });
}

export function useRefresh() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiJson<{ run_id: string; estimated_completion_at: string }>("/api/dashboard/refresh", {
        method: "POST",
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
  });
}
