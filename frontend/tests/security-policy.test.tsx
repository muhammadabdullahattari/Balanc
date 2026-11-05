import { describe, expect, it } from "vitest";
import { webSecurityPolicy } from "../security-policy";

describe("production browser content policy", () => {
  it("restricts scripts, objects and connections while allowing the Google button", () => {
    const csp = webSecurityPolicy("https://api.ledgerpilot.test");
    expect(csp).toContain(
      "script-src 'self' https://accounts.google.com/gsi/client",
    );
    expect(csp).toContain(
      "connect-src 'self' https://accounts.google.com/gsi/ https://api.ledgerpilot.test",
    );
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).not.toContain("unsafe-eval");
    expect(
      csp.split(";").find((s) => s.trim().startsWith("script-src")),
    ).not.toContain("unsafe-inline");
    expect(csp).not.toContain("*");
  });
  it.each([
    "http://api.example.com",
    "https://user:secret@api.test",
    "https://api.test/path",
    "https://api.test?x=1",
    "javascript:alert(1)",
  ])("rejects an unsafe API policy origin: %s", (origin) => {
    expect(() => webSecurityPolicy(origin)).toThrow();
  });
  it("allows same-origin deployment without a third-party API destination", () => {
    expect(webSecurityPolicy()).toContain(
      "connect-src 'self' https://accounts.google.com/gsi/",
    );
  });
});
