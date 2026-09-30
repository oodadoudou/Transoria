import { useEffect, useMemo, useRef, useState } from "react";
import { interactivePreview, type ReadingLocation } from "./epubPreview";
import { fixedPageSize, previewRendition } from "./epubRendition";

export function EpubPreviewFrame({ html, title, zoom, mode = "continuous", location, fragment = "", navigation = 0, inspect = false, sourceLine = 0, step = 0, onLocation, onInspect, onLink, onWarning }: {
  html: string; title: string; zoom: number; mode?: "continuous" | "paged"; location?: ReadingLocation;
  fragment?: string; navigation?: number; inspect?: boolean; sourceLine?: number; step?: number;
  onLocation?: (location: ReadingLocation & { pages: number }) => void;
  onInspect?: (line: number, styles: Record<string, string>) => void;
  onLink?: (target: string) => void;
  onWarning?: (reason: string) => void;
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
  const token = useRef(crypto.randomUUID().replace(/-/g, ""));
  const ready = useRef(false);
  const previousStep = useRef(step);
  const callbacks = useRef({ onLocation, onInspect, onLink, onWarning });
  callbacks.current = { onLocation, onInspect, onLink, onWarning };
  const config = useRef({ mode, inspect, ...location, fragment, navigation });
  config.current = { mode, inspect, ...location, fragment, navigation };
  const send = (data: Record<string, unknown>) => frame.current?.contentWindow?.postMessage({ token: token.current, ...data }, "*");
  useEffect(() => { ready.current = false; }, [html]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.token !== token.current || event.data?.type !== "epub-preview") return;
      const data = event.data;
      if (data.event === "ready") { ready.current = true; send({ event: "configure", ...config.current }); }
      if (data.event === "location" && Number.isFinite(data.page) && Number.isFinite(data.pages) && Number.isFinite(data.scroll)) {
        const left = Number.isFinite(data.clipLeft) ? Math.max(0, Math.min(10000, data.clipLeft)) : 0;
        const right = Number.isFinite(data.clipRight) ? Math.max(0, Math.min(10000, data.clipRight)) : 0;
        if (frame.current) frame.current.style.clipPath = left || right ? `inset(0 ${right}px 0 ${left}px)` : "";
        callbacks.current.onLocation?.({ page: data.page, pages: data.pages, scroll: data.scroll, scrollX: Number.isFinite(data.scrollX) ? data.scrollX : 0, anchorLine: Number.isFinite(data.anchorLine) ? data.anchorLine : 0 });
      }
      if (data.event === "warning" && typeof data.reason === "string") callbacks.current.onWarning?.(data.reason);
      if (data.event === "inspect" && Number.isFinite(data.line) && data.styles && typeof data.styles === "object") callbacks.current.onInspect?.(data.line, data.styles);
      if (data.event === "link" && typeof data.target === "string") callbacks.current.onLink?.(data.target);
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);
  useEffect(() => { if (ready.current) send({ event: "configure", ...config.current }); }, [mode, inspect, zoom, fragment, navigation]);
  useEffect(() => { if (ready.current && sourceLine > 0) send({ event: "line", line: sourceLine }); }, [sourceLine]);
  useEffect(() => {
    if (ready.current && previousStep.current !== step) send({ event: "step", direction: Math.sign(step - previousStep.current) });
    previousStep.current = step;
  }, [step]);
  const fixed = rendition.layout === "pre-paginated";
  const pageSize = fixedPageSize(rendition, size, zoom);
  const iframe = <iframe ref={frame} srcDoc={interactivePreview(html, token.current)} title={title} sandbox="allow-scripts" style={fixed ? { position: "absolute", left: (pageSize.stageWidth - pageSize.width * pageSize.scale) / 2, top: (pageSize.stageHeight - pageSize.height * pageSize.scale) / 2, width: pageSize.width, height: pageSize.height, transform: `scale(${pageSize.scale})`, transformOrigin: "top left" } : { width: `${10000 / zoom}%`, height: `${10000 / zoom}%`, transform: `scale(${zoom / 100})` }} />;
  return fixed ? <div ref={surface} style={{ width: "100%", height: "100%", overflow: "auto" }}><div style={{ position: "relative", width: pageSize.stageWidth, height: pageSize.stageHeight }}>{iframe}</div></div> : iframe;
}
