import { GoogleSignIn, useAuthConfiguration } from "./google-signin";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { ArrowUpRight, ShieldCheck, Sparkles } from "lucide-react";
import { api, keys, queryClient, result, ApiError } from "../api/runtime";
import { Button, ErrorNotice, Field, PasswordInput } from "../components/ui";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
  mfa: z.string(),
});
const strongPassword = z
  .string()
  .min(15, "Use at least 15 characters.")
  .max(128)
  .regex(/[A-Z]/, "Add an uppercase letter.")
  .regex(/[a-z]/, "Add a lowercase letter.")
  .regex(/[0-9]/, "Add a number.")
  .regex(/[^\p{L}\p{N}\s]/u, "Add a special character.");
type Credentials = z.infer<typeof schema>;
export function Auth() {
  const [mode, setMode] = useState<
    "login" | "register" | "verify" | "reset-request" | "reset"
  >("login");
  const config = useAuthConfiguration();
  const [verificationEmail, setVerificationEmail] = useState("");
  const [resendWait, setResendWait] = useState(0);
  useEffect(() => {
    if (!resendWait) return;
    const timer = window.setTimeout(
      () => setResendWait((v) => Math.max(0, v - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [resendWait]);
  const resend = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/auth/verification-request", {
          body: { email: verificationEmail },
        }),
      ),
    onError: (error) => {
      if (error instanceof ApiError && error.retryAfter)
        setResendWait(Math.min(error.retryAfter, 86400));
    },
    onSuccess: () => {
      setToken("");
      setNotice(
        "Your request was received. Check for the newest email, or try resending shortly. Use only the newest code.",
      );
      setResendWait(60);
    },
  });
  const [recoveryCode, setRecoveryCode] = useState("");
  const [token, setToken] = useState("");
  const [notice, setNotice] = useState("");
  const {
    register,
    getValues,
    setValue,
    handleSubmit,
    formState: { errors },
  } = useForm<Credentials>({
    resolver: zodResolver(
      mode === "register"
        ? schema.extend({ password: strongPassword })
        : schema,
    ),
    defaultValues: { email: "", password: "", mfa: "" },
  });
  const mutation = useMutation({
    mutationFn: async (v: Credentials) => {
      if (mode === "login")
        return result(
          api.POST("/api/v1/auth/login", {
            body: {
              email: v.email,
              password: v.password,
              ...(recoveryCode
                ? { recovery_code: recoveryCode }
                : v.mfa
                  ? { mfa_code: v.mfa }
                  : {}),
            },
          }),
        );
      return result(
        api.POST("/api/v1/auth/register", {
          body: { email: v.email, password: v.password },
        }),
      );
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "ERR_EMAIL_UNVERIFIED") {
        setVerificationEmail(getValues("email"));
        setNotice(
          "Verify your email using the browser where you signed up. You can request a new code below.",
        );
        setMode("verify");
      }
    },
    onSuccess: (_data, values) => {
      if (mode === "login")
        void queryClient.invalidateQueries({ queryKey: keys.session });
      else {
        setVerificationEmail(values.email);
        setNotice(
          "Your account is pending verification. Check your email, or try resending shortly.",
        );
        setMode("verify");
      }
    },
  });
  const recovery = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const values = new FormData(form);
      if (mode === "verify")
        return result(api.POST("/api/v1/auth/verify", { body: { token } }));
      if (mode === "reset-request")
        return result(
          api.POST("/api/v1/auth/reset-request", {
            body: { email: String(values.get("email")) },
          }),
        );
      return result(
        api.POST("/api/v1/auth/reset", {
          body: { token, new_password: String(values.get("password")) },
        }),
      );
    },
    onSuccess: () => {
      if (mode === "reset-request") {
        setNotice("If the account is eligible, a reset email is on its way.");
        setMode("reset");
      } else {
        setNotice("You're ready to sign in.");
        setToken("");
        setMode("login");
      }
    },
  });
  const titles = {
    login: "Welcome back.",
    register: "A clearer view of your business.",
    verify: "Check your inbox.",
    "reset-request": "Let's get you back in.",
    reset: "Choose a new password.",
  };
  const change = (next: typeof mode) => {
    setMode(next);
    setValue("password", "");
    setValue("mfa", "");
    setRecoveryCode("");
    setToken("");
    mutation.reset();
    recovery.reset();
    resend.reset();
    setNotice("");
  };
  return (
    <div className="auth-layout">
      <aside className="auth-story">
        <a className="brand" href="/">
          <span className="brand-mark">L</span>LedgerPilot
        </a>
        <div>
          <span className="eyebrow">CLARITY. CONFIDENCE. CONTROL.</span>
          <h1>
            Good books.
            <br />
            Better decisions.
          </h1>
          <p>
            Your finances, connected in one thoughtful workspace. Know what came
            in, what’s due, and what needs your attention.
          </p>
          <div className="story-lines">
            <div>
              <ShieldCheck /> Every entry, accountable.
            </div>
            <div>
              <Sparkles /> Assistance you stay in control of.
            </div>
          </div>
        </div>
        <span className="auth-foot">
          A little less bookkeeping. A lot more clarity.
        </span>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          <p className="eyebrow">YOUR FINANCE OFFICE</p>
          <h1>{titles[mode]}</h1>
          <p className="muted">
            {mode === "login"
              ? "Sign in to pick up where you left off."
              : "Secure access to your business finances."}
          </p>
          {notice && (
            <div role="status" className="notice">
              {notice}
            </div>
          )}
          <ErrorNotice
            error={mutation.error || recovery.error || resend.error}
          />
          {mode === "login" || mode === "register" ? (
            <form onSubmit={handleSubmit((v) => mutation.mutate(v))}>
              <Field label="Email address">
                <input
                  type="email"
                  autoComplete="email"
                  {...register("email")}
                  aria-invalid={!!errors.email}
                />
                {errors.email && (
                  <small role="alert">{errors.email.message}</small>
                )}
              </Field>
              <Field
                label="Password"
                hint={
                  mode === "register"
                    ? "15–128 characters, with uppercase, lowercase, a number and a special character."
                    : undefined
                }
              >
                <PasswordInput
                  key={mode}
                  maxLength={128}
                  autoComplete={
                    mode === "login" ? "current-password" : "new-password"
                  }
                  minLength={mode === "register" ? 15 : 1}
                  {...register("password")}
                />
                {errors.password && (
                  <small role="alert">{errors.password.message}</small>
                )}
              </Field>
              {mode === "login" && (
                <Field label="Authenticator code (if enabled)">
                  <input
                    autoComplete="one-time-code"
                    inputMode="numeric"
                    maxLength={6}
                    {...register("mfa")}
                  />
                </Field>
              )}
              {mode === "login" && (
                <Field label="Recovery code (if authenticator unavailable)">
                  <input
                    autoComplete="off"
                    value={recoveryCode}
                    onChange={(e) => setRecoveryCode(e.target.value)}
                  />
                </Field>
              )}
              <Button busy={mutation.isPending} type="submit">
                {mode === "login" ? "Sign in" : "Create account"}
                <ArrowUpRight size={17} />
              </Button>
            </form>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                recovery.mutate(e.currentTarget);
              }}
            >
              {mode === "reset-request" ? (
                <Field label="Email address">
                  <input
                    name="email"
                    type="email"
                    required
                    autoComplete="email"
                  />
                </Field>
              ) : (
                <Field label="Token from your email">
                  <input
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    required
                    minLength={16}
                    autoComplete="off"
                  />
                </Field>
              )}
              {mode === "reset" && (
                <Field
                  label="New password"
                  hint="15–128 characters, including uppercase, lowercase, a number and a special character."
                >
                  <PasswordInput
                    name="password"
                    pattern={
                      "(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[^\\p{L}\\p{N}\\s]).{15,128}"
                    }
                    autoComplete="new-password"
                    minLength={15}
                    maxLength={128}
                    required
                  />
                </Field>
              )}
              <Button busy={recovery.isPending} type="submit">
                Continue
              </Button>
            </form>
          )}
          {mode === "verify" && (
            <div className="verification-resend">
              <Field label="Verification email address">
                <input
                  type="email"
                  autoComplete="email"
                  required
                  value={verificationEmail}
                  onChange={(e) => setVerificationEmail(e.target.value)}
                />
              </Field>
              <Button
                variant="secondary"
                type="button"
                busy={resend.isPending}
                disabled={
                  !z.string().email().safeParse(verificationEmail).success ||
                  resendWait > 0
                }
                onClick={() => resend.mutate()}
              >
                {resendWait > 0
                  ? resendWait > 3600
                    ? `Resend available in about ${Math.ceil(resendWait / 3600)} hours`
                    : `Resend available in ${resendWait}s`
                  : "Resend verification code"}
              </Button>
              <p className="muted">
                Use the same browser and app address where you signed up. If you
                cleared cookies or changed browsers, start sign-up again here.
                Codes expire in 15 minutes. Requesting a new code replaces the
                previous one. After three unverified requests, new requests
                pause for 24 hours.
              </p>
            </div>
          )}
          {(mode === "login" || mode === "register") && (
            <GoogleSignIn
              factors={() => ({
                ...(getValues("mfa") ? { mfa_code: getValues("mfa") } : {}),
                ...(recoveryCode ? { recovery_code: recoveryCode } : {}),
              })}
            />
          )}
          {config.data?.local_mailbox && (
            <p className="notice">
              Local development: verification and reset emails appear in{" "}
              <a
                href={config.data.local_mailbox}
                target="_blank"
                rel="noreferrer"
              >
                Mailpit
              </a>
              . They are not delivered to your personal inbox.
            </p>
          )}
          <div className="auth-links">
            {mode === "login" ? (
              <>
                <button onClick={() => change("reset-request")}>
                  Forgot password?
                </button>
                <button onClick={() => change("register")}>
                  Create an account
                </button>
                <button onClick={() => change("verify")}>Verify email</button>
              </>
            ) : (
              <button onClick={() => change("login")}>Back to sign in</button>
            )}
          </div>
        </div>
        <p className="auth-privacy">
          <ShieldCheck size={15} /> Your session is protected with secure
          cookies.
        </p>
      </main>
    </div>
  );
}
