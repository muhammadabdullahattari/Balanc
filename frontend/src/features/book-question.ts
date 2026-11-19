// Resolve only unambiguous period changes; leave other questions to the report service.
const period =
  /(?:this month|last month|in (?:january|february|march|april|may|june|july|august|september|october|november|december)(?: \d{4})?)$/i;

export function resolveBookQuestion(input: string, previous: string): string {
  const earlier = previous.trim().replace(/[?.]+$/, "");
  const next = input
    .trim()
    .replace(/[?.]+$/, "")
    .replace(/^(?:and|how about|what about)\s+/i, "");
  if (!period.test(earlier)) return input;
  const when = earlier.match(period)![0];
  const metric = /^(revenue|expenses|net profit)$/i.exec(next);
  if (metric) return `show me ${metric[1]} ${when}`;
  const combined = /^for (.+) (revenue|expenses|net profit)$/i.exec(next);
  if (
    combined &&
    `in ${combined[1]}`.match(period)?.[0] === `in ${combined[1]}`
  )
    return `show me ${combined[2]} in ${combined[1]}`;
  if (/^show (?:the )?top 5 expense categories$/i.test(next))
    return `show top expense accounts ${when}`;
  const priorMetric = earlier.match(/\b(net profit|revenue|expenses)\b/i)?.[1];
  if (priorMetric && /^compare that with the previous quarter$/i.test(next))
    return `compare ${priorMetric} with previous quarter ${when}`;
  if (priorMetric && /^which accounts drove that$/i.test(next))
    return `explain ${priorMetric} change versus previous ${earlier.includes("previous quarter") ? "quarter" : "month"} ${when}`;
  for (const candidate of [next, `in ${next}`]) {
    if (candidate.match(period)?.[0] === candidate)
      return earlier.replace(period, candidate);
  }
  return input;
}
