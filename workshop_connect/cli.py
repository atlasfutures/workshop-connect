"""``wconnect`` CLI — dynamic Click app for connector actions.

Dynamic Click commands built from ``_catalog.json`` at import time.
No generated ``.py`` files — avoids merge-noisy generated code.

Usage::

    wconnect list
    wconnect gmail --help
    wconnect gmail GMAIL_GET_PROFILE --userId=me
    wconnect gmail triggers list
    wconnect gmail triggers create --trigger=GMAIL_NEW_GMAIL_MESSAGE \\
        --webhook-url=https://...
    wconnect --connector MYGMAL gmail GMAIL_GET_PROFILE --userId=me
    wconnect --pretty gmail GMAIL_GET_PROFILE --userId=me

Exit codes::

    0 — success (2xx)
    1 — client error (4xx)
    2 — server error (5xx)
    3 — local error (bad args, no connector, catalog miss)
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from .catalog import CatalogError, get_toolkit, list_toolkits, load_catalog
from .client import ConnectorClient
from .connector import Connector
from .errors import ActionError, ConnectorError, ConnectorNotFoundError


def _exit_code_for_status(status: int) -> int:
    if status < 400:
        return 0
    if status < 500:
        return 1
    return 2


def _output(data: Any, pretty: bool) -> None:
    """Print JSON to stdout and flush immediately.

    Explicit flush ensures output is not lost when running as a
    subprocess in environments that fully-buffer stdout (e.g. headless
    containers without a TTY).
    """
    if pretty:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        click.echo(json.dumps(data, ensure_ascii=False))
    sys.stdout.flush()


def _error_exit(e: ActionError, pretty: bool) -> None:
    """Output error to stdout (JSON) + stderr (text), then exit."""
    err = {"error": str(e), "status_code": e.status_code}
    _output(err, pretty)
    click.echo(str(e), err=True)
    sys.stderr.flush()
    sys.exit(_exit_code_for_status(e.status_code))


def _resolve_client(
    connector: str | None,
    prefix: str | None,
    toolkit: str | None,
) -> ConnectorClient:
    """Build a ConnectorClient from CLI options."""
    try:
        if prefix:
            return ConnectorClient.from_env(prefix)
        if connector:
            return ConnectorClient.from_connector(connector)
        # Auto-detect with toolkit hint
        conn = Connector.from_env(toolkit=toolkit)
        return ConnectorClient(
            proxy_url=conn.proxy_url,
            api_key=conn.api_key,
            connected_account_id=conn.connected_account_id,
        )
    except (ConnectorNotFoundError, ConnectorError) as e:
        # Write to BOTH stdout and stderr so callers always see output
        msg = json.dumps({"error": str(e)}, ensure_ascii=False)
        click.echo(msg)
        sys.stdout.flush()
        click.echo(str(e), err=True)
        sys.stderr.flush()
        sys.exit(3)


# ---- Top-level group ----


@click.group(invoke_without_command=True)
@click.option("--connector", default=None, help="Connector name for auto-detection.")
@click.option("--prefix", default=None, help="Explicit env-var prefix (e.g. MYSLCK).")
@click.option("--pretty", is_flag=True, default=False, help="Pretty-print JSON output.")
@click.pass_context
def main(
    ctx: click.Context,
    connector: str | None,
    prefix: str | None,
    pretty: bool,
) -> None:
    """Workshop connector CLI — execute actions and manage triggers."""
    ctx.ensure_object(dict)
    ctx.obj["connector"] = connector
    ctx.obj["prefix"] = prefix
    ctx.obj["pretty"] = pretty
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---- "list" command ----


@main.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List available toolkits in the catalog."""
    pretty: bool = ctx.obj["pretty"]
    try:
        toolkits = list_toolkits()
    except CatalogError as e:
        click.echo(str(e), err=True)
        sys.exit(3)
    _output(toolkits, pretty)


# ---- Dynamic toolkit commands ----


def _make_toolkit_group(slug: str) -> click.Group:
    """Create a Click group for a toolkit with its actions as subcommands."""

    @click.group(name=slug, invoke_without_command=True)
    @click.option(
        "--connector", default=None, help="Connector name for auto-detection."
    )
    @click.option("--prefix", default=None, help="Env-var prefix (e.g. MYSLCK).")
    @click.pass_context
    def toolkit_group(
        ctx: click.Context,
        connector: str | None,
        prefix: str | None,
    ) -> None:
        # Override parent values if specified at this level
        if connector:
            ctx.obj["connector"] = connector
        if prefix:
            ctx.obj["prefix"] = prefix
        if ctx.invoked_subcommand is None:
            # Show actions for this toolkit
            try:
                tk = get_toolkit(slug)
            except CatalogError as e:
                click.echo(str(e), err=True)
                sys.exit(3)
            pretty: bool = ctx.obj["pretty"]
            actions = [
                {"name": a["name"], "description": a["description"]}
                for a in tk.get("actions", [])
            ]
            _output({"toolkit": slug, "actions": actions}, pretty)

    toolkit_group.help = f"Execute actions for the {slug} toolkit."

    # Add action subcommands
    try:
        tk = get_toolkit(slug)
    except CatalogError:
        return toolkit_group

    for action_info in tk.get("actions", []):
        action_name = action_info["name"]
        _add_action_command(toolkit_group, slug, action_name, action_info)

    # Add triggers subgroup
    triggers = tk.get("triggers", [])
    if triggers:
        _add_trigger_group(toolkit_group, slug, triggers)

    return toolkit_group


def _add_action_command(
    group: click.Group,
    toolkit_slug: str,
    action_name: str,
    action_info: dict,
) -> None:
    """Add a single action as a Click command to the toolkit group."""

    @click.command(
        name=action_name,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.pass_context
    def action_cmd(ctx: click.Context) -> None:
        pretty: bool = ctx.obj["pretty"]
        connector: str | None = ctx.obj["connector"]
        prefix: str | None = ctx.obj["prefix"]

        # Parse extra args as --key=value pairs
        arguments = _parse_extra_args(ctx.args)

        client = _resolve_client(connector, prefix, toolkit_slug)
        try:
            result = client.execute(action_name, arguments)
            _output(result, pretty)
        except ActionError as e:
            _error_exit(e, pretty)
        finally:
            client.close()

    action_cmd.help = action_info.get("description", f"Execute {action_name}")
    group.add_command(action_cmd)


def _add_trigger_group(
    parent: click.Group,
    toolkit_slug: str,
    triggers: list[dict],
) -> None:
    """Add a 'triggers' subgroup with create/list/disable/delete."""

    @click.group(name="triggers")
    @click.pass_context
    def triggers_group(ctx: click.Context) -> None:
        """Manage triggers for this toolkit."""

    @triggers_group.command("list")
    @click.pass_context
    def trigger_list_cmd(ctx: click.Context) -> None:
        """List active trigger instances."""
        pretty: bool = ctx.obj["pretty"]
        connector: str | None = ctx.obj["connector"]
        prefix: str | None = ctx.obj["prefix"]
        client = _resolve_client(connector, prefix, toolkit_slug)
        try:
            result = client.trigger_list()
            _output(result, pretty)
        except ActionError as e:
            _error_exit(e, pretty)
        finally:
            client.close()

    @triggers_group.command("create")
    @click.option(
        "--trigger", required=True, help="Trigger name (e.g. GMAIL_NEW_GMAIL_MESSAGE)."
    )
    @click.option("--webhook-url", required=True, help="URL to receive trigger events.")
    @click.pass_context
    def trigger_create_cmd(
        ctx: click.Context,
        trigger: str,
        webhook_url: str,
    ) -> None:
        """Create a new trigger subscription."""
        pretty: bool = ctx.obj["pretty"]
        connector: str | None = ctx.obj["connector"]
        prefix: str | None = ctx.obj["prefix"]
        client = _resolve_client(connector, prefix, toolkit_slug)
        try:
            result = client.trigger_create(trigger, webhook_url=webhook_url)
            _output(result, pretty)
        except ActionError as e:
            _error_exit(e, pretty)
        finally:
            client.close()

    @triggers_group.command("disable")
    @click.argument("trigger_id")
    @click.pass_context
    def trigger_disable_cmd(ctx: click.Context, trigger_id: str) -> None:
        """Disable a trigger instance."""
        pretty: bool = ctx.obj["pretty"]
        connector: str | None = ctx.obj["connector"]
        prefix: str | None = ctx.obj["prefix"]
        client = _resolve_client(connector, prefix, toolkit_slug)
        try:
            result = client.trigger_disable(trigger_id)
            _output(result, pretty)
        except ActionError as e:
            _error_exit(e, pretty)
        finally:
            client.close()

    @triggers_group.command("delete")
    @click.argument("trigger_id")
    @click.pass_context
    def trigger_delete_cmd(ctx: click.Context, trigger_id: str) -> None:
        """Delete a trigger instance."""
        pretty: bool = ctx.obj["pretty"]
        connector: str | None = ctx.obj["connector"]
        prefix: str | None = ctx.obj["prefix"]
        client = _resolve_client(connector, prefix, toolkit_slug)
        try:
            result = client.trigger_delete(trigger_id)
            _output(result, pretty)
        except ActionError as e:
            _error_exit(e, pretty)
        finally:
            client.close()

    # Add available triggers as a reference subcommand
    @triggers_group.command("available")
    @click.pass_context
    def trigger_available_cmd(ctx: click.Context) -> None:
        """Show triggers available for this toolkit."""
        pretty: bool = ctx.obj["pretty"]
        _output(triggers, pretty)

    parent.add_command(triggers_group)


def _parse_extra_args(args: list[str]) -> dict[str, Any]:
    """Parse Click extra args (``--key=value``) into a dict.

    Handles:
    - ``--key=value`` → ``{"key": "value"}``
    - ``--key value`` → ``{"key": "value"}``
    - ``--flag`` (no value) → ``{"flag": true}``
    - Numeric strings → int/float
    - ``true``/``false`` → bool
    """
    result: dict[str, Any] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key_part = arg[2:]
            if "=" in key_part:
                key, val = key_part.split("=", 1)
                result[key] = _coerce_value(val)
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                result[key_part] = _coerce_value(args[i + 1])
                i += 1
            else:
                result[key_part] = True
        i += 1
    return result


def _coerce_value(val: str) -> Any:
    """Coerce a string value to int, float, bool, or list."""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    # Try int — but preserve leading-zero strings (IDs, phone numbers).
    if not (len(val) > 1 and val[0] == "0" and val[1] != "."):
        try:
            return int(val)
        except ValueError:
            pass
        # Try float
        try:
            return float(val)
        except ValueError:
            pass
    # Try JSON (for lists/objects)
    if val.startswith("[") or val.startswith("{"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val


# ---- Register dynamic toolkit commands ----


def _register_toolkits() -> None:
    """Load catalog and register all toolkits as subcommands."""
    try:
        catalog = load_catalog()
    except CatalogError:
        # Catalog missing — CLI still works for `list` which will error properly
        return
    for slug in catalog.get("toolkits", {}):
        group = _make_toolkit_group(slug)
        main.add_command(group)


_register_toolkits()

if __name__ == "__main__":
    main()
