import { useQuery } from "@tanstack/react-query";
import { api } from "./api/client";
import type { Company, Domain, User } from "./api/types";

export function useDomains() {
  return useQuery({
    queryKey: ["domains"],
    queryFn: async () => (await api.get<Domain[]>("/domains")).data,
    staleTime: 60_000,
  });
}

export function useAgents(enabled = true) {
  return useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get<User[]>("/users/agents")).data,
    staleTime: 60_000,
    enabled,
  });
}

/** The signed-in user's company: profile, limits and today's usage. */
export function useCompany(enabled = true) {
  return useQuery({
    queryKey: ["my-company"],
    queryFn: async () => (await api.get<Company>("/company")).data,
    staleTime: 30_000,
    enabled,
  });
}

/** Drop empty values so they aren't sent as query params. */
export function cleanParams(params: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== undefined && v !== null && v !== false));
}
