/** Build-time CSP: exact public API origin, local assets and Google's GIS endpoints. */
export function webSecurityPolicy(apiOrigin = ""): string {
  const connections = ["'self'", "https://accounts.google.com/gsi/"];
  if (apiOrigin) {
    const api = new URL(apiOrigin);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(api.hostname);
    if (
      api.username ||
      api.password ||
      api.pathname !== "/" ||
      api.search ||
      api.hash ||
      (api.protocol !== "https:" && !(api.protocol === "http:" && local))
    )
      throw new Error(
        "VITE_API_ORIGIN must be a secure public origin or local loopback origin",
      );
    connections.push(api.origin);
  }
  return [
    "default-src 'self'",
    "script-src 'self' https://accounts.google.com/gsi/client",
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    `connect-src ${connections.join(" ")}`,
    "frame-src https://accounts.google.com/gsi/",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}
