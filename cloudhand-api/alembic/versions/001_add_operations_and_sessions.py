"""Add operations and agent sessions tables

This migration adds:
- operations table for tracking setup/update/maintenance runs
- agent_sessions table for tracking agent conversations
- agent_messages table for session message history
- Updates to applications table to support the new model
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

# revision identifiers, used by Alembic.
revision = '001_add_operations_and_sessions'
down_revision = '908f09b25895'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to applications table
    op.add_column('applications', sa.Column('environments', JSON, nullable=True))
    op.add_column('applications', sa.Column('current_state', JSON, nullable=True))
    op.add_column('applications', sa.Column('agent_memory_summary_id', UUID(as_uuid=True), nullable=True))
    
    # Create operations table
    op.create_table(
        'operations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('application_id', UUID(as_uuid=True), sa.ForeignKey('applications.id'), nullable=True),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('trigger', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('phases', JSON, nullable=True),
        sa.Column('sandbox_id', sa.String(), nullable=True),
        sa.Column('changeset', JSON, nullable=True),
        sa.Column('session_id', UUID(as_uuid=True), nullable=True),
    )
    
    # Create agent_sessions table
    op.create_table(
        'agent_sessions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('application_id', UUID(as_uuid=True), sa.ForeignKey('applications.id'), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default='active', nullable=False),
        sa.Column('last_activity', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('primary_run_id', UUID(as_uuid=True), nullable=True),
        sa.Column('created_from_session_id', UUID(as_uuid=True), sa.ForeignKey('agent_sessions.id'), nullable=True),
    )
    
    # Create agent_messages table
    op.create_table(
        'agent_messages',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', UUID(as_uuid=True), sa.ForeignKey('agent_sessions.id'), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('type', sa.String(), server_default='text', nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('metadata', JSON, nullable=True),
    )
    
    # Add foreign key from operations to agent_sessions (circular reference handled with nullable)
    op.create_foreign_key(
        'fk_operations_session_id',
        'operations', 'agent_sessions',
        ['session_id'], ['id']
    )
    
    # Add foreign key from agent_sessions to operations for primary_run_id
    op.create_foreign_key(
        'fk_agent_sessions_primary_run_id',
        'agent_sessions', 'operations',
        ['primary_run_id'], ['id']
    )


def downgrade():
    # Drop foreign keys first
    op.drop_constraint('fk_agent_sessions_primary_run_id', 'agent_sessions', type_='foreignkey')
    op.drop_constraint('fk_operations_session_id', 'operations', type_='foreignkey')
    
    # Drop tables
    op.drop_table('agent_messages')
    op.drop_table('agent_sessions')
    op.drop_table('operations')
    
    # Remove columns from applications
    op.drop_column('applications', 'agent_memory_summary_id')
    op.drop_column('applications', 'current_state')
    op.drop_column('applications', 'environments')
