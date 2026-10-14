// Nút giao diện tự động / sáng / tối (nhớ trong localStorage). Tách khỏi index.html vì CSP không cho script nội tuyến.
// Màu thanh trạng thái khi mở như app từ màn hình chính (meta theme-color) theo màu thanh trên cùng (--panel).
(() => { const b=document.getElementById('themeBtn'), m=['auto','light','dark'], ic={auto:'🌓',light:'☀️',dark:'🌙'};
  const bar=document.querySelector('meta[name="theme-color"]'), dark=matchMedia('(prefers-color-scheme: dark)');
  const paint=()=>{ if(bar) bar.content=getComputedStyle(document.documentElement).getPropertyValue('--panel').trim()||bar.content; };
  const set=t=>{ if(t==='auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme=t;
    b.textContent=ic[t]; b.title='Giao diện: '+{auto:'tự động',light:'sáng',dark:'tối'}[t]; try{localStorage.theme=t}catch(e){} paint(); };
  let t='auto'; try{t=localStorage.theme||'auto'}catch(e){} set(t);
  dark.addEventListener?.('change', paint);
  b.onclick=()=>set(t=m[(m.indexOf(t)+1)%3]); })();
