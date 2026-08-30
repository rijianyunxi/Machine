import { useEffect, useRef, useState } from "react";

/* SVG 堆叠柱状图（移植旧 renderBars/drawBarsIn，视觉一致）。
 * data: { label, value } 或堆叠 { label, value, segments: [{ v, c, name }] }。
 * height: 数字或 "fill"（撑满容器高度）。重复渲染安全。 */

export interface BarSegment {
  v: number;
  c: string;
  name: string;
}

export interface BarData {
  label: string;
  value: number;
  segments?: BarSegment[];
}

export function BarChart({
  data,
  height = 170,
}: {
  data: BarData[];
  height?: number | "fill";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({
    w: 0,
    h: typeof height === "number" ? height : 170,
  });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const hh = height === "fill" ? Math.max(el.clientHeight, 170) : height;
      setSize({ w: el.clientWidth, h: hh });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [height]);

  const { w, h } = size;
  const anyValue = data.some((d) => d.value);

  const wrapStyle: React.CSSProperties =
    height === "fill" ? { height: "100%", width: "100%" } : { height };

  if (!w || !data.length || !anyValue) {
    return (
      <div ref={ref} style={wrapStyle}>
        {data.length ? (
          <div className="empty">
            <p>暂无告警数据</p>
          </div>
        ) : null}
      </div>
    );
  }

  const pad = { l: 34, r: 10, t: 20, b: 26 };
  const max = Math.max(...data.map((d) => d.value), 1);
  const p = Math.pow(10, Math.floor(Math.log10(max)));
  const top = [1, 2, 2.5, 5, 10].find((k) => k * p >= max)! * p;
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const bw = iw / data.length;

  const grid: React.ReactNode[] = [];
  for (let i = 0; i <= 2; i++) {
    const y = pad.t + ih - (ih * i) / 2;
    grid.push(
      <g key={i}>
        <line
          x1={pad.l}
          y1={y}
          x2={w - pad.r}
          y2={y}
          stroke="rgba(148,163,184,.09)"
        />
        <text
          x={pad.l - 7}
          y={y + 3.5}
          fill="var(--muted)"
          fontSize={10}
          textAnchor="end"
        >
          {Math.round((top * i) / 2)}
        </text>
      </g>,
    );
  }

  const bars: React.ReactNode[] = [];
  data.forEach((d, i) => {
    const cx = pad.l + i * bw;
    let yBottom = pad.t + ih;
    if (d.segments) {
      const shown = d.segments.filter((s) => s.v);
      shown.forEach((s, si) => {
        const sh = Math.max((s.v / top) * ih, 2);
        yBottom -= sh;
        const rx = si === shown.length - 1 ? 2.5 : 0;
        bars.push(
          <rect
            key={`s${i}-${si}`}
            x={+(cx + bw * 0.14).toFixed(1)}
            y={+yBottom.toFixed(1)}
            width={+(bw * 0.72).toFixed(1)}
            height={+sh.toFixed(1)}
            rx={rx}
            fill={s.c}
          >
            <title>{`${d.label}：${s.name} ${s.v} 条`}</title>
          </rect>,
        );
      });
    } else {
      const bh = Math.max((d.value / top) * ih, d.value ? 3 : 0);
      yBottom = pad.t + ih - bh;
      bars.push(
        <rect
          key={`b${i}`}
          x={+(cx + bw * 0.14).toFixed(1)}
          y={+yBottom.toFixed(1)}
          width={+(bw * 0.72).toFixed(1)}
          height={+bh.toFixed(1)}
          rx={3}
          fill="url(#grad)"
        >
          <title>{`${d.label}：${d.value} 条`}</title>
        </rect>,
      );
    }
    if (d.value) {
      bars.push(
        <text
          key={`v${i}`}
          x={+(cx + bw / 2).toFixed(1)}
          y={+(yBottom - 5).toFixed(1)}
          fill="var(--text-2)"
          fontSize={10}
          fontWeight={600}
          textAnchor="middle"
        >
          {d.value}
        </text>,
      );
    }
    bars.push(
      <text
        key={`x${i}`}
        x={+(cx + bw / 2).toFixed(1)}
        y={h - 8}
        fill="var(--muted)"
        fontSize={10}
        textAnchor="middle"
      >
        {String(d.label).slice(5)}
      </text>,
    );
  });

  return (
    <div ref={ref} style={{ ...wrapStyle, width: "100%" }}>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="grad" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="100%" stopColor="#4d9fff" />
          </linearGradient>
        </defs>
        {grid}
        {bars}
      </svg>
    </div>
  );
}
