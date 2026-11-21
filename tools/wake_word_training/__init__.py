"""MINDER's isolated wake-word training and validation utilities.

These modules are build-time tooling. The desktop application does not import
this package during startup and never starts microphone capture or training
because a model asset happens to exist.
"""
