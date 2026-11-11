"""Create the explicit foundation contract marker."""
from alembic import op
import sqlalchemy as sa

revision='0001_foundation'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('platform_metadata',sa.Column('singleton_id',sa.SmallInteger(),primary_key=True),sa.Column('contract_schema_version',sa.String(32),nullable=False),sa.Column('created_at_utc',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.CheckConstraint('singleton_id=1',name='ck_platform_metadata_singleton'))
    op.execute("INSERT INTO platform_metadata(singleton_id,contract_schema_version) VALUES(1,'1.0.0')")


def downgrade():op.drop_table('platform_metadata')
