import os
import re
from pathlib import Path
from zipfile import ZipFile

import yaml

if __name__ == "__main__":
    VERSION = re.split(r"\/", os.environ["GITHUB_REF"])[-1]
    WORKDIR = Path(__file__).resolve().parent
    dc_content = yaml.load((WORKDIR / "compose.yaml").open(), Loader=yaml.SafeLoader)

    # Replace build directives with pre-built images
    dc_content["services"]["backend"].pop("build")
    dc_content["services"]["backend"]["image"] = (
        f"ghcr.io/codelibs/recotem-backend:{VERSION}"
    )

    dc_content["services"]["worker"].pop("build")
    dc_content["services"]["worker"]["image"] = (
        f"ghcr.io/codelibs/recotem-worker:{VERSION}"
    )

    dc_content["services"]["proxy"].pop("build")
    dc_content["services"]["proxy"]["image"] = (
        f"ghcr.io/codelibs/recotem-proxy:{VERSION}"
    )

    nginx_conf_str = (WORKDIR / "nginx.conf").read_text()
    production_env_str = (WORKDIR / "envs" / "production.env").read_text()

    bat_file_content = r"""
@echo off
cd /d %~dp0
@echo off
WHERE docker.exe
@echo off
IF %ERRORLEVEL% NEQ 0 (
    echo msgbox "Could not find docker.exe. Please install it from docker official page.",vbCritical,"Recotem Error" > %TEMP%/msgboxtest.vbs & %TEMP%/msgboxtest.vbs
    EXIT /B
)

docker.exe compose up
"""
    with ZipFile(f"recotem-compose-{VERSION}.zip", mode="w") as zf:
        with zf.open("recotem-compose/compose.yaml", "w") as dcy_ofs:
            dcy_ofs.write(yaml.dump(dc_content).encode())
        with zf.open("recotem-compose/nginx.conf", "w") as nc_ofs:
            nc_ofs.write(nginx_conf_str.encode())
        with zf.open("recotem-compose/envs/production.env", "w") as pe_ofs:
            pe_ofs.write(production_env_str.encode())
        with zf.open("recotem-compose/recotem-compose.bat", "w") as rc_ofs:
            rc_ofs.write(bat_file_content.replace("\n", "\r\n").encode())
