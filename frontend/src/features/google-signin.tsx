import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, keys, queryClient, result } from "../api/runtime";
import { ErrorNotice } from "../components/ui";
import { SignInVerification, type SignInFactor } from "./sign-in-verification";
type Credential = { credential: string };
type GoogleSDK = {
  accounts: {
    id: {
      initialize: (o: {
        client_id: string;
        nonce: string;
        auto_select: boolean;
        callback: (r: Credential) => void;
      }) => void;
      renderButton: (
        node: HTMLElement,
        o: {
          theme: "outline" | "filled_black";
          size: "large";
          text: "continue_with" | "signin_with";
          shape: "rectangular";
          width: string;
        },
      ) => void;
      cancel: () => void;
    };
  };
};
declare global {
  interface Window {
    google?: GoogleSDK;
  }
}
let sdk: Promise<void> | undefined;
function loadSDK() {
  if (window.google) return Promise.resolve();
  sdk ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    const timer = window.setTimeout(() => fail(), 10000);
    const fail = () => {
      window.clearTimeout(timer);
      script.remove();
      sdk = undefined;
      reject(
        new Error(
          "Google sign-in could not load. Use email and password, or try again.",
        ),
      );
    };
    script.onload = () => {
      window.clearTimeout(timer);
      resolve();
    };
    script.onerror = fail;
    document.head.append(script);
  });
  return sdk;
}
export function useAuthConfiguration() {
  return useQuery({
    queryKey: ["auth-configuration"],
    queryFn: () => result(api.GET("/api/v1/auth/google/config")),
  });
}
export function GoogleSignIn({
  mode = "login",
  onLinked,
  onVerificationChange,
}: {
  mode?: "login" | "link";
  onLinked?: () => void;
  onVerificationChange?: (active: boolean) => void;
}) {
  const config = useAuthConfiguration();
  const node = useRef<HTMLDivElement>(null);
  const current = useRef({ onLinked, onVerificationChange });
  current.current = { onLinked, onVerificationChange };
  const proof = useRef<{ credential: string; nonce: string } | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!config.data?.enabled || !config.data.client_id) return;
    setReady(false);
    let cancelled = false;
    let observer: ResizeObserver | undefined;
    const client_id = config.data.client_id;
    void (async () => {
      await loadSDK();
      if (cancelled) return;
      const { nonce } = await result(api.POST("/api/v1/auth/google/challenge"));
      if (cancelled || !node.current || !window.google) return;
      const host = node.current;
      const google = window.google.accounts.id;
      google.initialize({
        client_id,
        nonce,
        auto_select: false,
        callback: ({ credential }) => {
          if (cancelled) return;
          setBusy(true);
          setError(undefined);
          const body = { credential, nonce };
          let requiresVerification = false;
          void (async () => {
            if (mode === "link") {
              await result(api.POST("/api/v1/auth/google/link", { body }));
              current.current.onLinked?.();
            } else {
              await result(api.POST("/api/v1/auth/google/login", { body }));
              await queryClient.invalidateQueries({ queryKey: keys.session });
            }
          })()
            .catch((failure: unknown) => {
              if (cancelled) return;
              if (
                mode === "login" &&
                failure instanceof ApiError &&
                failure.code === "ERR_MFA_REQUIRED"
              ) {
                requiresVerification = true;
                proof.current = { credential, nonce };
                setVerifying(true);
                current.current.onVerificationChange?.(true);
              } else {
                setError(failure);
              }
            })
            .finally(() => {
              if (!cancelled) {
                setBusy(false);
                if (!requiresVerification) setAttempt((a) => a + 1);
              }
            });
        },
      });
      let lastWidth = 0;
      const render = () => {
        const width = Math.min(400, Math.floor(host.clientWidth));
        if (cancelled || !width || width === lastWidth) return;
        lastWidth = width;
        host.replaceChildren();
        google.renderButton(host, {
          theme:
            document.documentElement.dataset.theme === "dark"
              ? "filled_black"
              : "outline",
          size: "large",
          text: mode === "link" ? "continue_with" : "signin_with",
          shape: "rectangular",
          width: String(width),
        });
        setReady(true);
      };
      render();
      if (typeof ResizeObserver !== "undefined") {
        observer = new ResizeObserver(render);
        observer.observe(host);
      }
    })().catch((e) => {
      if (!cancelled) setError(e);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      window.google?.accounts.id.cancel();
    };
  }, [config.data?.enabled, config.data?.client_id, attempt, mode]);
  if (!config.data?.enabled) return null;

  function restart() {
    proof.current = null;
    setVerifying(false);
    setExpired(false);
    setError(undefined);
    setAttempt((value) => value + 1);
    current.current.onVerificationChange?.(false);
  }

  async function verify(factor: SignInFactor) {
    if (!proof.current || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      await result(
        api.POST("/api/v1/auth/google/login", {
          body: { ...proof.current, ...factor },
        }),
      );
      proof.current = null;
      await queryClient.invalidateQueries({ queryKey: keys.session });
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        ["ERR_GOOGLE_AUTH", "ERR_CSRF"].includes(failure.code ?? "")
      ) {
        proof.current = null;
        setExpired(true);
        setError(
          new Error(
            "Your Google sign-in expired. Go back and choose your Google account again.",
          ),
        );
      } else {
        setError(failure);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="google-signin" hidden={verifying}>
        {mode === "login" && (
          <div className="auth-divider">
            <span>or use Google</span>
          </div>
        )}
        <div
          className="google-signin-slot"
          ref={node}
          aria-busy={busy || (!ready && !error)}
          inert={busy || !ready}
        />
        {(busy || (!ready && !error)) && (
          <p role="status" className="muted">
            {busy ? "Verifying Google sign-in…" : "Loading Google sign-in…"}
          </p>
        )}
        <ErrorNotice
          error={error}
          retry={() => {
            setError(undefined);
            setAttempt((v) => v + 1);
          }}
        />
        {mode === "link" && (
          <p className="muted">
            Use the Google account with the same email. Authenticator
            verification is required.
          </p>
        )}
      </div>
      {verifying && (
        <SignInVerification
          description="Google confirmed your identity. Enter a code to finish signing in to LedgerPilot."
          error={error}
          expired={expired}
          busy={busy}
          onVerify={(factor) => void verify(factor)}
          restart={restart}
          clearError={() => setError(undefined)}
        />
      )}
    </>
  );
}
