"""Added loan table again

Revision ID: df4da64c21e7
Revises: dbb5c24a1294
Create Date: 2022-01-27 02:51:52.818532

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'df4da64c21e7'
down_revision = 'dbb5c24a1294'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('loan',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('book_id', sa.Integer(), nullable=True),
    sa.Column('phone_num', sa.String(length=14), nullable=False),
    sa.Column('loanee', sa.String(length=32), nullable=False),
    sa.Column('out_timestamp', sa.Date(), nullable=True),
    sa.Column('in_timestamp', sa.Date(), nullable=False),
    sa.Column('returned', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['book.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('loan', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_loan_in_timestamp'), ['in_timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_loan_out_timestamp'), ['out_timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_loan_phone_num'), ['phone_num'], unique=False)
        batch_op.create_index(batch_op.f('ix_loan_returned'), ['returned'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('loan', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_loan_returned'))
        batch_op.drop_index(batch_op.f('ix_loan_phone_num'))
        batch_op.drop_index(batch_op.f('ix_loan_out_timestamp'))
        batch_op.drop_index(batch_op.f('ix_loan_in_timestamp'))

    op.drop_table('loan')
    # ### end Alembic commands ###
