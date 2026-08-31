from __future__ import annotations

import unittest
from pathlib import Path

import duckdb


class SchemaTests(unittest.TestCase):
    def test_schema_initializes_in_memory(self) -> None:
        schema_path = Path(__file__).parents[1] / "sql" / "schema.sql"
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(schema_path.read_text(encoding="utf-8"))
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
        finally:
            connection.close()

        self.assertIn("notice_versions", tables)
        self.assertIn("notice_version_observations", tables)
        self.assertIn("notice_index_observations", tables)
        self.assertIn("notice_index_pages", tables)
        self.assertIn("notice_index_exports", tables)
        self.assertIn("source_artifacts", tables)
        self.assertIn("events", tables)
        self.assertIn("market_observations", tables)
        self.assertIn("alerts", tables)
        self.assertIn("location_exports", tables)
        self.assertIn("location_observations", tables)
        self.assertIn("location_coordinate_observations", tables)
        self.assertIn("map_reference_layers", tables)
        self.assertIn("capacity_exports", tables)
        self.assertIn("capacity_observations", tables)
        self.assertIn("research_memos", tables)


if __name__ == "__main__":
    unittest.main()
