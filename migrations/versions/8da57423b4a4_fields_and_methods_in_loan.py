"""fields and methods in loan

Revision ID: 8da57423b4a4
Revises: 8bad1256e64d
Create Date: 2022-01-26 01:08:30.520103

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8da57423b4a4'
down_revision = '8bad1256e64d'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('out_timestamp', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('in_timestamp', sa.Date(), nullable=False))
        batch_op.add_column(sa.Column('returned', sa.Boolean(), nullable=False))
        batch_op.drop_index('ix_loan_timestamp')
        batch_op.create_index(batch_op.f('ix_loan_in_timestamp'), ['in_timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_loan_out_timestamp'), ['out_timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_loan_returned'), ['returned'], unique=False)
        batch_op.drop_column('timestamp')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timestamp', sa.DATETIME(), nullable=True))
        batch_op.drop_index(batch_op.f('ix_loan_returned'))
        batch_op.drop_index(batch_op.f('ix_loan_out_timestamp'))
        batch_op.drop_index(batch_op.f('ix_loan_in_timestamp'))
        batch_op.create_index('ix_loan_timestamp', ['timestamp'], unique=False)
        batch_op.drop_column('returned')
        batch_op.drop_column('in_timestamp')
        batch_op.drop_column('out_timestamp')

    # ### end Alembic commands ###
