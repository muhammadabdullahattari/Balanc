import { useEffect, useId, useMemo, useState } from "react";

export interface TimezoneOption {
  value: string;
  offsetMinutes: number;
  offsetLabel: string;
  label: string;
}

const FALLBACK_IANA_TIMEZONES = [
  "UTC",
  "Pacific/Honolulu",
  "America/Anchorage",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Toronto",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Zurich",
  "Europe/Istanbul",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "Asia/Riyadh",
  "Asia/Dubai",
  "Asia/Karachi",
  "Asia/Kolkata",
  "Asia/Dhaka",
  "Asia/Bangkok",
  "Asia/Singapore",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
];

function computeUtcOffsetInfo(
  timeZone: string,
  refDate: Date,
): { offsetMinutes: number; offsetLabel: string } {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      timeZoneName: "longOffset",
    }).formatToParts(refDate);
    const rawPart =
      parts.find((p) => p.type === "timeZoneName")?.value || "GMT";
    if (rawPart === "GMT" || rawPart === "UTC" || rawPart === "GMT+00:00") {
      return { offsetMinutes: 0, offsetLabel: "UTC+00:00" };
    }
    const match = rawPart.match(/^GMT([+-])(\d{1,2})(?::?(\d{2}))?$/);
    if (match) {
      const sign = match[1] === "-" ? -1 : 1;
      const hours = Number(match[2] || "0");
      const mins = Number(match[3] || "0");
      const padded = `UTC${match[1]}${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
      return { offsetMinutes: sign * (hours * 60 + mins), offsetLabel: padded };
    }
    const normalized = rawPart.replace(/^GMT/, "UTC");
    return { offsetMinutes: 0, offsetLabel: normalized };
  } catch {
    return { offsetMinutes: 0, offsetLabel: "UTC+00:00" };
  }
}

let cachedBaseOptions: TimezoneOption[] | null = null;

export function getSupportedTimezoneOptions(
  ensureZone?: string,
): TimezoneOption[] {
  if (!cachedBaseOptions) {
    const refDate = new Date();
    const rawZones =
      typeof Intl !== "undefined" &&
      typeof Intl.supportedValuesOf === "function"
        ? Intl.supportedValuesOf("timeZone")
        : FALLBACK_IANA_TIMEZONES;
    const unique = new Set<string>(["UTC", ...rawZones]);
    const list: TimezoneOption[] = [];
    for (const tz of unique) {
      const { offsetMinutes, offsetLabel } = computeUtcOffsetInfo(tz, refDate);
      list.push({
        value: tz,
        offsetMinutes,
        offsetLabel,
        label: `${tz} (${offsetLabel})`,
      });
    }
    list.sort(
      (a, b) =>
        a.offsetMinutes - b.offsetMinutes || a.value.localeCompare(b.value),
    );
    cachedBaseOptions = list;
  }

  const trimmed = (ensureZone || "").trim();
  if (!trimmed || cachedBaseOptions.some((opt) => opt.value === trimmed)) {
    return cachedBaseOptions;
  }
  const { offsetMinutes, offsetLabel } = computeUtcOffsetInfo(
    trimmed,
    new Date(),
  );
  return [
    {
      value: trimmed,
      offsetMinutes,
      offsetLabel,
      label: `${trimmed} (${offsetLabel})`,
    },
    ...cachedBaseOptions,
  ];
}

export function detectBrowserTimezone(): string {
  try {
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (detected && detected.trim()) {
      return detected.trim();
    }
  } catch {
    // ignore
  }
  return "UTC";
}

export function formatTimezoneWithOffset(timeZone: string | undefined): string {
  const clean = (timeZone || "").trim() || "UTC";
  const match = getSupportedTimezoneOptions(clean).find(
    (opt) => opt.value === clean,
  );
  return match ? match.label : `${clean} (UTC+00:00)`;
}

export function TimezoneSelect({
  name = "timezone",
  label = "Timezone",
  value,
  defaultValue,
  onChange,
  required = true,
  hint,
}: {
  name?: string;
  label?: string;
  value?: string;
  defaultValue?: string;
  onChange?: (nextTimezone: string) => void;
  required?: boolean;
  hint?: string;
}) {
  const selectId = useId();
  const initialTz = useMemo(
    () => (value ?? defaultValue)?.trim() || detectBrowserTimezone(),
    [defaultValue, value],
  );
  const [internalValue, setInternalValue] = useState<string>(initialTz);
  const [search, setSearch] = useState<string>("");

  const activeValue = value !== undefined ? value : internalValue;
  const allOptions = useMemo(
    () => getSupportedTimezoneOptions(activeValue),
    [activeValue],
  );

  const filteredOptions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allOptions;
    return allOptions.filter(
      (opt) =>
        opt.label.toLowerCase().includes(q) ||
        opt.value.toLowerCase().replace(/_/g, " ").includes(q),
    );
  }, [allOptions, search]);

  useEffect(() => {
    if (!search.trim()) return;
    if (
      filteredOptions.length > 0 &&
      !filteredOptions.some((opt) => opt.value === activeValue)
    ) {
      const next = filteredOptions[0].value;
      if (value === undefined) {
        setInternalValue(next);
      }
      onChange?.(next);
    }
  }, [activeValue, filteredOptions, onChange, search, value]);

  const visibleOptions = useMemo(() => {
    if (filteredOptions.length > 0) return filteredOptions;
    const current = allOptions.find((opt) => opt.value === activeValue);
    return current ? [current] : allOptions.slice(0, 1);
  }, [activeValue, allOptions, filteredOptions]);

  return (
    <div className="field tz-select-field">
      <label htmlFor={selectId}>{label}</label>
      <div className="tz-select-stack">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search city, country, or UTC offset…"
          aria-label={`Search ${label.toLowerCase()}`}
          autoComplete="off"
        />
        <select
          id={selectId}
          name={name}
          value={activeValue}
          required={required}
          onChange={(e) => {
            const next = e.target.value;
            if (value === undefined) {
              setInternalValue(next);
            }
            onChange?.(next);
          }}
        >
          {visibleOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
      {search.trim() && filteredOptions.length === 0 ? (
        <small>
          No time zones match &ldquo;{search.trim()}&rdquo; — keeping{" "}
          {activeValue}.
        </small>
      ) : (
        hint && <small>{hint}</small>
      )}
    </div>
  );
}
