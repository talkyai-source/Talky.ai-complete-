"""
Workers Package
Background workers for dialer, voice pipeline, and reminders.
"""
from importlib import import_module

__all__ = [
    "DialerWorker",
    "VoicePipelineWorker",
    "ReminderWorker",
]


def __getattr__(name: str):
    if name == "DialerWorker":
        return import_module("app.workers.dialer_worker").DialerWorker
    if name == "VoicePipelineWorker":
        return import_module("app.workers.voice_worker").VoicePipelineWorker
    if name == "ReminderWorker":
        return import_module("app.workers.reminder_worker").ReminderWorker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
