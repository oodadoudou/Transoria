export type Rendition = { layout: "reflowable" | "pre-paginated"; width?: number | string; height?: number | string; position?: string; spread?: string; direction?: string };

export function previewRendition(markup: string): Rendition {
  const value = new DOMParser().parseFromString(markup, "text/html").querySelector('meta[name="transoria-rendition"]')?.getAttribute("content");
  try {
    const result = JSON.parse(value || "null");
    return result?.layout === "pre-paginated" ? result : { layout: "reflowable" };
  } catch { return { layout: "reflowable" }; }
}

export function fixedPageSize(layout: Rendition, available: { width: number; height: number }, zoom: number) {
  const dimension = (value: unknown, fallback: number) => typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 100000 ? value : fallback;
  const width = dimension(layout.width, layout.width === "device-width" ? available.width : 600);
  const height = dimension(layout.height, layout.height === "device-height" ? available.height : 800);
  const scale = Math.min(available.width / width, available.height / height) * zoom / 100;
  return { width, height, scale, stageWidth: Math.max(available.width, width * scale), stageHeight: Math.max(available.height, height * scale) };
}

export function fixedSpreadMate(layout: Rendition, index: number, count: number): number | null {
  if (layout.layout !== "pre-paginated" || layout.spread === "none" || layout.position === "center" || (!layout.position && index === 0)) return null;
  const leading = layout.direction === "rtl" ? "right" : "left";
  const side = layout.position || (index % 2 ? leading : leading === "left" ? "right" : "left");
  const mate = index + (side === leading ? 1 : -1);
  return mate >= 0 && mate < count ? mate : null;
}
