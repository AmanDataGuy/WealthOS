// Report.tsx - a dashboard as a printable report: header, a summary whose every number was checked
// against the data, one section per tile in reading order, and a provenance appendix. "Print / Save PDF" prints
// only the report.
import { useState } from "react";
import { MetricDef, post, Spec, TIMES } from "./api";
import TileView from "./TileView";

type Props = { u: string; spec: Spec; metrics: Record<string, MetricDef>; onClose: () => void; onError: (m: string) => void };

// The printable report view of a dashboard.
export default function Report({ u, spec, metrics, onClose, onError }: Props) {
  const [subtitle, setSubtitle] = useState("");
  const [time, setTime] = useState(spec.global_time);
  const [narrative, setNarrative] = useState(true);
  const [report, setReport] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const dates = report?.sections.find((s: any) => s.provenance?.period)?.provenance.period;  // what "fy" etc. meant

  // Ask the backend to write the checked summary.
  async function generate() {
    setBusy(true);
    try { setReport(await post(`/dashboards/${u}/${spec.id}/report`, { time, narrative, subtitle })); }
    catch (e) { onError((e as Error).message); }
    setBusy(false);
  }

  return (
    <div className="report-page">
      <div className="report-controls row wrap">
        <button onClick={onClose}>← Back to dashboard</button>
        <input className="grow" placeholder="Subtitle, e.g. Acme Pvt Ltd · Monthly MIS" value={subtitle} onChange={(e) => setSubtitle(e.target.value)} />
        <select value={time} onChange={(e) => setTime(e.target.value)}>{[...new Set([spec.global_time, ...TIMES])].map((t) => <option key={t}>{t}</option>)}</select>
        <label className="row"><input type="checkbox" checked={narrative} onChange={(e) => setNarrative(e.target.checked)} /> Write a summary</label>
        <button className="primary" disabled={busy} onClick={generate}>{busy ? "Building…" : report ? "Rebuild" : "Build report"}</button>
        {report && <button onClick={() => window.print()}>Print / Save PDF</button>}
      </div>

      {report && (
        <article className="report">
          <div className="report-head">
            <h1>{report.title}</h1>
            {report.subtitle && <div className="report-sub">{report.subtitle}</div>}
            <div className="muted">Period: {report.period}{dates && ` (${dates.start} to ${dates.end})`} · Generated {new Date(report.generated_at).toLocaleString()}</div>
          </div>
          {report.narrative && (
            <section className="report-summary">
              <h2>Summary</h2>
              <p>{report.narrative}</p>
              {report.verified && <div className="muted">Every number in this summary was checked against the data below.</div>}
            </section>
          )}
          {report.warnings.map((w: string) => <div key={w} className="muted">Note: {w}</div>)}
          <div className="report-grid">
            {report.sections.map((s: any) => {
              const tile = spec.tiles[s.tile_id];
              return tile ? (
                <section key={s.tile_id} className={`report-section ${s.kind === "kpi" ? "small" : ""}`}>
                  <TileView tile={tile} result={s.error ? { error: s.error } : { rows: s.rows, provenance: s.provenance }} metrics={metrics} />
                </section>
              ) : null;
            })}
          </div>
          <section className="report-appendix">
            <h2>How these numbers were computed</h2>
            {report.sections.filter((s: any) => s.provenance).map((s: any) => (
              <div key={s.tile_id}>
                <b>{s.title}</b> <span className="muted">({s.provenance.badge}{s.provenance.period && `, ${s.provenance.period.start} to ${s.provenance.period.end}`}
                  {s.provenance.datasets?.length > 0 && `, from ${s.provenance.datasets.map((d: any) => d.file).join(", ")}`})</span>
                <pre>{s.provenance.sql}</pre>
              </div>
            ))}
          </section>
          <footer className="muted">{report.disclaimer}</footer>
        </article>
      )}
    </div>
  );
}
