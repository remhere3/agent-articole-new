"""drop unique constraint on search_results(topic_id, url)

Revision ID: drop_unique_results
Revises: add_improvements
Create Date: 2026-05-14
"""
from alembic import op

revision = "drop_unique_results"
down_revision = "add_improvements"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.drop_constraint("uq_result_topic_url", type_="unique")


def downgrade():
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.create_unique_constraint("uq_result_topic_url", ["topic_id", "url"])
