"""Single source of truth for the package version.

Note: the *ruleset* and *schema* are versioned separately at runtime
(``RULESET_VERSION``, ``SCHEMA_VERSION``, ``GENERATOR_VERSION``) so that a change to a
scoring constant or recipe starts a new leaderboard generation independent of the
package release. See docs/DESIGN.md (Versioning) and docs/SCORING.md.
"""

__version__ = "0.1.0"
