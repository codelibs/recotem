from typing import IO, Callable, Dict
from pathlib import Path
import pandas as pd
import gzip

from rest_framework.exceptions import ValidationError

READ_RULES: Dict[str, Callable[[IO], pd.DataFrame]] = {
    ".csv": lambda f: pd.read_csv(f),
    ".tsv": lambda f: pd.read_csv(f, sep="\t"),
    ".json": lambda f: pd.read_json(f),
    ".ndjson": lambda f: pd.read_json(f, lines=True, orient="records"),
    ".jsonl": lambda f: pd.read_json(f, lines=True, orient="records"),
    ".pkl": lambda f: pd.read_pickle(f),
    ".pickle": lambda f: pd.read_pickle(f),
}


def read_dataframe(filepath: Path, file: IO) -> pd.DataFrame:
    suffixes = filepath.suffixes
    suffix: str
    if len(suffixes) > 2:
        raise ValidationError(f"Suffix {filepath.suffix} not supported.")
    elif len(suffixes) == 2:
        suffix = suffixes[0]
        compression = suffixes[1]
        if compression not in (".gz", ".gzip"):
            raise ValidationError(f"Only .gzip or .gz compression are supported.")
        else:
            file = gzip.open(file, mode="rb")
    elif len(suffixes) == 1:
        suffix = suffixes[0]
    else:
        raise ValidationError("Suffix like .csv or .json.gzip or pickle.gz required.")
    df: pd.DataFrame

    if suffix in READ_RULES:
        try:
            df = READ_RULES[suffix](file)
        except Exception:
            raise ValidationError(f"Failed to parse {filepath.name} as {suffix} file.")
    else:
        raise ValidationError(
            f"{suffix} file not supported. Supported file formats are {'/'.join(list(READ_RULES.keys()))} with gzip compression (.gz/.gzip)."
        )
    return df
