"""Enable ``python -m recotem`` invocation.

The console-script entry point ``recotem`` is the recommended way to run the
CLI, but it is unavailable when the package is installed into a venv that is
not on ``PATH``. Falling back to ``python -m recotem`` keeps the troubleshooting
advice in ``https://recotem.org/2.1/guide/`` honest.
"""

from __future__ import annotations

from recotem.cli import app

if __name__ == "__main__":
    app()
