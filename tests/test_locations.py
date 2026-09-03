from __future__ import annotations

import csv
import tempfile
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

import pendulum

from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    finish_fetch_run,
    geocode_locations_to_counties,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_county_references,
    store_location_export,
)
from pipeline_pulse.quality import build_location_quality_report
from pipeline_pulse.sources.census import (
    normalize_county_name,
    parse_county_gazetteer,
)
from pipeline_pulse.sources.kinder_morgan_locations import (
    EXPECTED_LOCATION_SCHEMA_SHA256,
    parse_kinder_morgan_location_export,
    parse_tgp_location_export,
)
from pipeline_pulse.web import TgpReadModel

HEADERS = [
    "TSP",
    "TSP Name",
    "TSP FERC CID",
    "Date/Time",
    "Comments",
    "Loc",
    "Loc Name",
    "Dir Flo",
    "Loc Cnty",
    "Loc St Abbrev",
    "Loc Type Ind",
    "Loc Zone (Rec)",
    "Loc Zone (Del)",
    "Seg Nbr",
    "Nom Ind",
    "Loc Stat Ind",
    "Eff Date",
    "Inact Date",
    "Up/Dn Ind",
    "Up/Dn Name",
    "Up/Dn ID",
    "Up/Dn ID Prop",
    "Up/Dn FERC CID Ind",
    "Up/Dn FERC CID",
    "Up/Dn Loc",
    "Up/Dn Loc Name",
    "Up/Dn Loc 2",
    "Up/Dn Loc Name2",
    "Update D/T",
]


def location_csv(
    *,
    tsp_number: str = "1939164",
    tsp_name: str = "TENNESSEE GAS PIPELINE",
    ferc_cid: str = "C000020",
) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerow(
        [
            tsp_number,
            tsp_name,
            ferc_cid,
            "20260828 11:16",
            "Public location reference",
            "44423",
            "REX/TGP BIG MUSKIE GUERNSEY",
            "R",
            "GUERNSEY",
            "OH",
            "INT",
            "Z4",
            "Z4",
            "204",
            "Y",
            "A",
            "20151101",
            "",
            "Y",
            "ROCKIES EXPRESS PIPELINE LLC",
            "784256161",
            "955",
            "Y",
            "C000594",
            "44423",
            "TGP/REX GUERNSEY",
            "",
            "",
            "20160401 10:05",
        ]
    )
    writer.writerow(
        [
            tsp_number,
            tsp_name,
            ferc_cid,
            "20260828 11:16",
            "Public location reference",
            "50000",
            "TEST ST MARY",
            "D",
            "ST MARY",
            "LA",
            "LDC",
            "Z1",
            "Z1",
            "500",
            "Y",
            "A",
            "20200101",
            "",
            "N",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "20200102 09:00",
        ]
    )
    return output.getvalue().encode("utf-8")


def county_zip() -> bytes:
    text = (
        "USPS|GEOID|ANSICODE|NAME|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG\n"
        "OH|39059|01074050|Guernsey County|1|0|1|0|+40.0566658|-081.4978756\n"
        "LA|22101|00559547|St. Mary Parish|1|0|1|0|+29.6293493|-091.4638043\n"
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("2025_Gaz_counties_national.txt", text)
    return output.getvalue()


def artifact(root: Path, artifact_id: str, body: bytes) -> StoredArtifact:
    raw_path = root / f"{artifact_id}.bin"
    raw_path.write_bytes(body)
    observed_at = pendulum.datetime(2026, 8, 28, 17, 0, tz="UTC")
    return StoredArtifact(
        artifact_id=artifact_id,
        source_code=artifact_id,
        canonical_url=f"https://example.test/{artifact_id}",
        content_sha256=artifact_id.ljust(64, "a")[:64],
        mime_type="application/octet-stream",
        http_status=200,
        requested_at=observed_at.subtract(seconds=1),
        received_at=observed_at,
        recorded_at=observed_at,
        raw_path=raw_path.as_posix(),
        size_bytes=len(body),
        etag=None,
        last_modified=None,
        content_disposition=None,
    )


class LocationReferenceTests(unittest.TestCase):
    def test_parses_location_and_county_reference(self) -> None:
        locations = parse_tgp_location_export(location_csv())
        counties = parse_county_gazetteer(county_zip())

        self.assertEqual(len(locations.rows), 2)
        self.assertEqual(locations.rows[0].operator_segment_id, "204")
        self.assertEqual(
            locations.schema_sha256,
            EXPECTED_LOCATION_SCHEMA_SHA256,
        )
        self.assertTrue(locations.rows[0].interconnect_indicator)
        self.assertEqual(len(counties), 2)
        self.assertEqual(normalize_county_name("St. Mary Parish"), "STMARY")
        self.assertEqual(normalize_county_name("WORCHESTER"), "WORCESTER")
        self.assertEqual(normalize_county_name("VERMILLION"), "VERMILION")

    def test_validates_ngpl_identity_on_shared_location_schema(self) -> None:
        export = parse_kinder_morgan_location_export(
            location_csv(
                tsp_number="6931794",
                tsp_name="NATURAL GAS PIPELINE CO.",
                ferc_cid="C002096",
            ),
            expected_tsp_number="6931794",
            expected_ferc_cid="C002096",
            pipeline_label="NGPL",
        )

        self.assertEqual(export.tsp_number, "6931794")
        self.assertEqual(export.tsp_ferc_cid, "C002096")

    def test_preserves_blank_ngpl_state_as_unmapped(self) -> None:
        body = location_csv(
            tsp_number="6931794",
            tsp_name="NATURAL GAS PIPELINE CO.",
            ferc_cid="C002096",
        ).replace(b",LA,LDC,", b",,LDC,")

        export = parse_kinder_morgan_location_export(
            body,
            expected_tsp_number="6931794",
            expected_ferc_cid="C002096",
            pipeline_label="NGPL",
        )

        self.assertEqual(export.rows[1].state_abbreviation, "")

    def test_stores_and_geocodes_locations_at_county_precision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline.duckdb"
            location_artifact = artifact(root, "locations", location_csv())
            county_artifact = artifact(root, "counties", county_zip())
            connection = connect_database(database_path)
            initialize_database(connection)
            run_id = start_fetch_run(
                connection,
                "test_location_bundle",
                requested_at=location_artifact.requested_at,
            )
            store_artifact_record(connection, run_id, location_artifact)
            store_artifact_record(connection, run_id, county_artifact)
            store_location_export(
                connection,
                location_artifact,
                parse_tgp_location_export(location_csv()),
            )
            store_county_references(
                connection,
                county_artifact,
                parse_county_gazetteer(county_zip()),
            )
            matched, unmatched = geocode_locations_to_counties(
                connection,
                location_artifact,
                county_artifact,
            )
            finish_fetch_run(connection, run_id)
            rows = connection.execute(
                """
                SELECT operator_location_id, coordinate_precision, latitude
                FROM tgp_location_map
                ORDER BY operator_location_id
                """
            ).fetchall()
            connection.close()

            map_data = TgpReadModel(database_path).map_data()
            quality = build_location_quality_report(database_path)

            self.assertEqual((matched, unmatched), (2, 0))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], "county")
            self.assertAlmostEqual(rows[0][2], 40.0566658)
            self.assertEqual(map_data["coverage"]["location_count"], 2)
            self.assertEqual(map_data["coverage"]["geocoded_location_count"], 2)
            self.assertEqual(len(map_data["counties"]), 2)
            self.assertEqual(quality.status, "passed")
            self.assertTrue(quality.schema_matches_expected)
            self.assertEqual(quality.geocoded_location_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
