"""The app's version: the one place to change on each release.

Templates read it through the `app_version` / `app_stage` context variables,
/healthz reports it, and nothing else should hardcode it.
"""

__version__ = "0.2.0"

# Release stage shown in the top banner ("beta", …). Set to "" for a stable
# release and the banner disappears.
STAGE = "beta"
