// One consumer date format for metric panels and dashboard panels.
// Dates read "Oct 11, 2026". A fiscal quarter reads "Q2 2026".
// Quarterly period ends keep the quarter as the label and the full date as the tooltip.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatConsumerDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? "").trim());
  if (!match) return "";
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return "";
  return `${MONTHS[month - 1]} ${day}, ${match[1]}`;
}

export function formatQuarterLabel(value) {
  const match = /^(\d{4})Q([1-4])$/.exec(String(value ?? "").trim());
  if (!match) return "";
  return `Q${match[2]} ${match[1]}`;
}

export function readablePanelText(value) {
  return String(value ?? "")
    .replace(/(\d{4})-(\d{2})-(\d{2})(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?/g, (full, year, month, day) => (
      formatConsumerDate(`${year}-${month}-${day}`) || full
    ))
    .replace(/\b(\d{4})Q([1-4])\b/g, (_, year, quarter) => `Q${quarter} ${year}`);
}

export function formatPanelStamp(stamp, periodId = "") {
  const raw = String(stamp ?? "").trim();
  if (!raw) return "";
  const date = formatConsumerDate(raw);
  if (!date) return readablePanelText(raw);
  if (periodId === "1D" && raw.includes("T")) {
    const time = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw);
    if (time) return `${date} ${time[2]}`;
  }
  return date;
}

/** Visible quarter (or date) plus the period-end date for a tooltip. */
export function formatMetricPeriod(period, periodEnd = "") {
  const label = formatQuarterLabel(period);
  const title = formatConsumerDate(periodEnd);
  if (label) return { label, title };
  const fallback = readablePanelText(String(period || "").trim());
  if (fallback) return { label: fallback, title };
  if (title) return { label: title, title: "" };
  return { label: "", title: "" };
}

export function metricLatestCopy(metric) {
  const period = formatMetricPeriod(metric?.latestPeriod, metric?.periodEnd);
  if (metric?.unavailable) return { lead: "Unavailable. ", title: period.title || "" };
  return { lead: `Latest ${period.label ? `${period.label} ` : ""}`, title: period.title || "" };
}

export function metricSourceBits(metric) {
  const published = metric?.published
    ? (formatConsumerDate(metric.published) || readablePanelText(metric.published))
    : "";
  return [metric?.sourceTitle, published, metric?.confidence].filter(Boolean);
}

export function metricBarTitle(point, valueText = "", revisedNote = "") {
  const shown = formatMetricPeriod(point?.fiscal_period, point?.period_end);
  const label = shown.label || readablePanelText(point?.fiscal_period || "");
  if (!point?.reported) {
    const head = `${label || "Quarter"} not reported`;
    return shown.title ? `${head} · ${shown.title}` : head;
  }
  const head = [label, valueText, revisedNote].filter(Boolean).join(" ");
  return shown.title ? `${head} · ${shown.title}` : head;
}

export function tableCellCopy(key, value, row) {
  if (value == null || value === "") return { text: "—", title: "" };
  const raw = String(value);
  const quarter = formatQuarterLabel(row?.fiscal_period || row?.fiscal_quarter || "");
  const dated = formatConsumerDate(raw);
  const periodEnd = /period[_ ]?end|as[_ ]?of/i.test(String(key || ""));
  if (quarter && periodEnd && dated) return { text: quarter, title: dated };
  return { text: readablePanelText(raw), title: "" };
}

export function companyMetricLine(card) {
  const period = formatMetricPeriod(card?.latestPeriod, card?.periodEnd);
  const value = card?.emptyPeriod ? "No quarters in this period" : readablePanelText(card?.latest);
  const suffix = !card?.emptyPeriod && period.label ? ` · ${period.label}` : "";
  return { text: `${card?.name || ""} · ${value}${suffix}`, title: period.title || "" };
}

/** Every user-visible string a company metric card, list row, or call-note card would show. */
export function renderedMetricPanelText(panels) {
  const lines = ["Company metrics", "Operating KPIs", "Fundamentals", "Quarterly notes"];
  const cards = [...(panels?.operating || []), ...(panels?.fundamentals || []), ...(panels?.guidance || [])];
  cards.forEach((card) => {
    lines.push(card.name || "", card.detailName || "");
    if (card.unit && !card.unavailable && card.panelType !== "text") lines.push(card.unit);
    if (card.emptyPeriod) lines.push("No quarters in this period.");
    else if (card.panelType === "table") {
      (card.rows || []).forEach((row) => {
        if (!row || typeof row !== "object" || Array.isArray(row)) return;
        Object.keys(row).forEach((key) => {
          const cell = tableCellCopy(key, row[key], row);
          lines.push(cell.text, cell.title);
        });
      });
    } else if (card.panelType === "text") {
      lines.push(readablePanelText(card.text || card.definition || "Not reported yet."));
    } else if (card.panelType === "kpi") {
      lines.push(card.unavailable ? "not reported" : readablePanelText(card.latest));
    } else if (card.series?.length) {
      card.series.forEach((point) => {
        const shown = formatMetricPeriod(point.fiscal_period, point.period_end);
        lines.push(shown.label, shown.title, metricBarTitle(point, String(point.value ?? ""), point.revised ? "revised measure" : ""));
      });
    }
    if (card.revisionNote) lines.push(readablePanelText(card.revisionNote));
    if (card.panelType !== "text") {
      const latest = metricLatestCopy(card);
      lines.push(latest.lead, latest.title, card.unavailable ? "not reported" : readablePanelText(card.latest));
      if (card.sourceTitle) lines.push(metricSourceBits(card).join(" · "));
    }
    if (!card.edgar) {
      const row = companyMetricLine(card);
      lines.push(row.text, row.title);
    }
  });
  return lines.filter(Boolean).join("\n");
}
