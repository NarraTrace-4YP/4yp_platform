"""Fail a deployment if the website still embeds an obsolete Windmill share URL."""

import argparse
import json
import urllib.parse
import urllib.request


def check_objective_embed(windmill_url: str, expected_url: str, name: str = "o1") -> None:
    """Compare the website's runtime config (/runtime/<name>.json) with the share URL just
    deployed. name: o1 or o2, the objective app's page on the website."""
    origin = urllib.parse.urlsplit(windmill_url)
    if not origin.netloc.startswith("windmill."):
        raise ValueError("Expected the Windmill URL to use the windmill. subdomain")
    config_url = urllib.parse.urlunsplit(
        (origin.scheme, origin.netloc.removeprefix("windmill."), f"/runtime/{name}.json", "", "")
    )
    request = urllib.request.Request(config_url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=20) as response:
        configured_url = json.load(response).get("url")
    if configured_url != expected_url:
        raise RuntimeError(
            f"The website's {name.upper()} embed URL is stale. The app deployed successfully, but its "
            "public share ID changed. Refresh the server runtime configuration with "
            "sudo /opt/dhr/server/update.sh, then rerun this deployment."
        )
    print(f"The website embeds the current public {name.upper()} app.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windmill-url", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--name", default="o1", choices=("o1", "o2"))
    args = parser.parse_args()
    check_objective_embed(args.windmill_url, args.public_url, args.name)
