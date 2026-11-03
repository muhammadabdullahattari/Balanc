import { QueryClient, QueryCache, MutationCache } from "@tanstack/react-query";
import { createApiClient } from "./client";

function handleSessionError(error: Error) {
  if (
    error instanceof ApiError &&
    error.status === 401 &&
    error.code !== "ERR_GOOGLE_AUTH" &&
    error.code !== "ERR_MFA_REQUIRED" &&
    error.code !== "ERR_MFA_CODE" &&
    error.invalidatesSession
  ) {
    transport.clearSession();
    queryClient.clear();
    queryClient.setQueryData(["session"], null);
  }
}
export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: handleSessionError }),
  mutationCache: new MutationCache({ onError: handleSessionError }),
  defaultOptions: {
    queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});
export const transport = createApiClient(
  import.meta.env.VITE_API_ORIGIN || window.location.origin,
);
export const api = transport.client;
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string | null,
    public fields?: { location: (string | number)[]; type: string }[],
    public code?: string,
    public retryAfter?: number,
    public invalidatesSession = true,
  ) {
    super(message);
  }
}
export async function result<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const reply = await request.catch((error: unknown) => {
    if (error instanceof DOMException && error.name === "TimeoutError")
      throw new ApiError(
        "The request timed out. If this was a reviewed entry, retry the same request rather than creating another.",
        0,
      );
    if (error instanceof TypeError)
      throw new ApiError(
        "We couldn't reach the server. Check your connection and try again.",
        0,
      );
    throw error;
  });
  if (reply.error || !reply.response.ok) {
    const error = reply.error as {
      error?: {
        message?: string;
        code?: string;
        fields?: { location: (string | number)[]; type: string }[];
      };
      request_id?: string;
    };
    throw new ApiError(
      friendlyError(error?.error?.code, error?.error?.message) ||
        "We couldn't complete this request. Please try again.",
      reply.response.status,
      error?.request_id || reply.response.headers.get("X-Request-ID"),
      error?.error?.fields,
      error?.error?.code,
      Number(reply.response.headers.get("Retry-After")) || undefined,
      !/\/auth\/(?:login|google\/(?:login|link))$/.test(
        new URL(reply.response.url || window.location.origin).pathname,
      ),
    );
  }
  if (reply.data === undefined)
    throw new Error("The server returned an empty response.");
  return reply.data;
}
export const keys = {
  session: ["session"] as const,
  businesses: ["businesses"] as const,
  business: (id: string) => ["business", id] as const,
};
export function clearSession() {
  transport.clearSession();
  queryClient.clear();
}

function friendlyError(code?: string, message?: string) {
  const messages: Record<string, string> = {
    ERR_AUTH:
      "We couldn't verify your sign-in. Check your email, password and authenticator or recovery code, then try again.",
    ERR_MFA_CODE:
      "The authenticator or recovery code is incorrect, expired or already used. Try a fresh code.",
    ERR_MFA_REQUIRED:
      "Enter your authenticator or recovery code, then try signing in again.",
    ERR_LINK_EXPIRED:
      "This code is incorrect, expired or replaced. Request a new code and use the newest email.",
    ERR_CSRF: "Your secure session changed. Refresh this page and try again.",
    ERR_RATE_LIMIT: "Too many attempts. Please wait before trying again.",
    ERR_PASSWORD:
      "Use 15–128 characters, including an uppercase letter, lowercase letter, number and special character.",
    ERR_MAIL_UNAVAILABLE:
      "We couldn't send the email. Please try again shortly or contact support.",
  };
  return (code && messages[code]) || message;
}
