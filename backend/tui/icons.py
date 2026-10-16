"""Centralized icon constants for the FomoCCS Admin TUI.

All emoji/unicode icons used across screens live here for easy auditing and
replacement (e.g. swapping to Nerd Font icons if the terminal supports them).
"""

from __future__ import annotations

# Status
ICON_ACTIVE = "\u25cf"
ICON_DISABLED = "\u25cb"
ICON_ERROR = "\u2716"
ICON_RUNNING = "\u25cc"
ICON_DIM = "\u00b7"

# Dashboard cards
ICON_DB = "\U0001f5c4\ufe0f"
ICON_CRAWL = "\U0001f504"
ICON_EVENTS = "\U0001f4c5"
ICON_SOURCES = "\U0001f4e1"
ICON_LLM = "\U0001f916"
ICON_RECENT = "\U0001f195"
ICON_LAST_CRAWLS = "\U0001f4ca"
ICON_WARNING = "\u26a0"

# Actions
ICON_CHECK = "\u2714"
ICON_CROSS = "\u2716"
ICON_STAR = "\u2605"
ICON_LINK = "\U0001f517"
ICON_PIN = "\U0001f4cd"
ICON_CALENDAR = "\U0001f4c5"
ICON_ARROW = "\u2192"

# Navigation
ICON_BREADCRUMB = "\u25b8"
