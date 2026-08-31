from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from io import TextIOWrapper, BytesIO
from zipfile import BadZipFile, ZipFile


class CensusParseError(ValueError):
    """Raised when a Census geographic reference artifact is malformed."""


@dataclass(frozen=True)
class CountyReference:
    geoid: str
    state_abbreviation: str
    county_name: str
    latitude: float
    longitude: float


def normalize_county_name(value: str) -> str:
    normalized = value.upper().replace("SAINT", "ST")
    normalized = re.sub(
        r"\b(CITY AND BOROUGH|CENSUS AREA|MUNICIPALITY|BOROUGH|PARISH|COUNTY|CITY)\b",
        "",
        normalized,
    )
    normalized = re.sub(r"[^A-Z0-9]", "", normalized)
    # Deterministic aliases for spelling errors in the current TGP export.
    # Keep these narrow: fuzzy matching would make the coordinate provenance
    # much harder to audit.
    return {
        "WORCHESTER": "WORCESTER",
        "VERMILLION": "VERMILION",
    }.get(normalized, normalized)


def parse_county_gazetteer(body: bytes) -> tuple[CountyReference, ...]:
    try:
        archive = ZipFile(BytesIO(body))
        names = [name for name in archive.namelist() if name.lower().endswith(".txt")]
        if len(names) != 1:
            raise CensusParseError("county gazetteer ZIP must contain one text file")
        with archive.open(names[0]) as raw_file:
            text = TextIOWrapper(raw_file, encoding="utf-8").read()
            header = text.splitlines()[0] if text else ""
            delimiter = "|" if "|" in header else "\t"
            reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
            rows = list(reader)
    except (BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise CensusParseError("invalid Census county gazetteer ZIP") from exc
    required = {"USPS", "GEOID", "NAME", "INTPTLAT", "INTPTLONG"}
    if not rows or reader.fieldnames is None:
        raise CensusParseError("county gazetteer contains no records")
    cleaned_headers = {header.strip().lstrip("\ufeff") for header in reader.fieldnames}
    if not required.issubset(cleaned_headers):
        raise CensusParseError("county gazetteer is missing required columns")

    output: list[CountyReference] = []
    for row in rows:
        normalized_row = {
            key.strip().lstrip("\ufeff"): value.strip()
            for key, value in row.items()
        }
        output.append(
            CountyReference(
                geoid=normalized_row["GEOID"],
                state_abbreviation=normalized_row["USPS"],
                county_name=normalized_row["NAME"],
                latitude=float(normalized_row["INTPTLAT"]),
                longitude=float(normalized_row["INTPTLONG"]),
            )
        )
    return tuple(output)
