import { useEffect, useRef } from "react";
import { interactivePreview, type ReadingLocation } from "./epubPreview";

export function EpubPreviewFrame({ html, title, zoom, mode = "continuous", location, fragment = "", navigation = 0, inspect = false, sourceLine = 0, step = 0, onLocation, onInspect, onLink }: {
  html: string; title: string; zoom: number; mode?: "continuous" | "paged"; location?: ReadingLocation;
  fragment?: string; navigation?: number; inspect?: boolean; sourceLine?: number; step?: number;
  onLocation?: (location: ReadingLocation & { pages: number }) => void;
  onInspect?: (line: number, styles: Record<string, string>) => void;
  onLink?: (target: string) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const token = useRef(crypto.randomUUID().replace(/-/g, ""));
  const ready = useRef(false);
  const previousStep = useRef(step);
  const callbacks = useRef({ onLocation, onInspect, onLink });
  callbacks.current = { onLocation, onInspect, onLink };
  const config = useRef({ mode, inspect, ...location, fragment, navigation });
  config.current = { mode, inspect, ...location, fragment, navigation };
  const send = (data: Record<string, unknown>) => frame.current?.contentWindow?.postMessage({ token: token.current, ...data }, "*");
  useEffect(() => { ready.current = false; }, [html]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.token !== token.current || event.data?.type !== "epub-preview") return;
      const data = event.data;
      if (data.event === "ready") { ready.current = true; send({ event: "configure", ...config.current }); }
      if (data.event === "location" && Number.isFinite(data.page) && Number.isFinite(data.pages) && Number.isFinite(data.scroll)) callbacks.current.onLocation?.({ page: data.page, pages: data.pages, scroll: data.scroll });
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
  return <iframe ref={frame} srcDoc={interactivePreview(html, token.current)} title={title} sandbox="allow-scripts" style={{ width: `${10000 / zoom}%`, height: `${10000 / zoom}%`, transform: `scale(${zoom / 100})` }} />;
}
