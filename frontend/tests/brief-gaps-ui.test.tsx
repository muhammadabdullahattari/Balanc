import { render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, queryClient } from "../src/api/runtime";
import { AccessContext, BusinessContext } from "../src/features/context";
import { DocumentViewer } from "../src/components/document-viewer";
import { ReceiptCapture } from "../src/features/receipt-capture";
import { PurchaseOrdersAndCollections } from "../src/features/purchase-orders";
import { PortfolioTriage } from "../src/features/portfolio-triage";
import { ConnectorsAndApi } from "../src/features/connectors-and-api";

const FORBIDDEN_PATTERNS = [
  /\bP[1-4]\b/,
  /\bAI-\d{2}\b/,
  /\bSANDBOX\b/,
  /\bFernet\b/i,
  /\bUUID\b/,
  /\bSMTP\b/,
  /\bpending_review\b/,
];

const mockBusiness = {
  id: "42132f25-4948-45c0-9142-8def637b0a9c",
  name: "Acme Books",
  currency: "USD",
  currency_precision: 2,
  timezone: "UTC",
  basis: "accrual" as const,
  version: 2,
  policy_version: 1,
  fiscal_month: 1,
  jurisdiction: "US",
  p2_projects_available: true,
  p2_insights_available: true,
  p2_retention_available: true,
  p2_audit_available: true,
  p2_reconciliation_available: true,
  p2_micro_close_available: true,
  p2_prepaids_available: true,
};

describe("Phases A-H UI components & copy hygiene", () => {
  it("renders DocumentViewer with SVG bounding-box overlays and zoom controls", async () => {
    vi.spyOn(api, "GET").mockResolvedValue({
      response: new Response(null, { status: 200 }),
      data: new Blob(["fake-png"], { type: "image/png" }),
    } as never);
    if (!URL.createObjectURL) {
      URL.createObjectURL = () => "blob:preview";
      URL.revokeObjectURL = () => {};
    }

    const { container } = render(
      <DocumentViewer
        businessId={mockBusiness.id}
        evidenceId="ev-1"
        filename="invoice.pdf"
        regions={[
          {
            id: "h1",
            label: "Total amount",
            value: "1296.00",
            page: 1,
            left: 0.7,
            top: 0.78,
            right: 0.89,
            bottom: 0.82,
            confidence: 0.99,
          },
        ]}
      />,
    );

    expect(screen.getByText(/Zoom 100%/i)).toBeTruthy();
    expect(container.querySelector("svg rect")).toBeTruthy();
  });

  it("renders ReceiptCapture, PurchaseOrdersAndCollections, PortfolioTriage, and ConnectorsAndApi with zero developer jargon", async () => {
    queryClient.clear();
    vi.spyOn(api, "GET").mockImplementation((async (url: string) => {
      if (url === "/api/v1/businesses/{business_id}/purchase-orders") {
        return {
          response: new Response(null, { status: 200 }),
          data: { purchase_orders: [], matches: [] },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/collections/profile") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            profile: {
              tone: "warm",
              signature_name: "Accounts Team",
              auto_send_gentle: false,
              day_gentle: 3,
              day_firm: 14,
              day_final: 30,
              day_escalate_call: 60,
            },
            tasks: [],
          },
        };
      }
      if (url === "/api/v1/portfolio") {
        return {
          response: new Response(null, { status: 200 }),
          data: { items: [], next_cursor: null },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/connectors") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            inbound_invoice_email: "bills+acme@in.ledgerpilot.app",
            qbo_environment: "production",
            accounts: [],
            sync_runs: [],
            inbound_emails: [],
          },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/developer") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            available_scopes: ["read:accounts", "read:reports"],
            api_keys: [],
            webhooks: [],
            deliveries: [],
          },
        };
      }
      return {
        response: new Response(null, { status: 200 }),
        data: { items: [] },
      };
    }) as never);

    const screens = [
      { name: "ReceiptCapture", node: <ReceiptCapture /> },
      {
        name: "PurchaseOrders",
        node: <PurchaseOrdersAndCollections initialSection="po" />,
      },
      {
        name: "Collections",
        node: <PurchaseOrdersAndCollections initialSection="collections" />,
      },
      { name: "PortfolioTriage", node: <PortfolioTriage /> },
      {
        name: "Connectors",
        node: <ConnectorsAndApi initialTab="connectors" />,
      },
      {
        name: "DeveloperApi",
        node: <ConnectorsAndApi initialTab="developer" />,
      },
    ];

    for (const s of screens) {
      const { container, unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <AccessContext.Provider
              value={{
                business_id: mockBusiness.id,
                role: "owner",
                active: true,
                mfa_enabled: true,
              }}
            >
              <BusinessContext.Provider value={mockBusiness}>
                {s.node}
              </BusinessContext.Provider>
            </AccessContext.Provider>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      await waitFor(() => {
        expect(container.textContent?.length).toBeGreaterThan(20);
      });

      const txt = container.textContent || "";
      for (const pat of FORBIDDEN_PATTERNS) {
        expect(txt, `Forbidden jargon ${pat} in ${s.name}`).not.toMatch(pat);
      }
      unmount();
    }
  });
});
