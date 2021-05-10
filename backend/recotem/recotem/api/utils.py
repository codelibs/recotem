from typing import IO
from pathlib import Path
import pandas as pd
import gzip
from pickle import UnpicklingError
from pandas.errors import ParserError
from rest_framework.exceptions import ValidationError


def read_dataframe(filepath: Path, file: IO) -> pd.DataFrame:
    suffixes = filepath.suffixes
    if len(suffixes) > 2:
        raise ValidationError(f"Suffix {filepath.suffix} not supported.")
    elif len(suffixes) == 2:
        suffix = suffixes[0]
        compression = suffixes[1]
        if compression not in (".gz", "gzip"):
            raise ValidationError(f"Only .gzip or .gz compression are supported")
        else:
            file = gzip.open(file)
    elif len(suffixes) == 1:
        suffix = suffixes[0]
    else:
        raise ValidationError("Suffix like .csv or .json.gzip or pickle.gz required.")
    df: pd.DataFrame
    if suffix == ".csv":
        try:
            df = pd.read_csv(file)
        except ParserError:
            raise ValidationError(f"Failed to parse {filepath.name} as CSV.")
    elif suffix == ".json":
        try:
            df = pd.read_json(file)
        except ParserError:
            raise ValidationError(f"Failed to parse {filepath.name} as json.")
    elif suffix in (".pkl", ".pickle"):
        try:
            df = pd.read_pickle(filepath)
        except UnpicklingError:
            raise ValidationError(f"Failed to read {filepath.name} as a pickle file.")
    else:
        raise ValidationError(
            f"Supported file formats are .csv/.json/.pkl/.pickle/.csv.gz/.json.gz/.pkl.gz/.pickle.gz"
        )
    return df
