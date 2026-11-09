# Live totals and a reusable feeder example

The calculation canvas can show instantaneous readings and recorded month-to-date usage. Each input and total remains editable through its **…** button. Changes to labels, pinning and card positions do not alter the source devices.

## Example: three electrical feeders

Use six physical inputs and two calculated totals. Give the inputs names that identify their feeder and measurement, such as **Feeder A power** and **Feeder A energy**. Keep all actual addresses, device identities and site labels in the private web configuration.

| Input | Source units | Reading type | Usage unit | Rate basis | Live display |
| --- | --- | --- | --- | --- | --- |
| Each feeder's real power | kilowatts | Instantaneous rate | kWh | 3600 seconds | kW |
| Each feeder's energy register | kilowatt-hours | Cumulative counter | kWh | Not used for counters | kWh register |

A kW sample is a power rate. Integrating it for one hour gives kWh. The **Usage unit** field therefore stays **kWh** for a kW input; its live display automatically uses **kW**. If a source reports watts or watt-hours, configure the appropriate multiplier before using the quantities together.

1. Add or discover the three power points and the three cumulative energy points. Verify their expected BACnet names and units, scaling and source-quality flags. Use live real-power/energy objects, not trip-buffer, configuration or rolling-history values.
2. Add **Total power** as a calculated meter with three signed inputs: `+1 × Feeder A power`, `+1 × Feeder B power`, `+1 × Feeder C power`. Choose **Overview display → Pin live reading**.
3. Add **Total energy** as a calculated meter with the corresponding three energy-counter inputs, each with multiplier `+1`. Choose **Overview display → Pin month-to-date usage**.
4. Open **Calculation canvas** and select **Pinned totals & their inputs**. Drag the inputs and their totals into readable groups. **All meters** shows the rest of the workspace. Open a total's **…** button to review or edit its formula.
5. For a net-flow example, create a separate calculated meter with `+1 × Supply` and `−1 × Return`. Inputs must share the same accumulated quantity unit. A live formula also needs matching measurement types; it cannot add a kW rate to a lifetime kWh register.

The pinned total cards appear in Overview and Calculation canvas. Their display refreshes every 15 seconds while the page is visible; actual new measurements arrive at the configured polling interval. Source timestamps are shown. Moving a card preserves its formula and source mapping.

## Monthly totals and data quality

**Recorded this month** is the sum of counter changes over the current calendar month in the workspace timezone. It is not the sum of lifetime registers. Counter or power history needs samples bounding the desired intervals. A total uses only the common observed intervals of every nonzero input and shows its coverage and first observed interval. Missing early-month data is not reconstructed from today's register.

Operational displays may show unverified samples as a clearly marked provisional preview. The default billing calculation continues to exclude them. Faulted, offline, stale and missing readings cannot produce a complete live total; the display can show an explicitly labeled available subtotal. An unverified source must not be marked commissioned until its display, identity, units and scaling have actually been checked.

Power integration and cumulative-counter deltas are separate ways to estimate energy. Do not add the energy derived from a power point to the energy from that same feeder's counter. Similarly, avoid pricing both a total and its own inputs. Pinning a total does not automatically enable electricity billing or duplicate account charges.

## PG&E units

For the supported bundled workbooks, energy rates are **USD/kWh**, standard/base and R demand rates are **USD/kW per billing month**, and customer charges are **USD per meter per day**. Option S includes daily peak/partial-peak demand rates in **USD/kW/day** as well as monthly demand components. The Rates page shows each imported demand component, basis and rate precision. Bundled totals include generation and delivery; their generation components are not added a second time.

Billing demand is the applicable tariff's interval-average maximum, not simply the newest instantaneous kW value. B-19 generally uses 15-minute averages, with qualifications described in the [official B-19 tariff](https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_B-19.pdf). Account-level demand and fixed charges remain an explicit billing setting.
