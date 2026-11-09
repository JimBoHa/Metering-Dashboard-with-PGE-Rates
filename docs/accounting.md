# Accounting rules and practical limits

## Quantities and evidence

A cumulative counter contributes its nonnegative delta between adjacent good samples, with uniform interpolation at report/TOU boundaries. A decrease is excluded as a reset unless an explicit rollover modulus is configured. A rate input uses trapezoidal integration divided by its configured seconds-per-unit basis. An interval quantity applies to the configured interval ending at its timestamp; overlapping intervals are excluded. Meter/timestamp pairs are idempotent. Network readings also carry a source-configuration fingerprint; adjacent readings from different source configurations are not joined into a consumption interval.

Unverified, faulted and offline readings do not contribute usage. A gap longer than the meter's limit is not bridged. Coverage is the fraction of the requested time span for which quantities can be calculated. A complete allocation needs full, nonnegative coverage for all selected meters. The application cannot recreate consumption during outages or distinguish a legitimate reset from a rollover without meter configuration.

Virtual meters combine signed quantities over the intersection of their inputs' observed intervals. They do not silently treat a missing input as zero. Formula inputs must share a quantity unit. To combine unlike physical units, first configure an explicit conversion scale and confirm its meaning. The canvas supports linear signed combinations; it does not infer thermal energy from flow and temperature measurements.

Names are editable display labels. BACnet source object names/units are pinned separately and rechecked every five minutes. This detects common source mapping changes, but it cannot guarantee calibration, a meter's physical installation, or identity changes between checks. A source's own status flags and any pending-verification marker remain part of its data quality.

## Electricity

The supported B schedules use June–September summer and October–May winter, every day including weekends and holidays. Peak is 16:00–21:00. Summer B-19/B-20 also have partial peak 14:00–16:00 and 21:00–23:00. March–May super off-peak is 09:00–14:00. Time windows are half-open and use PG&E’s `America/Los_Angeles` timezone, including daylight-saving transitions. Report and shared-bill boundaries use the workspace timezone; changing it does not shift PG&E’s price windows.

Definitions: [B-6](https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_B-6.pdf), [B-19](https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_B-19.pdf), [B-20](https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_B-20.pdf). Prices come from runtime workbooks, never from these code definitions.

Energy is split at 15-minute boundaries and matched to the applicable imported effective-dated selection. Counter interpolation produces an estimate within each sample interval; coarse counters do not become revenue-grade interval meters. Counter-derived 15-minute demand can differ from the utility meter's demand register.

Tenant sub-meters default to energy-only pricing to avoid charging each tenant an entire utility account fee. Account-level demand and fixed charges are an explicit meter setting. Shared account costs can instead be entered once as a bill and allocated to the intended tenant meters.

When account-level charges are selected, demand and daily customer charges are calculated only for a complete local calendar month with complete data and one imported tariff version. Other periods show energy estimates with explicit notes. Base/R demand uses monthly maxima. Option S peak/partial demand uses daily maxima and retains both monthly all-hours components, including the solar-hour exclusion. A tariff change within a month excludes demand/fixed charges pending invoice reconciliation. Cross-season non-calendar utility billing cycles and other account adjustments are outside the automatic demand calculation.

Imported source versions retain their effective ranges and checksum. Changing a meter scale, formula, billing selection or markup recalculates reports using the current configuration. Archive configuration and exported worksheets when finalizing a bill. Reports are estimates; this application does not issue or settle payments.

The [monthly historical billing workflow](monthly-billing.md) saves an explicit rate selection for each calendar month and imports archived effective versions. It calculates each month's demand and fixed charges independently, then sums the monthly bills. Missing schedules or tariff coverage remain provisional. Monthly boundaries use PG&E Pacific time even if Overview uses another workspace timezone.

## Shared bills and markup

For an invoice amount C, usage U and positive weight W, each share before rounding is:

`C × (U_i × W_i) / sum(U_j × W_j)`

All selected meters must use the same unit. An incomplete or zero denominator leaves the invoice unallocated with a reason. Shares are floored to cents, and remaining cents go to the largest fractional remainders. Shares sum to the original invoice to the cent.

A report includes an invoice only when the invoice's complete usage period lies within the selected report range. Partially overlapping invoice periods are identified and excluded instead of being silently prorated. Allocated bills are additional cost inputs; configure the pool to match the actual supply and avoid entering the same cost twice.

Global markup applies to each meter's priced energy and allocated costs unless a per-meter override is provided. Base cost, markup and total are displayed separately. The per-meter markup is rounded to cents. Electrical tariff pricing is opt-in per meter; expense allocation is controlled by each invoice's own meter table.
