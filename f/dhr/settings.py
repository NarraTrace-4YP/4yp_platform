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


class ProgressBar:
    """Moves the progress bar of the Windmill run this code is in (nothing happens elsewhere).

    Call it with a percentage as the work goes on. Windmill takes 0 to 99 (it shows 100
    itself when the run finishes) and the bar only moves forwards. Each update is a request
    to Windmill, so it only sends one when the whole percentage goes up.
    """

    def __init__(self) -> None:
        self.shown = -1
        self.working = bool(os.environ.get("WM_JOB_ID"))  # Windmill sets this in every run

    def __call__(self, percent: float) -> None:
        value = max(0, min(99, int(percent)))
        if not self.working or value <= self.shown:
            return
        self.shown = value
        try:
            import wmill

            wmill.set_progress(value)
        except Exception as error:  # noqa: BLE001 - a progress bar is never worth failing a run
            print(f"NOTE  Stopped updating the progress bar: {error}", flush=True)
            self.working = False
