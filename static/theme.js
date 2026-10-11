// Nút giao diện tự động / sáng / tối (nhớ trong localStorage). Tách khỏi index.html vì CSP không cho script nội tuyến.
(() => { const b=document.getElementById('themeBtn'), m=['auto','light','dark'], ic={auto:'🌓',light:'☀️',dark:'🌙'};
  const set=t=>{ if(t==='auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme=t;
    b.textContent=ic[t]; b.title='Giao diện: '+{auto:'tự động',light:'sáng',dark:'tối'}[t]; try{localStorage.theme=t}catch(e){} };
  let t='auto'; try{t=localStorage.theme||'auto'}catch(e){} set(t);
  b.onclick=()=>set(t=m[(m.indexOf(t)+1)%3]); })();
