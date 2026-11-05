"""Stored files: one row per file, the bytes live behind the storage interface

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table document (
            id serial primary key,
            doc_type text not null,
            filename text not null,
            content_type text not null,
            size bigint not null,
            sha256 text not null,
            storage_key text not null unique,
            linked_type text,
            linked_id text,
            uploaded_by int,
            created_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index ix_document_link on document (linked_type, linked_id)")
    op.execute("create index ix_document_hash on document (sha256)")


def downgrade() -> None:
    op.execute("drop table document")
