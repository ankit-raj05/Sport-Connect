const nav=document.querySelector('nav.top');addEventListener('scroll',()=>nav.classList.toggle('scrolled',scrollY>8));
document.querySelector('.burger')?.addEventListener('click',()=>{nav.classList.toggle('open');const l=nav.querySelector('.links');nav.style.setProperty('--lh',66+l.offsetHeight+'px')});
document.querySelectorAll('.toast').forEach(t=>setTimeout(()=>{t.classList.add('out');setTimeout(()=>t.remove(),300)},4500));
document.querySelectorAll('.pw button').forEach(b=>b.addEventListener('click',()=>{const i=b.parentElement.querySelector('input');i.type=i.type=='password'?'text':'password';b.textContent=i.type=='password'?'Show':'Hide'}));
document.querySelectorAll('form').forEach(f=>f.addEventListener('submit',()=>{const b=f.querySelector('button:not([type=button]):not(.ghost)');if(b&&f.checkValidity()){b.disabled=true;b.dataset.t=b.textContent;b.textContent='Please wait…'}}));
const sd=document.querySelector('.side'),sc=document.querySelector('.scrim'),tg=v=>{sd.classList.toggle('show',v);sc.classList.toggle('show',v)};
document.querySelector('.ftoggle')?.addEventListener('click',()=>tg(true));sc?.addEventListener('click',()=>tg(false));document.querySelector('.closef')?.addEventListener('click',()=>tg(false));
if(sd){sd.addEventListener('submit',e=>{});sd.addEventListener('keydown',e=>{if(e.key=='Escape')tg(false)})}

const dlg=document.createElement('dialog');dlg.innerHTML='<form method=dialog><p style="margin:0"></p><div class=row><button value=ok>Confirm</button><button value=no class=ghost>Cancel</button></div></form>';document.body.append(dlg);
document.addEventListener('submit',e=>{const f=e.target;if(f.dataset.confirm&&!f.dataset.ok){e.preventDefault();e.stopImmediatePropagation();dlg.querySelector('p').textContent=f.dataset.confirm;dlg.returnValue='';dlg.showModal();dlg.onclose=()=>{if(dlg.returnValue=='ok'){f.dataset.ok=1;f.requestSubmit()}}}},true);
