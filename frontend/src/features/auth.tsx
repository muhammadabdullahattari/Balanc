import { GoogleSignIn, useAuthConfiguration } from "./google-signin";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { api, keys, queryClient, result, ApiError } from "../api/runtime";
import { Button, ErrorNotice, Field, PasswordInput } from "../components/ui";
import { SignInVerification, type SignInFactor } from "./sign-in-verification";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
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

function LedgerIllustration() {
  return (
    <svg
      className="auth-illustration"
      viewBox="0 0 480 270"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="275" cy="132" r="112" className="art-orbit" />
      <circle cx="275" cy="132" r="133" className="art-orbit" />
      <path d="M75 207h330M105 65h280M275 8v247" className="art-orbit" />
      <rect
        x="112"
        y="43"
        width="246"
        height="170"
        rx="18"
        className="art-back"
        transform="rotate(-9 112 43)"
      />
      <g className="art-sheet">
        <rect
          x="96"
          y="42"
          width="269"
          height="177"
          rx="16"
          className="art-paper"
        />
        <path
          d="M96 89h269M117 125h227M117 156h227M117 187h227"
          className="art-rule"
        />
        <rect
          x="118"
          y="61"
          width="18"
          height="14"
          rx="3"
          className="art-chip"
        />
        <path
          d="M147 68h65M122 108h64M122 140h84M122 171h58M122 201h74M304 108h37M314 140h27M298 171h43M307 201h34"
          className="art-lines"
        />
      </g>
      <g className="art-seal">
        <circle cx="366" cy="194" r="32" />
        <path d="m352 194 9 9 18-19" />
      </g>
      <path d="M404 58h20m-10-10v20M65 177h16m-8-8v16" className="art-spark" />
    </svg>
  );
}

export function Auth() {
  const [mode, setMode] = useState<
    "login" | "register" | "verify" | "reset-request" | "reset"
  >("login");
  const config = useAuthConfiguration();
  const [googleVerification, setGoogleVerification] = useState(false);
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
  const passwordProof = useRef<Credentials | null>(null);
  const [passwordVerification, setPasswordVerification] = useState(false);
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationError, setVerificationError] = useState<unknown>();
  const [verificationExpired, setVerificationExpired] = useState(false);
  const verifying = googleVerification || passwordVerification;
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
    defaultValues: { email: "", password: "" },
  });
  const mutation = useMutation({
    mutationFn: async (v: Credentials) => {
      if (mode === "login")
        return result(
          api.POST("/api/v1/auth/login", {
            body: { email: v.email, password: v.password },
          }),
        );
      return result(
        api.POST("/api/v1/auth/register", {
          body: { email: v.email, password: v.password },
        }),
      );
    },
    onError: (error, values) => {
      if (
        mode === "login" &&
        error instanceof ApiError &&
        error.code === "ERR_MFA_REQUIRED"
      ) {
        passwordProof.current = values;
        setValue("password", "");
        setPasswordVerification(true);
        mutation.reset();
        return;
      }
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
  async function verifyPasswordSignIn(factor: SignInFactor) {
    if (!passwordProof.current || verificationBusy) return;
    setVerificationBusy(true);
    setVerificationError(undefined);
    try {
      await result(
        api.POST("/api/v1/auth/login", {
          body: { ...passwordProof.current, ...factor },
        }),
      );
      passwordProof.current = null;
      await queryClient.invalidateQueries({ queryKey: keys.session });
    } catch (error) {
      if (
        error instanceof ApiError &&
        [
          "ERR_AUTH",
          "ERR_CSRF",
          "ERR_EMAIL_UNVERIFIED",
          "ERR_ACCOUNT_UNAVAILABLE",
        ].includes(error.code ?? "")
      ) {
        passwordProof.current = null;
        setVerificationExpired(true);
      }
      setVerificationError(error);
    } finally {
      setVerificationBusy(false);
    }
  }

  function restartPasswordSignIn() {
    passwordProof.current = null;
    setPasswordVerification(false);
    setVerificationError(undefined);
    setVerificationExpired(false);
  }

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
    register: "Create your account",
    verify: "Check your inbox.",
    "reset-request": "Reset your password",
    reset: "Choose a new password.",
  };
  const change = (next: typeof mode) => {
    setMode(next);
    setValue("password", "");
    setToken("");
    mutation.reset();
    recovery.reset();
    resend.reset();
    setNotice("");
  };
  return (
    <div className="auth-layout">
      <aside className="auth-story" aria-label="About LedgerPilot">
        <a className="brand" href="/">
          <span className="brand-mark">L</span>LedgerPilot
        </a>
        <div className="auth-story-content">
          <span className="eyebrow">THE FINANCE WORKSPACE</span>
          <p className="auth-display">A clear view of your books.</p>
          <p>
            Review transactions, organize supporting documents, and see what
            needs attention before you close the month.
          </p>
          <LedgerIllustration />
        </div>
        <span className="auth-foot">
          <ShieldCheck size={16} /> Secure workspace access
        </span>
      </aside>
      <main className="auth-main">
        <div className="auth-card" key={mode}>
          <div hidden={verifying}>
            <p className="eyebrow">LEDGERPILOT ACCOUNT</p>
            <h1>{titles[mode]}</h1>
            <p className="muted">
              {mode === "login"
                ? "Sign in to your finance workspace."
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
                <Button busy={mutation.isPending} type="submit">
                  {mode === "login" ? "Sign in" : "Create account"}
                  <ArrowRight size={17} />
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
                  Use the same browser and app address where you signed up. If
                  you cleared cookies or changed browsers, start sign-up again
                  here. Codes expire in 15 minutes. Requesting a new code
                  replaces the previous one. After three unverified requests,
                  new requests pause for 24 hours.
                </p>
              </div>
            )}
          </div>
          {!passwordVerification &&
            (mode === "login" || mode === "register") && (
              <GoogleSignIn onVerificationChange={setGoogleVerification} />
            )}
          {passwordVerification && (
            <SignInVerification
              description="Enter a code from your authenticator app to finish signing in to LedgerPilot."
              error={verificationError}
              busy={verificationBusy}
              expired={verificationExpired}
              onVerify={(factor) => void verifyPasswordSignIn(factor)}
              restart={restartPasswordSignIn}
              clearError={() => setVerificationError(undefined)}
            />
          )}
          <div hidden={verifying}>
            {config.data?.local_mailbox && (
              <p className="notice auth-mailbox">
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
        </div>
      </main>
    </div>
  );
}
