"""
One-time setup: installs every language runtime the programming
assessment stage needs onto a self-hosted Piston instance. A fresh
`ghcr.io/engineer-man/piston` container starts with ZERO runtimes
installed — this must be run once after `docker compose -f
docker-compose.piston.yml up -d` (see that file's own header
comment for the full setup sequence).

Safe to re-run any time (e.g. after a container recreate) —
installing an already-installed package is a no-op for Piston.

Usage:
    ./venv/Scripts/python.exe backend/scripts/piston_install_packages.py
    ./venv/Scripts/python.exe backend/scripts/piston_install_packages.py --api-url http://localhost:2000/api/v2
"""

import argparse
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# These are Piston's own PACKAGE/REPO names, used only for the
# install call -- NOT always the same as the language alias
# piston_runner.py uses at EXECUTE time (verified against Piston's
# actual package repo index, GET /api/v2/execute accepts aliases
# like "javascript"/"c"/"c++"/"csharp", but installation needs the
# underlying package: "node" (serves the javascript/typescript
# runtimes), "gcc" (serves both c and c++), "mono" (serves csharp).
# A single "gcc" install covers both c and c++ execution, so it
# only needs to be listed/installed once.
LANGUAGE_VERSIONS = [
    ("python", "3.10.0"),
    ("node", "18.15.0"),       # serves javascript
    ("typescript", "5.0.3"),
    ("java", "15.0.2"),
    ("gcc", "10.2.0"),         # serves both c and c++
    ("go", "1.16.2"),
    ("rust", "1.68.2"),
    ("php", "8.2.3"),
    ("ruby", "3.0.1"),
    ("kotlin", "1.8.20"),
    ("mono", "6.12.0"),        # serves csharp
]


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.getenv("PISTON_API_URL", "http://localhost:2000/api/v2"),
        help="Piston API base URL (default: http://localhost:2000/api/v2, or $PISTON_API_URL)",
    )
    args = parser.parse_args()

    base_url = args.api_url.rstrip("/")

    print(f"Installing {len(LANGUAGE_VERSIONS)} language runtimes onto {base_url} ...")
    print("(each install downloads/builds a real toolchain — this can take several minutes the first time)\n")

    failures = []

    for language, version in LANGUAGE_VERSIONS:

        print(f"  {language} {version} ... ", end="", flush=True)

        try:

            response = requests.post(
                f"{base_url}/packages",
                json={"language": language, "version": version},
                timeout=300,
            )

            if response.status_code == 200:
                print("ok")
            else:
                message = response.text
                try:
                    message = response.json().get("message", message)
                except ValueError:
                    pass
                print(f"FAILED ({response.status_code}: {message[:200]})")
                failures.append(language)

        except requests.RequestException as exc:
            print(f"FAILED (could not reach Piston: {exc})")
            failures.append(language)

        time.sleep(0.5)

    print()

    if failures:
        print(f"{len(failures)} package(s) failed to install: {', '.join(failures)}")
        print("Is the Piston container actually running? Check: docker compose -f docker-compose.piston.yml ps")
        return 1

    print("All language runtimes installed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
