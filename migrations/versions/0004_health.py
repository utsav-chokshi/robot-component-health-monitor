"""Register immutable development-health bundles without evaluating the final split."""
from alembic import op
revision='0004_health'
down_revision='0003_features'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE TABLE health_builds (
        build_id text PRIMARY KEY CHECK(build_id ~ '^[0-9a-f]{64}$'),
        feature_build_id text NOT NULL REFERENCES feature_builds(build_id),
        manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
        manifest jsonb NOT NULL,
        registered_at_utc timestamptz NOT NULL DEFAULT now()
    )''')


def downgrade(): op.drop_table('health_builds')
