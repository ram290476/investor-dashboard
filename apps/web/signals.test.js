import assert from "node:assert/strict";
import test from "node:test";
import { correlationDrift, driverChange, driverRows, driverTrend, driverValue, number, pressureSummary } from "./signals.js";

test("missing, nonfinite, and boolean values are not neutral observations", () => {
  for (const value of [null, undefined, NaN, Infinity, true, ""]) assert.equal(number(value), null);
  assert.equal(pressureSummary({trend: {rows: [{series_id: "DGS10", net_pressure: null}]}}).label, "Unavailable");
  assert.equal(pressureSummary({trend: {rows: [{series_id: "DGS10", net_pressure: 0, effect: 0}]}}).label, "Neutral");
});

test("driver ranking retains warm-up observations and excludes the price pill", () => {
  const rows = [{series_id: "PX:TSLA", effect: 100}, {series_id: "VIXCLS", value: 17, effect: null},
    {series_id: "DGS10", value: 4, effect: -0.3}, {series_id: "DGS2", effect: 0.7}];
  const result = driverRows({trend: {rows}});
  assert.deepEqual(result.map(row => row.series_id), ["DGS2", "DGS10", "VIXCLS"]);
  assert.equal(rows[0].series_id, "PX:TSLA");
});

test("units and z-score trend labels do not conflate rate levels, bp, or percent changes", () => {
  assert.equal(driverValue({value: 4.18, unit: "%"}), "4.18%");
  assert.equal(driverChange({change_1m_display: -15, change_unit: "bp"}), "-15bp");
  assert.equal(driverChange({change_1m_display: 1.2, change_unit: "%"}), "+1.20%");
  assert.equal(driverChange({change_1m_display: null}), "--");
  assert.equal(driverTrend({trend_state: "down", days_in_state: 17}), "Downtrend - 17d");
});

test("pressure gauge is bounded and describes incomplete driver coverage", () => {
  const summary = pressureSummary({trend: {rows: [
    {series_id: "DGS10", effect: 0.8, net_pressure: 0.26},
    {series_id: "CPI_YOY", effect: null, net_pressure: 0.26},
  ]}});
  assert.equal(summary.linked, 1);
  assert.equal(summary.total, 2);
  assert.equal(summary.width, 13);
  assert.equal(summary.label, "Tailwind");
});

test("correlation drift requires a full month of finite history and identifies sign flips", () => {
  assert.equal(correlationDrift({corr_90d: 0.5}, []), null);
  const history = Array.from({length: 22}, () => ({corr_90d: -0.2}));
  assert.deepEqual(correlationDrift({corr_90d: 0.5}, history), {delta: 0.7, signFlip: true, stronger: true});
});
