import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { ErrorNotice } from "../components/ui";
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
        o: { theme: string; size: string; text: string },
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
  factors = () => ({}),
  onLinked,
}: {
  mode?: "login" | "link";
  factors?: () => { mfa_code?: string; recovery_code?: string };
  onLinked?: () => void;
}) {
  const config = useAuthConfiguration();
  const node = useRef<HTMLDivElement>(null);
  const current = useRef({ factors, onLinked });
  current.current = { factors, onLinked };
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!config.data?.enabled || !config.data.client_id) return;
    let cancelled = false;
    const client_id = config.data.client_id;
    void (async () => {
      await loadSDK();
      if (cancelled) return;
      const { nonce } = await result(api.POST("/api/v1/auth/google/challenge"));
      if (cancelled || !node.current || !window.google) return;
      node.current.replaceChildren();
      window.google.accounts.id.initialize({
        client_id,
        nonce,
        auto_select: false,
        callback: ({ credential }) => {
          if (cancelled) return;
          setBusy(true);
          setError(undefined);
          const body = { credential, nonce, ...current.current.factors() };
          void (async () => {
            if (mode === "link") {
              await result(api.POST("/api/v1/auth/google/link", { body }));
              current.current.onLinked?.();
            } else {
              await result(api.POST("/api/v1/auth/google/login", { body }));
              await queryClient.invalidateQueries({ queryKey: keys.session });
            }
          })()
            .catch(setError)
            .finally(() => {
              if (!cancelled) {
                setBusy(false);
                setAttempt((a) => a + 1);
              }
            });
        },
      });
      window.google.accounts.id.renderButton(node.current, {
        theme: "outline",
        size: "large",
        text: mode === "link" ? "continue_with" : "signin_with",
      });
    })().catch((e) => {
      if (!cancelled) setError(e);
    });
    return () => {
      cancelled = true;
      window.google?.accounts.id.cancel();
    };
  }, [config.data?.enabled, config.data?.client_id, attempt, mode]);
  if (!config.data?.enabled) return null;
  return (
    <div className="google-signin">
      <div ref={node} aria-busy={busy} inert={busy} />
      {busy && <p role="status">Verifying Google sign-in…</p>}
      <ErrorNotice
        error={error}
        retry={() => {
          setError(undefined);
          setAttempt((v) => v + 1);
        }}
      />
      <p className="muted">
        {mode === "link"
          ? "Use the Google account with the same email. Authenticator verification is required."
          : "If you already use an authenticator, enter its code above before choosing Google."}
      </p>
    </div>
  );
}
