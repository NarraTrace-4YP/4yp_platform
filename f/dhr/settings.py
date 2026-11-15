"""The settings the pipelines need, wherever they run.

In Windmill they come from secret variables in f/dhr (server/windmill.sh puts them there),
so no password is ever written in code. On a laptop or in tests, an environment variable
takes their place: DHR_DATABASE_URL, HF_TOKEN, MLFLOW_TRACKING_URI and so on.
"""

import os


def windmill_variable(name: str) -> str | None:
    """The Windmill variable f/dhr/<name>, or None when not running in Windmill."""
    if not os.environ.get("WM_TOKEN"):  # Windmill sets this in every run
        return None
    import wmill

    return wmill.get_variable(f"f/dhr/{name}")


def database_url() -> str:
    url = os.environ.get("DHR_DATABASE_URL") or windmill_variable("database_url")
    if not url:
        raise RuntimeError("no database to connect to: set DHR_DATABASE_URL")
    return url


def use_hugging_face_token() -> None:
    """Makes the team's Hugging Face token available to huggingface_hub, if there is one."""
    if not os.environ.get("HF_TOKEN"):
        token = windmill_variable("hf_token")
        if token and token != "not-set":
            os.environ["HF_TOKEN"] = token


def use_mlflow() -> bool:
    """Points MLflow at the team's server. Returns False if there's no MLflow to log to."""
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        uri = windmill_variable("mlflow_tracking_uri")
        if not uri:
            return False
        os.environ["MLFLOW_TRACKING_URI"] = uri
        os.environ["MLFLOW_TRACKING_USERNAME"] = windmill_variable("mlflow_username") or ""
        os.environ["MLFLOW_TRACKING_PASSWORD"] = windmill_variable("mlflow_password") or ""
    os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
    return True
