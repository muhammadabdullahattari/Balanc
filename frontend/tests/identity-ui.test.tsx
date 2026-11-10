import { afterEach, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Auth } from "../src/features/auth";
import { MFASetup } from "../src/features/onboarding";
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
it("asks for MFA after Google, then retries a rejected code without choosing an account again", async () => {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(360);
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
        renderButton: (node, options) => {
          expect(options.width).toBe("360");
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
  const post = vi
    .spyOn(api, "POST")
    .mockImplementation(
      (
        path: string,
        options?: { body?: { mfa_code?: string; recovery_code?: string } },
      ) => {
        if (path.endsWith("/challenge"))
          return Promise.resolve(response({ nonce: "synthetic-nonce" })) as any;
        if (options?.body?.recovery_code)
          return Promise.resolve(response({ status: "authenticated" })) as any;
        return Promise.resolve({
          response: new Response(null, { status: 401 }),
          error: {
            error: {
              code: options?.body?.mfa_code
                ? "ERR_MFA_CODE"
                : "ERR_MFA_REQUIRED",
            },
          },
        }) as any;
      },
    );
  const next = vi
    .spyOn(queryClient, "invalidateQueries")
    .mockResolvedValue(undefined);
  show(<Auth />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Google test button" }),
  );
  expect(post).toHaveBeenCalledWith("/api/v1/auth/google/login", {
    body: { credential: "synthetic-provider-proof", nonce: "synthetic-nonce" },
  });
  expect(
    await screen.findByRole("heading", { name: "Verify your sign-in" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Sign in", exact: true }),
  ).not.toBeInTheDocument();
  expect(next).not.toHaveBeenCalled();
  const input = screen.getByLabelText("Authenticator code", { exact: true });
  expect(input).toHaveFocus();
  await userEvent.type(input, "123456");
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and sign in" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("incorrect");
  expect(post).toHaveBeenCalledWith("/api/v1/auth/google/login", {
    body: {
      credential: "synthetic-provider-proof",
      nonce: "synthetic-nonce",
      mfa_code: "123456",
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Use a recovery code" }),
  );
  await userEvent.type(
    screen.getByLabelText("Recovery code", { exact: true }),
    "recovery-test",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and sign in" }),
  );
  await waitFor(() =>
    expect(next).toHaveBeenCalledWith({ queryKey: ["session"] }),
  );
  expect(post).toHaveBeenLastCalledWith("/api/v1/auth/google/login", {
    body: {
      credential: "synthetic-provider-proof",
      nonce: "synthetic-nonce",
      recovery_code: "recovery-test",
    },
  });
  expect(
    post.mock.calls.filter(([path]) => path.endsWith("/challenge")),
  ).toHaveLength(1);
});

it("checks email and password before showing two-step verification", async () => {
  configuration();
  const rejected = (code: string) => ({
    response: new Response(null, { status: 401 }),
    error: { error: { code } },
  });
  const post = vi
    .spyOn(api, "POST")
    .mockResolvedValueOnce(rejected("ERR_AUTH") as any)
    .mockResolvedValueOnce(rejected("ERR_MFA_REQUIRED") as any)
    .mockResolvedValueOnce(response({ status: "authenticated" }) as any);
  const next = vi
    .spyOn(queryClient, "invalidateQueries")
    .mockResolvedValue(undefined);
  show(<Auth />);
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "person@example.com",
  );
  const password = screen.getByLabelText("Password", { exact: true });
  await userEvent.type(password, "example-wrong");
  const signIn = screen.getByRole("button", { name: "Sign in", exact: true });
  await userEvent.click(signIn);
  expect(await screen.findByRole("alert")).toHaveTextContent("couldn't verify");
  expect(
    screen.queryByRole("heading", { name: "Verify your sign-in" }),
  ).not.toBeInTheDocument();
  await userEvent.clear(password);
  await userEvent.type(password, "example-correct");
  await userEvent.click(signIn);
  const input = await screen.findByLabelText("Authenticator code", {
    exact: true,
  });
  expect(input).toHaveFocus();
  expect(password).not.toBeVisible();
  expect(password).toHaveValue("");
  const credentials = {
    email: "person@example.com",
    password: "example-correct",
  };
  expect(post).toHaveBeenLastCalledWith("/api/v1/auth/login", {
    body: credentials,
  });
  expect(next).not.toHaveBeenCalled();
  await userEvent.type(input, "123456");
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and sign in" }),
  );
  await waitFor(() =>
    expect(next).toHaveBeenCalledWith({ queryKey: ["session"] }),
  );
  expect(post).toHaveBeenLastCalledWith("/api/v1/auth/login", {
    body: { ...credentials, mfa_code: "123456" },
  });
});
