import os
import re
from pathlib import Path
from zipfile import ZipFile

if __name__ == "__main__":
    VERSION = re.split(r"\/", os.environ["GITHUB_REF"])[-1]
    WORKDIR = Path(__file__).resolve().parent
    dc_yml_content = (
        (WORKDIR / "docker-compose.yml.tpl").open().read().format(version=VERSION)
    )
    with ZipFile("hoge.zip", "w") as ofs:
        ofs.writestr("recotem/docker-compose.yml", dc_yml_content)
        ofs.writestr("recotem/nginx.conf", (WORKDIR / "nginx.conf").read_text())
        ofs.writestr(
            "recotem/production.env", (WORKDIR / "envs" / "production.env").read_text()
        )
