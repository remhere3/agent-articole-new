"""add_timeout_cost_unique_constraint

Revision ID: add_improvements
Revises: baseline
Create Date: 2026-05-14 12:00:08.495126

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_improvements'
down_revision: Union[str, Sequence[str], None] = 'baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adauga timeout_seconds la topics
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(sa.Column("timeout_seconds", sa.Integer(), nullable=True))

    op.execute("UPDATE topics SET timeout_seconds = 300 WHERE timeout_seconds IS NULL")

    # Adauga estimated_cost_usd la search_runs
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.add_column(sa.Column("estimated_cost_usd", sa.Float(), nullable=True))

    # Sterge duplicate (topic_id, url) pastrand randul cu id-ul cel mai mare
    op.execute("""
        DELETE FROM search_results
        WHERE id NOT IN (
            SELECT MAX(id) FROM search_results GROUP BY topic_id, url
        )
    """)

    # Adauga UNIQUE constraint pe (topic_id, url)
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.create_unique_constraint("uq_result_topic_url", ["topic_id", "url"])


def downgrade() -> None:
    with op.batch_alter_table("search_results") as batch_op:
        batch_op.drop_constraint("uq_result_topic_url", type_="unique")

    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.drop_column("estimated_cost_usd")

    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_column("timeout_seconds")
