"""MonitorCLT — public-record property intelligence for Mecklenburg County, NC.

See ``second-brain/wiki/monitorclt.md`` and the design cluster it links to for
the reasoning behind this package's structure. The short version: sources are
built in order of whether the record already names a parcel, and no numeric
"opportunity score" is emitted until it has been backtested.
"""

__version__ = "0.1.0"
