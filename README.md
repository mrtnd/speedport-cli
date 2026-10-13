# speedport-cli

Command-line management tool for Telekom Speedport routers, built on the
[`speedport_api` 0.6.0](https://pypi.org/project/speedport-api/) library
(`Speedport` / `SpeedportApi` / `Connection`).

It exposes **everything the library offers** as JSON on stdout, plus raw
`--api-get` / `--api-post` escape hatches and `--nat-probe` for endpoints the
library does not wrap — notably anything NAT / port-forwarding related, which
the library has **no native support** for.

## Contents

- `speedport_cli.py` — the CLI (single file, async, stdlib + `speedport` only)

## Requirements

- Python 3.11+ (tested with 3.14)
- Installed package: `speedport_api` 0.6.0 (provides the `speedport` module:
  `speedport.py`, `api.py`, `connection.py`, `device.py`, `call.py`)
- Network access to the router (default `192.168.2.1`), router login password

## Setup

```bash
python3 ~/github/speedport-cli/speedport_cli.py --help
```

No install step — run the script with a Python that has `speedport_api`
installed so `import speedport` resolves (e.g. `path/to/venv/bin/python`).
The `#!/usr/bin/env python3` shebang means `./speedport_cli.py` works too
when such a Python is first on `PATH` and the file is executable.

Credentials (either):

```bash
export SPEEDPORT_PASSWORD='your-router-password'
python3 speedport_cli.py --summary
# or per-call:
python3 speedport_cli.py --password 'your-router-password' --summary
```

## Usage

Exactly **one action flag** per call, plus global options:

```bash
speedport_cli.py [--ip IP] [--password PW] [--https | --no-https] <action>
                 [--path ENDPOINT] [--referer PAGE] [--auth | --no-auth]
                 [--data KEY=VALUE ...]
```

| Global option | Default | Meaning |
|---|---|---|
| `--ip` | `192.168.2.1` | Router address |
| `--password` | `$SPEEDPORT_PASSWORD` | Router login password (required) |
| `--https` / `--no-https` | `--https` | Transport scheme |
| `--path` | — | Endpoint for `--api-get` / `--api-post`, e.g. `data/NAT.json` |
| `--referer` | — | Referer page for `--api-get` / `--api-post`; required for `--api-post` because the router issues the `httoken` anti-CSRF token from that page |
| `--auth` / `--no-auth` | `--auth` | Decrypt `--api-get` responses with the login key vs. the default key |
| `--data` | — | `KEY=VALUE` POST fields for `--api-post` (values may contain `=`) |

Output contract:

- Read actions print pretty-printed **JSON to stdout**.
- Control actions (`--reconnect`, `--reboot`, `--wifi-*`, `--wps-on`) print
  `Triggering …` / `Done.` to **stderr** and no JSON.
- All failures print `{"error": "<message>"}` to **stderr** with exit code 1.
  Argparse misuse exits 2 and is validated **before** connecting.

## Command reference

### Status & summary

| Flag | Library call | Output |
|---|---|---|
| `--status` | `update_status()` + `update_ip_data()`, full `_status` dump | Raw status dict (DSL sync, WLAN, online state, …) |
| `--summary` | Same refresh, curated properties | One stable dict: device/serial/firmware, `online_status`, `login_state`, `rebooting`, DSL up/down, inet up/down, `inet_uptime`, WLAN + guest + office active/SSID, `wps_remaining`, public/gateway IPs v4+v6, DNS ×4 |
| `--ip-data` | `update_ip_data()`, full `_ip_data` dump | Public/gateway/DNS/IP-pool fields |
| `--router` | `SpeedportApi.get_router()` → `data/Router.json` (no auth) | Router info |
| `--secure-status` | `get_secure_status()` → `data/SecureStatus.json` (auth) | Security status |
| `--login-info` | `get_login()` → `data/Login.json` | Login/challenge state |

`--summary` tolerates missing keys: numeric/boolean fields fall back to
`0`/`False` instead of raising (the lib's `wlan_active`/`dsl_*` properties
raise `TypeError`/`ValueError` on absent values). `wps_remaining` failure
prints a stderr warning and yields `null` rather than failing the summary.

### Devices & calls

| Flag | Library call | Output |
|---|---|---|
| `--devices` | `Speedport.devices` (merges `DeviceList.json` lan/wlan/wlan5 lists, sorted by last IPv4 octet) | `{mac: device_dict}` with parsed fields |
| `--devices-raw` | `get_devices()` | Unprocessed `DeviceList.json` |
| `--calls` | `Speedport.calls` (dialed + missed + taken, sorted by date) | List with `_parsed_type`, `_parsed_number`, `_parsed_duration`, `_parsed_date`, `_parsed_connection` |
| `--calls-raw` | `get_phone_calls()` → `data/PhoneCalls.json` (auth, referer `phone_call_taken.html`) | Unprocessed phone-calls payload |
| `--phone-handler` | `get_phone_handler()` → `data/IPPhoneHandler.json` (auth) | VoIP handler config |
| `--wps-state` | `wps_remaining` + `get_wps_state()` → `data/WPSStatus.json` (referer `wlan_wps.html`) | `{"wps_remaining": N, "raw": {...}}` |

### Control actions (no JSON output)

| Flag | Library call | Router POST |
|---|---|---|
| `--reconnect` | `Speedport.reconnect()` | `data/Connect.json` `req_connect=reconnect`, referer `con_ipdata.html` |
| `--reboot` | `Speedport.reboot()` | `data/Reboot.json` `reboot_device=true`, referer `restart.html` |
| `--wifi-on` / `--wifi-off` | `wifi_on()` / `wifi_off()` | `data/Modules.json` `use_wlan=1/0`, referer `wlan_basic.html` |
| `--guest-wifi-on` / `--guest-wifi-off` | `wifi_guest_on()` / `wifi_guest_off()` | `data/WLANBasic.json` `wlan_guest_active=1/0`, referer `wlan_guest.html` |
| `--office-wifi-on` / `--office-wifi-off` | `wifi_office_on()` / `wifi_office_off()` | `data/WLANBasic.json` `wlan_office_active=1/0`, referer `wlan_office.html` |
| `--wps-on` | `wps_on()` | `data/WLANAccess.json` `wlan_add=on&wps_key=connect`, referer `wlan_wps.html` |

Note the naming asymmetry preserved from the lib: the CLI puts the qualifier
first (`--guest-wifi-on`) while the lib appends it (`wifi_guest_on`).

### Raw escape hatches (no native NAT support in the lib)

| Flag | Required args | Behavior |
|---|---|---|
| `--api-get` | `--path` | `Connection.get(path, auth, referer)` — raw GET, httoken appended when `--referer` given |
| `--api-post` | `--path` + `--referer` (+ `--data`) | `Connection.post(path, data, referer)` — fetches `httoken` from the referer page, encrypts the payload with the login key |
| `--nat-probe` | — | Parallel `Connection.get(endpoint, auth=True)` over `CANDIDATE_NAT_ENDPOINTS`; per-endpoint `{"ok": true, "data": …}` or `{"ok": false, "error": …}` |

Probed candidates (`CANDIDATE_NAT_ENDPOINTS` in the script):

```text
data/NAT.json  data/NATRules.json  data/PortForwarding.json  data/PortRule.json
data/PortRules.json  data/Firewall.json  data/VirtualServer.json
```

Extend the list and re-run `--nat-probe` when hunting for the port-forwarding
endpoint on a new firmware; then drive it with `--api-get`/`--api-post`.

## Examples

```bash
# Health overview
speedport_cli.py --summary

# Full status + connection data
speedport_cli.py --status | jq '.onlinestatus, .use_wlan'
speedport_cli.py --ip-data | jq '.public_ip_v4'

# LAN/WLAN clients and recent calls
speedport_cli.py --devices | jq 'to_entries | map({mac: .key, name: .value.mdevice_name})'
speedport_cli.py --calls | jq 'sort_by(._parsed_date) | .[-5:]'

# Control
speedport_cli.py --reconnect
speedport_cli.py --guest-wifi-on
speedport_cli.py --reboot

# Discover NAT endpoints, then read one raw
speedport_cli.py --nat-probe | jq 'with_entries(select(.value.ok))'
speedport_cli.py --api-get --path data/NAT.json
speedport_cli.py --api-get --path data/PhoneCalls.json \
  --referer html/content/phone/phone_call_taken.html

# Raw POST (needs the page that issues the httoken)
speedport_cli.py --api-post --path data/Connect.json \
  --referer html/content/internet/con_ipdata.html \
  --data req_connect=reconnect
```

## Exit codes & errors

| Code | Cause | Example stderr |
|---|---|---|
| 0 | Success | — |
| 1 | Runtime failure (login, network, router error, bad `--data`) | `{"error": "Can't login"}` |
| 2 | CLI misuse, fails before connecting | `error: --api-post requires --referer <page> for the httoken` |

## Architecture notes (for maintainers)

- `ACTION_ORDER` is the single source of truth for the CLI surface (`--help`
  order + flag derivation). Each action lives in exactly one of
  `SPEEDPORT_METHODS` (no-arg `Speedport` coroutine), `API_PASSTHROUGHS`
  (zero-arg `SpeedportApi` method), or `CUSTOM_HANDLERS`; a module-level
  assertion fails import on registry drift.
- Naming wart, isolated in `_connection()`: `Speedport.api` → `SpeedportApi`
  → `.api` → `Connection`. `SpeedportApi` has **no** generic `get`/`post`, so
  all raw access goes through the `Connection`.
- `_snapshot()` is the only place touching the lib's private `_status` /
  `_ip_data` (copied, never mutated) — there is no public full-dump accessor.
- Auth is two-layered: `SpeedportApi` methods with `@need_auth` auto-login and
  pause on `DecryptionKeyError`; the script additionally logs in explicitly to
  fail fast on a bad password. `Connection` encrypts POSTs with the login key
  and appends `httoken` scraped from the referer page.
- `fetch_status`/`fetch_summary`/`nat_probe` use `asyncio.gather` for parallel
  fetches.

## Troubleshooting

- `{"error": "Can't login"}` — wrong password, or the router rate-limits
  logins (`login_locked`); wait ~60 s and retry.
- `Paused for 00:MM:SS` (`LoginPausedError`) — the lib backs off after a
  decryption-key mismatch; retry after the shown interval.
- `--api-post` returns an error page — the `--referer` must be the HTML page
  that owns the target endpoint (it issues the `httoken`); check the referers
  in the control-action table above for the pattern.
- `--no-https` — only if the router's TLS endpoint is unreachable; credentials
  then travel in clear text on the LAN.
- Empty/missing fields in `--summary` — by design these degrade to `0`/`false`
  /`""` (see Architecture notes); compare with `--status` raw output.
