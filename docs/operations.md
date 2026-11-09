# Operations

The web service, collector and tariff updater run in one process. Run a single worker: BACnet owns one dedicated UDP reader socket per distinct configured source. Meter polling and discovery reads are serialized, with a small inter-request gap. Full inventory scans yield between pages so collection can continue.

The dashboard has no BACnet write, Modbus write, serial master or building-control endpoint. It opens an existing automation SQLite database with `mode=ro` and `query_only=ON`, reading only `current_samples`. It maintains its own database and does not run migrations against source databases. Credentials for independent HTTP sources belong in private source settings.

The data directory defaults to `~/.local/share/metering-dashboard`:

| Path | Purpose |
| --- | --- |
| `metering.sqlite3` and WAL/SHM companions | Configuration, readings, invoices, tariff versions and audit |
| `initial-access.txt` | Initial password, removed after password change |
| `sql-credentials.key` | Private SQL password encryption key; back up with the database |
| `sql-trust/` | Private trusted CA certificate files |
| `tariff-sources/` | Downloaded official workbook bytes by SHA-256 |

Back up the directory with a SQLite-aware snapshot or stop **only this dashboard's service** before copying it. Readings are retained without automatic deletion. Check disk use and plan retention/archival appropriate to the site's meter count. Source databases are not backed up or modified by this application.

Configuration saves use a revision token to reject stale browser edits. Sessions expire after eight hours and are held in memory; restarting the process signs users out. Password changes invalidate all sessions. Login attempts are rate-limited. The app intentionally has a single administrator role in this release.

For CSV/JSON reading imports, create physical meters first. JSON rows contain `meter_id`, Unix-second `timestamp`, numeric `value`, and optional `quality`/`error`. CSV supports `meter_id,timestamp,value,quality`; timestamps may also be ISO 8601 with an explicit timezone. Up to 10,000 rows / 2 MB are accepted per request. Future timestamps and virtual-meter readings are rejected.

BACnet discovery lists analog input/output/value, accumulator, pulse-converter and numeric value objects. Only add measurements whose semantics match their configured mode. A temperature, instantaneous power value, cumulative energy counter, diagnostic register and rolling total have different meanings even when all are numeric. Modbus discovery probes an explicit register block and does not infer a manufacturer's register map.

PG&E downloads use an HTTPS host allowlist, redirect checks and a file-size limit. No private meter configuration is sent to PG&E. Parser failures are visible in the Rates page. The current automatic importer supports modern commercial/industrial workbook layouts; it fails closed when required components or sheet selection are ambiguous.

To stop or remove this dashboard, use its own user service:

```bash
systemctl --user stop metering-dashboard
systemctl --user disable metering-dashboard
```

Keep private state if the service may be resumed. These commands do not target any other automation service.

Remote SQL sources use separate worker threads and durable import jobs. Read [SQL trend setup](sql-trends.md) for supported SQL Server authentication, point discovery, custom schema mappings, quality policy and resumable backfills.
