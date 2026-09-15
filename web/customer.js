'use strict';
// Credentials and lookup sessions stay in this page; only a normalized card is saved.
(() => {
  const FNS='https://egrul.nalog.ru/index.html';
  const DADATA='https://dadata.ru/api/find-party/';
  let generation=0, debounce, initialized=false;
  function extras(){project.extensions??={};return project.extensions;}
  function normalizedQuery(){return $('customer-query').value.replace(/\s+/g,'');}
  function validIdentifier(value){
    if(!/^(?:\d{10}|\d{12}|\d{13}|\d{15})$/.test(value)||new Set(value).size===1)return false;
    const n=[...value].map(Number),sum=w=>w.reduce((s,k,i)=>s+k*n[i],0)%11%10;
    if(n.length===10)return sum([2,4,10,3,5,9,4,6,8])===n[9];
    if(n.length===12)return sum([7,2,4,10,3,5,9,4,6,8])===n[10]&&sum([3,7,2,4,10,3,5,9,4,6,8])===n[11];
    return Number(value.slice(0,-1))%(n.length===13?11:13)%10===n[n.length-1];
  }
  function status(text,error=false){$('customer-status').textContent=text;$('customer-status').className=error?'error-box':'fine';}
  function cancel(){generation++;clearTimeout(debounce);$('customer-fetch').disabled=false;$('customer-results').replaceChildren();}
  function displayTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('ru-RU');}
  function render(){
    const c=extras().customer||{};
    $('customer-card').hidden=!c.full_name;
    $('customer-pdf').hidden=!c.document?.sha256;
    $('customer-fns-pdf').hidden=!c.full_name||c.provider!=='dadata';
    if(!c.full_name){$('customer-details').replaceChildren();return;}
    const labels={full_name:'Полное наименование',short_name:'Краткое наименование',inn:'ИНН',ogrn:'ОГРН / ОГРНИП',kpp:'КПП',address:'Адрес',head:'Руководитель',registration_date:'Дата регистрации',termination_date:'Дата прекращения',actuality_date:'Последние изменения по данным источника'};
    const states={ACTIVE:'Действующая',LIQUIDATING:'Ликвидируется',LIQUIDATED:'Ликвидирована',BANKRUPT:'Банкротство',REORGANIZING:'Реорганизуется'};
    $('customer-details').innerHTML=Object.entries(labels).filter(([k])=>c[k]).map(([k,v])=>`<dt>${esc(v)}</dt><dd>${esc(c[k])}</dd>`).join('')+
      (c.status?`<dt>Статус по данным источника</dt><dd>${esc(states[c.status]||c.status)}</dd>`:'')+
      `<dt>Источник</dt><dd><a href="${c.provider==='dadata'?DADATA:FNS}" target="_blank" rel="noopener noreferrer">${c.provider==='dadata'?'DaData':'ФНС'}</a></dd><dt>Получено</dt><dd>${esc(displayTime(c.retrieved_at))}</dd>`;
    $('customer-invalid').hidden=!c.invalid;
    $('customer-document-note').textContent=c.document?.sha256?
      'PDF сохранён локально '+displayTime(c.document.retrieved_at)+'. При переносе заказа на другой компьютер нужно перенести и файл выписки.':
      c.provider==='dadata'?'Реквизиты получены через DaData. Оригинал PDF-выписки можно запросить отдельно через ФНС.':'PDF-выписка пока не сохранена.';
  }
  function sourceHint(){
    const dadata=$('customer-provider').value==='dadata';
    $('customer-key-box').hidden=!dadata;
    $('customer-source-note').textContent=dadata?
      'Выбранной DaData передаётся только введённый номер. Находится головная организация или ИП; для подключения нужен API-ключ.':
      'В ФНС передаётся только введённый номер. Поиск и сохранение PDF зависят от доступности сайта; возможна капча.';
  }
  function savedHint(){const c=extras().customer;return c?.full_name?' Сохранены прежние сведения от '+displayTime(c.retrieved_at)+'.':'';}
  function schedule(){
    clearTimeout(debounce);
    if(!$('customer-auto').checked||!validIdentifier(normalizedQuery()))return;
    if($('customer-provider').value==='dadata'&&!$('customer-key').value&&!document.getElementById('advanced-use-dadata')?.checked){status('Для автоматического поиска введите API-ключ DaData или выберите ФНС.');return;}
    debounce=setTimeout(search,1200);
  }
  function queryChanged(){
    cancel();
    const e=extras(),old=e.customer;
    e.customer_query=$('customer-query').value;
    if(old?.query!==normalizedQuery()){
      const autoName=(old?.short_name||old?.full_name||'').slice(0,500);
      if(autoName&&$('project-client').value===autoName){$('project-client').value='';project.project.client='';}
      e.customer={};
    }
    render();markDirty();scheduleCalculate();
    status(normalizedQuery()?'Введите полный номер. Можно вставить ИНН, ОГРН или ОГРНИП.':'Укажите номер заказчика для получения реквизитов.');schedule();
  }
  async function pause(){await new Promise(resolve=>setTimeout(resolve,1000));}
  async function search(){
    cancel();
    const query=normalizedQuery(),provider=$('customer-provider').value,key=$('customer-key').value.trim();
    if(!validIdentifier(query)){status('Проверьте длину и контрольные цифры ИНН / ОГРН / ОГРНИП.',true);return;}
    if(provider==='dadata'&&!key&&!document.getElementById('advanced-use-dadata')?.checked){status('Введите API-ключ DaData из личного кабинета или выберите ФНС.',true);return;}
    const mine=generation,original=project;
    const current=()=>generation===mine&&project===original&&normalizedQuery()===query;
    $('customer-fetch').disabled=true;status('Ищем заказчика: '+(provider==='dadata'?'DaData':'ФНС')+'…');
    try{
      let response=await api(provider==='dadata'?'/api/customer/dadata':'/api/fns/search',provider==='dadata'?{query,api_key:key,use_saved_key:!!document.getElementById('advanced-use-dadata')?.checked}:{query});
      if(!current())return;
      if(provider==='fns'){
        const lookup=response.lookup_id;
        for(let attempt=0;attempt<30;attempt++){
          response=await api('/api/fns/search-result',{lookup_id:lookup});
          if(!current())return;
          if(response.status==='ready')break;
          await pause();if(!current())return;
        }
      }
      if(response.status!=='ready')throw new Error('Источник ещё готовит ответ. Повторите поиск позже.');
      if(!response.results.length){status('По этому номеру записи не найдены.'+savedHint(),true);return;}
      const choose=async item=>{
        if(!current())return;
        $('customer-results').replaceChildren();
        extras().customer=structuredClone(item.customer);extras().customer_query=query;
        $('customer-query').value=query;
        const name=(item.customer.short_name||item.customer.full_name).slice(0,500);
        $('project-client').value=name;project.project.client=name;
        render();markDirty();scheduleCalculate(0);status('Реквизиты заказчика заполнены.');
        if(provider!=='fns')return;
        $('customer-fetch').disabled=true;
        try{
          const payload={lookup_id:response.lookup_id,choice_id:item.choice_id};
          status('Реквизиты заполнены. ФНС формирует PDF-выписку…');
          await api('/api/fns/extract-start',payload);if(!current())return;
          for(let attempt=0;attempt<45;attempt++){
            const extract=await api('/api/fns/extract-result',payload);if(!current())return;
            if(extract.status==='ready'){
              extras().customer.document=extract.document;render();markDirty();
              status('Реквизиты заполнены, оригинал PDF сохранён на компьютере. Кнопка «Скачать PDF» сохранит копию в выбранную папку.');return;
            }
            await pause();if(!current())return;
          }
          throw new Error('ФНС пока не подготовила PDF. Повторите получение позже.');
        }catch(error){if(current())status('Реквизиты сохранены, PDF получить не удалось. '+error.message,true);}
        finally{if(current())$('customer-fetch').disabled=false;}
      };
      if(response.results.length===1)await choose(response.results[0]);
      else{
        status('По номеру найдено несколько записей. Выберите нужного заказчика.');
        response.results.forEach(item=>{
          const button=document.createElement('button');
          button.type='button';button.textContent=item.customer.full_name+' · ОГРН '+item.customer.ogrn+(item.customer.termination_date?' · прекращение '+item.customer.termination_date:'');
          button.addEventListener('click',()=>choose(item));$('customer-results').append(button);
        });
      }
    }catch(error){if(current())status(error.message+savedHint(),true);}
    finally{if(current())$('customer-fetch').disabled=false;}
  }
  async function downloadPDF(){
    const description=extras().customer?.document;if(!description)return;
    const original=project,mine=generation;
    $('customer-pdf').disabled=true;
    try{
      const result=await api('/api/fns/saved-document',{document:description});
      if(project!==original||generation!==mine)return;
      download(Uint8Array.from(atob(result.content),c=>c.charCodeAt(0)),result.document.filename,'application/pdf');
    }catch(error){if(project===original&&generation===mine)status(error.message,true);}
    finally{$('customer-pdf').disabled=false;}
  }
  function loaded(){
    cancel();$('customer-query').value=extras().customer_query||extras().customer?.query||'';
    render();status(extras().customer?.full_name?'Показаны сохранённые реквизиты. Нажмите «Получить / обновить», чтобы запросить их заново.':'Укажите ИНН / ОГРН заказчика.');
  }
  function init(){
    if(initialized)return;initialized=true;
    const section=document.createElement('section');section.className='customer-section';section.id='customer-section';
    section.innerHTML=`<div class="card-head"><h2>Реквизиты заказчика</h2></div>
      <div class="form-grid"><label>ИНН / ОГРН / ОГРНИП<input id="customer-query" inputmode="numeric" maxlength="40" autocomplete="off" placeholder="Введите или вставьте номер"></label>
      <label>Источник<select id="customer-provider"><option value="dadata">DaData · API-ключ</option><option value="fns">ФНС · без ключа</option></select></label></div>
      <div id="customer-key-box"><label>API-ключ DaData<input id="customer-key" type="password" autocomplete="off" spellcheck="false" maxlength="256" placeholder="API-ключ из кабинета DaData"></label>
      <div class="key-note"><span class="fine">Действует в открытой странице; в заказ и резервные копии не сохраняется. Secret key не требуется.</span><button id="customer-clear-key" type="button">Очистить ключ</button></div>
      <a href="https://dadata.ru/profile/" target="_blank" rel="noopener noreferrer">Открыть кабинет DaData</a></div>
      <p id="customer-source-note" class="fine"></p>
      <label class="check"><input id="customer-auto" type="checkbox" checked> Автоматически искать после ввода полного номера</label>
      <div class="customer-actions"><button id="customer-fetch" type="button">Получить / обновить</button><button id="customer-cancel" type="button">Остановить ожидание</button><a href="${FNS}" target="_blank" rel="noopener noreferrer">Открыть ФНС</a></div>
      <p id="customer-status" role="status" aria-live="polite" class="fine"></p><div id="customer-results"></div>
      <div id="customer-card" hidden><dl id="customer-details" class="price-detail-list"></dl><p id="customer-invalid" class="error-box" hidden>Источник сообщает о недостоверных сведениях. Проверьте реквизиты.</p>
      <p id="customer-document-note" class="fine"></p><div class="customer-actions"><button id="customer-pdf" type="button" hidden>Скачать PDF</button><button id="customer-fns-pdf" type="button" hidden>Получить PDF через ФНС</button></div></div>
      <p class="fine">Если сайт ФНС просит капчу, откройте его и скачайте выписку вручную. Сеанс сайта отделён от программы. При недоступности одного источника выберите другой и повторите поиск.</p>`;
    $('project-client').closest('.form-grid').after(section);$('project-client').maxLength=500;
    $('customer-query').addEventListener('input',queryChanged);
    $('customer-fetch').addEventListener('click',search);
    $('customer-provider').addEventListener('change',()=>{cancel();sourceHint();status('Источник изменён. Сохранённая карточка показывает свой прежний источник.');schedule();});
    $('customer-key').addEventListener('input',()=>cancel());
    $('customer-key').addEventListener('change',schedule);
    $('customer-clear-key').addEventListener('click',()=>{cancel();$('customer-key').value='';status('API-ключ очищен.');});
    $('customer-auto').addEventListener('change',()=>{cancel();if($('customer-auto').checked)schedule();else status('Автоматический поиск выключен. Используйте кнопку получения реквизитов.');});
    $('customer-cancel').addEventListener('click',()=>{cancel();status('Ожидание остановлено. Уже отправленный запрос может завершиться на сервере; его ответ не изменит карточку.');});
    $('customer-pdf').addEventListener('click',downloadPDF);
    $('customer-fns-pdf').addEventListener('click',()=>{$('customer-provider').value='fns';sourceHint();search();});
    $('project-client').addEventListener('input',()=>{
      cancel();if(extras().customer?.full_name){extras().customer={};render();markDirty();status('Наименование изменено вручную. Прежняя карточка отсоединена; при необходимости повторите поиск.');}
    });
    document.addEventListener('estimate-loaded',loaded);
    sourceHint();loaded();
  }
  document.addEventListener('estimator-ready',init);
  if(typeof boot!=='undefined'&&boot&&typeof project!=='undefined'&&project)init();
})();
