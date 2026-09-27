"""Data-quality primitives and artifact writers used across the simulator."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


PLACEHOLDER_MARKERS = (
    "available with enterprise subscription",
    "enterprise subscription",
    "not available",
    "not licensed",
    "upgrade required",
    "placeholder",
)


class QualityFlag(StrEnum):
    MISSING_WIND = "missing_wind"
    SYNTHETIC_WIND = "synthetic_wind"
    SENSOR_DROPOUT = "sensor_dropout"
    SENSOR_SATURATION = "sensor_saturation"
    INTERPOLATION = "interpolation"
    INTERPOLATION_GAP_EXCEEDED = "interpolation_gap_exceeded"
    OUT_OF_DOMAIN_POSITION = "out_of_domain_position"
    INVALID_INPUT_ROW = "invalid_input_row"


_FLAG_PENALTIES: dict[str, float] = {
    QualityFlag.MISSING_WIND: 0.45,
    QualityFlag.SYNTHETIC_WIND: 0.15,
    QualityFlag.SENSOR_DROPOUT: 0.50,
    QualityFlag.SENSOR_SATURATION: 0.25,
    QualityFlag.INTERPOLATION: 0.05,
    QualityFlag.INTERPOLATION_GAP_EXCEEDED: 0.35,
    QualityFlag.OUT_OF_DOMAIN_POSITION: 0.50,
    QualityFlag.INVALID_INPUT_ROW: 0.50,
}


def _as_flag_strings(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (str, StrEnum)):
        candidates = str(value).replace(",", ";").split(";")
    elif isinstance(value, Iterable):
        candidates = [str(item) for item in value]
    else:
        candidates = [str(value)]
    return [item.strip() for item in candidates if item and item.strip()]


def combine_quality_flags(*values: object) -> str:
    """Return stable, de-duplicated semicolon-separated quality flags."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for flag in _as_flag_strings(value):
            if flag not in seen:
                result.append(flag)
                seen.add(flag)
    return ";".join(result)


def quality_score(flags: object) -> float:
    """Map flags to a transparent 0-1 score (1 means no known issue)."""

    penalty = sum(_FLAG_PENALTIES.get(flag, 0.1) for flag in _as_flag_strings(flags))
    return float(max(0.0, 1.0 - penalty))


def quality_scores(flags: pd.Series) -> pd.Series:
    return flags.map(quality_score).astype(float)


def numeric_column_quality(series: pd.Series) -> dict[str, Any]:
    """Describe numeric coverage while keeping placeholders distinguishable.

    Coverage uses all input rows as its denominator. Blank values count as
    missing; non-blank strings that cannot be parsed count as invalid text.
    """

    text = series.astype("string").str.strip()
    blank = series.isna() | text.eq("")
    casefolded = text.str.casefold().fillna("")
    placeholder = casefolded.map(
        lambda value: any(marker in value for marker in PLACEHOLDER_MARKERS)
    ) & ~blank
    numeric = pd.to_numeric(series.mask(placeholder), errors="coerce")
    invalid_text = ~blank & ~placeholder & numeric.isna()
    nonnumeric_examples = (
        text[placeholder | invalid_text].value_counts().head(5).index.astype(str).tolist()
    )
    rows = int(len(series))
    numeric_count = int(numeric.notna().sum())
    nonblank_count = int((~blank).sum())
    return {
        "row_count": rows,
        "numeric_count": numeric_count,
        "numeric_coverage": float(numeric_count / rows) if rows else 0.0,
        "numeric_coverage_of_nonblank": (
            float(numeric_count / nonblank_count) if nonblank_count else 0.0
        ),
        "missing_count": int(blank.sum()),
        "missing_percent": float(blank.mean() * 100.0) if rows else 0.0,
        "placeholder_count": int(placeholder.sum()),
        "invalid_text_count": int(invalid_text.sum()),
        "nonnumeric_examples": nonnumeric_examples,
    }


def dataframe_quality_summary(frame: pd.DataFrame) -> dict[str, Any]:
    """Create a compact per-column schema and completeness summary."""

    return {
        str(name): {
            "dtype": str(frame[name].dtype),
            "missing_count": int(frame[name].isna().sum()),
            "missing_percent": float(frame[name].isna().mean() * 100.0),
            "unique_count": int(frame[name].nunique(dropna=True)),
        }
        for name in frame.columns
    }


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def write_json(payload: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return target


def write_data_quality_artifacts(
    report: Mapping[str, Any],
    mapping: Mapping[str, str | None],
    normalized_flight: pd.DataFrame,
    output_dir: str | Path,
) -> dict[str, Path]:
    """Write the three validation artifacts required by the project brief."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_path = write_json(report, destination / "data_quality_report.json")
    mapping_path = write_json(mapping, destination / "column_mapping.json")
    normalized_path = destination / "normalized_flight.csv"
    normalized_flight.to_csv(normalized_path, index=False)
    return {
        "data_quality_report": report_path,
        "column_mapping": mapping_path,
        "normalized_flight": normalized_path,
    }


__all__ = [
    "PLACEHOLDER_MARKERS",
    "QualityFlag",
    "combine_quality_flags",
    "dataframe_quality_summary",
    "numeric_column_quality",
    "quality_score",
    "quality_scores",
    "write_data_quality_artifacts",
    "write_json",
]
