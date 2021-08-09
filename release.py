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
    with ZipFile("recotem-compose.zip", "w") as ofs:
        ofs.writestr("recotem/docker-compose.yml", dc_yml_content)
        ofs.writestr("recotem/nginx.conf", nginx_conf_str)
        ofs.writestr("recotem/production.env", production_env_str)

    def str_to_echo(target: str) -> str:
        result = ""
        first = True
        for l in target.splitlines():
            if re.match(r"^\s*$", l):
                continue
            if not first:
                result += "\n"
            first = False
            result += "    echo " + re.sub(r"([\"])", r"^\1", l)
        return result

    bat_file_content = f"""
@echo off
cd /d %~dp0
@echo off
WHERE docker-compose.exe
@echo off
IF %ERRORLEVEL% NEQ 0 (
    echo msgbox "Could not find docker-compose.exe. Please install it from docker official page.",vbCritical,"Recotem Error" > %TEMP%/msgboxtest.vbs & %TEMP%/msgboxtest.vbs
    EXIT /B
)
MD %TEMP%\\recotem
cd %TEMP%\\recotem

(
{str_to_echo(dc_yml_content)}
) >docker-compose.yml

(
{str_to_echo(nginx_conf_str)}
) >nginx.conf

(
{str_to_echo(production_env_str)}
) >production.env

docker-compose.exe up
"""
    with open("recotem-compose.bat", "w", newline="\r\n") as ofs:
        ofs.write(bat_file_content)
