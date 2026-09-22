"""Health check for the WoL server and its ts-wol Tailscale sidecar.

Sends a POST {"token": ..., "action": "health"} to the WoL app on 127.0.0.1:8080
and a GET to the ts-wol sidecar's healthz endpoint on 127.0.0.1:41234. Exits 0 if
both succeed, non-zero (with a message on stderr) otherwise.

Used as the Docker HEALTHCHECK for the `wol` service — see ../compose.yaml.

Run standalone:
    export WOL_TOKEN=<token> && python3 server/test_health.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

WOL_URL = "http://127.0.0.1:8080/"
TAILSCALE_HEALTHZ_URL = "http://127.0.0.1:41234/healthz"
TIMEOUT = 5.0


def check_wol_app() -> None:
    """POST a health action to the WoL app; raises on any failure."""
    token = os.environ["WOL_TOKEN"]
    body = json.dumps({"token": token, "action": "health"}).encode()
    request = urllib.request.Request(
        WOL_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"wol app returned unexpected status {response.status}")


def check_tailscale_sidecar() -> None:
    """GET the ts-wol sidecar's healthz endpoint; raises on any failure."""
    with urllib.request.urlopen(TAILSCALE_HEALTHZ_URL, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"ts-wol healthz returned unexpected status {response.status}")


def main() -> int:
    try:
        check_wol_app()
        check_tailscale_sidecar()
    except KeyError:
        print("WOL_TOKEN environment variable is not set", file=sys.stderr)
        return 1
    except (urllib.error.URLError, RuntimeError, OSError) as e:
        print(f"health check failed: {e}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
