"""Register immutable feature-only analytical builds."""
from alembic import op

revision='0003_features'
down_revision='0002_ingestion'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE TABLE feature_builds (
        build_id text PRIMARY KEY CHECK (build_id ~ '^[0-9a-f]{64}$'),
        snapshot_sha256 text NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
        manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
        manifest jsonb NOT NULL,
        registered_at_utc timestamptz NOT NULL DEFAULT now()
    )''')


def downgrade():
    op.drop_table('feature_builds')
