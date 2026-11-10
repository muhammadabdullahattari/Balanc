import { useAccess } from "../features/context";
import {
  Children,
  cloneElement,
  isValidElement,
  useId,
  useState,
  type ReactNode,
  type ReactElement,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  ArrowRight,
  LoaderCircle,
  X,
  Inbox,
  Eye,
  EyeOff,
} from "lucide-react";
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
      aria-busy={busy || undefined}
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
  const labelId = useId();
  const hintId = useId();
  const controlId = useId();
  const controls = Children.toArray(children);
  const first = controls.find(
    (c) =>
      isValidElement(c) &&
      (["input", "select", "textarea"].includes(String(c.type)) ||
        c.type === PasswordInput),
  ) as ReactElement<{ id?: string }> | undefined;
  const inputId = first?.props.id || controlId;
  return (
    <div className="field">
      <label id={labelId} htmlFor={inputId}>
        {label}
      </label>
      {Children.map(children, (child) => {
        if (
          !isValidElement(child) ||
          (!["input", "select", "textarea"].includes(String(child.type)) &&
            child.type !== PasswordInput)
        )
          return child;
        const control = child as ReactElement<{
          id?: string;
          "aria-labelledby"?: string;
          "aria-describedby"?: string;
        }>;
        return cloneElement(control, {
          id: control.props.id || controlId,
          "aria-labelledby": control.props["aria-labelledby"] || labelId,
          "aria-describedby":
            [control.props["aria-describedby"], hint ? hintId : undefined]
              .filter(Boolean)
              .join(" ") || undefined,
        });
      })}
      {hint && <small id={hintId}>{hint}</small>}
    </div>
  );
}
export function PasswordInput(props: React.ComponentProps<"input">) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-control">
      <input {...props} type={visible ? "text" : "password"} />
      <button
        type="button"
        className="icon-button"
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        onClick={() => setVisible((v) => !v)}
      >
        {visible ? <EyeOff size={18} /> : <Eye size={18} />}
      </button>
    </div>
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
                : {validationMessage(field.type)}
              </li>
            ))}
          </ul>
        ) : null}
        {error instanceof ApiError && error.requestId && (
          <details>
            <summary>Support reference</summary>
            <small>{error.requestId}</small>
          </details>
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
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div role="status" className="loading">
      <div className="loading-lines" aria-hidden="true">
        <i />
        <i />
        <i />
      </div>
      <span className="sr-only">Loading your workspace…</span>
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
      {actions && <div className="actions">{actions}</div>}
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
export function SectionTabs({
  id,
  label,
  items,
  value,
  onChange,
}: {
  id: string;
  label: string;
  items: ReadonlyArray<readonly [string, string]>;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map(([key, text], index) => (
        <button
          type="button"
          key={key}
          id={`${id}-${key}`}
          role="tab"
          aria-selected={value === key}
          aria-controls={value === key ? `${id}-panel` : undefined}
          tabIndex={value === key ? 0 : -1}
          onClick={() => onChange(key)}
          onKeyDown={(event) => {
            const last = items.length - 1;
            const next =
              event.key === "ArrowRight"
                ? index === last
                  ? 0
                  : index + 1
                : event.key === "ArrowLeft"
                  ? index === 0
                    ? last
                    : index - 1
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? last
                      : undefined;
            if (next === undefined) return;
            event.preventDefault();
            const nextTab =
              event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                "button",
              )[next];
            nextTab?.focus();
            nextTab?.scrollIntoView({ block: "nearest", inline: "nearest" });
            onChange(items[next][0]);
          }}
        >
          {text}
        </button>
      ))}
    </div>
  );
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

function validationMessage(type: string) {
  const messages: Record<string, string> = {
    missing: "This field is required.",
    string_too_short: "This value is too short.",
    string_too_long: "This value is too long.",
    string_pattern_mismatch: "Use the requested format.",
    value_error: "Check this value.",
    extra_forbidden: "Refresh this form and try again.",
    int_parsing: "Enter a whole number.",
  };
  return messages[type] || "Check this field and try again.";
}
