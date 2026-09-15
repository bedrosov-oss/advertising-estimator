'use strict';
// UI organisation only: the existing forms keep their IDs, validation and handlers.
(()=>{
  const routes=[
    ['supply-hub','Автоподбор и доставка','Наличие, прайсы и ежедневные тарифы','box'],
    ['estimate','Текущая смета','Заказ, позиции и расчёт','file'],
    ['orders','Мои заказы','Сохранённые сметы, версии и копии','folder'],
    ['advanced-tools','Документы','PDF, Word и Excel для текущей сметы','document'],
    ['own','Материалы и расценки','Ваш справочник для новых заказов','grid'],
    ['price-updates','Обновление цен','Прайсы поставщиков и проверка изменений','refresh'],
    ['price-monitor','Расписание цен','Периодические проверки и журнал','clock'],
    ['stock','Склад','Остатки, резервы и движение материалов','box'],
    ['production','Шаблоны и операции','Расход материалов и стоимость работ','layers'],
    ['advanced-geometry','Раскрой и чертежи','Листы, смешанные детали, SVG и DXF','ruler'],
    ['compare','Сравнение вариантов','Сметы и предложения поставщиков','compare'],
    ['actual','План и факт','Проверка фактических затрат','chart'],
    ['catalog','Примеры рыночных цен','Исторические наблюдения из открытых источников','tag'],
    ['sources','Сайты поставщиков','Ссылки на прайсы и калькуляторы','link'],
    ['search','Поиск в интернете','Поиск предложений через Tavily','search'],
    ['assistant','Помощник','Черновик по описанию задания','spark'],
    ['settings','Моя компания','Реквизиты и логотип в документах','company'],
    ['connections','Подключения','Ключи DaData и Tavily','key'],
    ['help','Инструкция','Пошаговое руководство и ответы на вопросы','help']
  ];
  const paths={file:'M7 3h7l4 4v14H5V3h2m8 0v5h4M8 12h7M8 16h5',folder:'M3 7V5h6l2 2h10v13H3V7z',document:'M6 3h9l4 4v14H6V3zm3 9h7m-7 4h7',grid:'M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z',refresh:'M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3',clock:'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',box:'M3 7l9-4 9 4v10l-9 4-9-4V7zm0 0l9 5 9-5M12 12v9M7 5l10 5',layers:'M12 3l10 5-10 5L2 8l10-5zm-10 10 10 5 10-5M2 18l10 5 10-5',ruler:'M3 16 16 3l5 5L8 21l-5-5zm3-3 3 3m0-6 3 3m0-6 3 3',compare:'M7 3v18m10-18v18M3 7l4-4 4 4m2 10 4 4 4-4',chart:'M4 3v18h17M8 17v-5m5 5V7m5 10v-8',tag:'M3 3h9l9 9-9 9-9-9V3zm4 4h.01',link:'M10 14l4-4m-6 6-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 2 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0',search:'M10 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm6-2 6 6',spark:'m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3z',company:'M5 21V3h14v18M2 21h20M9 7h1m4 0h1M9 11h1m4 0h1m-5 10v-6h4v6',key:'M14 10a5 5 0 1 1-4-4 5 5 0 0 1 4 4zm-2 4 8 7 2-2-2-2 1-1-3-3',help:'M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'};
  const icon=name=>`<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.file}"/></svg>`;
  let ready=false,current='estimate',demo=true;
  const navFor=id=>document.querySelector(`.nav[data-tab="${id}"]`);
  function navigate(id,focus){
    const nav=navFor(id);if(!nav)return;
    nav.click();
    if(focus){const target=$(focus);if(target){let ancestor=target.parentElement;while(ancestor){if(ancestor.tagName==='DETAILS')ancestor.open=true;ancestor=ancestor.parentElement;}target.scrollIntoView?.({block:'center',behavior:'smooth'});target.focus({preventScroll:true});}}
  }
  function button(label,id,handler,primary=false){const b=document.createElement('button');b.type='button';if(id)b.id=id;b.textContent=label;b.className=primary?'primary':'';if(handler)b.addEventListener('click',handler);return b;}
  function section(id,title,description){const s=document.createElement('section');s.id=id;s.className='tab-panel';s.hidden=true;s.innerHTML=`<div class="intro"><h2>${esc(title)}</h2><p>${esc(description)}</p></div>`;document.querySelector('main footer').before(s);const nav=button(title,null,()=>{setTab(id);if(id==='stock')$('advanced-stock-refresh').click();});nav.className='nav';nav.dataset.tab=id;document.querySelector('.sidebar nav').append(nav);return s;}
  function disclosure(label,content,id){const d=document.createElement('details');d.className='section-disclosure';if(id)d.id=id;const summary=document.createElement('summary');summary.textContent=label;d.append(summary,content);return d;}
  function splitTools(){
    const docs=$('advanced-tools');const cards=Array.from(docs.querySelectorAll(':scope > .card'));
    const stock=section('stock','Склад материалов','Сначала внесите приход, затем зарезервируйте материал под сохранённый заказ.');stock.append(cards[1]);
    const connections=section('connections','Подключения сервисов','Для локальных смет ключи не нужны. Подключите сервис, когда понадобится поиск реквизитов или предложений.');connections.append(cards[3]);
    $('own').append(disclosure('Найти похожие названия в справочнике',cards[2]));
    const intro=document.createElement('div');intro.className='intro';intro.innerHTML='<h2>Документы текущей сметы</h2><p>Выберите получателя, затем формат. Выгрузка использует открытый заказ и ваши реквизиты.</p>';docs.prepend(intro);
    const grid=document.createElement('div');grid.className='document-grid';
    const client=document.createElement('section');client.className='card document-card';client.innerHTML='<span class="section-kicker">ЗАКАЗЧИКУ</span><h2>Коммерческое предложение</h2><p>Состав заказа и цена продажи. Закупочные цены и прибыль в этот документ не входят.</p><div class="document-actions"></div>';
    const internal=document.createElement('section');internal.className='card document-card';internal.innerHTML='<span class="section-kicker">ДЛЯ РАБОТЫ</span><h2>Внутренняя калькуляция</h2><p>Затраты, наценка и расчёт прибыли. Excel с формулами можно редактировать отдельно от программы.</p><div class="document-actions"></div>';
    grid.append(client,internal);docs.insertBefore(grid,cards[0]);
    const clientActions=client.querySelector('.document-actions'),internalActions=internal.querySelector('.document-actions');
    for(const id of ['pdf-client','advanced-docx','xlsx-client','client-report'])clientActions.append($(id));
    for(const id of ['advanced-xlsx','pdf-internal','xlsx-internal','internal-report'])internalActions.append($(id));
    $('pdf-client').textContent='Скачать PDF';$('advanced-docx').textContent='Скачать Word';$('xlsx-client').textContent='Скачать Excel';$('client-report').textContent='Версия для печати · HTML';
    $('advanced-xlsx').textContent='Excel с формулами';$('advanced-xlsx').classList.add('primary');$('pdf-internal').textContent='Калькуляция PDF';$('xlsx-internal').textContent='Калькуляция Excel';$('internal-report').textContent='Калькуляция HTML';
    $('client-report').classList.remove('primary');
    const backupButton=$('advanced-backup');
    const templateLabel=$('advanced-template-file').closest('label'),templateButton=$('advanced-template');
    cards[0].replaceChildren();cards[0].classList.add('document-options');cards[0].innerHTML='<h2>Фирменный стиль</h2><p>Укажите реквизиты в «Моей компании». Для Word можно скачать и изменить стандартный шаблон.</p>';
    cards[0].append(button('Открыть реквизиты компании',null,()=>navigate('settings')),disclosure('Свой шаблон Word',templateLabel));cards[0].querySelector('details').append(templateButton);
    const backupCard=document.createElement('section');backupCard.className='card backup-card';backupCard.innerHTML='<h2>Резервное копирование</h2><p>Полная копия сохраняет сметы, склад, журнал и исходные документы. Ключи сервисов перенесите отдельно.</p><div class="row-tools"></div>';
    backupCard.querySelector('.row-tools').append(backupButton,button('Как восстановить копию',null,()=>document.dispatchEvent(new CustomEvent('open-guide',{detail:{topic:'backup'}}))));backupButton.classList.add('primary');$('orders').append(backupCard);
    const report=document.querySelector('.report-buttons');report.replaceChildren(button('Подготовить документы', 'summary-documents',()=>navigate('advanced-tools'),true));
    const reportNote=document.querySelector('.summary > .fine');if(reportNote)reportNote.textContent='PDF и Word для заказчика, Excel и калькуляция — в одном разделе.';
  }
  function organiseNavigation(){
    const nav=document.querySelector('.sidebar nav');nav.id='main-navigation';
    const search=document.createElement('label');search.className='nav-search';search.innerHTML=icon('search')+'<span class="sr-only">Найти раздел программы</span><input id="navigation-search" type="search" placeholder="Найти раздел…" autocomplete="off">';nav.before(search);
    const groups=[['СМЕТЫ',['estimate','orders','advanced-tools']],['МАТЕРИАЛЫ',['own','supply-hub','price-updates','price-monitor','stock']],['ПРОИЗВОДСТВО',['production','advanced-geometry','compare','actual']],['ЕЩЁ ВОЗМОЖНОСТИ',['catalog','sources','search','assistant','settings','connections']]];
    for(const [id,title,description,glyph]of routes){const b=navFor(id);if(!b)continue;const count=id==='catalog'?$('price-count'):null;b.replaceChildren();b.insertAdjacentHTML('beforeend',icon(glyph)+`<span class="nav-label">${esc(title)}</span>`);if(count)b.append(count);b.title=description;b.setAttribute('aria-controls',id);titles[id]=title;}
    groups.forEach(([label,ids],index)=>{const group=document.createElement('details');group.className='nav-group';group.open=index<3;const summary=document.createElement('summary');summary.textContent=label;group.append(summary);for(const id of ids){const b=navFor(id);if(b)group.append(b);}nav.append(group);});
    const foot=document.querySelector('.sidebar-foot');foot.prepend(navFor('help'));foot.querySelector('p').textContent=boot.hosted?'Проекты сохранены на сервере':'Сметы и данные на этом компьютере';
    const local=foot.querySelector('.status-dot');if(local)local.parentNode.insertBefore(document.createTextNode(''),local);
    const no=document.createElement('p');no.id='navigation-empty';no.hidden=true;no.className='muted';no.textContent='Раздел не найден. Попробуйте «цены», «склад» или «инструкция».';nav.append(no);
    let priorGroups=null;
    $('navigation-search').addEventListener('input',event=>{const q=event.target.value.trim().toLocaleLowerCase('ru');if(q&&!priorGroups)priorGroups=Array.from(nav.querySelectorAll('details')).map(d=>d.open);let found=0;for(const [id,title,description]of routes){const b=navFor(id);if(!b)continue;const visible=!q||(title+' '+description).toLocaleLowerCase('ru').includes(q);b.hidden=!visible;if(visible)found++;}nav.querySelectorAll('.nav-group').forEach((group,index)=>{group.hidden=!Array.from(group.querySelectorAll('.nav')).some(b=>!b.hidden);if(q)group.open=true;else if(priorGroups)group.open=priorGroups[index];});no.hidden=found>0;if(!q)priorGroups=null;});
  }
  function topActions(){
    const actions=document.querySelector('.top-actions');$('save-project').classList.remove('primary');$('save-project').textContent='Скачать заказ · JSON';$('open-project').textContent='Открыть файл · JSON';
    const more=document.createElement('details');more.className='actions-menu';more.id='file-actions';more.innerHTML='<summary>Файл <span aria-hidden="true">⌄</span></summary><div class="action-popover"></div>';
    actions.append(more);more.querySelector('div').append($('open-project'),$('save-project'),$('demo-project'));$('demo-project').textContent='Открыть учебный пример';
    const save=$('top-order-save');save.textContent='Сохранить смету';save.className='primary';save.title=(boot.hosted?'Сохранить на сервере':'Сохранить на этом компьютере')+' · Ctrl / ⌘ + S';
    const fresh=$('new-project');fresh.textContent='+ Новая смета';fresh.className='';
    actions.replaceChildren(fresh,more,save);
    const label=document.querySelector('.topbar .eyebrow');label.textContent='РАБОЧЕЕ МЕСТО / СМЕТЫ';
    const subtitle=document.createElement('p');subtitle.id='page-description';subtitle.className='muted';$('page-title').after(subtitle);
    const utility=document.createElement('div');utility.className='page-utility';utility.append(button('Помощь по разделу · F1','context-help',()=>document.dispatchEvent(new CustomEvent('open-guide',{detail:{tab:current}}))));
    const version=document.createElement('span');version.dataset.appVersion='';version.className='version-label';utility.append(version);document.querySelector('.topbar').after(utility);
    const menu=button('Разделы','navigation-toggle',()=>toggleMobile());menu.setAttribute('aria-controls','main-navigation');menu.setAttribute('aria-expanded','false');document.querySelector('.topbar').prepend(menu);
    const close=button('Закрыть меню','navigation-close',()=>toggleMobile(false));document.querySelector('.sidebar .brand').after(close);
    const overlay=button('Закрыть меню разделов','navigation-backdrop',()=>toggleMobile(false));overlay.tabIndex=-1;overlay.hidden=true;document.querySelector('.shell').prepend(overlay);
    document.addEventListener('click',event=>{if(!more.contains(event.target))more.open=false;});more.addEventListener('click',event=>{if(event.target.closest('button'))more.open=false;});
  }
  function toggleMobile(force){const open=force??!document.body.classList.contains('navigation-open');document.body.classList.toggle('navigation-open',open);$('navigation-toggle').setAttribute('aria-expanded',String(open));$('navigation-backdrop').hidden=!open;document.querySelector('main').inert=open;document.querySelector('.sidebar').inert=!open&&!!window.matchMedia?.('(max-width:800px)').matches;if(open)$('navigation-close').focus();else $('navigation-toggle').focus();}
  function estimateLayout(){
    const cards=document.querySelectorAll('#estimate .work-column > .card');cards[0].id='order-details';cards[1].id='estimate-positions';cards[2].id='estimate-pricing';
    const head=cards[0].querySelector('h2');head.innerHTML='<span class="section-number">01</span> Заказ';
    cards[1].querySelector('h2').innerHTML='<span class="section-number">02</span> Позиции сметы';cards[2].querySelector('h2').innerHTML='<span class="section-number">03</span> Цена и проверка';
    const customer=document.querySelector('.customer-section');if(customer){const d=disclosure('Реквизиты заказчика · ИНН / ОГРН',customer,'customer-disclosure');cards[0].append(d);}
    const fields=cards[0].querySelector('.form-grid');fields.classList.add('order-fields');$('project-title').closest('label').classList.add('order-title-field');$('project-quantity').closest('label').classList.add('order-quantity-field');
    $('project-title').closest('label').after($('project-quantity').closest('label'));
    const notes=$('project-notes').closest('label');const noteDetails=disclosure('Комплектация, сроки и примечания',notes,'order-notes-disclosure');cards[0].append(noteDetails);
    const strip=document.createElement('div');strip.className='workflow-strip';strip.setAttribute('aria-label','Порядок подготовки сметы');
    [['01','Заказ','order-details','project-title'],['02','Позиции','estimate-positions','quick-entry'],['03','Проверка','estimate-pricing','pricing-mode'],['04','Документы',null,null]].forEach(([n,text,id,focus])=>{const b=button('',null,()=>id?navigate('estimate',focus):navigate('advanced-tools'));b.innerHTML=`<span>${n}</span>${text}`;strip.append(b);});$('estimate').prepend(strip);
    const banner=document.createElement('div');banner.className='demo-banner';banner.id='demo-banner';banner.innerHTML='<span><strong>Учебный пример</strong> · Это образец, цены нужно заменить своими.</span>';banner.append(button('Начать свою смету',null,()=>$('new-project').click()));strip.after(banner);
    $('go-catalog').textContent='Примеры рыночных цен';$('quick-entry').textContent='+ Добавить позицию';$('add-row').textContent='Полная карточка';
    const own=button('Из моих материалов','pick-own-material',()=>navigate('own'));document.querySelector('.entry-actions').append(own);
    const extra=document.querySelector('#estimate-positions .row-tools');const extraParent=extra.parentNode;extraParent.append(disclosure('Импорт, экспорт и расчёт расхода',extra));
    const check=document.createElement('p');check.className='inline-hint';check.textContent='Количество и цену можно менять прямо в таблице. Пустая цена означает «нужно уточнить», 0 — бесплатную позицию.';document.querySelector('#estimate-positions .table-wrap').before(check);
    const summary=document.querySelector('.summary');summary.id='estimate-summary';summary.tabIndex=-1;summary.setAttribute('aria-label','Итог текущей сметы');
    const checkAction=button('Что осталось проверить','review-missing',()=>{const d=$('missing-details');d.open=true;d.scrollIntoView?.({block:'center',behavior:'smooth'});d.querySelector('summary').focus();});$('unit-total').after(checkAction);
    const saveState=document.createElement('span');saveState.id='save-state';saveState.className='save-state';saveState.setAttribute('role','status');$('project-hint').before(saveState);
  }
  function improveLabels(){
    for(const panel of document.querySelectorAll('.tab-panel')){panel.setAttribute('aria-labelledby','page-title');panel.querySelectorAll('input[type=search]').forEach(input=>{if(!input.getAttribute('aria-label')&&!input.closest('label'))input.setAttribute('aria-label',input.placeholder||'Поиск');});}
    const status=$('notice');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    for(const table of document.querySelectorAll('.table-wrap')){table.tabIndex=0;table.setAttribute('role','region');table.setAttribute('aria-label','Таблица: прокрутите вправо, если не все столбцы видны');}
    $('page-title').tabIndex=-1;
  }
  function updateState(){
    if(!$('save-state'))return;$('save-state').textContent=dirty?'Есть изменения':'Изменений нет';$('save-state').classList.toggle('changed',dirty);
    $('demo-banner').hidden=!demo;
    const count=result?.missing?.length;$('review-missing').hidden=!count;$('review-missing').textContent=`Проверить замечания · ${count||0}`;
  }
  function tabChanged(event){current=event.detail.tab;const route=routes.find(x=>x[0]===current);$('page-description').textContent=route?.[2]||'';document.querySelector('.topbar .eyebrow').textContent=current==='estimate'?'РАБОЧЕЕ МЕСТО / ТЕКУЩИЙ ЗАКАЗ':'РАБОЧЕЕ МЕСТО / '+(route?.[1]||'').toLocaleUpperCase('ru');document.querySelectorAll('.nav').forEach(b=>{if(b.dataset.tab===current){b.setAttribute('aria-current','page');const group=b.closest('details');if(group)group.open=true;}else b.removeAttribute('aria-current');});if(document.body.classList.contains('navigation-open'))toggleMobile(false);$('page-title').focus({preventScroll:true});document.querySelector('.topbar').scrollIntoView?.({block:'start'});}
  function init(){if(ready)return;ready=true;splitTools();organiseNavigation();topActions();estimateLayout();improveLabels();document.querySelectorAll('[data-app-version]').forEach(e=>e.textContent='v'+boot.version);document.addEventListener('estimator-tab-changed',tabChanged);document.addEventListener('estimate-loaded',e=>{demo=!!e.detail?.demo;updateState();});for(const event of ['estimate-change','estimate-calculated','estimate-saved'])document.addEventListener(event,updateState);document.addEventListener('navigate-estimator',e=>navigate(e.detail.tab,e.detail.focus));
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(document.body.classList.contains('navigation-open'))toggleMobile(false);$('file-actions').open=false;}if(document.querySelector('dialog[open]'))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();$('top-order-save').click();}if(e.key==='F1'){e.preventDefault();$('context-help').click();}if(e.key==='Tab'&&document.body.classList.contains('navigation-open')){const visible=Array.from(document.querySelector('.sidebar').querySelectorAll('button,input,summary')).filter(x=>!x.hidden&&x.getClientRects().length);const first=visible[0],last=visible.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
    const mobile=window.matchMedia?.('(max-width:800px)');if(mobile){const sync=()=>{if(!mobile.matches&&document.body.classList.contains('navigation-open'))toggleMobile(false);document.querySelector('.sidebar').inert=mobile.matches&&!document.body.classList.contains('navigation-open');};mobile.addEventListener?.('change',sync);sync();}
    tabChanged({detail:{tab:'estimate'}});updateState();document.dispatchEvent(new CustomEvent('estimator-interface-ready'));
  }
  document.addEventListener('estimator-ready',()=>queueMicrotask(init));if(typeof boot!=='undefined'&&boot)queueMicrotask(init);
})();
