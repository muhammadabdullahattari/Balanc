import { afterEach, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Auth } from "../src/features/auth";
import { MFASetup } from "../src/features/onboarding";
import { GoogleSignIn } from "../src/features/google-signin";
import { Field, PasswordInput, ErrorNotice } from "../src/components/ui";
import { api, result, queryClient } from "../src/api/runtime";
const clients: QueryClient[] = [];
function show(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}
afterEach(() => {
  clients.forEach((c) => c.clear());
  clients.length = 0;
  vi.restoreAllMocks();
  delete window.google;
});
function response(data: unknown, status = 200) {
  return { data, response: new Response(null, { status }) };
}
function configuration() {
  vi.spyOn(api, "GET").mockResolvedValue(
    response({
      enabled: false,
      client_id: null,
      local_mailbox: "http://127.0.0.1:8025",
    }) as any,
  );
}
it("shows and hides the same password without submitting the form", async () => {
  const submit = vi.fn();
  show(
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Field label="Password">
        <PasswordInput defaultValue="Synthetic phrase 7!" />
      </Field>
    </form>,
  );
  const input = screen.getByLabelText("Password");
  expect(input).toHaveAttribute("type", "password");
  await userEvent.click(screen.getByRole("button", { name: "Show password" }));
  expect(input).toHaveAttribute("type", "text");
  expect(input).toHaveValue("Synthetic phrase 7!");
  await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
  expect(input).toHaveAttribute("type", "password");
  expect(submit).not.toHaveBeenCalled();
});
it("validates registration composition before sending a password", async () => {
  configuration();
  const post = vi.spyOn(api, "POST");
  show(<Auth />);
  await userEvent.click(screen.getByText("Create an account"));
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "synthetic@example.invalid",
  );
  await userEvent.type(
    screen.getByLabelText("Password"),
    "missing uppercase 7!",
  );
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create account", exact: true })
      .closest("form")!,
  );
  expect(await screen.findByText("Add an uppercase letter.")).toBeVisible();
  expect(post).not.toHaveBeenCalled();
});
it("resends to the entered address, clears an old code and explains Mailpit", async () => {
  configuration();
  const post = vi
    .spyOn(api, "POST")
    .mockResolvedValue(response({ message: "If eligible, sent" }, 202) as any);
  show(<Auth />);
  await userEvent.click(screen.getByText("Create an account"));
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "synthetic@example.invalid",
  );
  await userEvent.type(
    screen.getByLabelText("Password"),
    "Valid synthetic phrase 7!",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Create account", exact: true }),
  );
  await screen.findByLabelText("Token from your email");
  expect(screen.getByLabelText("Verification email address")).toHaveValue(
    "synthetic@example.invalid",
  );
  await userEvent.type(
    screen.getByLabelText("Token from your email"),
    "obsolete-synthetic-code",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Resend verification code" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Resend available/ }),
    ).toBeDisabled(),
  );
  expect(screen.getByLabelText("Token from your email")).toHaveValue("");
  expect(post).toHaveBeenCalledWith("/api/v1/auth/verification-request", {
    body: { email: "synthetic@example.invalid" },
  });
  expect(screen.getByRole("link", { name: "Mailpit" })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8025",
  );
});
it("explains an invalid code without exposing token contents", async () => {
  let failure: unknown;
  try {
    await result(
      Promise.resolve({
        error: {
          error: {
            code: "ERR_LINK_EXPIRED",
            message: "Token is invalid or expired.",
          },
        },
        response: new Response(null, { status: 410 }),
      }),
    );
  } catch (e) {
    failure = e;
  }
  show(<ErrorNotice error={failure} />);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Request a new code and use the newest email",
  );
});
it("renders authenticator QR locally and keeps manual setup available", async () => {
  vi.spyOn(api, "POST").mockResolvedValue(
    response({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri:
        "otpauth://totp/LedgerPilot:synthetic?secret=JBSWY3DPEHPK3PXP&issuer=LedgerPilot",
    }) as any,
  );
  const view = show(<MFASetup onComplete={() => {}} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Set up authenticator" }),
  );
  expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeVisible();
  expect(
    screen
      .getByTitle("Scan to add LedgerPilot to your authenticator")
      .closest("svg"),
  ).not.toBeNull();
  expect(view.container.querySelector('img[src^="http"]')).toBeNull();
});
it("Google button exchanges only credential/nonce/MFA and uses the owned API", async () => {
  vi.spyOn(api, "GET").mockResolvedValue(
    response({
      enabled: true,
      client_id: "test.apps.googleusercontent.com",
      local_mailbox: null,
    }) as any,
  );
  let callback!: (value: { credential: string }) => void;
  window.google = {
    accounts: {
      id: {
        initialize: (options) => {
          callback = options.callback;
          expect(options.nonce).toBe("synthetic-nonce");
        },
        renderButton: (node) => {
          const b = document.createElement("button");
          b.textContent = "Google test button";
          b.onclick = () =>
            callback({ credential: "synthetic-provider-proof" });
          node.append(b);
        },
        cancel: vi.fn(),
      },
    },
  };
  const post = vi.spyOn(api, "POST").mockImplementation(
    (path: string) =>
      Promise.resolve(
        response(
          path.endsWith("/challenge")
            ? { nonce: "synthetic-nonce" }
            : {
                status: "authenticated",
              },
        ),
      ) as any,
  );
  const next = vi
    .spyOn(queryClient, "invalidateQueries")
    .mockResolvedValue(undefined);
  show(<GoogleSignIn factors={() => ({ mfa_code: "123456" })} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Google test button" }),
  );
  await waitFor(() =>
    expect(next).toHaveBeenCalledWith({ queryKey: ["session"] }),
  );
  expect(post).toHaveBeenCalledWith("/api/v1/auth/google/login", {
    body: {
      credential: "synthetic-provider-proof",
      nonce: "synthetic-nonce",
      mfa_code: "123456",
    },
  });
});
