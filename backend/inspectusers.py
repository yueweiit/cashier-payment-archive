"""Read-only inspection helper for DingTalk user snapshot mappings."""

from backend.app.external_expenses import source_connection, source_database_config


USER_IDS = [
    "17652401837108647",
    "0120062629272056577751",
    "181465000138010390",
]


def main() -> None:
    config = source_database_config()
    with source_connection(config.user_dbname) as conn:
        columns = conn.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ding_user_snapshot'
            ORDER BY ordinal_position
            """
        ).fetchall()
        print("COLUMNS")
        for row in columns:
            print(dict(row))
        rows = conn.execute(
            """
            SELECT user_id, name, is_current, valid_from, valid_to, updated_at, id
            FROM public.ding_user_snapshot
            WHERE BTRIM(user_id) = ANY(%s)
            ORDER BY user_id,
                     is_current DESC NULLS LAST,
                     valid_from DESC NULLS LAST,
                     updated_at DESC NULLS LAST,
                     id DESC
            """,
            [USER_IDS],
        ).fetchall()
        print("ROWS")
        for row in rows:
            print(dict(row))


if __name__ == "__main__":
    main()
