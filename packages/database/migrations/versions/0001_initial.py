"""Initial schema.

Revision ID: 0001
Revises: None

ponytail: creates all tables from the current model metadata instead of
hand-listing every column. Future schema changes: `alembic revision
--autogenerate` produces normal incremental migrations on top of this.
"""
from alembic import op

from packages.database.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
