import { Check, X } from "lucide-react";
import { useAccess } from "./context";

type PermissionRow = {
  feature: string;
  owner: boolean;
  accountant: boolean;
  bookkeeper: boolean;
  reviewer: boolean;
  auditor: boolean;
};

const PERMISSION_MATRIX: PermissionRow[] = [
  {
    feature: "View business overview & reports",
    owner: true,
    accountant: true,
    bookkeeper: true,
    reviewer: true,
    auditor: true,
  },
  {
    feature: "Post & approve journal entries",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Draft journal entries",
    owner: true,
    accountant: true,
    bookkeeper: true,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Review & approve drafts",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: true,
    auditor: false,
  },
  {
    feature: "Manage banking & reconciliation",
    owner: true,
    accountant: true,
    bookkeeper: true,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Manage documents & evidence",
    owner: true,
    accountant: true,
    bookkeeper: true,
    reviewer: true,
    auditor: false,
  },
  {
    feature: "View & export audit trail",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: true,
  },
  {
    feature: "Manage team members & invitations",
    owner: true,
    accountant: false,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Invite teammates (if granted)",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Manage billing & subscriptions",
    owner: true,
    accountant: false,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Configure integrations (QuickBooks, etc.)",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Submit privacy & data requests",
    owner: true,
    accountant: true,
    bookkeeper: true,
    reviewer: true,
    auditor: true,
  },
  {
    feature: "Manage retention & legal holds",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
  {
    feature: "Month-end close",
    owner: true,
    accountant: true,
    bookkeeper: false,
    reviewer: false,
    auditor: false,
  },
];

const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  accountant: "Accountant",
  bookkeeper: "Bookkeeper",
  reviewer: "Reviewer",
  auditor: "Auditor",
};

const ROLE_DESCRIPTIONS: Record<string, string> = {
  owner:
    "Full workspace control — billing, team, integrations, posting, and all reports.",
  accountant:
    "Financial controller — post entries, reconcile, manage integrations, and export.",
  bookkeeper:
    "Day-to-day data entry — draft entries, upload documents, and view reports.",
  reviewer:
    "Approval authority — review and approve drafts; read-only on other features.",
  auditor: "Read-only observer — view and export the audit trail.",
};

type Role = keyof PermissionRow;

function Tick({ allowed }: { allowed: boolean }) {
  return allowed ? (
    <Check size={16} className="perm-check" aria-label="Allowed" />
  ) : (
    <X size={16} className="perm-cross" aria-label="Not allowed" />
  );
}

export function RolePermissions() {
  const access = useAccess();
  const role = (access?.role ?? "bookkeeper") as Role;
  const label = ROLE_LABELS[role] ?? role;
  const description = ROLE_DESCRIPTIONS[role] ?? "";

  const myPermissions = PERMISSION_MATRIX.filter((row) => row[role]);
  const restricted = PERMISSION_MATRIX.filter((row) => !row[role]);

  return (
    <div className="role-permissions">
      <h2>Your role &amp; permissions</h2>
      <p className="muted">
        You are signed in as <strong>{label}</strong>. {description}
      </p>

      <div className="perm-summary-cards">
        <div className="perm-card perm-allowed">
          <h3>✓ What you can do ({myPermissions.length})</h3>
          <ul>
            {myPermissions.map((row) => (
              <li key={row.feature}>{row.feature}</li>
            ))}
          </ul>
        </div>
        {restricted.length > 0 && (
          <div className="perm-card perm-restricted">
            <h3>✗ Not available for your role ({restricted.length})</h3>
            <ul>
              {restricted.map((row) => (
                <li key={row.feature}>{row.feature}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <h3 className="perm-matrix-heading">Full permission matrix</h3>
      <div className="perm-table-wrap">
        <table className="perm-table">
          <thead>
            <tr>
              <th>Feature</th>
              {Object.entries(ROLE_LABELS).map(([r, lbl]) => (
                <th key={r} className={r === role ? "perm-active-col" : ""}>
                  {lbl}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PERMISSION_MATRIX.map((row) => (
              <tr
                key={row.feature}
                className={row[role] ? "" : "perm-row-restricted"}
              >
                <td>{row.feature}</td>
                {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
                  <td
                    key={r}
                    className={`perm-cell${r === role ? " perm-active-col" : ""}`}
                  >
                    <Tick allowed={!!row[r]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
