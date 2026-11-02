import type { createApiClient } from "../src/api/client";

declare const client: ReturnType<typeof createApiClient>["client"];

// These examples are compiled, never executed or included in the product bundle.
client.GET("/api/v1/businesses", { params: { query: { limit: 25 } } });
client.GET("/api/v1/businesses/{business_id}", {
  params: { path: { business_id: "synthetic-id" } },
});
// @ts-expect-error Unknown routes must fail at compile time.
client.GET("/api/v1/nonexistent");
// @ts-expect-error The API requires an integer pagination limit.
client.GET("/api/v1/businesses", { params: { query: { limit: "unbounded" } } });
// @ts-expect-error Business-scoped requests must include their path identifier.
client.GET("/api/v1/businesses/{business_id}");
