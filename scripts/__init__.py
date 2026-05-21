"""Weekly multi-agent reports — implementation modules.

Each step of the pipeline lives in its own module here. Per Decision 24,
every module exposes a top-level function the orchestrator imports, AND
each module has an `if __name__ == "__main__":` block so it can be run
standalone for debugging.
"""

__version__ = "0.1.0"
