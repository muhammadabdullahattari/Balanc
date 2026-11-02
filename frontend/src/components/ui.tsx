import { useAccess } from "../features/context";
import { useId, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, ArrowRight, LoaderCircle, X, Inbox } from "lucide-react";
import { ApiError, queryClient } from "../api/runtime";

export function Button({
  children,
  busy,
  variant = "primary",
  permission,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  busy?: boolean;
  permission?: "post" | "invite";
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  const access = useAccess();
  const forbidden =
    permission === "post"
      ? !(access?.can_post || access?.role === "accountant")
      : permission === "invite"
        ? !(access?.can_invite || access?.role === "owner")
        : false;
  return (
    <button
      {...props}
      title={forbidden ? "Your role does not allow this action." : props.title}
      className={`button ${variant} ${props.className || ""}`}
      disabled={busy || props.disabled || forbidden}
    >
      {busy && <LoaderCircle className="spin" size={16} />} {children}
    </button>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div role="alert" className="notice error">
      <AlertCircle size={18} />
      <div>
        {error instanceof Error ? error.message : "Something went wrong."}
        {error instanceof ApiError && error.fields?.length ? (
          <ul>
            {error.fields.map((field, i) => (
              <li key={i}>
                Check{" "}
                {field.location
                  .filter((p) => p !== "body" && p !== "query")
                  .join(" / ")
                  .replaceAll("_", " ")}
                : {field.type.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        ) : null}
        {error instanceof ApiError && error.requestId && (
          <small>Reference: {error.requestId}</small>
        )}
      </div>
      {!retry && error instanceof ApiError && error.status === 409 && (
        <Button
          variant="ghost"
          onClick={() => {
            void queryClient.invalidateQueries();
          }}
        >
          Refresh records
        </Button>
      )}
      {retry && (
        <Button variant="ghost" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Inbox size={25} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div role="status" className="loading">
      <LoaderCircle className="spin" size={20} /> Loading your workspaceâ€¦
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow || "YOUR FINANCE OFFICE"}</p>
        <h1>{title}</h1>
        <p className="muted">{description}</p>
      </div>
      <div className="actions">{actions}</div>
    </header>
  );
}
export function Modal({
  title,
  description,
  open,
  onClose,
  children,
}: {
  title: string;
  description: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className="modal" aria-describedby={id}>
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description id={id}>{description}</Dialog.Description>
          <Dialog.Close
            className="icon-button modal-close"
            aria-label="Close dialog"
          >
            <X size={20} />
          </Dialog.Close>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warning";
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Table<T>({
  rows,
  columns,
  rowKey,
  caption,
}: {
  rows: T[];
  columns: { label: string; render: (row: T) => ReactNode }[];
  rowKey: (row: T) => string;
  caption: string;
}) {
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th scope="col" key={c.label}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.label}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function NextPage({
  cursor,
  onNext,
  onReset,
}: {
  cursor?: string | null;
  onNext: () => void;
  onReset: () => void;
}) {
  return (
    <div className="pagination">
      <Button variant="ghost" onClick={onReset}>
        First page
      </Button>
      <Button variant="secondary" disabled={!cursor} onClick={onNext}>
        Next page <ArrowRight size={14} />
      </Button>
    </div>
  );
}
export function money(value: string, currency: string) {
  // Format the exact decimal text; never convert ledger values to floating point.
  const [whole, decimal = ""] = value.split(".");
  const digits =
    new Intl.NumberFormat("en", {
      style: "currency",
      currency,
    }).resolvedOptions().maximumFractionDigits ?? 2;
  const sign = whole.startsWith("-") ? "-" : "";
  return `${currency} ${sign}${BigInt(whole.replace(/^-/, "")).toLocaleString("en")}${digits ? "." + decimal.padEnd(digits, "0").slice(0, digits) : ""}`;
}
