import { QueryClient, QueryCache, MutationCache } from "@tanstack/react-query";
import { createApiClient } from "./client";

function handleSessionError(error: Error) {
  if (error instanceof ApiError && error.status === 401) {
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
        fields?: { location: (string | number)[]; type: string }[];
      };
      request_id?: string;
    };
    throw new ApiError(
      error?.error?.message ||
        "We couldn't complete this request. Please try again.",
      reply.response.status,
      error?.request_id,
      error?.error?.fields,
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
