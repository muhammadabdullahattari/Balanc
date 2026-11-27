import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Camera, Upload } from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  PageHeading,
  Table,
  money,
} from "../components/ui";
import { useBusiness } from "./context";

export function ReceiptCapture() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [notesText, setNotesText] = useState("");
  const [projectId, setProjectId] = useState("");
  const [cameraError, setCameraError] = useState("");
  const [streamActive, setStreamActive] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const projects = useQuery({
    queryKey: [...keys.business(b.id), "projects-capture"],
    enabled: Boolean(b.p2_projects_available),
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/projects", {
          signal,
          params: { path },
        }),
      ),
  });

  const fingerprints = useQuery({
    queryKey: [...keys.business(b.id), "document-fingerprints"],
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/documents/fingerprints", {
          signal,
          params: { path, query: { limit: 20 } },
        }),
      ),
  });

  const stopCamera = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setStreamActive(false);
  };

  const startCamera = async () => {
    setCameraError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError(
        "Live camera preview is not supported in this browser. Use the Snap or choose receipt button below.",
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      streamRef.current = stream;
      setStreamActive(true);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
    } catch {
      setCameraError(
        "Camera permission was declined or unavailable. You can still snap or choose a photo below.",
      );
    }
  };

  const captureFrame = () => {
    const video = videoRef.current;
    if (!video) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 800;
    canvas.height = video.videoHeight || 1000;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (blob) {
        const f = new File([blob], `receipt-${Date.now()}.jpg`, {
          type: "image/jpeg",
        });
        setSelectedFile(f);
        stopCamera();
      }
    }, "image/jpeg");
  };

  const captureMutation = useMutation({
    mutationFn: async () => {
      let b64 = "";
      let fname = "mobile-receipt.jpg";
      if (selectedFile) {
        fname = selectedFile.name;
        const buf = await selectedFile.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = "";
        for (let i = 0; i < Math.min(bytes.length, 512_000); i += 1) {
          bin += String.fromCharCode(bytes[i]);
        }
        b64 = btoa(bin);
      }
      return result(
        api.POST("/api/v1/businesses/{business_id}/documents/ingest", {
          params: { path },
          body: {
            channel: "mobile_capture",
            filename: fname,
            raw_text: notesText,
            content_base64: b64,
            project_id: projectId || null,
            create_draft: true,
          } as never,
        }),
      );
    },
    onSuccess: () => {
      setSelectedFile(null);
      setNotesText("");
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "document-fingerprints"],
      });
    },
  });

  const lastResult = captureMutation.data as
    | {
        extraction?: {
          vendor_name?: string;
          document_type?: string;
          issue_date?: string;
          total_amount?: string;
          currency?: string;
          overall_confidence?: string;
        };
        deduplication?: {
          deduplicated?: boolean;
          channels?: string[];
          source_draft_id?: string | null;
        };
      }
    | undefined;

  return (
    <>
      <PageHeading
        eyebrow="MOBILE RECEIPT CAPTURE"
        title="Snap a receipt or invoice"
        description="Take a photo from your phone or upload a scan. LedgerPilot reads the vendor, date, tax, and total, prevents duplicates across channels, and prepares a reviewable expense draft."
      />

      {cameraError && (
        <div className="notice error" role="alert">
          {cameraError}
        </div>
      )}
      <ErrorNotice error={captureMutation.error} />

      <div className="dashboard-grid">
        <section className="panel settings-section">
          <h2>Capture receipt</h2>
          <div
            className="p4-inline-actions"
            style={{ marginBottom: "0.75rem" }}
          >
            {!streamActive ? (
              <Button type="button" variant="secondary" onClick={startCamera}>
                <Camera size={16} /> Open rear camera
              </Button>
            ) : (
              <>
                <Button type="button" onClick={captureFrame}>
                  Snap photo
                </Button>
                <Button type="button" variant="ghost" onClick={stopCamera}>
                  Close camera
                </Button>
              </>
            )}
          </div>

          {streamActive && (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              style={{
                width: "100%",
                maxHeight: "300px",
                borderRadius: "8px",
                marginBottom: "0.75rem",
                background: "#000",
              }}
            />
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              captureMutation.mutate();
            }}
          >
            <label className="upload-zone" style={{ marginBottom: "0.75rem" }}>
              <Upload size={24} />
              <strong>Snap or choose a receipt photo</strong>
              <span>JPEG, PNG, HEIC, or PDF</span>
              <input
                type="file"
                accept="image/*,.pdf"
                capture="environment"
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
              />
            </label>
            {selectedFile && (
              <p className="muted">
                Selected: <strong>{selectedFile.name}</strong> (
                {Math.max(1, Math.round(selectedFile.size / 1024))} KB)
              </p>
            )}

            <div className="form-grid">
              <Field
                label="Receipt details or OCR text (optional)"
                hint="e.g. Cafe Clifton lunch meeting Total 48.50 USD"
              >
                <input
                  value={notesText}
                  onChange={(e) => setNotesText(e.target.value)}
                  placeholder="Optional merchant or note"
                />
              </Field>
              {Array.isArray(projects.data?.items) &&
                projects.data.items.length > 0 && (
                  <Field label="Attach to project (optional)">
                    <select
                      value={projectId}
                      onChange={(e) => setProjectId(e.target.value)}
                    >
                      <option value="">No project</option>
                      {projects.data.items.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
            </div>

            <div className="actions" style={{ marginTop: "0.85rem" }}>
              <Button
                type="submit"
                busy={captureMutation.isPending}
                disabled={!selectedFile && !notesText.trim()}
              >
                Extract receipt & prepare draft
              </Button>
            </div>
          </form>

          {lastResult?.extraction && (
            <div className="notice" role="status" style={{ marginTop: "1rem" }}>
              <strong>
                {lastResult.deduplication?.deduplicated
                  ? "Matched existing document (duplicate prevented)"
                  : "Draft prepared for review"}
              </strong>
              <p style={{ margin: "0.35rem 0 0" }}>
                Vendor: {lastResult.extraction.vendor_name} · Date:{" "}
                {lastResult.extraction.issue_date} · Total:{" "}
                {money(
                  lastResult.extraction.total_amount || "0.00",
                  lastResult.extraction.currency || b.currency,
                )}{" "}
                · Channels:{" "}
                {(
                  lastResult.deduplication?.channels || ["mobile_capture"]
                ).join(", ")}
              </p>
            </div>
          )}
        </section>

        <section className="panel settings-section">
          <h2>Recent captured & deduplicated documents</h2>
          <p className="muted">
            Documents arriving via mobile camera, upload, supplier portal, or
            email share one fingerprint so nothing is entered twice.
          </p>
          <Table
            caption="Cross-channel document intake log"
            rows={
              Array.isArray(
                (fingerprints.data as { items?: unknown[] } | undefined)?.items,
              )
                ? ((
                    fingerprints.data as {
                      items: Array<{
                        id: string;
                        vendor_name: string;
                        doc_number: string;
                        total_amount: string;
                        currency: string;
                        channels: string[];
                      }>;
                    }
                  ).items ?? [])
                : []
            }
            rowKey={(r) => r.id}
            emptyText="No captured receipts yet."
            columns={[
              { label: "Vendor", render: (r) => r.vendor_name || "—" },
              { label: "Reference", render: (r) => r.doc_number || "—" },
              {
                label: "Total",
                render: (r) => money(r.total_amount, r.currency || b.currency),
              },
              {
                label: "Channels",
                render: (r) => (
                  <Badge>{(r.channels || []).join(", ") || "upload"}</Badge>
                ),
              },
            ]}
          />
        </section>
      </div>
    </>
  );
}
