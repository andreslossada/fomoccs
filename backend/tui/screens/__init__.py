"""TUI screen modules."""

from tui.screens.dashboard import DashboardScreen
from tui.screens.events import EventsScreen
from tui.screens.events_detail import EventDetailScreen
from tui.screens.locations import LocationsScreen
from tui.screens.locations_detail import LocationDetailScreen
from tui.screens.logs import LogsScreen
from tui.screens.operations import OperationsScreen
from tui.screens.pipeline_run import PipelineRunScreen
from tui.screens.source_wizard import SourceWizardScreen
from tui.screens.sources import SourcesScreen
from tui.screens.sources_detail import SourceDetailScreen
from tui.screens.tag_rules import TagRulesScreen

__all__ = [
    "DashboardScreen",
    "EventDetailScreen",
    "EventsScreen",
    "LocationDetailScreen",
    "LocationsScreen",
    "LogsScreen",
    "OperationsScreen",
    "PipelineRunScreen",
    "SourceDetailScreen",
    "SourceWizardScreen",
    "SourcesScreen",
    "TagRulesScreen",
]
