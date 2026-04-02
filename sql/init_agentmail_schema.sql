CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS emails (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_msg_id text UNIQUE NOT NULL,
  thread_id text,
  from_addr text NOT NULL,
  to_addr text NOT NULL,
  subject text,
  body_text text,
  body_html text,
  detected_lang varchar(16),
  category varchar(32),
  priority varchar(16),
  status varchar(32) DEFAULT 'received',
  risk_score numeric(5,2) DEFAULT 0,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS replies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email_id uuid REFERENCES emails(id),
  draft_text text NOT NULL,
  final_text text,
  confidence numeric(5,2),
  citations jsonb DEFAULT '[]'::jsonb,
  review_required boolean DEFAULT true,
  reviewer text,
  sent_at timestamptz,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
  id bigserial PRIMARY KEY,
  email_id uuid REFERENCES emails(id),
  event_type varchar(64) NOT NULL,
  payload jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_emails_provider_msg_id ON emails(provider_msg_id);
CREATE INDEX IF NOT EXISTS idx_replies_email_id ON replies(email_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_email_id ON audit_events(email_id);
