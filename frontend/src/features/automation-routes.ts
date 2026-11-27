export type AutomationTabId =
  | "reminders"
  | "portals"
  | "payments"
  | "sla"
  | "payouts"
  | "voice"
  | "encryption"
  | "ai-benchmarks"
  | "accounting-expansion"
  | "enterprise-sso"
  | "p4-operations"
  | "p4-migrations"
  | "p4-bulk-ingestion"
  | "p4-disaster-recovery"
  | "p4-inventory"
  | "p4-group-consolidation";

export interface AutomationPageMeta {
  eyebrow: string;
  title: string;
  shortLabel: string;
  description: string;
  route: string;
  group: "workflows" | "documents" | "operations" | "security";
  standaloneSubgroup?: "workspace-governance";
}

export const AUTOMATION_PAGE_CONFIG: Record<
  AutomationTabId,
  AutomationPageMeta
> = {
  reminders: {
    eyebrow: "PAYMENTS & REMINDERS",
    title: "Reminders",
    shortLabel: "Reminders",
    description:
      "Connect WhatsApp and Slack separately, then send polite follow-ups for missing receipts, overdue bills, and pending approvals.",
    route: "/reminders",
    group: "workflows",
  },
  portals: {
    eyebrow: "PAYMENTS & REMINDERS",
    title: "Supplier portals",
    shortLabel: "Supplier portals",
    description:
      "Connect utility, software, and vendor portals so new invoices are imported automatically as draft bills for your review.",
    route: "/supplier-portals",
    group: "workflows",
  },
  payments: {
    eyebrow: "PAYMENTS & REMINDERS",
    title: "Supplier payments",
    shortLabel: "Supplier payments",
    description:
      "Prepare supplier payouts and require owner or accountant approval with a verification code before any money leaves your account.",
    route: "/supplier-payments",
    group: "workflows",
  },
  sla: {
    eyebrow: "PAYMENTS & REMINDERS",
    title: "Daily reconciliation",
    shortLabel: "Daily reconciliation",
    description:
      "See every open bank transaction, missing receipt, and unapproved draft in one daily checklist.",
    route: "/reconciliation-sla",
    group: "workflows",
  },
  payouts: {
    eyebrow: "BANK FEEDS & DOCUMENTS",
    title: "Bank feed & payouts",
    shortLabel: "Bank feed & payouts",
    description:
      "Import bank feed transactions and split merchant payouts into gross sales, fees, refunds, and net bank deposits.",
    route: "/payout-reconciliation",
    group: "documents",
  },
  voice: {
    eyebrow: "BANK FEEDS & DOCUMENTS",
    title: "Voice notes",
    shortLabel: "Voice notes",
    description:
      "Turn quick spoken notes or uploaded audio memos into reviewable bill and invoice drafts.",
    route: "/voice-instructions",
    group: "documents",
  },
  "ai-benchmarks": {
    eyebrow: "BANK FEEDS & DOCUMENTS",
    title: "Document scanning",
    shortLabel: "Document scanning",
    description:
      "Test how invoice and receipt scanning handles multiple languages, rotated photos, and blurry receipts.",
    route: "/ai-benchmarks",
    group: "documents",
  },
  "accounting-expansion": {
    eyebrow: "BANK FEEDS & DOCUMENTS",
    title: "Revenue & payroll",
    shortLabel: "Revenue & payroll",
    description:
      "Create monthly contract revenue schedules, split payroll into taxes and benefits, and resolve short-paid invoices.",
    route: "/accounting-expansion",
    group: "documents",
  },
  "p4-inventory": {
    eyebrow: "INVENTORY",
    title: "Inventory",
    shortLabel: "Inventory",
    description:
      "Track stock across warehouses, add shipping or customs costs, and record cost of goods sold automatically.",
    route: "/inventory",
    group: "operations",
  },
  "p4-group-consolidation": {
    eyebrow: "CONSOLIDATION",
    title: "Group reporting",
    shortLabel: "Group reporting",
    description:
      "Combine financial statements across multiple companies and currencies while removing internal group transactions.",
    route: "/group-reporting",
    group: "operations",
  },
  "p4-bulk-ingestion": {
    eyebrow: "DATA & IMPORTS",
    title: "Bulk document import",
    shortLabel: "Bulk import",
    description:
      "Import large historical archives of invoices and receipts in the background without slowing down daily work.",
    route: "/bulk-import",
    group: "operations",
  },
  "p4-migrations": {
    eyebrow: "DATA & IMPORTS",
    title: "Data migration",
    shortLabel: "Data migration",
    description:
      "Run background updates across historical records while verifying that your trial balance stays in balance.",
    route: "/data-migration",
    group: "operations",
  },
  "p4-operations": {
    eyebrow: "WORKSPACE GOVERNANCE",
    title: "Usage limits",
    shortLabel: "Usage limits",
    description:
      "Manage daily document limits, storage allowances, and monthly AI spending caps for your workspace.",
    route: "/usage-limits",
    group: "security",
    standaloneSubgroup: "workspace-governance",
  },
  "p4-disaster-recovery": {
    eyebrow: "RESILIENCE",
    title: "Disaster recovery",
    shortLabel: "Disaster recovery",
    description:
      "Verify regional backup readiness and test switching regions safely with zero lost accounting entries.",
    route: "/disaster-recovery",
    group: "security",
  },
  encryption: {
    eyebrow: "WORKSPACE GOVERNANCE",
    title: "Data encryption",
    shortLabel: "Data encryption",
    description:
      "Manage and rotate the dedicated encryption key protecting stored credentials in this workspace.",
    route: "/encryption-keys",
    group: "security",
    standaloneSubgroup: "workspace-governance",
  },
  "enterprise-sso": {
    eyebrow: "WORKSPACE GOVERNANCE",
    title: "Single sign-on & region",
    shortLabel: "Single sign-on & region",
    description:
      "Connect your company identity provider and choose the geographic region where workspace data is stored.",
    route: "/enterprise-sso",
    group: "security",
    standaloneSubgroup: "workspace-governance",
  },
};
