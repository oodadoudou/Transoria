import { useEffect, useMemo, useRef, useState } from "react";
import { interactivePreview, PreviewCommands, type ReadingLocation } from "./epubPreview";
import { fixedPageSize, previewRendition } from "./epubRendition";

export function EpubPreviewFrame({ html, title, zoom, mode = "continuous", location, fragment = "", navigation = 0, inspect = false, sourceLine = 0, sourceColumn = 0, sourceRequest = 0, step = 0, onLocation, onLocate, onBoundary, onFind, onInspect, onLink, onWarning, onReady }: {
  html: string; title: string; zoom: number; mode?: "continuous" | "paged"; location?: ReadingLocation;
  fragment?: string; navigation?: number; inspect?: boolean; sourceLine?: number; sourceColumn?: number; sourceRequest?: number; step?: number;
  onLocation?: (location: ReadingLocation & { pages: number; origin: string }) => void;
  onLocate?: (line: number, column: number) => void;
  onBoundary?: (direction: -1 | 1) => void;
  onFind?: () => void;
  onInspect?: (line: number, styles: Record<string, string>) => void;
  onLink?: (target: string) => void;
  onWarning?: (reason: string) => void;
  onReady?: (ready: boolean) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const rendition = useMemo(() => previewRendition(html), [html]);
  const [size, setSize] = useState({ width: 600, height: 800 });
  useEffect(() => {
    if (!surface.current) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.max(1, entry.contentRect.width), height: Math.max(1, entry.contentRect.height) }));
    observer.observe(surface.current);
    return () => observer.disconnect();
  }, [rendition.layout]);
  const token = useMemo(() => crypto.randomUUID().replace(/-/g, ""), [html]);
  const commands = useRef(new PreviewCommands(token));
  const previousStep = useRef(step);
  if (commands.current.token !== token) {
    commands.current = new PreviewCommands(token);
    previousStep.current = step;
  }
  const latestPoint = useRef({ line: sourceLine, column: sourceColumn });
  latestPoint.current = { line: sourceLine, column: sourceColumn };
  const callbacks = useRef({ onLocation, onLocate, onBoundary, onFind, onInspect, onLink, onWarning, onReady });
  callbacks.current = { onLocation, onLocate, onBoundary, onFind, onInspect, onLink, onWarning, onReady };
  const config = useRef({ mode, inspect, ...location, fragment, navigation });
  config.current = { mode, inspect, ...location, fragment, navigation };
  const send = (data: Record<string, unknown>) => frame.current?.contentWindow?.postMessage({ token: commands.current.token, ...data }, "*");
  const configure = () => {
    const revision = commands.current.configure();
    callbacks.current.onReady?.(false);
    if (revision !== null) send({ event: "configure", ...config.current, revision });
  };
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.token !== commands.current.token || event.data?.type !== "epub-preview") return;
      const data = event.data;
      if (data.event === "ready") { commands.current.loaded = true; configure(); }
      if (data.event === "configured" && commands.current.acknowledge(data.revision)) {
        if (latestPoint.current.line > 0) send({ event: "line", ...latestPoint.current });
        const direction = commands.current.drain();
        if (direction) send({ event: "step", direction });
        callbacks.current.onReady?.(true);
      }
      if (data.event === "location" && commands.current.configured && data.revision === commands.current.revision && Number.isFinite(data.page) && Number.isFinite(data.pages) && Number.isFinite(data.scroll)) {
        const left = Number.isFinite(data.clipLeft) ? Math.max(0, Math.min(10000, data.clipLeft)) : 0;
        const right = Number.isFinite(data.clipRight) ? Math.max(0, Math.min(10000, data.clipRight)) : 0;
        if (frame.current) frame.current.style.clipPath = left || right ? `inset(0 ${right}px 0 ${left}px)` : "";
        callbacks.current.onLocation?.({ page: data.page, pages: data.pages, scroll: data.scroll, scrollX: Number.isFinite(data.scrollX) ? data.scrollX : 0, anchorLine: Number.isFinite(data.anchorLine) ? data.anchorLine : 0, anchorColumn: Number.isFinite(data.anchorColumn) ? data.anchorColumn : 0, origin: typeof data.origin === "string" ? data.origin : "layout" });
      }
      if (data.event === "locate" && commands.current.configured && data.revision === commands.current.revision && Number.isInteger(data.line) && data.line > 0 && Number.isInteger(data.column) && data.column >= 0) callbacks.current.onLocate?.(data.line, data.column);
      if (data.event === "boundary" && commands.current.configured && data.revision === commands.current.revision && (data.direction === -1 || data.direction === 1)) callbacks.current.onBoundary?.(data.direction);
      if (data.event === "find") callbacks.current.onFind?.();
      if (data.event === "warning" && typeof data.reason === "string") callbacks.current.onWarning?.(data.reason);
      if (data.event === "inspect" && Number.isFinite(data.line) && data.styles && typeof data.styles === "object") callbacks.current.onInspect?.(data.line, data.styles);
      if (data.event === "link" && typeof data.target === "string") callbacks.current.onLink?.(data.target);
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);
  useEffect(configure, [token, mode, inspect, zoom, fragment, navigation]);
  useEffect(() => { if (commands.current.configured && sourceLine > 0) send({ event: "line", line: sourceLine, column: sourceColumn }); }, [sourceLine, sourceColumn, sourceRequest]);
  useEffect(() => {
    const direction = commands.current.step(step - previousStep.current);
    if (direction) send({ event: "step", direction });
    previousStep.current = step;
  }, [step]);
  const fixed = rendition.layout === "pre-paginated";
  const pageSize = fixedPageSize(rendition, size, zoom);
  const iframe = <iframe ref={frame} srcDoc={interactivePreview(html, token)} title={title} sandbox="allow-scripts" style={fixed ? { position: "absolute", left: (pageSize.stageWidth - pageSize.width * pageSize.scale) / 2, top: (pageSize.stageHeight - pageSize.height * pageSize.scale) / 2, width: pageSize.width, height: pageSize.height, transform: `scale(${pageSize.scale})`, transformOrigin: "top left" } : { width: `${10000 / zoom}%`, height: `${10000 / zoom}%`, transform: `scale(${zoom / 100})` }} />;
  return fixed ? <div ref={surface} style={{ width: "100%", height: "100%", overflow: "auto" }}><div style={{ position: "relative", width: pageSize.stageWidth, height: pageSize.stageHeight }}>{iframe}</div></div> : iframe;
}
