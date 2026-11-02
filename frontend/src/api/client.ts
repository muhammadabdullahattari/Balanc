import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./generated/schema";

/** One browser transport. Session cookies stay HttpOnly; writes are never retried. */
export function createApiClient(baseUrl: string, timeoutMs = 30_000) {
  const url = new URL(baseUrl, window.location.origin);
  if (url.origin !== window.location.origin && url.protocol !== "https:") {
    throw new Error("Cross-origin API connections require HTTPS");
  }
  let csrf: string | undefined;
  let refreshRequest: Promise<boolean> | undefined;
  let csrfRequest: Promise<string> | undefined;
  const client = createClient<paths>({
    baseUrl: url.origin,
    credentials: "include",
  });
  const middleware: Middleware = {
    async onRequest({ request }) {
      if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
        if (!csrf) {
          csrfRequest ??= client
            .GET("/api/v1/auth/csrf")
            .then(({ data, error }) => {
              if (error || !data)
                throw new Error("Unable to initialize secure request");
              return data.csrf_token;
            });
          try {
            csrf = await csrfRequest;
          } finally {
            csrfRequest = undefined;
          }
        }
        request.headers.set("X-CSRF-Token", csrf);
      }
      return new Request(request, {
        signal: AbortSignal.any([
          request.signal,
          AbortSignal.timeout(timeoutMs),
        ]),
      });
    },
    async onResponse({ response, request }) {
      const pathname = new URL(request.url).pathname;
      if (
        response.status === 401 &&
        request.method === "GET" &&
        pathname !== "/api/v1/auth/csrf"
      ) {
        refreshRequest ??= client
          .POST("/api/v1/auth/refresh")
          .then((r) => r.response.ok)
          .catch(() => false);
        let refreshed = false;
        try {
          refreshed = await refreshRequest;
        } finally {
          refreshRequest = undefined;
        }
        if (refreshed)
          return fetch(new Request(request, { credentials: "include" }));
      }
      // Login/refresh/logout can replace the session's CSRF value.
      if (
        response.status === 401 ||
        response.status === 403 ||
        (request.method !== "GET" &&
          new URL(request.url).pathname.startsWith("/api/v1/auth/"))
      ) {
        csrf = undefined;
      }
      return response;
    },
  };
  client.use(middleware);
  return {
    client,
    clearSession: () => {
      csrf = undefined;
    },
  };
}
