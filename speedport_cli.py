#!/usr/bin/env python3
"""Speedport Router Management CLI.

Covers everything the ``speedport`` lib exposes (``speedport/speedport.py``
+ ``speedport/api.py``). The lib has NO native port-forwarding support, so
raw ``--api-get``/``--api-post`` escape hatches plus ``--nat-probe`` are
provided to discover and talk to NAT endpoints directly.

Layout: ``ACTION_ORDER`` is the single source of truth for the CLI surface
(``--help`` order and flag names). Each action is dispatched via exactly one
of ``SPEEDPORT_METHODS`` (no-arg ``Speedport`` coroutine, run generically),
``API_PASSTHROUGHS`` (zero-arg ``SpeedportApi`` method, forwarded
generically), or ``CUSTOM_HANDLERS`` (local coroutine below). A module-level
assertion guarantees the three maps stay in sync with ``ACTION_ORDER``.

Naming wart, isolated in ``_connection()``: ``Speedport.api`` is a
``SpeedportApi``, whose ``.api`` is the ``Connection``. ``SpeedportApi`` has
no generic ``get``/``post`` — raw endpoint access must go through the
``Connection``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, NoReturn

from speedport import Speedport

__version__ = "0.1.0"
DEFAULT_IP = "192.168.2.1"

# Probed via the generic Connection.get(); the lib implements none of these.
CANDIDATE_NAT_ENDPOINTS = [
    "data/NAT.json",
    "data/NATRules.json",
    "data/PortForwarding.json",
    "data/PortRule.json",
    "data/PortRules.json",
    "data/Firewall.json",
    "data/VirtualServer.json",
]

# No-arg Speedport coroutines, invoked generically (reconnect, reboot, wifi_*, wps_on).
SPEEDPORT_METHODS = frozenset({
    "reconnect",
    "reboot",
    "wifi_on",
    "wifi_off",
    "wifi_guest_on",
    "wifi_guest_off",
    "wifi_office_on",
    "wifi_office_off",
    "wps_on",
})

# Zero-arg SpeedportApi coroutines, forwarded generically: CLI action -> api method.
API_PASSTHROUGHS = {
    "devices_raw": "get_devices",
    "calls_raw": "get_phone_calls",
    "router": "get_router",
    "secure_status": "get_secure_status",
    "phone_handler": "get_phone_handler",
    "login_info": "get_login",
}

Handler = Callable[[Speedport, argparse.Namespace], Awaitable[Any]]

# Canonical CLI surface, in --help order. Flags derive from names (see
# _flag_for); only guest/office flags need overrides since the CLI puts the
# qualifier first (--guest-wifi-on) while the lib appends it (wifi_guest_on).
ACTION_ORDER = [
    "status",
    "reconnect",
    "reboot",
    "wifi_on",
    "wifi_off",
    "wifi_guest_on",
    "wifi_guest_off",
    "wifi_office_on",
    "wifi_office_off",
    "wps_on",
    "devices",
    "devices_raw",
    "calls",
    "calls_raw",
    "ip_data",
    "wps_state",
    "router",
    "secure_status",
    "phone_handler",
    "login_info",
    "summary",
    "api_get",
    "api_post",
    "nat_probe",
]

_FLAG_OVERRIDES = {
    "wifi_guest_on": "--guest-wifi-on",
    "wifi_guest_off": "--guest-wifi-off",
    "wifi_office_on": "--office-wifi-on",
    "wifi_office_off": "--office-wifi-off",
}


def _flag_for(action: str) -> str:
    return _FLAG_OVERRIDES.get(action, "--" + action.replace("_", "-"))


def _dest_for_flag(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


# Flag -> action, derived so adding an action means one ACTION_ORDER entry
# plus one entry in SPEEDPORT_METHODS / API_PASSTHROUGHS / CUSTOM_HANDLERS.
FLAGS: dict[str, str] = {_flag_for(action): action for action in ACTION_ORDER}


def json_serial(obj: Any) -> str:
    if isinstance(obj, (datetime, timedelta)):
        return str(obj)
    if hasattr(obj, "isoformat"):
        with suppress(Exception):
            return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def emit(data: Any) -> None:
    print(json.dumps(data, indent=2, default=json_serial))


def die(message: str, code: int = 1) -> NoReturn:
    print(json.dumps({"error": message}), file=sys.stderr)
    sys.exit(code)


def device_to_dict(dev: Any) -> dict:
    data = getattr(dev, "data", None)
    return dict(data) if isinstance(data, dict) else {"name": str(dev)}


def call_to_dict(call: Any) -> dict:
    base = dict(call.data)
    base["_parsed_type"] = call.type
    base["_parsed_number"] = call.number
    base["_parsed_duration"] = call.duration
    with suppress(Exception):
        base["_parsed_date"] = str(call.date)
    with suppress(Exception):
        base["_parsed_connection"] = call.connection
    return base


def _snapshot(speedport: Speedport, attr: str) -> dict:
    # The lib keeps fetched state in private _status / _ip_data with no
    # public full-dump accessor; confined to this helper (copied, never mutated).
    return dict(getattr(speedport, attr, {}) or {})


def _safe(getter: Callable[[], Any], default: Any) -> Any:
    # Several lib properties (wlan_active, dsl_downstream, ...) raise
    # TypeError/ValueError on missing/empty status values; summaries must
    # degrade to a default instead of dying on one absent key.
    try:
        return getter()
    except Exception:
        return default


def _connection(speedport: Speedport) -> Any:
    # Speedport.api -> SpeedportApi -> .api -> Connection. SpeedportApi
    # exposes only fixed get_* helpers, so raw endpoint access goes here.
    return speedport.api.api


async def fetch_status(speedport: Speedport) -> dict:
    await asyncio.gather(speedport.update_status(), speedport.update_ip_data())
    return _snapshot(speedport, "_status")


async def fetch_summary(speedport: Speedport) -> dict:
    await asyncio.gather(speedport.update_status(), speedport.update_ip_data())
    try:
        wps_remaining: Any = await speedport.wps_remaining
    except Exception as exc:
        print(f"warning: wps_remaining unavailable: {exc}", file=sys.stderr)
        wps_remaining = None
    return {
        "device_name": speedport.device_name,
        "serial_number": speedport.serial_number,
        "firmware_version": speedport.firmware_version,
        "online_status": speedport.online_status,
        "login_state": speedport.login_state,
        "rebooting": speedport.rebooting,
        "dsl_downstream": _safe(lambda: speedport.dsl_downstream, 0),
        "dsl_upstream": _safe(lambda: speedport.dsl_upstream, 0),
        "inet_download": _safe(lambda: speedport.inet_download, 0),
        "inet_upload": _safe(lambda: speedport.inet_upload, 0),
        "inet_uptime": str(speedport.get("inet_uptime", "")),
        "wlan_active": _safe(lambda: speedport.wlan_active, False),
        "wlan_ssid": speedport.wlan_ssid,
        "wlan_guest_active": _safe(lambda: speedport.wlan_guest_active, False),
        "wlan_guest_ssid": speedport.wlan_guest_ssid,
        "wlan_office_active": _safe(lambda: speedport.wlan_office_active, False),
        "wlan_office_ssid": speedport.wlan_office_ssid,
        "wps_remaining": wps_remaining,
        "public_ip_v4": speedport.public_ip_v4,
        "public_ip_v6": speedport.public_ip_v6,
        "gateway_ip_v4": speedport.gateway_ip_v4,
        "gateway_ip_v6": speedport.gateway_ip_v6,
        "dns_v4": speedport.dns_v4,
        "sec_dns_v4": speedport.sec_dns_v4,
        "dns_v6": speedport.dns_v6,
        "sec_dns_v6": speedport.sec_dns_v6,
    }


async def run_simple_action(speedport: Speedport, action: str) -> None:
    print(f"Triggering {action}...", file=sys.stderr)
    await getattr(speedport, action)()
    print("Done.")


def parse_kv_pairs(pairs: list[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Invalid --data item {pair!r}, expected KEY=VALUE")
        key, value = pair.split("=", 1)
        data[key] = value
    return data


async def cmd_status(speedport: Speedport, args: argparse.Namespace) -> dict:
    return await fetch_status(speedport)


async def cmd_summary(speedport: Speedport, args: argparse.Namespace) -> dict:
    return await fetch_summary(speedport)


async def cmd_devices(speedport: Speedport, args: argparse.Namespace) -> dict:
    devices = await speedport.devices
    return {mac: device_to_dict(dev) for mac, dev in devices.items()}


async def cmd_calls(speedport: Speedport, args: argparse.Namespace) -> list:
    return [call_to_dict(call) for call in await speedport.calls]


async def cmd_ip_data(speedport: Speedport, args: argparse.Namespace) -> dict:
    await speedport.update_ip_data()
    return _snapshot(speedport, "_ip_data")


async def cmd_wps_state(speedport: Speedport, args: argparse.Namespace) -> dict:
    return {
        "wps_remaining": await speedport.wps_remaining,
        "raw": await speedport.api.get_wps_state(),
    }


async def cmd_api_get(speedport: Speedport, args: argparse.Namespace) -> dict:
    return await _connection(speedport).get(args.path, auth=args.auth, referer=args.referer)


async def cmd_api_post(speedport: Speedport, args: argparse.Namespace) -> dict:
    # ValueError from parse_kv_pairs propagates to main(), which reports it
    # as a JSON error identically to the other failures.
    return await _connection(speedport).post(args.path, parse_kv_pairs(args.data), args.referer)


async def _probe_one(conn: Any, endpoint: str) -> dict:
    try:
        return {"ok": True, "data": await conn.get(endpoint, auth=True)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def cmd_nat_probe(speedport: Speedport, args: argparse.Namespace) -> dict:
    conn = _connection(speedport)
    results = await asyncio.gather(
        *(_probe_one(conn, endpoint) for endpoint in CANDIDATE_NAT_ENDPOINTS)
    )
    return dict(zip(CANDIDATE_NAT_ENDPOINTS, results))


def _api_passthrough(api_method: str) -> Handler:
    async def _handler(speedport: Speedport, _args: argparse.Namespace) -> Any:
        return await getattr(speedport.api, api_method)()

    _handler.__name__ = f"passthrough_{api_method}"
    return _handler


CUSTOM_HANDLERS: dict[str, Handler] = {
    "status": cmd_status,
    "summary": cmd_summary,
    "devices": cmd_devices,
    "calls": cmd_calls,
    "ip_data": cmd_ip_data,
    "wps_state": cmd_wps_state,
    "api_get": cmd_api_get,
    "api_post": cmd_api_post,
    "nat_probe": cmd_nat_probe,
}

COMMANDS: dict[str, Handler] = {
    action: _api_passthrough(method) for action, method in API_PASSTHROUGHS.items()
} | CUSTOM_HANDLERS

_missing = set(ACTION_ORDER) - set(SPEEDPORT_METHODS) - set(COMMANDS)
_extra = (set(SPEEDPORT_METHODS) | set(COMMANDS)) - set(ACTION_ORDER)
assert not _missing and not _extra, (
    f"action registry drift: missing={sorted(_missing)} extra={sorted(_extra)}"
)


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, str]]:
    parser = argparse.ArgumentParser(description="Speedport Router Management CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--ip", default=DEFAULT_IP, help=f"Router IP (default: {DEFAULT_IP})")
    parser.add_argument("--password", default=os.environ.get("SPEEDPORT_PASSWORD"),
                        help="Router login password (or set SPEEDPORT_PASSWORD env var)")
    parser.add_argument("--https", default=True, action=argparse.BooleanOptionalAction,
                        help="Use HTTPS (default: --https, use --no-https for plain HTTP)")

    group = parser.add_mutually_exclusive_group(required=True)
    for action in ACTION_ORDER:
        group.add_argument(_flag_for(action), action="store_true", help=f"Trigger {action}")

    parser.add_argument("--path", default="", help="JSON endpoint for --api-get/--api-post (e.g. data/NAT.json)")
    parser.add_argument("--referer", default="", help="Referer page for --api-get/--api-post (needed for token-protected endpoints)")
    parser.add_argument("--auth", default=True, action=argparse.BooleanOptionalAction,
                        help="Decrypt --api-get with login key (default: --auth)")
    parser.add_argument("--data", nargs="*", default=[], metavar="KEY=VALUE",
                        help="POST fields for --api-post (e.g. --data req_connect=reconnect)")
    return parser, FLAGS


def resolve_action(args: argparse.Namespace, actions: dict[str, str]) -> str:
    for flag, action in actions.items():
        if getattr(args, _dest_for_flag(flag), False):
            return action
    raise AssertionError("unreachable: argparse guarantees one action flag")


def validate_action_args(parser: argparse.ArgumentParser, args: argparse.Namespace, action: str) -> None:
    # Validate before connecting so misuse fails fast without a login round-trip.
    if action == "api_get" and not args.path:
        parser.error("--api-get requires --path <endpoint> (e.g. --path data/NAT.json)")
    if action == "api_post":
        if not args.path:
            parser.error("--api-post requires --path <endpoint> and --data KEY=VALUE ...")
        if not args.referer:
            parser.error("--api-post requires --referer <page> for the httoken")


async def main() -> None:
    parser, actions = build_parser()
    args = parser.parse_args()
    if not args.password:
        parser.error("--password is required (or set the SPEEDPORT_PASSWORD env var)")
    action = resolve_action(args, actions)
    validate_action_args(parser, args, action)

    try:
        async with Speedport(args.ip, args.password, https=args.https) as speedport:
            await speedport.login(args.password)
            if action in SPEEDPORT_METHODS:
                await run_simple_action(speedport, action)
            else:
                emit(await COMMANDS[action](speedport, args))
    except Exception as exc:
        die(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
