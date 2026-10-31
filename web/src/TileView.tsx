// TileView.tsx - draws one tile from its refresh result: a big number (kpi), a table, text, or an ECharts chart.
// The numbers come only from the backend's rows; nothing here computes a figure.
import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { CHART, formatNumber, MetricDef, Tile, TileResult } from "./api";

type Props = { tile: Tile; result?: TileResult; metrics: Record<string, MetricDef>; changed?: boolean };

// Draw one tile: number, table, text or chart.
export default function TileView({ tile, result, metrics, changed }: Props) {
  const unit = metrics[tile.query?.metrics[0] ?? ""]?.unit;
  // Format a number as the tile's style asks.
  const fmt = (v: unknown) => formatNumber(v, tile.style.number_format, unit);
  const names = useMemo(() => Object.fromEntries(Object.entries(metrics).map(([k, m]) => [k, m.name])), [metrics]);
  let body;
  if (tile.kind === "text") body = <div className="text">{tile.text}</div>;
  else if (!result) body = <div className="muted">Not refreshed yet</div>;
  else if (result.error) body = <div className="error">{result.error}</div>;
  else if (!result.rows?.length) body = <div className="muted">No matching data</div>;
  else if (tile.kind === "kpi") {
    const row = result.rows[0];
    const keys = tile.fields.value ? [tile.fields.value] : Object.keys(row).filter((k) => typeof row[k] === "number");
    body = keys.length === 1
      ? <div className="kpi" style={{ color: tile.style.color }}>{fmt(row[keys[0]])}</div>
      : <div className="kpi-list">{keys.map((k) => <div key={k}><span className="muted">{k.replace(/_/g, " ")}</span> <b>{fmt(row[k])}</b></div>)}</div>;
  } else if (tile.kind === "table") {
    const cols = Object.keys(result.rows[0]);
    body = (
      <div className="table-wrap"><table>
        <thead><tr>{cols.map((c) => <th key={c} className={typeof result.rows![0][c] === "number" ? "num" : ""}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
        <tbody>{result.rows.map((r, i) => <tr key={i}>{cols.map((c) => typeof r[c] === "number"
          ? <td key={c} className="num">{fmt(r[c])}</td> : <td key={c}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody>
      </table></div>
    );
  } else body = <Chart tile={tile} rows={result.rows} names={names} />;
  return (
    <div className="tile-inner">
      <div className="tile-title">{tile.title || tile.id}{changed && <span className="badge">updated</span>}</div>
      <div className="tile-body">{body}</div>
      {result?.provenance?.badge && <div className="prov">{result.provenance.badge === "trusted" ? "Trusted" : "Exploratory"}</div>}
    </div>
  );
}

// An ECharts chart that resizes with its grid cell. x = fields.x (or the first text column), y = the number columns.
function Chart({ tile, rows, names }: { tile: Tile; rows: Record<string, any>[]; names: Record<string, string> }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(el.current!);
    const keys = Object.keys(rows[0]);
    const x = tile.fields.x || keys.find((k) => typeof rows[0][k] !== "number") || keys[0];
    const ys = tile.query?.metrics.filter((m) => m in rows[0]) ?? keys.filter((k) => k !== x && typeof rows[0][k] === "number");
    const colors = tile.style.colors || (tile.style.color ? [tile.style.color, ...CHART.colors.slice(1)] : CHART.colors);
    const base = { color: colors, textStyle: { color: CHART.text, fontFamily: "inherit", fontSize: 12 } };
    const axis = CHART.axisStyle();
    if (tile.kind === "pie" || tile.kind === "donut") {
      chart.setOption({ ...base, tooltip: CHART.tooltip, legend: { show: tile.style.show_legend ?? true, bottom: 0, textStyle: base.textStyle },
        series: [{ type: "pie", radius: tile.kind === "donut" ? ["45%", "70%"] : "70%",
          data: rows.map((r) => ({ name: String(r[x]), value: r[ys[0]] })) }] });
    } else {
      const type = tile.kind === "line" || tile.kind === "area" ? "line" : "bar";  // ponytail: waterfall drawn as bars
      chart.setOption({ ...base, tooltip: { ...CHART.tooltip, trigger: "axis" }, legend: { show: tile.style.show_legend ?? ys.length > 1, bottom: 0, textStyle: base.textStyle },
        grid: { left: 8, right: 8, top: 8, bottom: ys.length > 1 ? 32 : 8, containLabel: true },
        xAxis: { type: "category", data: rows.map((r) => String(r[x])), ...axis },
        yAxis: { type: "value", ...axis, axisLabel: { ...axis.axisLabel, formatter: (v: number) => v.toLocaleString("en-IN") } },
        series: ys.map((y) => ({ name: names[y] || y.replace(/_/g, " "), type, data: rows.map((r) => r[y]), stack: tile.kind === "stacked_bar" ? "all" : undefined,
          areaStyle: tile.kind === "area" ? {} : undefined })) });
    }
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el.current!);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [tile, rows, names]);
  return <div ref={el} className="chart" />;
}
