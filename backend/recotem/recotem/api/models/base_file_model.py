import re
from pathlib import PurePath
from typing import Optional

from django.db import models
from django.utils.crypto import get_random_string
from rest_framework.exceptions import ValidationError

remove_rand = re.compile("_.{7}$")


def upload_to(instance, filename: str):
    filename_as_path = PurePath(filename)
    suffixes = filename_as_path.suffixes
    while filename_as_path.suffix:
        filename_as_path = filename_as_path.with_suffix("")
    random_string = get_random_string(length=7)
    save_directory_name = re.sub(
        r"([A-Z]+)", r"_\1", instance.__class__.__name__
    ).lower()
    res = f"{save_directory_name}/{filename_as_path.name}_{random_string}{''.join(suffixes)}"
    return res


class BaseFileModel(models.Model):
    file = models.FileField(upload_to=upload_to, null=True)
    filesize = models.BigIntegerField(null=True)

    class Meta:
        abstract = True

    def delete_file(self) -> None:
        self.file.delete()
        self.filesize = None
        self.save()

    def basename(self) -> Optional[str]:
        if self.file is None or self.file.name is None:
            return None
        path = PurePath(self.file.name)
        suffixes = path.suffixes
        while path.suffixes:
            path = path.with_suffix("")
        return remove_rand.sub("", path.name) + ("".join(suffixes))
