(() => {
  const cfg = window.GAME_INVITE || {};
  const $ = id => document.getElementById(id);
  const statusBar = $('statusBar');
  function status(message, error=false){
    if(!statusBar)return;
    statusBar.textContent=message;
    statusBar.classList.remove('hidden','error');
    if(error)statusBar.classList.add('error');
  }
  async function post(action){
    const r=await fetch(`/api/games/${cfg.code}/${action}`,{method:'POST'});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(data.error||'Não foi possível concluir a ação.');
    return data;
  }
  $('acceptInviteBtn')?.addEventListener('click',async e=>{
    const btn=e.currentTarget;btn.disabled=true;btn.textContent='Aceitando...';
    try{const data=await post('accept');location.href=data.url;}catch(err){status(err.message,true);btn.disabled=false;btn.textContent='✓ Aceitar partida';}
  });
  $('refuseInviteBtn')?.addEventListener('click',async e=>{
    if(!confirm('Recusar este convite?'))return;
    const btn=e.currentTarget;btn.disabled=true;
    try{const data=await post('refuse');location.href=data.url;}catch(err){status(err.message,true);btn.disabled=false;}
  });
  $('cancelInviteBtn')?.addEventListener('click',async e=>{
    if(!confirm('Cancelar este convite?'))return;
    const btn=e.currentTarget;btn.disabled=true;
    try{const data=await post('cancel-invite');location.href=data.url;}catch(err){status(err.message,true);btn.disabled=false;}
  });
  $('copyInviteBtn')?.addEventListener('click',async()=>{
    try{
      await navigator.clipboard.writeText(cfg.invite_url);
      status('Link copiado.');
    }catch(_){
      const input=$('inviteLink');input?.focus();input?.select();status('Selecione e copie o link.');
    }
  });
  $('shareInviteBtn')?.addEventListener('click',async()=>{
    if(navigator.share){
      try{await navigator.share({title:'Convite para jogar • Nossa Sala',text:'Aceite ou recuse minha partida:',url:cfg.invite_url});return;}catch(_){ }
    }
    try{await navigator.clipboard.writeText(cfg.invite_url);status('Link copiado para você compartilhar.');}catch(_){status('Copie o link acima para compartilhar.');}
  });
  setInterval(async()=>{
    if(!cfg.is_creator)return;
    try{
      const r=await fetch(`/api/games/${cfg.code}`,{cache:'no-store'});
      if(r.ok){const data=await r.json();if(data.status==='active')location.href=data.url||`/game/${cfg.code}`;}
      else if(r.status===404)location.href='/games';
    }catch(_){ }
  },2500);
  setInterval(()=>window.NossaSala?.ping?.(),20000);
})();
