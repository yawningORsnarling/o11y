#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any

import httpx
from dotenv import load_dotenv

DOPPLER_API_BASE = "https://api.doppler.com/v3"


def fetch_doppler_secrets(token: str, project: str, config: str) -> Dict[str, str]:
    """
    Fetch secrets from Doppler as a flat key/value dict.
    """
    url = f"{DOPPLER_API_BASE}/configs/config/secrets/download"
    params = {"project": project, "config": config, "format": "json"}
    auth = (token, "")

    with httpx.Client(timeout=20.0) as client:
        r = client.get(url, params=params, auth=auth)
        r.raise_for_status()
        data: Any = r.json()

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Doppler response type: {type(data)}")

    # Ensure everything is stringified for env usage
    secrets: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        secrets[str(k)] = str(v)

    return secrets


def run_compose_up(
    *,
    workdir: Path,
    compose_files: list[Path],
    env: Dict[str, str],
    do_pull: bool,
) -> None:
    cmd = ["docker", "compose"]
    for f in compose_files:
        cmd += ["-f", str(f)]

    if do_pull:
        subprocess.run(cmd + ["pull"], cwd=str(workdir), env=env, check=True)

    subprocess.run(cmd + ["up", "-d"], cwd=str(workdir), env=env, check=True)


def run_compose_down(
    *,
    workdir: Path,
    compose_files: list[Path],
    env: Dict[str, str],
    remove_volumes: bool = False,
) -> None:
    cmd = ["docker", "compose"]

    for f in compose_files:
        cmd += ["-f", str(f)]

    down_cmd = cmd + ["down"]

    if remove_volumes:
        down_cmd.append("-v")

    subprocess.run(down_cmd, cwd=str(workdir), env=env, check=True)


def run() -> int:
    # Load .env (for DOPPLER_TOKEN and optional defaults)
    load_dotenv()

    p = argparse.ArgumentParser()
    p.add_argument("--action", "-a", required=True, help="Doppler project name")
    p.add_argument("--project", "-p", required=True, help="Doppler project name")
    p.add_argument("--config", "-c", required=True, help="Doppler config name (dev/prod)")
    p.add_argument(
        "--file",
        action="append",
        default=[],
        help="Compose file path (repeatable). Default: docker-compose.yaml",
    )
    p.add_argument("--workdir", default=".", help="Directory to run docker compose from")
    p.add_argument("--no-pull", action="store_true", help="Skip docker compose pull")
    p.add_argument("--remove-volumes", action="store_true", help="Destroy container volumes when stopping services")
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="Do not fail if some expected vars are missing (useful while iterating).",
    )
    args = p.parse_args()

    token = os.getenv("DOPPLER_TOKEN")
    if not token:
        print("ERROR: DOPPLER_TOKEN not found in .env or environment", file=sys.stderr)
        return 2

    workdir = Path(args.workdir).expanduser().resolve()

    compose_files = (
        [Path("docker-compose.yaml")]
        if len(args.file) == 0
        else [Path(f) for f in args.file]
    )
    compose_files = [f.expanduser().resolve() for f in compose_files]
    for f in compose_files:
        if not f.exists():
            print(f"ERROR: compose file not found: {f}", file=sys.stderr)
            return 2

    # Pull secrets from Doppler
    doppler_secrets = fetch_doppler_secrets(token, args.project, args.config)
    print(doppler_secrets)

    # Build env for docker compose:
    # start from current env, then overlay Doppler secrets
    child_env = dict(os.environ)
    child_env.update(doppler_secrets)

    # Optional sanity check: ensure Grafana vars exist
    required = ["GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD"]
    missing = [k for k in required if k not in child_env or not child_env[k]]
    if missing and not args.allow_missing:
        print(f"ERROR: missing required secrets from Doppler: {', '.join(missing)}", file=sys.stderr)
        return 3

    if args.action == "up":
        # Run compose with Doppler-provided env injected at runtime
        run_compose_up(
            workdir=workdir,
            compose_files=compose_files,
            env=child_env,
            do_pull=(not args.no_pull),
        )
    elif args.action == "down":
        # Run compose with Doppler-provided env injected at runtime
        run_compose_down(
            workdir=workdir,
            compose_files=compose_files,
            env=child_env,
            remove_volumes=args.remove_volumes,
        )

    print("docker compose up -d completed (with Doppler env injected at runtime).")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
