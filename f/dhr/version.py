"""Which commit of the GitHub repo this code came from.

The deploy (.github/workflows/deploy.yml) writes the real commit here just before pushing
to Windmill, so every pipeline run can record exactly which code produced it.
"""

GIT_COMMIT = "local"
