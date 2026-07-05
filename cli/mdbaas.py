"""Thin HTTP client for demos (ARCHITECTURE 4: cli/).

Reads MDBAAS_API_URL (default http://localhost:8000) and MDBAAS_API_KEY. It is a
convenience wrapper over the REST API — it holds no business logic of its own.
"""

from __future__ import annotations

import json
import os

import httpx
import typer

app = typer.Typer(help="Mini-DBaaS CLI", no_args_is_help=True)
teams = typer.Typer(help="Team administration")
instances = typer.Typer(help="Instance lifecycle")
backups = typer.Typer(help="Backups")
app.add_typer(teams, name="teams")
app.add_typer(instances, name="instances")
app.add_typer(backups, name="backups")


def _base() -> str:
    return os.environ.get("MDBAAS_API_URL", "http://localhost:8000").rstrip("/")


def _client() -> httpx.Client:
    headers = {}
    key = os.environ.get("MDBAAS_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=_base(), headers=headers, timeout=60)


def _show(resp: httpx.Response) -> None:
    try:
        typer.echo(json.dumps(resp.json(), indent=2))
    except ValueError:
        typer.echo(resp.text)
    if resp.is_error:
        raise typer.Exit(code=1)


@app.command()
def health() -> None:
    """Ping the control plane."""
    with _client() as c:
        _show(c.get("/health"))


@teams.command("create")
def team_create(name: str, max_instances: int = 10) -> None:
    with _client() as c:
        _show(c.post("/v1/teams", json={"name": name, "max_instances": max_instances}))


@instances.command("create")
def instance_create(name: str, team: str, size: str = "small", image: str | None = None) -> None:
    body = {"team_id": team, "name": name, "size": size}
    if image:
        body["image"] = image
    with _client() as c:
        _show(c.post("/v1/instances", json=body))


@instances.command("list")
def instance_list(team: str | None = None, tag: str | None = None) -> None:
    params = {k: v for k, v in {"team_id": team, "tag": tag}.items() if v}
    with _client() as c:
        _show(c.get("/v1/instances", params=params))


@instances.command("status")
def instance_status(instance_id: str) -> None:
    with _client() as c:
        _show(c.get(f"/v1/instances/{instance_id}/status"))


@instances.command("delete")
def instance_delete(instance_id: str, final_backup: bool = True) -> None:
    with _client() as c:
        _show(c.delete(f"/v1/instances/{instance_id}", params={"final_backup": final_backup}))


@instances.command("rotate")
def instance_rotate(instance_id: str) -> None:
    """Rotate the superuser credential."""
    with _client() as c:
        _show(c.post(f"/v1/instances/{instance_id}/credentials/rotate"))


@backups.command("create")
def backup_create(instance_id: str) -> None:
    with _client() as c:
        _show(c.post(f"/v1/instances/{instance_id}/backups", json={}))


@backups.command("list")
def backup_list(instance_id: str) -> None:
    with _client() as c:
        _show(c.get(f"/v1/instances/{instance_id}/backups"))


if __name__ == "__main__":
    app()
