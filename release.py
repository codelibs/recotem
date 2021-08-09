import os
import re
from pathlib import Path

if __name__ == "__main__":
    VERSION = re.split(r"\/", os.environ["GITHUB_REF"])[-1]
    WORKDIR = Path(__file__).resolve().parent
    dc_yml_content = (
        (WORKDIR / "docker-compose.yml.tpl").open().read().format(version=VERSION)
    )

    nginx_conf_str = (WORKDIR / "nginx.conf").read_text()
    production_env_str = (WORKDIR / "envs" / "production.env").read_text()

    def str_to_echo_unix(target: str) -> str:
        result = ""
        first = True
        for l in target.splitlines():
            if not first:
                result += "\n"
            first = False
            result += re.sub(r"([\$])", r"\\\1", l)
        return result

    sh_file_content = rf"""#!/bin/sh
cd /tmp
mkdir -p recotem
cd recotem
cat > docker-compose.yml << EOM
{str_to_echo_unix(dc_yml_content)}
EOM

cat > nginx.conf << EOM
{str_to_echo_unix(nginx_conf_str)}
EOM

cat > production.env << EOM
{str_to_echo_unix(production_env_str)}
EOM

docker-compose up
"""
    with open("recotem-compose.sh", "w", newline="\n") as ofs:
        ofs.write(sh_file_content)

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
