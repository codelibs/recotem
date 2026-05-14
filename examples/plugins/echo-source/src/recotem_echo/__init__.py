"""recotem_echo — example DataSource plugin for Recotem.

Demonstrates the minimal plugin contract.  EchoSource returns a static
DataFrame and is suitable only for testing and documentation purposes.

Usage in a recipe YAML::

    source:
      type: echo
      n_users: 20
      n_items: 50
      n_rows: 200

Install::

    pip install recotem-echo-source
    # or, from the examples directory:
    pip install examples/plugins/echo-source/
"""

from recotem_echo.source import EchoSource

__all__ = ["EchoSource"]
