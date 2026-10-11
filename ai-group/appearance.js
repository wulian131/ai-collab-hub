(()=>{const root=document.documentElement, system=matchMedia('(prefers-color-scheme: dark)');
function read(k,f){try{return localStorage.getItem(k)||f}catch{return f}}
let mode=read('aicollabhub_theme','system'),accent=read('aicollabhub_accent','mint');
function apply(){root.dataset.theme=mode==='system'?(system.matches?'dark':'light'):mode;root.dataset.accent=accent}
apply();system.addEventListener('change',apply);
document.addEventListener('DOMContentLoaded',()=>{const modeEl=document.getElementById('themeMode'),accentEl=document.getElementById('accentMode');modeEl.value=mode;accentEl.value=accent;modeEl.onchange=()=>{mode=modeEl.value;try{localStorage.setItem('aicollabhub_theme',mode)}catch{}apply()};accentEl.onchange=()=>{accent=accentEl.value;try{localStorage.setItem('aicollabhub_accent',accent)}catch{}apply()}})})();
