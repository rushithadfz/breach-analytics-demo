"""add page/char locator to extractions

Lets the evidence drill-down cite a page instead of a whole document:
"medical, page 3 of discharge_summary.pdf" rather than "medical,
somewhere in discharge_summary.pdf".

All three columns are nullable on purpose. Unpaginated formats (CSV,
email, HTML) have no page, and a detector may report no position — a
0 default would be a false citation rather than a missing one.

Revision ID: c3a91b4e7d20
Revises: f183d5dec75f
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3a91b4e7d20'
down_revision: Union[str, Sequence[str], None] = 'f183d5dec75f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('extractions', sa.Column('page_number', sa.Integer(), nullable=True))
    op.add_column('extractions', sa.Column('char_start', sa.Integer(), nullable=True))
    op.add_column('extractions', sa.Column('char_end', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('extractions', 'char_end')
    op.drop_column('extractions', 'char_start')
    op.drop_column('extractions', 'page_number')
