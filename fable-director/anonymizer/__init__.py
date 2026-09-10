"""fable-director anonymizer — pseudonymisation engine (phase A).

Pure stdlib. Three public functions live in `engine`: redact, restore, scan.
Nothing in this package knows about hooks, external-exec.py or Claude Code:
adapters (scripts/anonymizer.py today, hook adapters in phase E) call these.
"""
__version__ = "0.1.0"
