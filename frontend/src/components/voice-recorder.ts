export interface PersistedVoiceReply {
  recordingId: string;
  sessionId: string;
  locale: "en_US" | "ur_PK" | "en_ur_mixed";
  rawTranscript: string;
  correctedTranscript: string;
  extractedIntent: string;
  extractedEntities: {
    party_name?: string;
    amount?: string;
    currency?: string;
    accounting_date?: string;
    account_code?: string;
  };
  uncertainFields: string[];
  ttsReadbackText: string;
  status: string;
  savedAt: string;
}

const PREFERRED_AUDIO_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/wav",
] as const;

export const MAX_VOICE_AUDIO_BYTES = 2_800_000;

export function pickSupportedAudioMimeType(): string {
  if (
    typeof MediaRecorder === "undefined" ||
    typeof MediaRecorder.isTypeSupported !== "function"
  ) {
    return "";
  }
  for (const candidate of PREFERRED_AUDIO_MIME_TYPES) {
    try {
      if (MediaRecorder.isTypeSupported(candidate)) {
        return candidate;
      }
    } catch {
      // ignore unsupported probe error
    }
  }
  return "";
}

export function describeMicrophoneError(err: unknown): string {
  if (typeof window !== "undefined" && window.isSecureContext === false) {
    return "Microphone recording requires a secure connection (HTTPS or localhost). Open this page over HTTPS, or upload an audio file instead.";
  }
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function" ||
    typeof MediaRecorder === "undefined"
  ) {
    return "Microphone recording is not supported in this browser. Try Chrome, Edge, Firefox, or Safari, or upload an audio file below.";
  }

  const name =
    err && typeof err === "object" && "name" in err
      ? String((err as { name?: unknown }).name || "")
      : "";
  const message =
    err && typeof err === "object" && "message" in err
      ? String((err as { message?: unknown }).message || "")
      : "";
  const combined = `${name} ${message}`.toLowerCase();

  if (
    name === "NotAllowedError" ||
    name === "PermissionDeniedError" ||
    name === "SecurityError" ||
    combined.includes("permission") ||
    combined.includes("denied") ||
    combined.includes("not allowed")
  ) {
    return "Microphone access was denied or blocked. Allow microphone permission in your browser address bar settings, then click Record with microphone again.";
  }
  if (
    name === "NotFoundError" ||
    name === "DevicesNotFoundError" ||
    combined.includes("device not found") ||
    combined.includes("requested device not found")
  ) {
    return "No microphone was detected on this device. Connect a microphone and try again, or upload an audio file.";
  }
  if (
    name === "NotReadableError" ||
    name === "TrackStartError" ||
    combined.includes("could not start audio source") ||
    combined.includes("in use")
  ) {
    return "Your microphone is busy or being used by another application. Close other apps using the microphone and try again.";
  }
  if (name === "OverconstrainedError" || name === "AbortError") {
    return "Could not initialize your microphone with default audio settings. Reconnect your microphone or upload an audio file.";
  }
  return "Could not start microphone recording. Check your browser microphone permissions and input device, or upload an audio file.";
}

export function formatRecordingDuration(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function deterministicFallbackHash(input: string): string {
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < input.length; i += 1) {
    const code = input.charCodeAt(i);
    h1 ^= code;
    h1 = Math.imul(h1, 0x01000193) >>> 0;
    h2 ^= code + ((i & 0xff) << 8);
    h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
  }
  return `${h1.toString(16).padStart(8, "0")}${h2.toString(16).padStart(8, "0")}${input.length.toString(16).padStart(8, "0")}`;
}

export async function computeRecordingId(
  locale: string,
  audioBase64: string,
  transcriptInput: string,
): Promise<string> {
  const payload = `${locale}|${audioBase64 || transcriptInput.trim()}`;
  try {
    if (
      typeof crypto !== "undefined" &&
      crypto.subtle &&
      typeof crypto.subtle.digest === "function" &&
      typeof TextEncoder !== "undefined"
    ) {
      const digest = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(payload),
      );
      const hex = Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      return `rec_${hex.slice(0, 28)}`;
    }
  } catch {
    // fall back to deterministic JS hash
  }
  return `rec_${deterministicFallbackHash(payload)}`;
}

function storageKeyForBusiness(businessId: string): string {
  return `ledgerpilot.voiceReplies.v1.${businessId}`;
}

export function loadPersistedVoiceReply(
  businessId: string,
  recordingId: string,
): PersistedVoiceReply | null {
  if (!businessId || !recordingId || typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(storageKeyForBusiness(businessId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, PersistedVoiceReply>;
    return parsed && typeof parsed === "object"
      ? (parsed[recordingId] ?? null)
      : null;
  } catch {
    return null;
  }
}

export function savePersistedVoiceReply(
  businessId: string,
  reply: PersistedVoiceReply,
): void {
  if (!businessId || !reply.recordingId || typeof window === "undefined") {
    return;
  }
  try {
    const key = storageKeyForBusiness(businessId);
    const raw = window.localStorage.getItem(key);
    const existing = raw
      ? (JSON.parse(raw) as Record<string, PersistedVoiceReply>)
      : {};
    const next: Record<string, PersistedVoiceReply> = {
      ...(existing && typeof existing === "object" ? existing : {}),
      [reply.recordingId]: reply,
    };
    const entries = Object.entries(next);
    if (entries.length > 50) {
      entries.sort((a, b) => a[1].savedAt.localeCompare(b[1].savedAt));
      for (let i = 0; i < entries.length - 50; i += 1) {
        delete next[entries[i][0]];
      }
    }
    window.localStorage.setItem(key, JSON.stringify(next));
  } catch {
    // ignore storage quota errors
  }
}
