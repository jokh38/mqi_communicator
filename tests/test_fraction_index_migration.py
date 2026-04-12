from unittest.mock import MagicMock

from src.database.connection import DatabaseConnection


def test_deliveries_table_has_fraction_index_column(tmp_path):
    db_path = tmp_path / "test.db"
    logger = MagicMock()
    settings = MagicMock()
    settings.get_database_config.return_value = {}
    conn = DatabaseConnection(db_path=db_path, settings=settings, logger=logger)
    conn.init_db()

    with conn.transaction() as raw_conn:
        cursor = raw_conn.execute("PRAGMA table_info(deliveries)")
        columns = [row[1] for row in cursor.fetchall()]

    assert "fraction_index" in columns
