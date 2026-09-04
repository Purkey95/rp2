"""MonitorCLT: a public-records monitoring and entity-resolution engine for real property.

Sources are captured raw and versioned bitemporally; every name occurrence becomes a
mention; a single calibrated resolver links mentions across sources with evidence
attached; reviewers' decisions become labels the model retrains on; and nothing
leaves the system except through a deny-by-default policy layer. Pure stdlib.
"""

__version__ = "2.1.0"
