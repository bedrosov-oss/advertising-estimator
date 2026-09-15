'use strict';
(()=>{
  let ready=false;
  const get=id=>document.getElementById('monitor-'+id);
  const date=value=>value?new Date(value).toLocaleString('ru-RU'):'';
  async function refresh(){
    const [sources,state]=await Promise.all([api('/api/workspace/list',{kind:'price_source'}),api('/api/monitor/status',{})]);
    get('notice').textContent=state.notice+(!state.running&&state.schedules.some(row=>row.enabled)?' Планировщик сейчас не запущен: установите дополнения и перезапустите программу.':'');
    const configs=new Map(state.schedules.map(row=>[row.source_id,row]));
    get('sources').innerHTML=sources.items.length?'<table class="workspace-table"><thead><tr><th>Источник</th><th>Каждые, ч</th><th>Включено</th><th>Следующая проверка</th><th>Действия</th></tr></thead><tbody>'+sources.items.map(row=>{
      const config=configs.get(row.id)||{};
      return `<tr data-source="${esc(row.id)}"><td>${esc(row.name)}</td><td><input data-hours aria-label="Интервал в часах" type="number" min="1" max="168" value="${config.hours||24}"></td><td><input data-enabled aria-label="Расписание включено" type="checkbox" ${config.enabled?'checked':''}></td><td>${esc(config.enabled?date(config.next_due):'Выключено')}</td><td><button data-save>Сохранить</button> <button data-check>Проверить сейчас</button></td></tr>`;
    }).join('')+'</tbody></table>':'<p>Сначала сохраните подключение с URL во вкладке «Обновление цен».</p>';
    get('journal').innerHTML=state.checks.length?'<table class="workspace-table"><thead><tr><th>Завершена</th><th>Источник</th><th>Результат</th><th>Документ</th></tr></thead><tbody>'+state.checks.map(row=>`<tr><td>${esc(date(row.finished))}</td><td>${esc(sources.items.find(item=>item.id===row.source_id)?.name||'Удалённый источник')}</td><td>${esc(row.summary)}</td><td>${row.status!=='error'?`<button data-review="${esc(row.id)}">Открыть</button>`:''}</td></tr>`).join('')+'</tbody></table>':'<p>Журнал пока пуст.</p>';
  }
  async function act(event){
    const button=event.target.closest('button');if(!button||button.disabled)return;
    button.disabled=true;
    try{
      const row=button.closest('[data-source]');
      if(button.hasAttribute('data-save'))await api('/api/monitor/configure',{source_id:row.dataset.source,hours:Number(row.querySelector('[data-hours]').value),enabled:row.querySelector('[data-enabled]').checked});
      else if(button.hasAttribute('data-check')){get('status').textContent='Проверка поставщика…';await api('/api/monitor/check',{source_id:row.dataset.source});}
      else if(button.dataset.review){const book=await api('/api/monitor/review',{id:button.dataset.review});document.dispatchEvent(new CustomEvent('estimator-price-review',{detail:book}));return;}
      await refresh();get('status').textContent='Готово.';
    }catch(error){get('status').textContent=error.message;}
    finally{button.disabled=false;}
  }
  function init(){
    if(ready)return;ready=true;
    const button=document.createElement('button');button.type='button';button.className='nav';button.dataset.tab='price-monitor';button.textContent='Расписание цен';button.addEventListener('click',()=>{setTab('price-monitor');refresh().catch(error=>get('status').textContent=error.message);});document.querySelector('nav').append(button);titles['price-monitor']='Расписание цен';
    const section=document.createElement('section');section.id='price-monitor';section.className='tab-panel';section.hidden=true;
    section.innerHTML='<div class="card"><h2>Расписание поставщиков</h2><p id="monitor-notice">Включается отдельно для каждого сохранённого источника.</p><button>Обновить журнал</button><p id="monitor-status" role="status"></p><div id="monitor-sources" class="prices-scroll"></div></div><div class="card"><h2>Журнал проверок</h2><div id="monitor-journal" class="prices-scroll"></div></div>';
    document.querySelector('main footer').before(section);section.addEventListener('click',act);
  }
  document.addEventListener('estimator-ready',init);
})();
