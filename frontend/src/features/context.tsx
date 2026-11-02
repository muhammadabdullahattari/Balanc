import { createContext, useContext } from "react";
import type { components } from "../api/generated/schema";
export type Business = components["schemas"]["BusinessRead"];
export const BusinessContext = createContext<Business | null>(null);
export function useBusiness() {
  const value = useContext(BusinessContext);
  if (!value) throw new Error("Choose a business first.");
  return value;
}
export function today(timezone?: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const part = (name: string) => parts.find((p) => p.type === name)!.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export const AccessContext = createContext<{
  role: string;
  can_post: boolean;
  can_invite: boolean;
} | null>(null);
export function useAccess() {
  return useContext(AccessContext);
}
