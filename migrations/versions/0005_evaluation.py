"""Freeze evaluation plans before registering immutable evaluated bundles."""
from alembic import op
revision='0005_evaluation'
down_revision='0004_health'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE TABLE evaluation_plans (
        plan_id text PRIMARY KEY CHECK(plan_id ~ '^[0-9a-f]{64}$'),
        health_build_id text NOT NULL REFERENCES health_builds(build_id),
        document jsonb NOT NULL,
        frozen_at_utc timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute('''CREATE TABLE evaluation_builds (
        build_id text PRIMARY KEY CHECK(build_id ~ '^[0-9a-f]{64}$'),
        plan_id text NOT NULL REFERENCES evaluation_plans(plan_id),
        manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
        manifest jsonb NOT NULL,
        metrics jsonb NOT NULL,
        registered_at_utc timestamptz NOT NULL DEFAULT now()
    )''')


def downgrade():
    op.drop_table('evaluation_builds');op.drop_table('evaluation_plans')
