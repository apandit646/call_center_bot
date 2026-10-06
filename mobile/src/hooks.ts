import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Company, User } from "@/api/types";

/** The value, once it has stopped changing for a moment: a search box does not ask the server on every key press. */
export function useDebounced<V>(value: V, ms = 350): V {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
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
