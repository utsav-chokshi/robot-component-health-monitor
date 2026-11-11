"""Expected recording catalog and immutable receipt projections."""
from alembic import op

revision = '0002_ingestion'
down_revision = '0001_foundation'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE recordings (
        recording_id uuid PRIMARY KEY,
        source_key text NOT NULL UNIQUE,
        source_time timestamp without time zone NOT NULL,
        ordinal integer NOT NULL UNIQUE CHECK (ordinal > 0),
        expected_sha256 text NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
        accepted_receipt_id uuid UNIQUE
    )''')
    op.execute('''CREATE TABLE recording_receipts (
        receipt_id uuid PRIMARY KEY,
        recording_id uuid NOT NULL REFERENCES recordings(recording_id),
        admission_status text NOT NULL CHECK (admission_status IN ('ACCEPTED','QUARANTINED','CONFLICT','REJECTED')),
        evidence jsonb NOT NULL
    )''')
    op.execute('ALTER TABLE recordings ADD CONSTRAINT accepted_receipt_fk FOREIGN KEY (accepted_receipt_id) REFERENCES recording_receipts(receipt_id)')


def downgrade():
    op.execute('ALTER TABLE recordings DROP CONSTRAINT accepted_receipt_fk')
    op.drop_table('recording_receipts')
    op.drop_table('recordings')
