"""Explicit settlement fees, and vendor-scoped invoice numbering."""

from alembic import op
from sqlalchemy import text

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(text("""
ALTER TABLE app.payments ADD COLUMN fee_amount numeric(24,6) NOT NULL DEFAULT 0 CHECK(fee_amount>=0);
ALTER TABLE app.payments ADD COLUMN fee_account_id uuid;
ALTER TABLE app.payments ADD FOREIGN KEY(business_id,fee_account_id) REFERENCES app.accounts(business_id,id);
ALTER TABLE app.payments ADD CHECK((fee_amount=0)=(fee_account_id IS NULL));
ALTER TABLE app.payments ADD CHECK(kind NOT IN ('customer_refund','vendor_refund') OR fee_amount=0);
ALTER TABLE app.payments ADD CHECK(kind<>'receipt' OR fee_amount<total);
ALTER TABLE app.trade_documents DROP CONSTRAINT trade_documents_business_id_kind_number_key;
CREATE UNIQUE INDEX vendor_document_number ON app.trade_documents(business_id,party_id,kind,number)
 WHERE kind IN ('bill','vendor_credit');
CREATE UNIQUE INDEX sales_document_number ON app.trade_documents(business_id,kind,number)
 WHERE kind NOT IN ('bill','vendor_credit');
"""))


def downgrade() -> None:
    raise RuntimeError("Preserve approved settlement fees through a forward repair")
