"""add user_id to agent_sessions

Revision ID: 416d0f57cd0c
Revises: 908f09b25895
Create Date: 2025-11-25 12:14:42.502672

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '416d0f57cd0c'
down_revision: Union[str, Sequence[str], None] = '001_add_operations_and_sessions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('agent_sessions', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_agent_sessions_user_id', 'agent_sessions', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_agent_sessions_user_id', 'agent_sessions', type_='foreignkey')
    op.drop_column('agent_sessions', 'user_id')
