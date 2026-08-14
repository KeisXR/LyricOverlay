"""Player package — platform-specific media session listener.

On Linux/macOS the listener uses D-Bus MPRIS (``player.mpris``).
On Windows it uses the System Media Transport Controls API (``player.smtc``).
The optional BrowserWsListener works on all platforms.
"""

import sys

if sys.platform == "win32":
    from .smtc import SmtcListener as MprisListener  # noqa: F401
else:
    from .mpris import MprisListener  # noqa: F401

from .browser_ws import BrowserWsListener  # noqa: F401

__all__ = ["MprisListener", "BrowserWsListener"]
