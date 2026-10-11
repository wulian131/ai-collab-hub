const attachmentDraftMap = new Map();
function attachmentKey(id) { return 'aicollabhub_attachments:'+me+':'+id; }
function attachmentDrafts(id) {
  const key=attachmentKey(id);
  if(!attachmentDraftMap.has(key)) {
    let saved=[];try{saved=JSON.parse(sessionStorage.getItem(key)||'[]')}catch{}
    attachmentDraftMap.set(key,saved.map(a=>({...a,state:a.state==='ready'?'ready':'error',error:'上传未完成，请移除后重新选择文件'})));
  }
  return attachmentDraftMap.get(key);
}
function saveAttachmentDrafts(id) {
  sessionStorage.setItem(attachmentKey(id),JSON.stringify(attachmentDrafts(id).map(({id,name,size,state,offset})=>({id,name,size,state,offset}))));
  if(id===cid)renderAttachmentDrafts();
}
function attachmentIds(id) { return attachmentDrafts(id).filter(a=>a.state==='ready').map(a=>a.id); }
function attachmentsBusy(id) { return attachmentDrafts(id).some(a=>a.state!=='ready'); }
function clearAttachmentDrafts(id) { attachmentDraftMap.delete(attachmentKey(id));sessionStorage.removeItem(attachmentKey(id)); }
function attachmentSize(n) { return n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?Math.ceil(n/1024)+' KB':n+' B'; }
function renderAttachmentDrafts() {
  if(typeof updateSendAvailability==='function')updateSendAvailability();
  const host=$('attachmentDrafts');if(!host)return;host.replaceChildren();
  const locked=!!pending(cid)||sending.has(cid);
  $('attachButton').disabled=locked||readonly;
  for(const a of attachmentDrafts(cid)) {
    const row=node('div',undefined,'attachment-draft'),label=node('span',a.name+' · '+attachmentSize(a.size));
    label.title=a.name;row.append(label);
    row.append(node('small',a.state==='ready'?'已上传 · 待发送':a.state==='error'?a.error:'上传中 '+Math.round((a.offset||0)/Math.max(a.size,1)*100)+'%'));
    if(a.state==='error'&&a.file){const retry=node('button','重试');retry.type='button';retry.disabled=locked;retry.onclick=()=>uploadAttachment(a,cid);row.append(retry)}
    const remove=node('button','移除');remove.type='button';remove.disabled=locked;remove.setAttribute('aria-label','移除附件 '+a.name);remove.onclick=()=>removeAttachment(a,cid);row.append(remove);host.append(row);
  }
}
async function attachmentRequest(path,body,signal) {
  let response;
  try {response=await request(path,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/octet-stream'},body,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(30000)]):AbortSignal.timeout(30000)})}
  catch {throw Error('上传中断，请重试')}
  let result;try{result=await response.json()}catch{throw Error('上传服务暂不可用，请重试')}
  if(!response.ok)throw Error(result.error||'上传失败');return result;
}
async function uploadAttachment(a,conversation) {
  if(a.state==='uploading')return;
  a.state='uploading';a.controller=new AbortController();saveAttachmentDrafts(conversation);
  try {
    const init=await attachmentRequest('/api/attachments',JSON.stringify({id:a.id,name:a.name,size:a.size,conversation_id:conversation}),a.controller.signal);
    a.offset=init.offset;
    while(a.offset<a.size&&!a.removed) {
      const result=await attachmentRequest('/api/attachments/'+a.id+'?offset='+a.offset,a.file.slice(a.offset,a.offset+512*1024),a.controller.signal);
      a.offset=result.offset;saveAttachmentDrafts(conversation);
    }
    if(!a.removed){a.state='ready';saveAttachmentDrafts(conversation)}
  } catch(e) {if(!a.removed){a.state='error';a.error=e.message;saveAttachmentDrafts(conversation)}}
  finally {if(a.removed)api('/api/attachments/'+a.id+'/remove',{}).catch(()=>{});}
}
async function removeAttachment(a,conversation) {
  a.removed=true;a.controller?.abort();
  const items=attachmentDrafts(conversation),index=items.indexOf(a);if(index>=0)items.splice(index,1);saveAttachmentDrafts(conversation);
  try{await api('/api/attachments/'+a.id+'/remove',{})}catch{if(conversation===cid)status('已从草稿移除；服务端未发送附件将在 7 天后自动清理。')}
}
async function addAttachments(files) {
  if(readonly||pending(cid)||sending.has(cid))return;
  const conversation=cid,items=attachmentDrafts(conversation),queue=[];
  for(const file of files) {
    if(items.length>=5){status('每条消息最多 5 个附件。',true);break}
    if(file.size>10*1024*1024){status(file.name+' 超过 10 MB，请选择较小的文件。',true);continue}
    if(file.name.length>240){status('文件名过长，请缩短后重试。',true);continue}
    const a={id:crypto.randomUUID(),name:file.name,size:file.size,file,state:'queued',offset:0};items.push(a);queue.push(a);
  }
  saveAttachmentDrafts(conversation);
  for(const a of queue)if(!a.removed)await uploadAttachment(a,conversation);
}
function attachmentCards(items) {
  const list=node('div',undefined,'attachment-cards');
  for(const a of items) {
    const button=node('button','↓ '+a.name+' · '+attachmentSize(a.size),'attachment-download');button.type='button';button.title='下载 '+a.name;
    button.onclick=async()=>{
      button.disabled=true;
      try {
        const response=await request('/api/attachments/'+encodeURIComponent(a.id),{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(60000)});
        if(!response.ok){let error;try{error=await response.json()}catch{}throw Error(error?.error||'下载失败，请重试')}
        const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=a.name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(e){status(e.message,true)}finally{button.disabled=false}
    };
    list.append(button);
  }
  return list;
}
function installAttachments() {
  $('attachButton').onclick=()=>$('attachmentInput').click();
  $('attachmentInput').onchange=e=>{addAttachments([...e.target.files]);e.target.value=''};
  $('form').addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();$('form').classList.add('attachment-drag')}});
  $('form').addEventListener('dragleave',()=>$('form').classList.remove('attachment-drag'));
  $('form').addEventListener('drop',e=>{if(e.dataTransfer.files.length){e.preventDefault();$('form').classList.remove('attachment-drag');addAttachments([...e.dataTransfer.files])}});
  $('text').addEventListener('paste',e=>{if(e.clipboardData.files.length){e.preventDefault();addAttachments([...e.clipboardData.files])}});
}
