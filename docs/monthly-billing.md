# Monthly historical billing

Use **Monthly billing** to calculate a year of historical meter data as twelve separate bills. The **Through month** field is inclusive. Each bill covers a calendar month in `America/Los_Angeles`, including daylight-saving changes and leap years. Up to 36 months can be calculated at once. This page does not use the current rate schedule as a fallback for an unassigned historical month.

1. Import readings using SQL trend backfill, CSV or JSON. Match each meter's unit, counter/rate/interval mode, scale, quality and maximum gap to the source. Counter and power trends need samples bounding the month: include a sample before its start and at or after its exclusive end. Missing samples are not fabricated.
2. In **Meter inventory**, select **Price electric usage** for the intended kWh billing meters. Include account demand/fixed charges only on the appropriate account meter. Avoid charging both a net meter and its inputs for the same electricity. Other utility costs use the existing **Bills & allocation** tables.
3. In **Monthly billing**, choose the first and last months, then **Load months**. Enter the PG&E schedule from each month's bill. Expand **Service details** to choose voltage, bundled/delivery service, customer class and phase. **Copy first month to all** provides a starting point; edit months where the schedule or option changed. **Save monthly schedules** retains these assignments in private runtime configuration.
4. Choose **Fetch historical PG&E rates**. The service reads PG&E's [current and historic electric rate index](https://www.pge.com/tariffs/en/rate-information/electric-rates.html), downloads each applicable archived workbook and retains verified effective dates, source checksum, worksheet and component-cell provenance. One workbook can cover several months. A PG&E price revision inside a month is applied to energy on its actual effective date. Retrying an interrupted import reuses retained historical workbooks after checking their checksums.
5. Choose **Calculate monthly bills**. The table separates energy, demand, daily fixed charges, allocated bills, markup and total. **Details** shows each meter's quantity, coverage and charges. Use **Monthly CSV** or **Meter details CSV** to export the results.

For example, import November through the following October, assign the applicable schedule to each of those twelve rows and calculate. Each month uses its assigned service selection and the PG&E prices effective during that month. Monthly demand maxima reset each month; daily customer charges use that month's actual calendar-day count. Each meter's energy, demand and fixed components are rounded to cents before summing base cost and applying markup, so the displayed components reconcile. The period total sums the separately calculated monthly bills.

## Coverage and supported charges

Automatic workbook parsing supports bundled B-6, B-19 and B-20, including supported R/S options. An unsupported schedule or delivery-only/CCA selection cannot silently receive a bundled substitute. Historical workbooks with unrecognized layouts fail with an import error. Only rates fetched at runtime are retained; no actual PG&E prices are distributed with the source.

An unassigned month, missing effective rate version or incomplete good-quality trend history leaves the month **Provisional**. The displayed amount is the priced subtotal. It is not treated as a zero-price complete bill. Overlapping partial shared-invoice periods are identified for review rather than silently prorated. Account-level demand/fixed charges require complete monthly readings and one applicable tariff version; if a tariff revision occurs inside the month, energy is split correctly and the account charges remain provisional pending invoice reconciliation. Taxes, special riders and account-specific adjustments can be entered as dated shared bills.

Tenant sub-meters default to energy-only pricing, with shared account charges allocated once through the bill table. Do not also enter an account charge as a shared bill if that same charge is already priced directly on an account meter included in the billed total.

Saved assignments override the workspace's current rate selection for their historical month, including ordinary Overview reports. The monthly page requires explicit monthly assignments. The daily refresh follows the current month's assignment when one is present, otherwise the workspace selection. It does not alter historical assignments or backdate current prices. When PG&E replaces a previously open-ended “Present” workbook, its published closing date is reconciled so the old prices cannot fill a later gap.

Reports recalculate from retained readings and tariffs using the current meter scaling, formulas and markup. CSV exports also recalculate from saved configuration. These are estimates with provenance, not immutable finalized invoices; archive the configuration and exports when finalizing a bill. Calendar-month billing is distinct from a utility meter-read cycle that starts on another day.

## API

- `GET /api/monthly-report?start_month=YYYY-MM&end_month=YYYY-MM`: end month is exclusive; returns each monthly bill, coverage, selected schedule, tariff IDs and aggregate totals.
- `GET /api/monthly-report.csv?...&detail=summary` or `detail=meters`: monthly or per-meter CSV, with formula-injection escaping.
- `GET /api/rates/monthly-coverage?...`: saved assignments and effective-date coverage without calculating readings.
- `POST /api/rates/history`: JSON `start_month`, exclusive `end_month` and current configuration `revision`; starts a background import using the saved monthly assignments.
- `GET /api/rates/history`: progress and import errors. Restarted imports are marked interrupted and can be retried.

Mutations retain the same CSRF and origin checks as the rest of the application. Meter addresses, credentials, assignments, downloaded workbooks and prices remain outside the public checkout.
