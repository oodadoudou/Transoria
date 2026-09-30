export type ReadingLocation = { page: number; scroll: number };

export function interactivePreview(markup: string, token: string): string {
  const nonce = token.replace(/[^a-zA-Z0-9]/g, "");
  const safe = markup.replace(/default-src (?:'none'|&#x27;none&#x27;);/, `default-src 'none'; script-src 'nonce-${nonce}';`);
  const script = `(() => {
    const token = ${JSON.stringify(token)};
    let page = 0, mode = 'continuous', inspect = false, restoring = false, lastNavigation = -1;
    let ready = false;
    const send = (data) => parent.postMessage({type:'epub-preview',token,...data}, '*');
    const pages = () => Math.max(1, Math.ceil(document.documentElement.scrollWidth / innerWidth - 0.01));
    const report = () => send({event:'location',page,pages:pages(),scroll:scrollY});
    const go = () => { restoring = true; scrollTo(mode === 'paged' ? page * innerWidth : 0, mode === 'paged' ? 0 : scrollY); restoring = false; report(); };
    const layout = document.createElement('style');
    document.head.append(layout);
    addEventListener('message', (event) => {
      if(event.source !== parent || event.data?.token !== token) return;
      const data = event.data;
      if(data.event === 'configure') {
        mode = data.mode === 'paged' ? 'paged' : 'continuous'; inspect = !!data.inspect;
        layout.textContent = mode === 'paged' ? 'html{height:100%!important;overflow:hidden!important}body{box-sizing:border-box!important;height:calc(100vh - 24px)!important;max-height:calc(100vh - 24px)!important;width:calc(100vw - 24px)!important;max-width:none!important;margin:12px!important;column-width:calc(100vw - 24px)!important;column-gap:24px!important;column-fill:auto!important;overflow:visible!important}img,svg{break-inside:avoid}' : '';
        requestAnimationFrame(() => requestAnimationFrame(() => {
          page = Math.max(0, Math.min(Number(data.page) || 0, pages()-1));
          scrollTo(mode === 'paged' ? page*innerWidth : 0, mode === 'paged' ? 0 : Number(data.scroll)||0);
          if(data.fragment && data.navigation !== lastNavigation) { document.getElementById(data.fragment)?.scrollIntoView(); lastNavigation = data.navigation; }
          if(mode==='paged') page = Math.round(scrollX/innerWidth);
          ready = true; report();
        }));
      } else if(data.event === 'step') {
        page = Math.max(0, Math.min(page + data.direction, pages()-1)); go();
      } else if(data.event === 'line') {
        const nodes = [...document.querySelectorAll('[data-transoria-line]')];
        const target = nodes.reverse().find((node) => Number(node.dataset.transoriaLine) <= data.line);
        target?.scrollIntoView({block:'center'}); if(mode==='paged') page = Math.round(scrollX/innerWidth); report();
      }
    });
    addEventListener('scroll', () => { if(ready && !restoring) { if(mode==='paged') page=Math.round(scrollX/innerWidth); report(); } }, {passive:true});
    addEventListener('resize', () => { if(ready) { page=Math.min(page,pages()-1); go(); } });
    document.addEventListener('click', (event) => {
      const target = event.target.closest('[data-transoria-line]');
      if(!target) return;
      const link = event.target.closest('a[data-transoria-target]');
      if(link && !inspect) { event.preventDefault(); send({event:'link',target:link.dataset.transoriaTarget}); return; }
      if(!inspect) return;
      event.preventDefault();
      const style = getComputedStyle(target);
      const properties = ['font-family','font-size','font-weight','color','background-color','line-height','text-align','margin','padding','display','width','height'];
      send({event:'inspect',line:Number(target.dataset.transoriaLine),tag:target.tagName,styles:Object.fromEntries(properties.map((key)=>[key,style.getPropertyValue(key)]))});
    });
    Promise.all([document.fonts.ready, ...[...document.images].map((image)=>image.complete ? Promise.resolve() : new Promise((resolve)=>{image.addEventListener('load',resolve,{once:true});image.addEventListener('error',resolve,{once:true});}))]).then(() => send({event:'ready'}));
  })();`;
  return safe + `<script nonce="${nonce}">${script}</script>`;
}
