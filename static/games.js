(() => {
  const $ = id => document.getElementById(id);
  const gameTypeSelect = $('gameTypeSelect'), opponentSelect = $('opponentSelect'), gamesList = $('gamesList'), createBtn = $('createGameBtn'), statusBar = $('statusBar');
  let loading = false;
  function escapeHtml(v){ const d=document.createElement('div'); d.textContent=v??''; return d.innerHTML; }
  function status(msg, error=false){ statusBar.textContent=msg; statusBar.classList.remove('hidden','error'); if(error)statusBar.classList.add('error'); setTimeout(()=>statusBar.classList.add('hidden'),5000); }
  function avatar(name, url){ return url ? `<div class="avatar has-photo"><img src="${escapeHtml(url)}" alt="Foto de ${escapeHtml(name)}"></div>` : `<div class="avatar">${escapeHtml((name||'?')[0].toUpperCase())}</div>`; }
  function when(iso){ if(!iso)return ''; const d=new Date(iso); return d.toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); }
  async function copyText(text){try{await navigator.clipboard.writeText(text);status('Link do convite copiado.');}catch(_){prompt('Copie o link do convite:',text);}}
  function renderGames(games){
    gamesList.innerHTML='';
    if(!games.length){ gamesList.innerHTML='<div class="empty-state">Nenhuma partida ativa ou convite pendente.</div>'; return; }
    for(const g of games){
      const row=document.createElement('div'); row.className='game-list-row';
      if(g.status==='pending'){
        const pendingText=g.is_creator?`Aguardando ${g.opponent_name} aceitar`:`Convite de ${g.opponent_name}`;
        const action=g.is_creator
          ? `<button class="secondary compact copy-game-invite" type="button">Copiar link</button>`
          : `<a class="primary compact link-button" href="${escapeHtml(g.invite_url||g.url)}">Ver convite</a>`;
        row.innerHTML=`<div class="game-list-icon">${escapeHtml(g.game_icon)}</div>${avatar(g.opponent_name,g.opponent_photo_url)}<div class="game-list-main"><strong>${escapeHtml(g.game_name)}</strong><span>${escapeHtml(pendingText)}</span><small>${when(g.updated_at)}</small></div><span class="game-turn-pill pending">Convite</span><div class="game-list-actions">${action}<a class="secondary compact link-button" href="${escapeHtml(g.invite_url||g.url)}">Abrir</a></div>`;
        row.querySelector('.copy-game-invite')?.addEventListener('click',()=>copyText(g.invite_url));
      }else{
        const state = g.is_my_turn ? 'Sua vez' : `Vez de ${g.opponent_name}`;
        row.innerHTML=`<a class="game-list-click" href="${escapeHtml(g.url)}"><div class="game-list-icon">${escapeHtml(g.game_icon)}</div>${avatar(g.opponent_name,g.opponent_photo_url)}<div class="game-list-main"><strong>${escapeHtml(g.game_name)}</strong><span>com ${escapeHtml(g.opponent_name)} • ${escapeHtml(state)}</span><small>${when(g.updated_at)}</small></div><span class="game-turn-pill ${g.is_my_turn?'mine':''}">${escapeHtml(state)}</span></a>`;
      }
      gamesList.appendChild(row);
    }
  }
  async function load(){ if(loading)return; loading=true; try{
    const r=await fetch('/api/games',{cache:'no-store'}); if(r.status===401){location.href='/login';return;} const data=await r.json();
    const currentGame=gameTypeSelect.value, currentOpponent=opponentSelect.value;
    gameTypeSelect.innerHTML=''; for(const t of data.game_types||[]){const o=document.createElement('option');o.value=t.id;o.textContent=`${t.icon} ${t.name}`;gameTypeSelect.appendChild(o);} if(currentGame)gameTypeSelect.value=currentGame;
    opponentSelect.innerHTML=''; for(const u of data.users||[]){const o=document.createElement('option');o.value=u.id;o.textContent=`${u.full_name} (@${u.username})`;opponentSelect.appendChild(o);} if(currentOpponent)opponentSelect.value=currentOpponent;
    createBtn.disabled=!(data.users||[]).length;
    renderGames(data.games||[]);
  }catch(e){status('Não consegui carregar as partidas.',true);}finally{loading=false;} }
  createBtn.addEventListener('click',async()=>{
    if(!opponentSelect.value)return;
    createBtn.disabled=true;createBtn.textContent='Criando convite...';
    try{
      const r=await fetch('/api/games',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({game_type:gameTypeSelect.value,opponent_id:Number(opponentSelect.value)})});
      const data=await r.json();if(!r.ok)throw new Error(data.error||'Falha ao criar convite.');
      await copyText(data.invite_url);
      status('Convite criado. O link foi copiado.');
      await load();
    }catch(e){status(e.message,true);}finally{createBtn.disabled=false;createBtn.textContent='Criar partida';}
  });
  load(); setInterval(load,5000); setInterval(()=>NossaSala.ping(),20000);
})();
