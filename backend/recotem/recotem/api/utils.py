import gzip
from collections.abc import Callable
from pathlib import Path
from typing import IO, Union

import pandas as pd
from rest_framework.exceptions import ValidationError

INPUT_STREAM_TYPE = Union[IO, gzip.GzipFile]

PREVIEW_ROW_LIMIT = 200

READ_RULES: dict[str, Callable[[INPUT_STREAM_TYPE], pd.DataFrame]] = {
    ".csv": lambda f: pd.read_csv(f),
    ".tsv": lambda f: pd.read_csv(f, sep="\t"),
    ".json": lambda f: pd.read_json(f),
    ".ndjson": lambda f: pd.read_json(f, lines=True, orient="records"),
    ".jsonl": lambda f: pd.read_json(f, lines=True, orient="records"),
    ".pkl": lambda f: pd.read_pickle(f),  # noqa: S301
    ".pickle": lambda f: pd.read_pickle(f),  # noqa: S301
}

# Formats that support nrows parameter for partial reads
_NROWS_FORMATS: dict[str, Callable[[INPUT_STREAM_TYPE, int], pd.DataFrame]] = {
    ".csv": lambda f, n: pd.read_csv(f, nrows=n),
    ".tsv": lambda f, n: pd.read_csv(f, sep="\t", nrows=n),
    ".ndjson": lambda f, n: pd.read_json(f, lines=True, orient="records", nrows=n),
    ".jsonl": lambda f, n: pd.read_json(f, lines=True, orient="records", nrows=n),
}


def read_dataframe(
    filepath: Path, file: IO, *, nrows: int | None = None
) -> pd.DataFrame:
    input_stream: INPUT_STREAM_TYPE = file
    suffixes = filepath.suffixes
    suffix: str
    if len(suffixes) > 2:
        raise ValidationError(f"Suffix {filepath.suffix} not supported.")
    elif len(suffixes) == 2:
        suffix = suffixes[0]
        compression = suffixes[1]
        if compression not in (".gz", ".gzip"):
            raise ValidationError("Only .gzip or .gz compression are supported.")
        else:
            input_stream = gzip.open(file, mode="rb")
    elif len(suffixes) == 1:
        suffix = suffixes[0]
    else:
        raise ValidationError("Suffix like .csv or .json.gzip or pickle.gz required.")
    df: pd.DataFrame

    # Use nrows-aware reader when available for memory efficiency
    if nrows is not None and suffix in _NROWS_FORMATS:
        try:
            df = _NROWS_FORMATS[suffix](input_stream, nrows)
        except (TypeError, ValueError, pd.errors.ParserError) as e:
            raise ValidationError(
                f"Failed to parse {filepath.name} as {suffix} file: {e}"
            )
    elif suffix in READ_RULES:
        try:
            df = READ_RULES[suffix](input_stream)
        except (TypeError, ValueError, pd.errors.ParserError) as e:
            raise ValidationError(
                f"Failed to parse {filepath.name} as {suffix} file: {e}"
            )
    else:
        raise ValidationError(
            f"{suffix} file not supported. Supported file formats are {'/'.join(list(READ_RULES.keys()))} with gzip compression (.gz/.gzip)."
        )
    return df
