# ADR-015 - Optional Google federation and verification hardening

Date: 2 November 2026. Status: Accepted within the user's explicit authentication improvement request.

FastAPI remains the identity/session authority. Google Identity Services provides an optional verified identity proof; it cannot create a financial authorization or bypass LedgerPilot MFA. Subject mappings are explicit, existing email accounts require authenticated/MFA linking, and non-authoritative Google email addresses require a verified local account first. A browser-bound one-use nonce and existing Origin/CSRF checks protect exchanges. Provider credentials are transient; public client configuration contains no server secrets. The feature remains disabled without its configured client ID.

The user explicitly requests composition rules for new/reset passwords in addition to the existing 15-character minimum and compromised-password defense. Existing hashes remain valid for sign-in. Three unverified issuance requests share a durable 24-hour resend pause; successful verification restores eligibility. Verification is bound to the signup browser to prevent pending-password/account pre-hijacking across signup sessions. Account existence remains undisclosed by resend/register responses. See authentication-improvements.md for recovery UX and Mailpit/Google setup.

No hosted auth provider, new session store or banking/accounting design is introduced. Additive migrations 0021/0022 preserve existing data and force RLS on new proof/mapping tables. HTTPS/browser/provider acceptance and production retention remain later operational gates. Roll forward rather than deleting identity history.
