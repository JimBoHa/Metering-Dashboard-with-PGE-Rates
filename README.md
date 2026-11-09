# Metering Dashboard with PG&E Rates

A standalone, private-network web application for utility metering, time-of-use electricity estimates and shared bill allocation. It runs independently of any existing building automation or protocol converter software.

## Features

- Meter inventory with editable display names, systems, units, scaling and data quality.
- Read-only BACnet/IP discovery, directed subnet discovery, paged point lists and cancellable full-device inventory. ReadPropertyMultiple has a single-property fallback for older devices.
- Modbus TCP holding/input register reads, HTTP JSON numeric point discovery, manual CSV/JSON readings, and a read-only adapter for an existing `current_samples` SQLite table.
- Remote Microsoft SQL Server trend sources with encrypted saved passwords, searchable point trees, Metasys analog historian detection, configurable table/column mapping, historical backfill and incremental sync. See [SQL trend setup](docs/sql-trends.md).
- Monthly historical billing: assign a PG&E schedule and service options to every month, fetch the official archived versions for those dates, and calculate separate monthly charges with a period total and CSV exports. See [monthly billing](docs/monthly-billing.md).
- A draggable calculation canvas: combine signed inputs into net meters, including supply minus return. Formula cycles, mismatched units and overlapping parent/child billing are rejected.
- Runtime imports from PG&E's official published workbooks for **B-6, B-19 and B-20**, including B-19/B-20 R/S options. Users enter their schedule, voltage, service option and customer class in the web page. Unsupported selections produce an explicit error.
- Pacific-time seasonal TOU calculations, 15-minute demand estimates, daily fixed charges, global/per-meter markup, effective-dated tariff versions and retained source checksums.
- Dated water, chemical, fuel and other bills allocated across selected meters by weighted usage. Allocations reconcile to the original amount to the cent.
- Utility-system categories for electrical, heating hot water, chilled water, domestic hot water, sauna, cafeteria, pool/hot tub and cooling towers. The operator maps physical points to these categories.
- CSV billing worksheets, coverage indicators, a change audit, password sessions and CSRF protection.

## Run locally

Requires Python 3.11 or later. No Node build, CDN, cloud account or browser network access to meters is required.

```bash
git clone https://github.com/JimBoHa/Metering-Dashboard-with-PGE-Rates.git
cd Metering-Dashboard-with-PGE-Rates
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/metering-dashboard
```

Open <http://127.0.0.1:8095>. On first startup the service generates an administrator password in `~/.local/share/metering-dashboard/initial-access.txt` (mode 0600). Sign in with that password, then change it in Settings. The initial file is removed after a password change.

To temporarily open the dashboard without sign-in, start it with `METERING_AUTH_DISABLED=1`. Anyone who can reach it can then view and edit the workspace. The existing administrator password is preserved; remove this environment variable and restart to require sign-in again. CSRF and same-origin checks stay enabled. See [operations](docs/operations.md#temporary-access-without-sign-in) for systemd configuration.

The default listener is loopback. To serve a trusted LAN, choose the interface with `--host`, and use an independent port. Use TLS through a reverse proxy or a private tunnel when needed; this app does not change an existing web server. `--data-dir` or `METERING_DATA_DIR` selects a different **private directory outside the checkout**.

## Install on a Raspberry Pi / Linux host

Clone this repository into `~/metering-dashboard`, then run:

```bash
./scripts/install_user_service.sh
```

This creates this application's own virtual environment and **user** systemd unit. It does not edit another application's repository, service, database, protocol port or serial configuration. The provided unit serves port 8095. For startup without an interactive login, the host's administrator can enable user lingering if it is not already enabled.

```bash
systemctl --user status metering-dashboard
journalctl --user -u metering-dashboard
```

## First configuration

1. In **Sources & discovery**, add a connection. For BACnet, enter the local interface address/prefix with an unused explicit UDP reader port such as `47810`, and the destination device or subnet broadcast with its BACnet port. Port `47808` is deliberately refused as the reader's bind port.
2. Discover devices, then list points or scan a complete device. An optional private CIDR `/24` or smaller probes directed Who-Is requests at five requests per second when a device does not answer a broadcast. Discovery uses only the selected network. It does not register foreign devices, change BBMD tables or open serial ports.
3. Add the desired usage points. Confirm units, counter/rate/interval semantics, scale and sample-gap limits. A rate in gallons per minute uses `gal` with a rate basis of `60`; kW uses `kWh` with a basis of `3600`. A Modbus register address is zero-based.
4. Keep a meter's editable name separate from its expected BACnet object identity. The collector checks the source name and units at startup and every five minutes. A changed identity stops collection until reviewed. A source reporting pending display verification remains unverified until the operator checks its commissioning box.
5. In **Calculation canvas**, add a net meter with signed inputs. For a supply/return pair, use `+1` and `-1` and matching units. Drag cards to arrange the diagram, or use arrow keys. Positions and formulas persist.
6. In **PG&E rates**, enter the exact schedule and option from the account's bill. Validate and fetch. Select only the intended kWh meter(s) for electrical pricing; do not price a net meter and its own inputs simultaneously. Tenant sub-meters default to energy-only pricing. Enable account-level demand/fixed charges only for the appropriate service meter, or enter those shared charges once as an allocated bill.
7. Enter other bills with their bill date and **start-inclusive, end-exclusive** usage dates. Select meters with the same unit and adjust positive allocation weights as needed. A net meter and its inputs cannot share the same allocation pool.

Remote SQL users can enter their server and credentials in Sources & discovery, then select trend points from the discovered tree. No SQL server address or login is embedded in the source.

No initial site configuration or tariff is provided. See [accounting rules](docs/accounting.md) and [operations](docs/operations.md) before using estimates in a billing process.

## PG&E rate updates

The importer discovers current workbook links and effective dates from the [official electric-rate index](https://www.pge.com/tariffs/en/rate-information/electric-rates.html). It downloads on the server, validates required energy/demand/customer components, and retains each source workbook privately. It checks daily after a schedule is configured. An import error leaves existing versions intact.

To refresh the currently configured tariff from a scheduler:

```bash
.venv/bin/python scripts/fetch_rates.py
```

The published summary workbooks contain **bundled** prices. Delivery-only / CCA service, unsupported schedules, PDP, standby rates, special riders, demand ratchets, account taxes and other account-specific terms require verified component importers or separate invoice entries. The application refuses to substitute bundled prices for delivery-only prices. Downloading a workbook does not establish account eligibility.

Tariffs imported now are not fabricated for periods before their effective date. Historical official workbooks are discoverable in the source index; this release automatically imports the currently effective version and retains subsequent imported versions. Import verified historical data separately if historical billing is required.

## Privacy and publication

Runtime state lives outside the source checkout in a mode-0700 directory. It contains meter addresses/names, source credentials, readings, invoices, authentication data and downloaded tariffs. These are **not public repository assets**. The repository contains no real meter names, private network addresses, account details or PG&E price tables. All numeric test prices are explicitly synthetic.

Before publishing, run `python scripts/check_public_source.py` and inspect `git diff --cached`. The scanner catches common private-address and secret signatures and rejects runtime/binary artifacts; it supplements review and cannot prove that arbitrary content contains no sensitive information.

## Verify

```bash
.venv/bin/python -m pytest -q
node --check metering/static/app.js
node --check metering/static/sql.js
.venv/bin/python scripts/check_public_source.py
```

Tests cover TOU transitions, daylight-saving days, counter resets and rollovers, quality/gap exclusion, rate integration, net-meter coverage, exact-cent allocation, auth/CSRF, CSV formula escaping, configuration conflicts and read-only source access. Tests generate synthetic workbooks and do not need a building network or live PG&E connection.
