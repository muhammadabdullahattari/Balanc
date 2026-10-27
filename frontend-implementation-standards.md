# LedgerPilot Web Implementation Standards

These standards are for the responsive React, TypeScript, and Vite client defined by the LedgerPilot SRS. The reference frontend guide supplied for this project targets Next.js; Next.js App Router rules do not apply here.

## Structure and UI

- Keep one canonical route, component, hook, and API operation per product capability. Reuse shared components and design tokens.
- Use semantic HTML, labeled controls, keyboard access, visible focus, sufficient contrast, and clear loading, empty, error, success, partial, and stale-data states.
- Keep the product responsive in a browser. Do not scaffold native mobile applications for P1.
- Use the SRS-approved component and styling system. Avoid one-off design systems or magic visual values when a shared token exists.
- Use React Hook Form and Zod for client-side form usability; FastAPI/Pydantic remains authoritative for validation.

## API and server state

- Generate TypeScript API types and the client from the reviewed FastAPI OpenAPI contract; do not hand-edit generated output.
- Route every request through one typed API client that owns the base URL, credentials, cancellation, error normalization, and safe retry policy. Components must not make ad hoc `fetch` or Axios calls.
- Use TanStack Query as the source of server state. Keep local component state for transient UI only; do not mirror API records into a second client-side store.
- Keep query keys centralized and invalidate/update affected queries after mutations. Roll back optimistic changes if a mutation fails.
- Never retry a financial write blindly. Retry only safe or explicitly idempotent operations, using request IDs/idempotency keys as required by the API contract.
- The browser never connects directly to PostgreSQL or receives server secrets. Client-side role checks affect presentation only; the API enforces permissions.

## Identity and privacy

- Follow the LedgerPilot SRS session design: secure HttpOnly cookies, explicit CSRF protection, approved Origin checks, refresh rotation, and session-expiry handling.
- Never put session tokens or private configuration in localStorage, a Vite `VITE_*` variable, a source map, or a browser log.
- On logout or session expiry, clear sensitive cached data and return the user to an explicit signed-out state.
- Display only tenant-authorized data and avoid leaking another business's existence through errors or search results.

## Accounting presentation

- Render accounting amounts, dates, currency, and cash/accrual basis using server-provided business context and shared formatting utilities.
- Financial totals and report rollups come from authoritative backend APIs. The UI must not derive ledger balances by summing a partial or paginated transaction list.
- Present uncertain bank matches, document extraction, categorizations, and AI suggestions for review with source evidence and confidence where specified. Never imply a proposal has posted to the books.
- For graphs, expose date range, currency, accounting basis, freshness, and accessible text/table alternatives where the SRS requires them.

## Accessibility, performance, and testing

- Support keyboard and screen-reader workflows, reduced motion, and narrow, medium, and wide browser layouts without horizontal overflow.
- Avoid duplicate requests, unbounded rendering of long tables, unnecessary re-renders, and unmanaged event subscriptions.
- Component tests may use contract-accurate network handlers; those handlers must not enter production code. Playwright UI-only checks may use the planned mock adapter before integration.
- Integration and deployed E2E checks use the real API and isolated PostgreSQL-backed data. Cover permission failures and accounting side effects, not only visible text.
- Run the relevant lint, type, unit/component, integration, accessibility, and production-build checks at their planned phase gates. Report checks that could not run and why.
