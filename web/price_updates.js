'use strict';
// Supplier documents are read only on request. Catalogue writes use a server preview.
(() => {
  let initialized=false, generation=0, busy=false, applying=false, records=[], currentRecord=null;
  let book=null, preview=null, draft=null, listGeneration=0, downloading=false;
  const ids=['name','supplier','url','format','currency','unit','date','notes','start','pdf-mode','ocr-language'];
  const mappings={name:'Наименование',article:'Артикул',unit:'Единица цены',price:'Цена',currency:'Валюта'};
  const defaults=()=>({supplier:'',url:'',format:'csv',sheet:0,start:1,mapping:{name:-1,article:-1,unit:-1,price:-1,currency:-1},currency:'unknown',unit:'',source_date:'',notes:''});
  const ws=(action,payload)=>api('/api/workspace/'+action,payload);
  const el=key=>$('prices-'+key);
  function message(text,error=false){el('status').textContent=text;el('status').className=error?'error-box':'prices-status';}
  function time(value){const date=new Date(value);return Number.isNaN(date.getTime())?String(value||'Не указана'):date.toLocaleString('ru-RU');}
  function source(){
    const mapping={...draft.mapping};
    document.querySelectorAll('#prices-mapping select').forEach(select=>mapping[select.dataset.column]=Number(select.value));
    return {pdf_mode:el('pdf-mode').value,ocr_language:el('ocr-language').value,supplier:el('supplier').value.trim(),url:el('url').value.trim(),format:el('format').value,
      sheet:Number(el('sheet').value||draft.sheet||0),start:Number(el('start').value)-1,mapping,
      currency:el('currency').value,unit:el('unit').value.trim(),source_date:el('date').value,notes:el('notes').value.trim()};
  }
  function formatHint(){
    const product=['product','zenon','forda'].includes(el('format').value);
    el('format-hint').textContent=product?
      'Карточка товара поддерживается, если сайт публикует Product / Offer в JSON-LD с конкретной ценой в RUB. Укажите единицу этой цены по условиям поставщика. При отсутствии этих данных используйте прайс CSV / XLSX или внесите цену в «Мои материалы».':
      'Нужна прямая ссылка на CSV / XLSX или файл, полученный у поставщика. После чтения выберите столбцы. PDF: выберите страницу и режим чтения; для скана — OCR. Неоднозначные цены вносятся вручную.';
    el('currency').disabled=product||applying;
    el('currency-hint').textContent=product?'Валюта должна быть указана на сайте в данных товара. Настройка общей валюты здесь не применяется.':'Выбирайте RUB только если прайс поставщика действительно выражен в рублях. Можно вместо этого указать столбец валюты.';
    el('table-options').hidden=!book||product;
    el('file').disabled=product||applying;
    el('unit').placeholder=product?'Например: м², лист, рулон':'Если единица едина для всего прайса';
  }
  function selectedKeys(){return Array.from(document.querySelectorAll('#prices-changes [data-price-key]:checked')).map(input=>input.dataset.priceKey);}
  function controls(){
    const selected=selectedKeys().length;
    el('read').disabled=busy||applying;
    el('preview').disabled=!book||busy||applying;
    el('apply').disabled=!preview||!selected||!el('ack').checked||busy||applying;
    el('selected').textContent=selected?'Выбрано позиций: '+selected:'Выберите позиции, которые нужно сохранить.';
    el('select-all').disabled=!preview||busy||applying;
    el('select-none').disabled=!preview||busy||applying;
    el('delete').disabled=!currentRecord||applying;
    el('document').disabled=!book?.document||applying||downloading;
  }
  function invalidate(readAgain=false){
    generation++;busy=false;preview=null;
    el('ack').checked=false;el('review').hidden=true;el('changes').replaceChildren();el('issues').replaceChildren();
    if(readAgain){book=null;el('document-meta').replaceChildren();el('sample').replaceChildren();el('read-warnings').replaceChildren();el('mapping').replaceChildren();el('sheet').replaceChildren();el('document-box').hidden=true;}
    formatHint();controls();
  }
  function changed(readAgain=false){
    draft=source();invalidate(readAgain);
    message(readAgain?'Источник изменён. Нажмите «Проверить цены», чтобы получить данные.':'Параметры изменены. Нажмите «Показать изменения», чтобы заново сопоставить цены.');
  }
  function fill(record){
    currentRecord=record||null;draft={...defaults(),...(record?.data||{}),mapping:{...defaults().mapping,...(record?.data?.mapping||{})}};
    el('name').value=record?.name||'';
    for(const key of ['supplier','url','format','currency','unit','notes'])el(key).value=draft[key]??'';
    el('pdf-mode').value=draft.pdf_mode||'text';el('ocr-language').value=draft.ocr_language||'rus+eng';el('date').value=draft.source_date||'';el('start').value=String((draft.start??1)+1);el('file').value='';el('file-label').textContent='Локальный файл не выбран.';
    invalidate(true);formatHint();el('source-select').value=record?.id||'';
    message(record?'Подключение загружено. Нажмите «Проверить цены» для получения свежего документа.':'Добавьте поставщика и ссылку на прайс или выберите файл. Цены появятся в собственном справочнике после проверки и подтверждения.');
  }
  async function refreshList(){
    const mine=++listGeneration,response=await ws('list',{kind:'price_source'});
    if(mine!==listGeneration)return;
    records=response.items||[];el('source-select').innerHTML='<option value="">Новое подключение</option>'+records.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
    el('source-select').value=currentRecord?.id||'';
    el('source-empty').hidden=!!records.length;
  }
  async function lockWrite(button,action){
    if(applying)return;
    applying=true;generation++;busy=false;controls();
    const fields=Array.from(document.querySelectorAll('#price-updates input,#price-updates select,#price-updates textarea,#price-updates button'));
    const previous=fields.map(field=>field.disabled);fields.forEach(field=>field.disabled=true);
    button.setAttribute('aria-busy','true');
    try{await action();}catch(error){message(error.message,true);}
    finally{applying=false;fields.forEach((field,index)=>field.disabled=previous[index]);button.removeAttribute('aria-busy');formatHint();controls();}
  }
  async function saveSource(){
    const data=source(),name=el('name').value.trim();
    if(!name||!data.supplier){message('Укажите название подключения и поставщика.',true);return;}
    await lockWrite(el('save'),async()=>{
      currentRecord=await ws('save',{kind:'price_source',name,data,id:currentRecord?.id,expected_revision:currentRecord?.revision});
      draft={...data,mapping:{...data.mapping}};await refreshList();message('Подключение сохранено. Файл с компьютера при следующей проверке нужно выбрать заново.');
    });
  }
  async function deleteSource(){
    if(!currentRecord||!confirm('Удалить это подключение к поставщику?'))return;
    const record=currentRecord;
    await lockWrite(el('delete'),async()=>{await ws('delete',{kind:'price_source',id:record.id,expected_revision:record.revision});fill(null);await refreshList();message('Подключение удалено.');});
  }
  async function bytes64(file){
    const buffer=await file.arrayBuffer(),bytes=new Uint8Array(buffer);let value='';
    for(let i=0;i<bytes.length;i+=8192)value+=String.fromCharCode(...bytes.subarray(i,i+8192));
    return btoa(value);
  }
  function drawDocument(){
    const doc=book.document||{};el('document-box').hidden=false;
    el('document-meta').innerHTML=`<strong>${esc(doc.filename||'Документ поставщика')}</strong><span>Данные получены: ${esc(time(doc.retrieved_at))}</span><span>Источник: ${esc(doc.source_url||'Файл с компьютера')}</span>`;
    el('read-warnings').innerHTML=(book.warnings||[]).map(text=>`<li>${esc(text)}</li>`).join('');
    el('document').hidden=!doc.sha256;controls();
  }
  function drawRows(){
    const data=source(),rows=book.rows||[],headers=rows[0]?.values||[];
    el('sheet').innerHTML=(book.sheets||[]).map((sheet,index)=>`<option value="${index}"${sheet.hidden?' disabled':''}>${esc(sheet.name)}${sheet.hidden?' (скрыт)':''}</option>`).join('');
    el('sheet').value=String(data.sheet);if(!el('sheet').value&&el('sheet').options.length)el('sheet').value=el('sheet').options[0].value;
    const columns=Math.max(1,...rows.slice(0,30).map(row=>(row.values||[]).length));
    el('mapping').innerHTML=Object.entries(mappings).map(([key,label])=>`<label>${label}<select data-column="${key}"><option value="-1">Не выбран</option>${Array.from({length:columns},(_,index)=>`<option value="${index}">${index+1}: ${esc(String(headers[index]||'без заголовка').slice(0,120))}</option>`).join('')}</select></label>`).join('');
    document.querySelectorAll('#prices-mapping select').forEach(select=>{select.value=String(data.mapping[select.dataset.column]??-1);if(select.value==='')select.value='-1';});
    el('sample').innerHTML=rows.length?'<table class="workspace-table"><caption>Первые пять видимых строк документа</caption><tbody>'+rows.slice(0,5).map(row=>'<tr><th scope="row">'+esc(row.number)+'</th>'+row.values.map((value,index)=>'<td>'+esc(value)+((row.formulas||[]).includes(index)?' [формула]':'')+'</td>').join('')+'</tr>').join('')+'</tbody></table>':'<p class="muted">Строки не найдены. Проверьте выбранный лист.</p>';
    draft=source();formatHint();
  }
  function hasMapping(data){return ['product','zenon','forda'].includes(data.format)?!!data.unit:data.mapping.name>=0&&data.mapping.price>=0&&(data.mapping.unit>=0||!!data.unit)&&(data.mapping.currency>=0||data.currency==='RUB');}
  async function readPrices(){
    if(busy||applying)return;
    const data=source(),file=el('file').files[0];
    if(!data.supplier){message('Укажите поставщика, которому принадлежит прайс.',true);return;}
    if(!file&&!data.url){message('Вставьте прямую ссылку на прайс / карточку товара или выберите CSV / XLSX.',true);return;}
    if(file&&(file.size>8388608||!/[.](csv|xlsx|pdf)$/i.test(file.name))){message('Выберите CSV, XLSX или PDF размером не больше 8 МБ.',true);return;}
    draft=data;invalidate(true);const mine=generation;busy=true;controls();message('Получаем документ поставщика…');
    try{
      const payload={source:data};if(file){payload.content=await bytes64(file);payload.file_name=file.name;if(mine!==generation)return;}
      const response=await api('/api/prices/read',payload);if(mine!==generation)return;
      book=response;drawDocument();drawRows();
      if(hasMapping(source()))await makePreview(mine);
      else message('Данные получены. Укажите столбцы наименования и цены, единицу и валюту. Затем нажмите «Показать изменения».');
    }catch(error){if(mine===generation)message(error.message,true);}
    finally{if(mine===generation){busy=false;controls();}}
  }
  async function changeSheet(){
    if(!book||applying)return;
    const run=book.run_id,documentInfo=book.document,data=source(),original=book;draft=data;
    invalidate();book=null;el('table-options').hidden=true;const mine=generation;busy=true;controls();message('Читаем выбранный лист…');
    try{
      const response=await api('/api/prices/sheet',{run_id:run,sheet:data.sheet});if(mine!==generation)return;
      book={...original,...response,run_id:run,document:documentInfo};draft.sheet=data.sheet;el('sheet').value=String(data.sheet);drawRows();message('Лист прочитан. Проверьте столбцы и нажмите «Показать изменения».');
    }catch(error){if(mine===generation){book=null;el('table-options').hidden=true;message(error.message,true);}}
    finally{if(mine===generation){busy=false;controls();}}
  }
  function drawPreview(response){
    preview=response;el('review').hidden=false;el('ack').checked=false;
    el('checked').textContent='Проверено строк: '+response.checked_count+'. Дата прайса: '+(source().source_date||'поставщиком не указана / не внесена')+'.';
    el('issues').innerHTML=(response.issues||[]).length?'<h3>Строки, которые требуют уточнения</h3><ul>'+response.issues.map(issue=>'<li>Строка '+esc(issue.row)+': '+esc(issue.message)+'</li>').join('')+'</ul>':'';
    const labels={update:'Обновить',new:'Новая позиция',unchanged:'Цена прежняя · обновить сведения'};
    el('changes').innerHTML=response.items.length?'<table class="workspace-table prices-changes-table"><thead><tr><th>Выбор</th><th>Позиция / артикул</th><th>Единица</th><th>Было, ₽</th><th>Станет, ₽</th><th>Изменение</th><th>Действие / проверка</th></tr></thead><tbody>'+response.items.map(item=>`<tr><td><input type="checkbox" data-price-key="${esc(item.key)}" aria-label="Выбрать ${esc(item.name)}"></td><td><strong>${esc(item.name)}</strong><span>${esc(item.article||'Без артикула')}</span><span>${esc(item.supplier)}</span></td><td>${esc(item.unit)}</td><td class="prices-amount">${money(item.old_price)}</td><td class="prices-amount">${money(item.new_price)}</td><td class="prices-amount">${item.change_percent===null||item.change_percent===undefined?'—':esc(item.change_percent)+' %'}</td><td><span class="prices-action prices-action-${['new','update','unchanged'].includes(item.action)?item.action:'update'}">${esc(labels[item.action]||item.action)}</span>${item.warning?'<p class="prices-warning">'+esc(item.warning)+'</p>':''}</td></tr>`).join('')+'</tbody></table>':'<p class="empty">Нет позиций для сохранения. Проверьте столбцы и замечания к строкам.</p>';
    controls();message('Сопоставление готово. Отметьте нужные позиции и подтвердите условия цены перед сохранением.');
  }
  async function makePreview(mine){
    if(!book)return;
    const response=await api('/api/prices/preview',{run_id:book.run_id,source:source()});
    if(mine!==generation)return;drawPreview(response);
  }
  async function previewPrices(){
    if(!book||busy||applying)return;
    draft=source();invalidate();const mine=generation;busy=true;controls();message('Сопоставляем прайс с вашими материалами…');
    try{await makePreview(mine);}catch(error){if(mine===generation)message(error.message,true);}
    finally{if(mine===generation){busy=false;controls();}}
  }
  async function applyPrices(){
    if(!preview||!selectedKeys().length||!el('ack').checked||applying||busy)return;
    const payload={preview_id:preview.preview_id,keys:selectedKeys(),acknowledged:true};
    await lockWrite(el('apply'),async()=>{
      const response=await api('/api/prices/apply',payload);invalidate();
      message('Сохранено позиций: '+response.saved+'. '+(response.note||'Новые расценки доступны в «Мои материалы».'));
      document.dispatchEvent(new CustomEvent('price-catalog-updated'));
    });
  }
  async function downloadDocument(){
    if(!book?.document||el('document').disabled||downloading)return;
    const documentInfo=book.document,mine=generation;downloading=true;controls();
    try{
      const response=await api('/api/prices/document',{document:documentInfo});if(mine!==generation)return;
      download(Uint8Array.from(atob(response.content),char=>char.charCodeAt(0)),response.filename,'application/octet-stream');
    }catch(error){if(mine===generation)message(error.message,true);}
    finally{downloading=false;controls();}
  }
  function addMarkup(){
    titles['price-updates']='Обновление цен';
    const nav=document.createElement('button');nav.className='nav';nav.dataset.tab='price-updates';nav.textContent='Обновление цен';
    document.querySelector('.sidebar nav').append(nav);
    nav.addEventListener('click',()=>{setTab('price-updates');refreshList().catch(error=>message(error.message,true));});
    const section=document.createElement('section');section.id='price-updates';section.className='tab-panel';section.hidden=true;
    section.innerHTML=`<div class="intro"><h2>Обновление цен поставщиков</h2><p>Получите свежий прайс, проверьте изменения и сохраните выбранные цены в «Мои материалы». В сохранённых заказах останутся прежние расценки.</p></div>
      <div class="prices-connections card"><label>Сохранённое подключение<select id="prices-source-select"><option value="">Новое подключение</option></select></label><button id="prices-new" type="button">Новое подключение</button><button id="prices-refresh-list" type="button">Обновить список</button><button id="prices-delete" type="button" disabled>Удалить подключение</button></div>
      <p id="prices-source-empty" class="muted">Подключений пока нет. Добавьте собственный прайс. Новые позиции попадут в раздел «Мои материалы» после проверки.</p>
      <section class="card prices-source-card"><h2>Источник цен</h2><div class="form-grid">
        <label>Название подключения<input id="prices-name" maxlength="300" placeholder="Например: прайс материалов поставщика"></label>
        <label>Поставщик<input id="prices-supplier" maxlength="300" placeholder="Название поставщика"></label>
        <label class="wide">Прямая ссылка на прайс или карточку товара<input id="prices-url" type="url" maxlength="2000" placeholder="https://…" autocomplete="off"></label>
        <label>Формат источника<select id="prices-format"><option value="csv">Прайс CSV</option><option value="xlsx">Прайс XLSX</option><option value="pdf">PDF / скан</option><option value="zenon">Zenon: карточка товара</option><option value="forda">Forda: Product/Offer или JSON</option><option value="product">Карточка товара на сайте</option></select></label>
        <label>Дата прайса, указанная поставщиком<input id="prices-date" type="date"><span class="prices-field-note">Оставьте пустой, если дата неизвестна.</span></label>
        <label>Общая валюта прайса<select id="prices-currency"><option value="unknown">Не определена / из столбца</option><option value="RUB">RUB · рубли</option></select></label>
        <label>Единица цены по умолчанию<input id="prices-unit" maxlength="30" list="units" placeholder="Если единица едина для всего прайса"></label>
        <label class="wide">Условия и примечания<textarea id="prices-notes" rows="2" maxlength="1000" placeholder="Тираж, размер, НДС, скидки, срок действия и другие условия"></textarea></label>
      </div><p id="prices-currency-hint" class="muted"></p><p id="prices-format-hint" class="muted"></p>
      <div class="prices-file-row"><label>Или загрузите файл CSV / XLSX / PDF до 8 МБ<input id="prices-file" type="file" accept=".csv,.xlsx,.pdf"></label><button id="prices-clear-file" type="button">Очистить файл</button></div><p id="prices-file-label" class="muted">Локальный файл не выбран.</p>
      <div class="prices-actions"><button id="prices-read" type="button" class="primary">Проверить цены</button><button id="prices-save" type="button">Сохранить подключение</button></div></section>
      <p id="prices-status" class="prices-status" role="status" aria-live="polite"></p>
      <div class="form-grid"><label>Чтение PDF<select id="prices-pdf-mode"><option value="text">Текст и таблицы</option><option value="ocr">OCR скана</option></select></label><label>Язык OCR<select id="prices-ocr-language"><option value="rus+eng">Русский и английский</option><option value="eng">Английский</option><option value="rus">Русский</option></select></label></div><section id="prices-document-box" class="card" hidden><div class="card-head"><h2>Полученный документ</h2><button id="prices-document" type="button">Скачать исходный документ</button></div><div id="prices-document-meta" class="prices-document-meta"></div><ul id="prices-read-warnings" class="prices-warning"></ul>
      <div id="prices-table-options" hidden><div class="form-grid"><label>Лист<select id="prices-sheet"></select></label><label>Начать с видимой строки №<input id="prices-start" type="number" min="1" max="5001" step="1" value="2"></label></div><div id="prices-mapping" class="form-grid"></div><div id="prices-sample" class="prices-scroll"></div><p class="muted">Укажите столбцы с наименованием и ценой. Единица и валюта берутся из выбранного столбца либо общей настройки. Строки с формулами цены требуют проверки в исходном файле.</p></div>
      <button id="prices-preview" type="button" disabled>Показать изменения</button></section>
      <section id="prices-review" class="card" hidden><h2>Проверьте изменения перед сохранением</h2><p id="prices-checked" class="muted"></p><div id="prices-issues" class="prices-issues"></div><div class="prices-actions"><button id="prices-select-all" type="button">Выбрать доступные позиции</button><button id="prices-select-none" type="button">Снять выбор</button><span id="prices-selected" class="muted"></span></div><div id="prices-changes" class="prices-scroll"></div>
      <label class="check prices-confirm"><input id="prices-ack" type="checkbox">Проверены рубли, единица цены, НДС и условия поставки; выбранные цены относятся к этим позициям и подходят для справочника.</label>
      <button id="prices-apply" type="button" class="primary" disabled>Сохранить выбранные цены</button><p class="fine">Дата получения документа и дата прайса показаны отдельно. Проверку по расписанию можно включить отдельно во вкладке «Расписание цен».</p></section>`;
    document.querySelector('main footer').before(section);
  }
  function bind(){
    document.addEventListener('estimator-price-review',event=>{
      if(applying)return;
      fill({name:'Документ из журнала',data:event.detail.source});book=event.detail;drawDocument();drawRows();controls();setTab('price-updates');
      message('Открыт сохранённый документ из журнала. Сверьте столбцы и нажмите «Показать изменения».');
    });
    el('read').addEventListener('click',readPrices);el('preview').addEventListener('click',previewPrices);el('apply').addEventListener('click',applyPrices);
    el('save').addEventListener('click',saveSource);el('delete').addEventListener('click',deleteSource);
    el('new').addEventListener('click',()=>{if(!applying)fill(null);});
    el('refresh-list').addEventListener('click',()=>refreshList().catch(error=>message(error.message,true)));
    el('source-select').addEventListener('change',()=>fill(records.find(item=>item.id===el('source-select').value)));
    for(const key of ids){
      const field=el(key);field.addEventListener(field.tagName==='SELECT'?'change':'input',()=>{
        if(key==='name'){generation++;busy=false;controls();return;}
        if(key==='format'){el('file').value='';el('file-label').textContent='Локальный файл не выбран.';}
        changed(['url','format','pdf-mode','ocr-language'].includes(key));
      });
    }
    el('mapping').addEventListener('change',()=>changed());el('sheet').addEventListener('change',changeSheet);
    el('file').addEventListener('change',()=>{
      const file=el('file').files[0];if(file){el('format').value=/[.]pdf$/i.test(file.name)?'pdf':/[.]xlsx$/i.test(file.name)?'xlsx':'csv';el('url').value='';}
      el('file-label').textContent=file?'Выбран файл: '+file.name+'. Для получения по ссылке очистите файл и укажите URL.':'Локальный файл не выбран.';
      changed(true);
    });
    el('clear-file').addEventListener('click',()=>{el('file').value='';el('file-label').textContent='Локальный файл не выбран.';changed(true);});
    el('ack').addEventListener('change',controls);el('changes').addEventListener('change',controls);
    el('select-all').addEventListener('click',()=>{document.querySelectorAll('#prices-changes [data-price-key]:not(:disabled)').forEach(input=>input.checked=true);controls();});
    el('select-none').addEventListener('click',()=>{document.querySelectorAll('#prices-changes [data-price-key]').forEach(input=>input.checked=false);controls();});
    el('document').addEventListener('click',downloadDocument);
  }
  function init(){if(initialized)return;initialized=true;addMarkup();bind();fill(null);}
  document.addEventListener('estimator-ready',init);
  if(typeof boot!=='undefined'&&boot&&typeof project!=='undefined'&&project)init();
})();
