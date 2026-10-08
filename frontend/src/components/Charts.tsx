import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";

/** One position on the x axis with the values of the two series drawn there. */
export interface Point { key: string; axis: string; title: string; one: number; two: number }
type Names = [string, string];
const plain = (v: number) => v.toLocaleString();

export const shortDay = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
export const weekday = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" });

/** The element's current width, so a chart is drawn at real pixels and its text never stretches. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** A "nice" top for the y axis that divides into four whole steps: 0 -> 4, 7 -> 8, 13 -> 16, 130 -> 160. */
export function niceMax(max: number) {
  if (max <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(max));
  const nice = [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 6, 8, 10].map((m) => m * step).find((v) => v >= max) ?? max;
  return Math.ceil(nice / 4) * 4; // four equal steps, so every grid line is a whole number
}

export function Legend({ names, boxes }: { names: Names; boxes?: boolean }) {
  return (
    <div className="legend">
      <span><i className={`key ${boxes ? "box" : ""}`} /> {names[0]}</span>
      <span><i className={`key two ${boxes ? "box" : ""}`} /> {names[1]}</span>
    </div>
  );
}

function Tip({ point, names, x, width, format, boxes }: { point: Point; names: Names; x: number; width: number; format: (v: number) => string; boxes?: boolean }) {
  return (
    <div className="chart-tip" style={{ left: Math.min(Math.max(x, 90), Math.max(90, width - 90)) }} role="status">
      <div className="chart-tip-title">{point.title}</div>
      <div><span className={`key ${boxes ? "box" : ""}`} /> <strong>{format(point.one)}</strong> {names[0].toLowerCase()}</div>
      <div><span className={`key two ${boxes ? "box" : ""}`} /> <strong>{format(point.two)}</strong> {names[1].toLowerCase()}</div>
    </div>
  );
}

function Grid({ max, left, w, y, tick }: { max: number; left: number; w: number; y: (v: number) => number; tick: (v: number) => string }) {
  return (
    <>
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <g key={f}>
          <line x1={left} x2={left + w} y1={y(max * f)} y2={y(max * f)} className="chart-grid" />
          <text x={left - 8} y={y(max * f) + 4} textAnchor="end" className="chart-tick">{tick(max * f)}</text>
        </g>
      ))}
    </>
  );
}

/** Two lines over time, a crosshair that reads out both, and (as children) the same numbers as a table. */
export function TwoLines({ points, names, label, format = plain, children }: {
  points: Point[]; names: Names; label: string; format?: (v: number) => string; children?: ReactNode;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const H = 240, left = 40, right = 14, top = 12, bottom = 28;
  const w = Math.max(0, width - left - right), h = H - top - bottom;
  const max = niceMax(Math.max(0, ...points.map((p) => Math.max(p.one, p.two))));
  const x = (i: number) => left + (points.length > 1 ? (i * w) / (points.length - 1) : w / 2);
  const y = (v: number) => top + h - (v / max) * h;
  const line = (key: "one" | "two") => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const every = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(w / 64))));
  const move = (e: PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setAt(Math.min(points.length - 1, Math.max(0, Math.round(((px - left) / Math.max(1, w)) * (points.length - 1)))));
  };
  const point = at === null ? null : points[at];

  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={`${label} The same numbers are in the table below.`} onPointerMove={move} onPointerLeave={() => setAt(null)}>
          <Grid max={max} left={left} w={w} y={y} tick={(v) => String(Math.round(v))} />
          {/* every few days a date, and always the last one; a date too close to the last one is left out so they never overlap */}
          {points.map((p, i) => (i === points.length - 1 || (i % every === 0 && x(points.length - 1) - x(i) >= 56)) && (
            <text key={p.key} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"} className="chart-tick">{p.axis}</text>
          ))}
          {at !== null && <line x1={x(at)} x2={x(at)} y1={top} y2={top + h} className="chart-cross" />}
          <path d={line("two")} className="chart-line two" />
          <path d={line("one")} className="chart-line one" />
          {at !== null && point && (
            <>
              <circle cx={x(at)} cy={y(point.two)} r={5} className="chart-dot two" />
              <circle cx={x(at)} cy={y(point.one)} r={5} className="chart-dot one" />
            </>
          )}
        </svg>
      )}
      {point && at !== null && <Tip point={point} names={names} x={x(at)} width={width} format={format} />}
      {children && <details className="chart-table"><summary>Show the numbers</summary>{children}</details>}
    </div>
  );
}

/** Pairs of columns for a few periods (months), with the values read out on hover and in a table. */
export function Columns({ points, names, label, format = plain, tick = plain, children }: {
  points: Point[]; names: Names; label: string; format?: (v: number) => string; tick?: (v: number) => string; children?: ReactNode;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const H = 240, left = 52, right = 8, top = 12, bottom = 28;
  const w = Math.max(0, width - left - right), h = H - top - bottom;
  const max = niceMax(Math.max(0, ...points.map((p) => Math.max(p.one, p.two))));
  const band = points.length ? w / points.length : w;
  const bar = Math.max(4, Math.min(24, (band - 14) / 2));
  const y = (v: number) => top + h - (v / max) * h;
  const centre = (i: number) => left + band * (i + 0.5);
  // A column with a rounded top, standing on the baseline. An amount of 0 draws nothing.
  const column = (cx: number, value: number, cls: string) => {
    if (value <= 0) return null;
    const yy = Math.min(y(value), top + h - 2), r = Math.min(4, bar / 2, top + h - yy);
    return <path className={`chart-col ${cls}`} d={`M${cx},${top + h} V${yy + r} Q${cx},${yy} ${cx + r},${yy} H${cx + bar - r} Q${cx + bar},${yy} ${cx + bar},${yy + r} V${top + h} Z`} />;
  };
  const point = at === null ? null : points[at];

  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label={`${label} The same numbers are in the table below.`} onPointerLeave={() => setAt(null)}>
          <Grid max={max} left={left} w={w} y={y} tick={tick} />
          {points.map((p, i) => (
            <g key={p.key} onPointerEnter={() => setAt(i)} onPointerDown={() => setAt(i)}>
              <rect x={left + band * i} y={top} width={band} height={h + bottom} className={`chart-hit ${at === i ? "on" : ""}`} />
              {column(centre(i) - bar - 1, p.one, "one")}
              {column(centre(i) + 1, p.two, "two")}
              <text x={centre(i)} y={H - 8} textAnchor="middle" className="chart-tick">{p.axis}</text>
            </g>
          ))}
        </svg>
      )}
      {point && at !== null && <Tip point={point} names={names} x={centre(at)} width={width} format={format} boxes />}
      {children && <details className="chart-table"><summary>Show the numbers</summary>{children}</details>}
    </div>
  );
}

/** Horizontal bars for a handful of named amounts. Every value is written out, so nothing depends on hovering. */
export function Bars({ rows, unit, empty }: { rows: { label: string; value: number; note?: string }[]; unit: string; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.some((r) => r.value)) return <p className="muted" style={{ margin: 0 }}>{empty}</p>;
  return (
    <div className="bars" role="list">
      {rows.map((r) => (
        <div className="bar-row" role="listitem" key={r.label} title={`${r.label}: ${r.value} ${unit}${r.note ? ` · ${r.note}` : ""}`}>
          <div className="bar-head">
            <span>{r.label}</span>
            <strong>{r.value.toLocaleString()}</strong>
          </div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(100 * r.value) / max}%` }} /></div>
          {r.note && <div className="small muted">{r.note}</div>}
        </div>
      ))}
    </div>
  );
}
