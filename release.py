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

    nginx_conf_str = (WORKDIR / "nginx.conf").read_text()
    production_env_str = (WORKDIR / "envs" / "production.env").read_text()

    bat_file_content = r"""
@echo off
cd /d %~dp0
@echo off
WHERE docker-compose.exe
@echo off
IF %ERRORLEVEL% NEQ 0 (
    echo msgbox "Could not find docker-compose.exe. Please install it from docker official page.",vbCritical,"Recotem Error" > %TEMP%/msgboxtest.vbs & %TEMP%/msgboxtest.vbs
    EXIT /B
)

docker-compose.exe up
"""
    with ZipFile("recotem-compose.zip", mode="w") as zf:
        with zf.open("recotem-compose/docker-compose.yml", "w") as dcy_ofs:
            dcy_ofs.write(dc_yml_content.encode())
        with zf.open("recotem-compose/nginx.conf", "w") as nc_ofs:
            nc_ofs.write(nginx_conf_str.encode())
        with zf.open("recotem-compose/production.env", "w") as pe_ofs:
            pe_ofs.write(production_env_str.encode())
        with zf.open("recotem-compose/recotem-compose.bat", "w") as rc_ofs:
            rc_ofs.write(bat_file_content.replace("\n", "\r\n").encode())
