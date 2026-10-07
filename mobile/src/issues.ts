// Shared by the issue-report screens and the menu badge.
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { IssueSummary } from "@/api/types";
import type { Tone } from "@/components/ui";

export const CATEGORIES = [
  ["calls", "Phone calls"], ["whatsapp", "WhatsApp"], ["email", "Email"], ["campaigns", "Campaigns"], ["knowledge", "AI answers"],
  ["billing", "Billing"], ["account", "Login / account"], ["bug", "Something broken"], ["other", "Something else"],
] as const;
export const CATEGORY: Record<string, string> = Object.fromEntries(CATEGORIES);
export const PRIORITY_TONE: Record<string, Tone> = { high: "red", normal: "blue", low: "grey" };
export function useIssueSummary(enabled = true) {
  return useQuery({
    queryKey: ["issues-summary"],
    queryFn: async () => (await api.get<IssueSummary>("/issues/summary")).data,
    refetchInterval: 60_000,
    enabled,
  });
}
