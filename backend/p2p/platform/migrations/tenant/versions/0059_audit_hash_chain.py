"""Audit protection (D-25): a hash chain on the audit log and the setup history, the setup history insert only, and what
was masked before each model call

Revision ID: 0059
Revises: 0058
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

CHAINED = ("audit_event", "setup_change")
GENESIS = "repeat('0', 64)"


def upgrade() -> None:
    # The hash of a row: its previous row's hash followed by the row as JSON (without the two hash columns). Written in
    # UTC so the text of a timestamp does not depend on the session's time zone; jsonb text has one form for one value.
    op.execute(
        """
        create function chain_row_hash(prev text, rec anyelement) returns text language sql stable
        set timezone = 'UTC' as $$
            select encode(sha256(convert_to(prev || ((to_jsonb(rec) - 'prev_hash' - 'row_hash')::text), 'UTF8')), 'hex')
        $$
        """
    )
    # Each insert links to the last row. The lock is held to the end of the writer's transaction, so the next writer
    # waits and then sees this row committed: two transactions can never link to the same row (no fork). The id is
    # taken again after the lock, so the chain's order is the id order.
    op.execute(
        """
        create function chain_link() returns trigger language plpgsql set timezone = 'UTC' as $$
        declare
            last text;
        begin
            perform pg_advisory_xact_lock(hashtext('chain:' || tg_table_name));
            new.id := nextval(pg_get_serial_sequence(quote_ident(tg_table_name), 'id'));
            execute format('select row_hash from %I order by id desc limit 1', tg_table_name) into last;
            new.prev_hash := coalesce(last, repeat('0', 64));
            new.row_hash := chain_row_hash(new.prev_hash, new);
            return new;
        end;
        $$
        """
    )
    # The setup history is insert only, like the audit log (migration 0004).
    op.execute(
        """
        create function history_is_append_only() returns trigger language plpgsql as $$
        begin
            raise exception '% is insert only (% refused)', tg_table_name, tg_op;
        end;
        $$
        """
    )
    op.execute(
        "create trigger setup_change_no_change before update or delete on setup_change "
        "for each row execute function history_is_append_only()"
    )
    op.execute(
        "create trigger setup_change_no_truncate before truncate on setup_change "
        "for each statement execute function history_is_append_only()"
    )
    for table in CHAINED:
        op.execute(f"alter table {table} add column prev_hash text, add column row_hash text")
        # Existing rows are linked once, in id order; the insert-only trigger is off only for this.
        op.execute(f"alter table {table} disable trigger {table}_no_change")
        op.execute(
            f"""
            do $$
            declare
                r {table};
                last text := {GENESIS};
            begin
                for r in select * from {table} order by id loop
                    update {table} set prev_hash = last, row_hash = chain_row_hash(last, r) where id = r.id;
                    last := chain_row_hash(last, r);
                end loop;
            end;
            $$
            """  # noqa: S608 - the table is one of CHAINED
        )
        op.execute(f"alter table {table} enable trigger {table}_no_change")
        op.execute(f"alter table {table} alter column prev_hash set not null, alter column row_hash set not null")
        op.execute(f"create trigger {table}_chain before insert on {table} for each row execute function chain_link()")
    # What the gateway masked before a model call, as counts per kind (never the values).
    op.execute("alter table model_call add column masked jsonb")


def downgrade() -> None:
    op.execute("alter table model_call drop column masked")
    for table in CHAINED:
        op.execute(f"drop trigger {table}_chain on {table}")
        op.execute(f"alter table {table} drop column prev_hash, drop column row_hash")
    op.execute("drop trigger setup_change_no_truncate on setup_change")
    op.execute("drop trigger setup_change_no_change on setup_change")
    op.execute("drop function history_is_append_only()")
    op.execute("drop function chain_link()")
    op.execute("drop function chain_row_hash(text, anyelement)")
