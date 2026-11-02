import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { ArrowUpRight, ShieldCheck, Sparkles } from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field } from "../components/ui";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
  mfa: z.string(),
});
type Credentials = z.infer<typeof schema>;
export function Auth() {
  const [mode, setMode] = useState<
    "login" | "register" | "verify" | "reset-request" | "reset"
  >("login");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [token, setToken] = useState("");
  const [notice, setNotice] = useState("");
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<Credentials>({
    resolver: zodResolver(schema),
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
    onSuccess: () => {
      if (mode === "login")
        void queryClient.invalidateQueries({ queryKey: keys.session });
      else {
        setNotice("Check your email for your verification token.");
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
    mutation.reset();
    recovery.reset();
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
          <ErrorNotice error={mutation.error || recovery.error} />
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
                    ? "Use at least 15 characters. A memorable phrase works well."
                    : undefined
                }
              >
                <input
                  type="password"
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
                <Field label="New password">
                  <input
                    name="password"
                    type="password"
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
