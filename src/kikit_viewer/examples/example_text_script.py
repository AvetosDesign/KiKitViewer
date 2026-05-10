"""
Example KiKitViewer text script.

KiKitViewer executes this script and calls get_text() to obtain the string
that will be printed on the panel.  The script runs in an isolated namespace
in the main (host) Python process, so any package available to KiKitViewer is
also available here.

Requirements
------------
* The script must define a top-level function named ``get_text``.
* ``get_text`` must return a plain ``str``.
* No other structure is required; helper functions and imports are allowed.

To use this script:
  1. Copy it to a convenient location and edit get_text() to suit your needs.
  2. In KiKitViewer, open the Text tab, set Text type to "scripted", and browse
     to your copy of the script.
"""

import datetime


def get_text() -> str:
    """Return a build-date stamp suitable for panel annotation."""
    today = datetime.date.today()
    return f"Built {today:%Y-%m-%d}"
