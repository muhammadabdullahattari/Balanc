-- Frozen initial Phase 2 schema. Do not regenerate after applying.

CREATE TABLE app.auth_rate_buckets (
	key VARCHAR(64) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	attempts INTEGER NOT NULL, 
	PRIMARY KEY (key)
)

;

CREATE TABLE app.businesses (
	id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	currency_precision INTEGER NOT NULL, 
	timezone VARCHAR(100) NOT NULL, 
	basis VARCHAR(10) NOT NULL, 
	fiscal_month INTEGER NOT NULL, 
	jurisdiction VARCHAR(100) NOT NULL, 
	policy_version INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (currency_precision BETWEEN 0 AND 6), 
	CHECK (basis IN ('cash','accrual')), 
	CHECK (fiscal_month BETWEEN 1 AND 12)
)

;

CREATE TABLE app.users (
	id UUID NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	password_hash VARCHAR NOT NULL, 
	verified BOOLEAN DEFAULT false NOT NULL, 
	suspended BOOLEAN DEFAULT false NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
)

;

CREATE TABLE app.accounts (
	id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	control VARCHAR(30), 
	active BOOLEAN NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id, id), 
	UNIQUE (business_id, code), 
	CHECK (kind IN ('asset','liability','equity','income','expense')), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id)
)

;
CREATE INDEX ix_app_accounts_business_id ON app.accounts (business_id);

CREATE TABLE app.action_tokens (
	token_hash VARCHAR(64) NOT NULL, 
	user_id UUID NOT NULL, 
	purpose VARCHAR(20) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_action_tokens_user_id ON app.action_tokens (user_id);

CREATE TABLE app.audit_events (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	business_id UUID, 
	action VARCHAR(100) NOT NULL, 
	resource_id UUID, 
	occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id)
)

;
CREATE INDEX ix_app_audit_events_business_id ON app.audit_events (business_id);
CREATE INDEX ix_app_audit_events_user_id ON app.audit_events (user_id);

CREATE TABLE app.invitations (
	token_hash VARCHAR(64) NOT NULL, 
	business_id UUID NOT NULL, 
	email VARCHAR(254) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id)
)

;
CREATE INDEX ix_app_invitations_business_id ON app.invitations (business_id);

CREATE TABLE app.journals (
	id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	accounting_date DATE NOT NULL, 
	memo VARCHAR(2000) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	idempotency_key VARCHAR(100) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	reversal_of UUID, 
	created_by UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id, id), 
	UNIQUE (business_id, idempotency_key), 
	UNIQUE (business_id, reversal_of), 
	FOREIGN KEY(business_id, reversal_of) REFERENCES app.journals (business_id, id), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id), 
	FOREIGN KEY(created_by) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_journals_business_id ON app.journals (business_id);
CREATE INDEX ix_app_journals_accounting_date ON app.journals (accounting_date);

CREATE TABLE app.memberships (
	business_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	can_post BOOLEAN NOT NULL, 
	can_invite BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (business_id, user_id), 
	CHECK (role IN ('owner','accountant','bookkeeper','reviewer','auditor')), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_memberships_user_id ON app.memberships (user_id);

CREATE TABLE app.mfa_factors (
	user_id UUID NOT NULL, 
	encrypted_secret VARCHAR NOT NULL, 
	last_counter INTEGER NOT NULL, 
	verified BOOLEAN NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;

CREATE TABLE app.periods (
	id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	locked BOOLEAN NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (start_date <= end_date), 
	UNIQUE (business_id, start_date), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id)
)

;
CREATE INDEX ix_app_periods_business_id ON app.periods (business_id);

CREATE TABLE app.policy_history (
	id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	effective_date DATE NOT NULL, 
	basis VARCHAR(10) NOT NULL, 
	jurisdiction VARCHAR(100) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id, version), 
	FOREIGN KEY(business_id) REFERENCES app.businesses (id)
)

;
CREATE INDEX ix_app_policy_history_business_id ON app.policy_history (business_id);

CREATE TABLE app.recovery_codes (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	code_hash VARCHAR(64) NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, code_hash), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_recovery_codes_user_id ON app.recovery_codes (user_id);

CREATE TABLE app.sessions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	csrf_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	mfa BOOLEAN NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_sessions_user_id ON app.sessions (user_id);

CREATE TABLE app.journal_lines (
	id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	journal_id UUID NOT NULL, 
	account_id UUID NOT NULL, 
	position INTEGER NOT NULL, 
	debit NUMERIC(24, 6) NOT NULL, 
	credit NUMERIC(24, 6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id, journal_id) REFERENCES app.journals (business_id, id), 
	FOREIGN KEY(business_id, account_id) REFERENCES app.accounts (business_id, id), 
	CHECK (debit >= 0 AND credit >= 0 AND ((debit > 0) <> (credit > 0))), 
	UNIQUE (journal_id, position)
)

;
CREATE INDEX ix_app_journal_lines_account_id ON app.journal_lines (account_id);
CREATE INDEX ix_app_journal_lines_business_id ON app.journal_lines (business_id);
CREATE INDEX ix_app_journal_lines_journal_id ON app.journal_lines (journal_id);

CREATE TABLE app.refresh_tokens (
	token_hash VARCHAR(64) NOT NULL, 
	session_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(session_id) REFERENCES app.sessions (id), 
	FOREIGN KEY(user_id) REFERENCES app.users (id)
)

;
CREATE INDEX ix_app_refresh_tokens_session_id ON app.refresh_tokens (session_id);
CREATE INDEX ix_app_refresh_tokens_user_id ON app.refresh_tokens (user_id);
