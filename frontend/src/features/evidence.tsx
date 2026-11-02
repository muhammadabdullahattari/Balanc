import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Upload, FileText } from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  NextPage,
  PageHeading,
  Table,
} from "../components/ui";
import { useBusiness } from "./context";

export function Evidence() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [cursor, setCursor] = useState<string | undefined>();
  const [selected, setSelected] = useState<string>();
  const [annotationCursor, setAnnotationCursor] = useState<string>();
  const [file, setFile] = useState<File>();
  const [open, setOpen] = useState(false);
  const files = useQuery({
    queryKey: [...keys.business(b.id), "evidence", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/evidence", {
          signal,
          params: { path, query: { limit: 50, cursor } },
        }),
      ),
    refetchInterval: open ? false : 15000,
  });
  const detail = useQuery({
    queryKey: [...keys.business(b.id), "evidence-detail", selected],
    enabled: !!selected,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/evidence/{evidence_id}", {
          signal,
          params: { path: { ...path, evidence_id: selected! } },
        }),
      ),
  });
  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a document first.");
      if (file.size > 25 * 1024 * 1024)
        throw new Error(
          "The maximum document size is 25 MiB; your business quota may be lower.",
        );
      return result(
        api.POST("/api/v1/businesses/{business_id}/evidence/upload", {
          params: { path, query: { filename: file.name } },
          body: "",
          bodySerializer: () => file,
          headers: { "Content-Type": "application/octet-stream" },
        }),
      );
    },
    onSuccess: () => {
      setOpen(false);
      setFile(undefined);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "evidence"],
      });
    },
  });
  const download = useMutation({
    mutationFn: async () => {
      const blob = await result(
        api.GET(
          "/api/v1/businesses/{business_id}/evidence/{evidence_id}/download",
          {
            params: { path: { ...path, evidence_id: selected! } },
            parseAs: "blob",
          },
        ),
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = detail.data?.filename || "document";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
  const note = useMutation({
    mutationFn: (text: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/evidence/{evidence_id}/notes",
          {
            params: { path: { ...path, evidence_id: selected! } },
            body: { note: text },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "annotations", selected],
      });
    },
  });
  const annotations = useQuery({
    queryKey: [
      ...keys.business(b.id),
      "annotations",
      selected,
      annotationCursor,
    ],
    enabled: !!selected,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/evidence/{evidence_id}/notes",
          {
            signal,
            params: {
              path: { ...path, evidence_id: selected! },
              query: { limit: 50, cursor: annotationCursor },
            },
          },
        ),
      ),
  });
  const region = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST(
          "/api/v1/businesses/{business_id}/evidence/{evidence_id}/notes",
          {
            params: { path: { ...path, evidence_id: selected! } },
            body: {
              note: String(v.get("note")),
              fields: [
                {
                  name: String(v.get("name")),
                  value: String(v.get("value")),
                  value_type: v.get("type") as "text" | "date" | "amount",
                  page: Number(v.get("page")),
                  left: Number(v.get("left")),
                  top: Number(v.get("top")),
                  right: Number(v.get("right")),
                  bottom: Number(v.get("bottom")),
                },
              ],
            },
          },
        ),
      );
    },
    onSuccess: () => {
      void annotations.refetch();
    },
  });
  const link = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST(
          "/api/v1/businesses/{business_id}/evidence/{evidence_id}/links",
          {
            params: { path: { ...path, evidence_id: selected! } },
            body: {
              resource_kind: v.get("kind") as
                "trade_document" | "bank_transaction" | "journal",
              resource_id: String(v.get("resource")),
            },
          },
        ),
      );
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="DOCUMENTS & EVIDENCE"
        title="Every number has a story."
        description="Keep receipts, invoices and supporting documents close to the books they explain."
        actions={
          <Button
            onClick={() => {
              setOpen(true);
              upload.reset();
            }}
          >
            <Upload size={16} />
            Upload document
          </Button>
        }
      />
      <ErrorNotice error={files.error} />
      <section className="panel">
        {files.isPending ? (
          <Loading />
        ) : files.data?.items.length ? (
          <>
            <Table
              caption="Supporting documents"
              rows={files.data.items}
              rowKey={(f) => f.id}
              columns={[
                {
                  label: "Document",
                  render: (f) => (
                    <button
                      className="text-button"
                      onClick={() => {
                        setSelected(f.id);
                        setAnnotationCursor(undefined);
                        region.reset();
                        link.reset();
                        note.reset();
                        download.reset();
                      }}
                    >
                      <FileText size={18} />
                      {f.filename}
                    </button>
                  ),
                },
                { label: "Format", render: (f) => f.media_type },
                {
                  label: "Size",
                  render: (f) => `${Math.ceil(f.size / 1024)} KB`,
                },
                {
                  label: "Status",
                  render: (f) => (
                    <Badge
                      tone={f.status === "quarantined" ? "warning" : "neutral"}
                    >
                      {f.status.replaceAll("_", " ")}
                    </Badge>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={files.data.next_cursor}
              onNext={() => setCursor(files.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="A home for your paperwork.">
            Upload a document to scan it and keep its source evidence securely
            linked to your business.
          </Empty>
        )}
      </section>
      <Modal
        open={open}
        onClose={() => {
          if (!upload.isPending) setOpen(false);
        }}
        title="Upload a document"
        description="Documents are quarantined for scanning before extraction. Uploading never posts to your books."
      >
        <ErrorNotice error={upload.error} />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            upload.mutate();
          }}
        >
          <label className="upload-zone">
            <Upload size={28} />
            <strong>Choose a receipt, invoice or statement</strong>
            <span>PDF, JPEG, PNG or supported source file</span>
            <input
              type="file"
              required
              accept=".pdf,.jpg,.jpeg,.png,.csv,.xlsx,.heic"
              onChange={(e) => setFile(e.target.files?.[0])}
            />
          </label>
          {file && (
            <p>
              {file.name} · {Math.ceil(file.size / 1024)} KB
            </p>
          )}
          <Button busy={upload.isPending} disabled={!file}>
            Upload securely
          </Button>
        </form>
      </Modal>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!note.isPending && !region.isPending && !link.isPending)
            setSelected(undefined);
        }}
        title={detail.data?.filename || "Document"}
        description="Original evidence and extracted text remain separate from your review notes."
      >
        <ErrorNotice error={detail.error || download.error || note.error} />
        {detail.isPending ? (
          <Loading />
        ) : (
          detail.data && (
            <>
              <div className="actions">
                <Badge>{detail.data.status}</Badge>
                <Button
                  variant="secondary"
                  busy={download.isPending}
                  onClick={() => download.mutate()}
                >
                  <Download size={15} />
                  Download original
                </Button>
              </div>
              <dl className="review-summary">
                <dt>Content fingerprint</dt>
                <dd>{detail.data.content_hash}</dd>
                <dt>Scan version</dt>
                <dd>{detail.data.scan_version || "Pending"}</dd>
              </dl>
              <h3>Review history</h3>
              <ErrorNotice error={annotations.error} />
              {annotations.data?.items.map((annotation) => (
                <article key={annotation.id} className="notice">
                  <div>
                    <strong>{annotation.note || "Reviewed fields"}</strong>
                    <small>
                      {new Date(annotation.created_at).toLocaleString("en", {
                        timeZone: b.timezone,
                      })}{" "}
                      · Reviewer {annotation.created_by}
                    </small>
                    {annotation.fields.map((field, i) => (
                      <div key={i}>
                        <strong>
                          {field.name}: {field.value}
                        </strong>
                        <p>
                          Page {field.page} · {field.source}. Region:{" "}
                          {field.left}, {field.top} to {field.right},{" "}
                          {field.bottom}.
                        </p>
                        <svg
                          viewBox="0 0 100 100"
                          width="140"
                          height="140"
                          role="img"
                          aria-label={`Source region for ${field.name} on page ${field.page}`}
                        >
                          <rect
                            x="0"
                            y="0"
                            width="100"
                            height="100"
                            fill="var(--surface-alt)"
                            stroke="var(--line)"
                          />
                          <rect
                            x={field.left * 100}
                            y={field.top * 100}
                            width={(field.right - field.left) * 100}
                            height={(field.bottom - field.top) * 100}
                            fill="var(--accent-soft)"
                            stroke="var(--accent)"
                          />
                        </svg>
                      </div>
                    ))}
                  </div>
                </article>
              ))}
              <NextPage
                cursor={annotations.data?.next_cursor}
                onNext={() =>
                  setAnnotationCursor(
                    annotations.data?.next_cursor || undefined,
                  )
                }
                onReset={() => setAnnotationCursor(undefined)}
              />
              <h3>Extracted text</h3>
              {detail.data.raw_text ? (
                <pre className="document-text">{detail.data.raw_text}</pre>
              ) : (
                <p className="muted">
                  Text is not available yet. Scanning may still be running, or
                  this format may need manual review.
                </p>
              )}
              <details>
                <summary>Review an extracted field and source region</summary>
                <ErrorNotice error={region.error} />
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    region.mutate(e.currentTarget);
                  }}
                >
                  <div className="form-grid">
                    <Field label="Field name">
                      <input name="name" required maxLength={80} />
                    </Field>
                    <Field label="Reviewed value">
                      <input name="value" required maxLength={1000} />
                    </Field>
                    <Field label="Value type">
                      <select name="type">
                        <option>text</option>
                        <option>date</option>
                        <option>amount</option>
                      </select>
                    </Field>
                    <Field label="Page">
                      <input
                        type="number"
                        name="page"
                        min={1}
                        max={1000}
                        required
                        defaultValue={1}
                      />
                    </Field>
                    {["left", "top", "right", "bottom"].map((n) => (
                      <Field key={n} label={`${n} (0 to 1)`}>
                        <input
                          name={n}
                          type="number"
                          min={0}
                          max={1}
                          step="0.001"
                          required
                        />
                      </Field>
                    ))}
                  </div>
                  <Field label="Review note">
                    <textarea name="note" required />
                  </Field>
                  <Button busy={region.isPending}>Save reviewed field</Button>
                  {region.isSuccess && (
                    <p role="status">
                      Reviewed field saved with source coordinates.
                    </p>
                  )}
                </form>
              </details>
              <details>
                <summary>Link to a financial record</summary>
                <ErrorNotice error={link.error} />
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    link.mutate(e.currentTarget);
                  }}
                >
                  <Field label="Record type">
                    <select name="kind">
                      <option value="trade_document">Invoice / bill</option>
                      <option value="bank_transaction">Bank transaction</option>
                      <option value="journal">Journal</option>
                    </select>
                  </Field>
                  <Field label="Record reference">
                    <input name="resource" required />
                  </Field>
                  <Button busy={link.isPending}>Link evidence</Button>
                  {link.isSuccess && <p role="status">Evidence linked.</p>}
                </form>
              </details>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  note.mutate(
                    String(new FormData(e.currentTarget).get("note")),
                  );
                }}
              >
                <Field label="Add a review note">
                  <textarea name="note" required maxLength={2000} />
                </Field>
                <Button busy={note.isPending}>Save note</Button>
                {note.isSuccess && (
                  <p role="status" className="success-text">
                    Review note saved.
                  </p>
                )}
              </form>
            </>
          )
        )}
      </Modal>
    </>
  );
}
