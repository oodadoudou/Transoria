import { useEffect, useRef, useState, type UIEvent } from "react";

interface VirtualWindowOptions {
  count: number;
  rowHeight: number;
  overscan?: number;
  defaultViewportHeight?: number;
}

export function useVirtualWindow({
  count,
  rowHeight,
  overscan = 8,
  defaultViewportHeight = 600,
}: VirtualWindowOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(defaultViewportHeight);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setViewportHeight(el.clientHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const startIndex = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const endIndex = Math.min(
    count,
    Math.ceil((scrollTop + viewportHeight) / rowHeight) + overscan,
  );

  return {
    containerRef,
    scrollTop,
    startIndex,
    endIndex,
    totalHeight: count * rowHeight,
    topForIndex: (index: number) => index * rowHeight,
    handleScroll: (event: UIEvent<HTMLDivElement>) =>
      setScrollTop(event.currentTarget.scrollTop),
    scrollToIndex: (index: number) => {
      const top = index * rowHeight;
      setScrollTop(top);
      containerRef.current?.scrollTo({ top });
    },
    scrollToOffset: (top: number) => {
      const nextTop = Math.max(0, top);
      setScrollTop(nextTop);
      containerRef.current?.scrollTo({ top: nextTop });
    },
  };
}
