# Security policy

## Reporting

Do not publish credentials, customer financial data, or suspected vulnerabilities in issues. Contact the project maintainer privately with a concise reproduction and impact description.

## Development requirements

- Never commit `.env` or provider credentials.
- Keep browser configuration public-only; database, signing, and service-role keys remain server-side.
- Scope every business-owned query and operation to the authenticated tenant.
- Treat imported bank statements and uploaded documents as untrusted input.
- Record security-relevant actions in the audit trail.
