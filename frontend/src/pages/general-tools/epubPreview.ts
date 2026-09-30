export function columnPageOffsets(rectangles: Array<[number, number]>, viewport: number, extent: number): number[] {
  if (!Number.isFinite(viewport) || viewport <= 24 || !Number.isFinite(extent)) return [0];
  const columns: Array<[number, number]> = [];
  for (const [start, end] of rectangles.filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end) && end > start).sort((a, b) => a[0] - b[0])) {
    const previous = columns.at(-1);
    if (previous && start < previous[1] - .5) previous[1] = Math.max(previous[1], end);
    else columns.push([start, end]);
  }
  const result = [0];
  let columnIndex = 0;
  while (result.at(-1)! + viewport < extent - .5 && result.length < 10000) {
    const offset = result.at(-1)!;
    while (columnIndex < columns.length && (columns[columnIndex][1] <= offset + viewport - 12 || columns[columnIndex][0] <= offset + 12)) columnIndex++;
    const next = columns[columnIndex];
    const boundary = next && next[1] - next[0] <= viewport - 24 ? next[0] - 12 : offset + viewport;
    result.push(Math.max(offset + 1, boundary));
  }
  return result;
}

export type ReadingLocation = { page: number; scroll: number; scrollX?: number; anchorLine?: number };

export class PreviewCommands {
  loaded = false;
  configured = false;
  revision = 0;
  private pendingSteps = 0;

  readonly token: string;

  constructor(token: string) { this.token = token; }

  configure(): number | null {
    if (!this.loaded) return null;
    this.configured = false;
    return ++this.revision;
  }

  acknowledge(revision: number): boolean {
    if (revision !== this.revision || !this.loaded) return false;
    this.configured = true;
    return true;
  }

  step(delta: number): number {
    if (this.configured) return delta;
    this.pendingSteps += delta;
    return 0;
  }

  drain(): number {
    const steps = this.pendingSteps;
    this.pendingSteps = 0;
    return steps;
  }
}

export function previewAtZoom(markup: string, zoom: number, wrap: boolean, fixed = false): string {
  if (fixed) return markup;
  const scaled = markup
    .replace("max-width:100%!important;", `max-width:${zoom}%!important;`)
    .replace("max-height:calc(100vh - 24px)!important;", `max-height:calc(${zoom}vh - 24px)!important;`);
  if (!wrap) return scaled;
  return scaled.replace("</style>", "</style><style>body{overflow-wrap:anywhere}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}</style>");
}

export function interactivePreview(markup: string, token: string): string {
  const nonce = token.replace(/[^a-zA-Z0-9]/g, "");
  const safe = markup.replace(/default-src (?:'none'|&#x27;none&#x27;);/, `default-src 'none'; script-src 'nonce-${nonce}';`);
  const script = `(() => {
    const token = ${JSON.stringify(token)};
    let page = 0, mode = 'continuous', inspect = false, restoring = false, lastNavigation = -1;
    let ready = false, sign = 1, vertical = false, offsets = [], configuration = 0, revision = 0;
    const paginateColumns = ${columnPageOffsets.toString()};
    const send = (data) => parent.postMessage({type:'epub-preview',token,...data}, '*');
    const fixed = document.querySelector('meta[name="transoria-rendition"]')?.content.includes('pre-paginated');
    const pages = () => fixed ? 1 : mode === 'paged' && offsets.length ? offsets.length : Math.max(1, Math.ceil(document.documentElement.scrollWidth / innerWidth - 0.01));
    const currentPage = () => {
      if(!offsets.length || mode !== 'paged') return Math.max(0, Math.min(pages()-1, Math.round(sign*scrollX/innerWidth)));
      const distance = sign*scrollX;
      let best=0;
      for(let index=1;index<offsets.length;index++) if(Math.abs(offsets[index]-distance)<Math.abs(offsets[best]-distance)) best=index;
      return best;
    };
    const anchorLine = () => {
      let best=Infinity, line=0;
      for(const node of document.querySelectorAll('[data-transoria-line]')) {
        if(!node.getBoundingClientRect || !/^(P|H[1-6]|LI|DT|DD|IMG|SVG)$/.test(node.tagName)) continue;
        const rect=node.getBoundingClientRect();
        const distance=vertical ? (sign < 0 ? innerWidth-rect.right : rect.left) : rect.top;
        if(rect.width && rect.height && distance>=-1 && distance<best) { best=distance; line=Number(node.dataset.transoriaLine); }
      }
      return line;
    };
    const report = () => {
      const gap=mode==='paged' && vertical && offsets[page+1]!==undefined ? Math.max(0,innerWidth-(offsets[page+1]-offsets[page]+12)) : 0;
      const leading=mode==='paged' && vertical && page>0 ? 12 : 0;
      send({event:'location',revision,page,pages:pages(),scroll:scrollY,scrollX,anchorLine:anchorLine(),clipLeft:sign<0?gap:leading,clipRight:sign<0?leading:gap});
    };
    const go = () => {
      restoring = true;
      scrollTo(mode === 'paged' ? sign * (offsets[page] ?? page * innerWidth) : scrollX, mode === 'paged' ? 0 : scrollY);
      restoring = false; report();
    };
    const extentNode = document.createElement('div');
    extentNode.setAttribute?.('aria-hidden','true');
    const measure = () => {
      extentNode.remove?.();
      offsets = [];
      if(mode !== 'paged' || fixed) return;
      if(!vertical || !document.createTreeWalker || !document.createRange) {
        offsets=Array.from({length:Math.max(1,Math.ceil(document.documentElement.scrollWidth/innerWidth-.01))},(_,index)=>index*innerWidth);
        return;
      }
      scrollTo(0,0);
      const walker=document.createTreeWalker(document.body,4);
      const range=document.createRange(), rectangles=[];
      let node, fragments=0;
      while((node=walker.nextNode())) {
        if(!node.textContent.trim()) continue;
        range.selectNodeContents(node);
        for(const rect of range.getClientRects()) {
          if(rect.width<=.5 || rect.height<=.5) continue;
          rectangles.push(sign<0 ? [innerWidth-rect.right,innerWidth-rect.left] : [rect.left,rect.right]);
          if(++fragments>50000) break;
        }
        if(fragments>50000) break;
      }
      for(const image of document.querySelectorAll('img,svg,video')) {
        const rect=image.getBoundingClientRect();
        if(rect.width && rect.height) rectangles.push(sign<0 ? [innerWidth-rect.right,innerWidth-rect.left] : [rect.left,rect.right]);
      }
      const extent=rectangles.length && fragments<=50000 ? rectangles.reduce((end,rect)=>Math.max(end,rect[1]),0)+12 : document.documentElement.scrollWidth;
      offsets=paginateColumns(rectangles,innerWidth,extent);
      if(fragments>50000) send({event:'warning',reason:'pagination-limit'});
      if(offsets.length>1) {
        const last=offsets.at(-1);
        extentNode.style.cssText='position:absolute;top:0;width:1px;height:1px;pointer-events:none;left:'+(sign<0?-last:last+innerWidth-1)+'px';
        document.documentElement.append(extentNode);
      }
    };
    const layout = document.createElement('style');
    document.head.append(layout);
    addEventListener('message', (event) => {
      if(event.source !== parent || event.data?.token !== token) return;
      const data = event.data;
      if(data.event === 'configure') {
        ready = false;
        const requested = ++configuration;
        revision = data.revision;
        mode = !fixed && data.mode === 'paged' ? 'paged' : 'continuous'; inspect = !!data.inspect;
        layout.textContent = '';
        const bodyStyle = getComputedStyle(document.body);
        const writingMode = bodyStyle.getPropertyValue('writing-mode');
        vertical = writingMode.startsWith('vertical') || writingMode.startsWith('sideways');
        sign = writingMode.endsWith('-rl') || (!vertical && bodyStyle.getPropertyValue('direction') === 'rtl') ? -1 : 1;
        layout.textContent = mode === 'paged' ? 'html{height:100%!important;overflow:hidden!important;writing-mode:'+writingMode+'!important;direction:'+bodyStyle.getPropertyValue('direction')+'!important}body{box-sizing:border-box!important;height:calc(100vh - 24px)!important;max-height:calc(100vh - 24px)!important;width:calc(100vw - 24px)!important;max-width:none!important;margin:12px!important;overflow:visible!important;'+(vertical ? 'column-count:auto!important;column-width:auto!important;' : 'column-width:calc(100vw - 24px)!important;column-gap:24px!important;column-fill:auto!important;')+'}img,svg{break-inside:avoid}' : '';
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if(requested !== configuration) return;
          measure();
          const end = Number(data.page) === -1;
          page = end ? pages()-1 : Math.max(0, Math.min(Number(data.page) || 0, pages()-1));
          scrollTo(mode === 'paged' ? sign*(offsets[page] ?? page*innerWidth) : end && vertical ? sign*Math.max(0,document.documentElement.scrollWidth-innerWidth) : Number(data.scrollX)||0, mode === 'paged' ? 0 : end && !vertical ? Math.max(0,document.documentElement.scrollHeight-innerHeight) : Number(data.scroll)||0);
          if(!end && data.anchorLine && mode==='paged' && !data.fragment) document.querySelector('[data-transoria-line="'+Number(data.anchorLine)+'"]')?.scrollIntoView();
          if(data.fragment && data.navigation !== lastNavigation) { document.getElementById(data.fragment)?.scrollIntoView(); lastNavigation = data.navigation; }
          if(mode==='paged') { page = currentPage(); go(); }
          ready = true; send({event:'configured',revision}); report();
        }));
      } else if(data.event === 'step' && ready) {
        page = Math.max(0, Math.min(page + data.direction, pages()-1)); go();
      } else if(data.event === 'line' && ready) {
        const nodes = [...document.querySelectorAll('[data-transoria-line]')];
        const target = nodes.reverse().find((node) => Number(node.dataset.transoriaLine) <= data.line);
        target?.scrollIntoView({block:'center'}); if(mode==='paged') { page = currentPage(); go(); } else report();
      }
    });
    addEventListener('scroll', () => { if(ready && !restoring) { if(mode==='paged') page=currentPage(); report(); } }, {passive:true});
    const reflow = () => {
      if(!ready) return;
      const previousPage=page, line=anchorLine(); measure();
      if(line) document.querySelector('[data-transoria-line="'+line+'"]')?.scrollIntoView();
      page=Math.min(line?currentPage():previousPage,pages()-1); go();
    };
    addEventListener('resize', reflow);
    document.fonts.addEventListener?.('loadingdone',reflow);
    document.addEventListener('click', (event) => {
      const target = event.target.closest('[data-transoria-line]');
      if(!target) return;
      const link = event.target.closest('a[data-transoria-target]');
      if(link && !inspect) { event.preventDefault(); send({event:'link',target:link.dataset.transoriaTarget}); return; }
      if(!inspect) return;
      event.preventDefault();
      const style = getComputedStyle(target);
      const properties = ['font-family','font-size','font-weight','color','background-color','line-height','text-align','margin','padding','display','width','height','writing-mode','text-orientation','direction','grid-template-columns','gap'];
      send({event:'inspect',line:Number(target.dataset.transoriaLine),tag:target.tagName,styles:Object.fromEntries(properties.map((key)=>[key,style.getPropertyValue(key)]))});
    });
    Promise.all([document.fonts.ready, ...[...document.images].map((image)=>image.complete ? Promise.resolve() : new Promise((resolve)=>{image.addEventListener('load',resolve,{once:true});image.addEventListener('error',resolve,{once:true});}))]).then(() => send({event:'ready'}));
  })();`;
  return safe + `<script nonce="${nonce}">${script}</script>`;
}
