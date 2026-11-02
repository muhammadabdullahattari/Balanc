import { useQueryClient, useMutation } from "@tanstack/react-query";
import { api, result, keys } from "../api/runtime";
import { useBusiness } from "./context";
import { Button, ErrorNotice } from "../components/ui";
import type { components } from "../api/generated/schema";
type Resource = "accounts" | "parties";
type Item =
  components["schemas"]["AccountRead"] | components["schemas"]["PartyRead"];
export function LookupMore({ resource }: { resource: Resource }) {
  const b = useBusiness(),
    cache = useQueryClient(),
    key = [...keys.business(b.id), resource];
  const data = cache.getQueryData<{
    items: Item[];
    next_cursor?: string | null;
  }>(key);
  const more = useMutation({
    mutationFn: async () => {
      const cursor = data?.next_cursor;
      if (!cursor) return;
      const options = {
        params: { path: { business_id: b.id }, query: { cursor, limit: 200 } },
      };
      const page =
        resource === "accounts"
          ? await result(
              api.GET("/api/v1/businesses/{business_id}/accounts", options),
            )
          : await result(
              api.GET("/api/v1/businesses/{business_id}/parties", options),
            );
      cache.setQueryData(key, (previous: typeof data) => ({
        items: [...(previous?.items || []), ...page.items].filter(
          (item, i, items) => items.findIndex((v) => v.id === item.id) === i,
        ),
        next_cursor: page.next_cursor,
      }));
    },
  });
  return data?.next_cursor ? (
    <div className="notice">
      <span>
        More {resource === "parties" ? "contacts" : resource} are available for
        the selection fields.
      </span>
      <Button
        variant="secondary"
        busy={more.isPending}
        onClick={() => more.mutate()}
      >
        Load next 200
      </Button>
      <ErrorNotice error={more.error} />
    </div>
  ) : null;
}
