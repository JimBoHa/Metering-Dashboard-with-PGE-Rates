# Remote SQL Server trends

The SQL source connects to **Microsoft SQL Server 2012 or later using a SQL authentication username/password**. It runs on the dashboard host with a pure Python TDS adapter, so a native Microsoft ODBC driver is not required on the Pi. Windows integrated authentication and other database engines are outside this connector's current scope.

## Connect and select points

1. Open **Sources & discovery → Add source → Microsoft SQL Server trends**.
2. Enter a connection name, server DNS name, TCP port, SQL username/password and database. A blank database uses the login's default database. For a named SQL instance, supply its actual TCP host and port.
3. Keep encrypted certificate-verified transport selected. If the server uses an internal CA, paste its PEM certificate chain. The host name must match the server certificate. An explicit unencrypted legacy-LAN option is available for older installations. Browser-to-dashboard transport is separate: use the existing trusted LAN, HTTPS reverse proxy or private tunnel when entering credentials.
4. Save the connection. The application inspects accessible databases, tables/views and columns, then displays the point tree. Passwords are not returned to the browser; leave the password field blank on later edits to retain the saved credential.
5. Expand the tree, or search the whole database by point name/path. Up to 500 points load per page; **Load more points** extends the tree. Selections persist across search/pages for the current source.
6. Select up to 50 points, then choose **Create meters**. Confirm names, usage units, counter/rate/interval semantics, scale and maximum sample gap. Instantaneous rates need their seconds-per-unit basis; interval quantities need their interval duration. Select a historical date range and start the import.
7. Existing SQL meters can be backfilled again with **Import trends**, or **Incremental sync now**. Scheduled sync runs at the source's interval and rereads a configurable recent overlap to pick up late samples.

Dates are start-inclusive and end-exclusive in the workspace's billing timezone. A future end is capped at now. Samples retain their original database timestamps, rather than the import time.

## Metasys detection

The standard analog historian profile detects these accessible objects and columns:

- `dbo.tblPoint`: `PointID`, `PointName`, `PointDataTypeID`.
- `dbo.tblPointSlice`: `PointID`, `PointSliceID`, `IsRawData`.
- `dbo.tblActualValueFloat`: `PointSliceID`, `UTCDateTime`, `ActualValue`.
- Optional `dbo.tblUnitOfMeasure` metadata supplies unit names when available.

The point tree uses the historian point paths. The reader selects analog points (`PointDataTypeID = 1`) and raw slices (`IsRawData = 1`), excluding rollups and digital states. `UTCDateTime` is interpreted as UTC regardless of the custom timestamp setting. Unsupported Metasys layouts can use a custom table/view mapping.

This profile does not assume that every numeric trend is a consumption meter. Select actual energy, flow, volume or other appropriate measurements and confirm their meanings before enabling billing.

## Custom mappings

Use **Table and column mapping** to choose:

| Field | Meaning |
| --- | --- |
| Trend table/view | Source samples in the selected database |
| Point ID column | Identifies the point for each sample |
| Timestamp column and format | SQL date/time, Unix seconds or Unix milliseconds |
| Numeric value column | A numeric SQL type, excluding Boolean/bit |
| Quality column | Optional; only configured good values are usable |
| Point metadata table/view | Optional lookup table joined by point ID |
| Metadata key/name/path/unit columns | Supply names, hierarchy and units |

Without a metadata table, the tree uses distinct point IDs from the trend table. Use a read-only view supplied by the database administrator when a schema needs more complex joins. The dashboard does not execute user-written SQL or create remote views, users, indexes or tables.

Identifiers are checked against the inspected catalog and quoted. Point IDs, searches and date boundaries use bound parameters. This avoids inserting user input into SQL text.

## Time and quality

For custom naive date/time columns, enter the correct IANA timezone, such as `UTC`. A `datetimeoffset` value retains its explicit offset. Local timestamps that are ambiguous or nonexistent during a daylight-saving transition are rejected; use UTC or offset-aware data instead of guessing the intended hour.

Rows without a mapped quality flag default to **unverified**. After checking the source's provenance, an operator can choose to accept these as good. With a mapped quality column, only the configured good values qualify; NULL/bad quality is excluded. A NULL/non-finite value is stored as a fault marker, so the accounting engine cannot bridge that missing interval as if it were a measured zero.

Point identity, units and the mapping signature are rechecked before every page. A changed schema, name, unit or source binding stops the import for review.

## Imports and ongoing sync

Imports run in background jobs separate from the BACnet polling lock. Each SQL read has a timeout, fetches a bounded page, and yields between pages. A maximum of two SQL jobs run at once, with one job per SQL source.

Each page commits samples and a checkpoint to the dashboard's own SQLite database. Cancelling stops at the next page boundary. Failed, cancelled or interrupted jobs can be resumed from the last committed page. A dashboard restart marks active imports interrupted and leaves the checkpoint available for resume.

Identical meter/timestamp readings are deduplicated. Conflicting values in the remote database, or a different existing local value at the same timestamp, stop the job; existing readings are retained. A completed import may update quality for identical readings from the same binding when the source quality policy changes. Historical billing should therefore use archived worksheets and configuration as described in the accounting guide.

Scheduled sync starts from the saved watermark minus the configured overlap. A point without a watermark uses the first-sync lookback. Backfill an older range explicitly when samples arrive outside that overlap. New SQL meters start with electricity pricing disabled so source selection alone does not create charges.

## Credentials and permissions

Use a SQL login granted only the required `SELECT` access. The connector issues SELECT queries and requests read-only connection intent; that intent does **not** replace SQL Server permissions. It does not modify the remote database.

Passwords are encrypted in the dashboard's private `sql_credentials` table. The mode-0600 `sql-credentials.key` file is the local encryption key. Back up **both** this key and the SQLite database; the encryption protects against disclosure of the database alone, not compromise of the dashboard host. Trusted CA files are held under the private `sql-trust/` directory. None of these files belong in GitHub.

Connection and query errors are sanitized so database driver messages cannot echo credentials. The web API returns only a `password_set` flag for saved SQL connections.

References: [Python TDS connection options](https://python-tds.readthedocs.io/en/stable/_modules/pytds.html), [SQL Server catalog metadata](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-columns-transact-sql), [SQL Server database users](https://learn.microsoft.com/en-us/sql/relational-databases/security/authentication-access/create-a-database-user).
