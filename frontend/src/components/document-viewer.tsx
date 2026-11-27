import { useEffect, useState } from "react";
import { api } from "../api/runtime";
import { Badge, Button } from "./ui";

export interface HighlightRegion {
  id: string;
  label: string;
  value: string;
  page: number;
  left: number;
  top: number;
  right: number;
  bottom: number;
  confidence?: number;
}

export function DocumentViewer({
  businessId,
  evidenceId,
  filename,
  regions = [],
  selectedRegionId,
  onSelectRegion,
}: {
  businessId: string;
  evidenceId: string;
  filename?: string;
  regions?: HighlightRegion[];
  selectedRegionId?: string;
  onSelectRegion?: (region: HighlightRegion) => void;
}) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState<100 | 140 | 180>(100);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const maxPage = Math.max(1, ...regions.map((r) => r.page || 1));

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setFailed(false);

    void api
      .GET("/api/v1/businesses/{business_id}/evidence/{evidence_id}/preview", {
        params: {
          path: { business_id: businessId, evidence_id: evidenceId },
          query: { page },
        },
        parseAs: "blob",
      })
      .then((res) => {
        if (!active) return;
        if (res.response.ok && res.data instanceof Blob && res.data.size > 0) {
          objectUrl = URL.createObjectURL(res.data);
          setImgUrl(objectUrl);
        } else {
          setFailed(true);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [businessId, evidenceId, page]);

  const pageRegions = regions.filter((r) => (r.page || 1) === page);

  return (
    <div
      className="visual-card"
      aria-label="Document preview with highlighted regions"
    >
      <div className="visual-card-head">
        <div>
          <strong>{filename || "Original document"}</strong>
          <small className="muted" style={{ display: "block" }}>
            Page {page} of {maxPage} · Click any highlighted box to inspect its
            extracted value
          </small>
        </div>
        <div className="p4-inline-actions">
          {maxPage > 1 && (
            <>
              <Button
                type="button"
                variant="secondary"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous page
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={page >= maxPage}
                onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
              >
                Next page
              </Button>
            </>
          )}
          <Button
            type="button"
            variant="secondary"
            onClick={() =>
              setZoom((z) => (z === 100 ? 140 : z === 140 ? 180 : 100))
            }
          >
            Zoom {zoom}%
          </Button>
        </div>
      </div>

      <div
        style={{
          overflow: "auto",
          maxHeight: "460px",
          border: "1px solid var(--line)",
          borderRadius: "8px",
          background: "var(--surface-alt)",
          padding: "0.5rem",
        }}
      >
        <div
          style={{
            position: "relative",
            width: `${zoom}%`,
            minWidth: "280px",
            margin: "0 auto",
          }}
        >
          {imgUrl && !failed ? (
            <img
              src={imgUrl}
              alt={`Document preview page ${page}`}
              style={{ width: "100%", display: "block", borderRadius: "4px" }}
            />
          ) : (
            <div
              style={{
                aspectRatio: "1 / 1.29",
                background: "var(--surface)",
                border: "1px solid var(--line)",
                display: "grid",
                placeItems: "center",
                padding: "1rem",
              }}
            >
              <span className="muted">
                Document preview ready for region inspection (Page {page})
              </span>
            </div>
          )}

          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            role="img"
            aria-label={`Highlighted extraction regions on page ${page}`}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "all",
            }}
          >
            {pageRegions.map((reg) => {
              const isSelected = reg.id === selectedRegionId;
              const lowConf =
                reg.confidence !== undefined && reg.confidence < 0.8;
              const stroke = isSelected
                ? "#274b7a"
                : lowConf
                  ? "#b86b2b"
                  : "#1f7a5c";
              const fill = isSelected
                ? "rgba(39, 75, 122, 0.24)"
                : lowConf
                  ? "rgba(184, 107, 43, 0.18)"
                  : "rgba(31, 122, 92, 0.16)";
              const w = Math.max(1.5, (reg.right - reg.left) * 100);
              const h = Math.max(1.5, (reg.bottom - reg.top) * 100);
              return (
                <rect
                  key={reg.id}
                  x={reg.left * 100}
                  y={reg.top * 100}
                  width={w}
                  height={h}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={isSelected ? "0.8" : "0.45"}
                  style={{ cursor: "pointer" }}
                  onClick={() => onSelectRegion?.(reg)}
                >
                  <title>
                    {`${reg.label}: ${reg.value}${
                      reg.confidence !== undefined
                        ? ` (${Math.round(reg.confidence * 100)}% confidence)`
                        : ""
                    }`}
                  </title>
                </rect>
              );
            })}
          </svg>
        </div>
      </div>

      {pageRegions.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "0.4rem",
            marginTop: "0.65rem",
          }}
        >
          {pageRegions.slice(0, 12).map((reg) => (
            <button
              key={reg.id}
              type="button"
              className="button secondary"
              style={{ padding: "0.25rem 0.55rem", fontSize: "0.8rem" }}
              onClick={() => onSelectRegion?.(reg)}
            >
              <span>{reg.label}:</span> <strong>{reg.value}</strong>
              {reg.confidence !== undefined && (
                <Badge tone={reg.confidence < 0.8 ? "warning" : "good"}>
                  {Math.round(reg.confidence * 100)}%
                </Badge>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
