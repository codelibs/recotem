# This module previously contained a custom exception handler that wrapped
# DRF responses in a {success, error, data} envelope. The envelope has been
# removed in favor of standard DRF error responses (HTTP status + body).
#
# The module is kept empty to avoid breaking any stale imports.
