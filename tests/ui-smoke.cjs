/* Developer-only integration test: npm install --no-save jsdom.
   Start python server.py --no-browser --port 8765, then node tests/ui-smoke.cjs.
   Uses a simulated DOM, not a visual browser or Windows execution. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const {JSDOM, VirtualConsole} = require('jsdom');
const base = process.env.ESTIMATOR_TEST_URL || 'http://127.0.0.1:8765';
const root = path.resolve(__dirname,'..');
const guideSource=JSON.parse(fs.readFileSync(path.join(root,'web/guide.json'),'utf8'));
const errors=[], downloads=[], blobs=new Map();
const virtualConsole=new VirtualConsole();
virtualConsole.on('jsdomError',e=>errors.push(e.message));
const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
const w=dom.window, d=w.document;
w.fetch=(url,opts)=>fetch(new URL(url,base),opts);
w.structuredClone=structuredClone;
w.crypto.randomUUID=randomUUID;
w.Blob=Blob;
w.URL.createObjectURL=blob=>{const key='blob:'+randomUUID();blobs.set(key,blob);return key;};
w.URL.revokeObjectURL=url=>blobs.delete(url);
w.HTMLAnchorElement.prototype.click=function(){downloads.push({name:this.download,blob:blobs.get(this.href)});};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.confirm=()=>true;
w.addEventListener('error',event=>errors.push(event.message));
w.eval(['app.js','workspace.js','customer.js','price_updates.js','monitor.js','geometry.js','advanced.js','ergonomic.js','guide.js','supply_hub.js'].map(name=>fs.readFileSync(path.join(root,'web',name),'utf8')).join('\n'));
w.prompt=(message,defaultValue)=>defaultValue||'Проверочная запись';
const $=id=>d.getElementById(id);
const wait=async(fn,label)=>{const end=Date.now()+4000;while(Date.now()<end){if(fn())return;await new Promise(resolve=>setTimeout(resolve,20));}throw new Error('Timeout: '+label+' | '+$('notice').textContent+' | '+$('calc-error').textContent+' | '+$('manual-error').textContent);};
function input(el,value){if(typeof value==='boolean')el.checked=value;else el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));}
function submit(form){form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));}
async function addRow(overrides={}){$('add-row').click();const form=$('row-form');const defaults={name:'Проверочная работа',quantity:'1',price:'100',unit:'заказ',source:'Учебное предложение',price_date:'2026-09-11',note:'Тестовые исходные данные'};for(const [key,value]of Object.entries({...defaults,...overrides}))input(form.elements.namedItem(key),value);input(form.elements.namedItem('confirmed'),true);submit(form);await wait(()=>!$('row-dialog').open,'row saved');await wait(()=>!$('internal-report').disabled,'calculation ready');}
async function fileInput(id,name,text){Object.defineProperty($(id),'files',{configurable:true,value:[{name,size:Buffer.byteLength(text),text:async()=>text}]});$(id).dispatchEvent(new w.Event('change',{bubbles:true}));}
(async()=>{
 await wait(()=>$('rows-body').children.length===6&&!$('internal-report').disabled,'initial demo');
 assert.equal($('total').textContent,'Не определена');assert.match($('totals-list').textContent,/12\s130,00/);assert.equal($('price-count').textContent,'67');
 // Unknown quantities stay editable in the row form.
 const editButtons=d.querySelectorAll('[data-action="edit"]');editButtons[5].click();assert.equal($('row-form').elements.namedItem('quantity').required,false);input($('row-form').elements.namedItem('note'),'Монтаж ещё не определён');submit($('row-form'));await wait(()=>!$('row-dialog').open,'unknown quantity row edit');
 $('new-project').click();input($('project-title'),'Проверочный заказ');input($('project-notes'),'Одна работа по изготовлению таблички');input($('project-quantity'),'3');await addRow();
 input($('profit-percent'),'0');input($('tax-mode'),'none');input($('cost-basis-confirmed'),true);input($('scope-confirmed'),true);
 await wait(()=>$('estimate-status').textContent==='Смета по подтверждённым данным','complete order');assert.equal($('total').textContent,'100,00 ₽');assert.match($('unit-total').textContent,/33,33.*округлённая средняя/);
 // Editing previously verified evidence must clear old confirmation.
 d.querySelector('[data-action="edit"]').click();assert.equal($('row-form').elements.namedItem('confirmed').checked,true);input($('row-form').elements.namedItem('price'),'120');assert.equal($('row-form').elements.namedItem('confirmed').checked,false);input($('row-form').elements.namedItem('confirmed'),true);submit($('row-form'));await wait(()=>!$('row-dialog').open&&$('total').textContent==='120,00 ₽','reconfirmed edit');
 input($('pricing-mode'),'margin');input($('profit-percent'),'20');await wait(()=>$('total').textContent==='150,00 ₽','margin formula');
 // Manual JSON download/open preserves project, while the key is absent.
 $('tavily-key').value='tvly-TEST-NOT-A-REAL-KEY';$('save-project').click();await wait(()=>downloads.length===1,'JSON export');const json=await downloads[0].blob.text();assert(!json.includes('TEST-NOT-A-REAL-KEY'));assert.equal(JSON.parse(json).settings.pricing_mode,'margin');
 $('client-report').click();await wait(()=>downloads.length===2,'client HTML export');const report=await downloads[1].blob.text();assert(report.includes('150'));assert(!report.includes('Учебное предложение'));assert(!report.includes('Расчётная прибыль'));assert(report.includes('Одна работа по изготовлению таблички'));
 $('export-csv').click();await wait(()=>downloads.length===3,'CSV export');const csv=await downloads[2].blob.text();await fileInput('csv-file','rows.csv',csv);await wait(()=>$('rows-body').children.length===2,'CSV import appends');
 await fileInput('project-file','saved.json',json);await wait(()=>$('rows-body').children.length===1&&$('project-hint').textContent.includes('saved.json'),'JSON restore');
 // Defaultable external JSON must normalize before rendering.
 await fileInput('project-file','partial.json','{"rows":[]}');await wait(()=>$('project-hint').textContent.includes('partial.json'),'partial JSON normalization');assert.equal($('project-title').value,'');assert.equal($('rows-body').children.length,0);
 // Fractional geometry must be transferable into estimator quantities.
 $('show-geometry').click();const gf=$('geometry-form');input(gf.elements.namedItem('width_mm'),'600.5');input(gf.elements.namedItem('height_mm'),'400.5');input(gf.elements.namedItem('quantity'),'1');submit(gf);await wait(()=>!!d.querySelector('[data-geometry="area"]'),'geometry');d.querySelector('[data-geometry="area"]').click();assert.equal($('row-form').elements.namedItem('quantity').value,'0.240501');submit($('row-form'));await wait(()=>!$('row-dialog').open,'geometry row accepted');
 // Catalog filters, blocked historic pricing, current source transfer.
 d.querySelector('[data-tab="catalog"]').click();input($('status-filter'),'historical_snapshot');assert.equal($('catalog-list').children.length,3);d.querySelector('[data-price-id]').click();assert.equal($('use-price').disabled,true);$('price-dialog').querySelector('.close-dialog').click();input($('status-filter'),'published_snapshot');input($('catalog-query'),'ПВХ');d.querySelector('[data-price-id]').click();assert.equal($('use-price').disabled,false);$('use-price').click();assert.equal($('row-dialog').open,true);assert.equal($('row-form').elements.namedItem('confirmed').checked,false);assert.match($('row-form').elements.namedItem('source').value,/^https:/);$('row-dialog').querySelector('.close-dialog').click();
 // Fast manual entry accepts Russian decimals, preserves unknowns and saves
 // independent rows; invalid prices and repeat clicks must not add duplicates.
 $('new-project').click();
 input($('project-title'),'Собственная смета');input($('project-notes'),'Материалы и монтаж по своим расценкам');
 $('quick-entry').click();assert.equal($('quick-entry').getAttribute('aria-expanded'),'true');
 const mf=$('manual-form');const manualField=name=>mf.elements.namedItem(name);
 const firstName='Печать <баннер> "свой прайс"';
 for(const [key,value]of Object.entries({name:firstName,category:'work',unit:'м²',quantity:'2,5',price:'1 234,50',note:'Собственная расценка; ламинация отдельно'}))input(manualField(key),value);
 submit(mf);submit(mf);
 await wait(()=>$('rows-body').children.length===1&&!mf.querySelector('[type="submit"]').disabled&&d.querySelector('.row-amount').textContent==='3 086,25','manual decimal cost and duplicate protection');
 assert.equal(manualField('name').value,'');assert.equal(mf.hidden,false);
 assert.equal(d.querySelector('[data-field="name"]').value,firstName);
 assert.equal(d.querySelector('[data-field="unit"]').value,'м²');
 input(manualField('name'),'Доставка');input(manualField('category'),'delivery');input(manualField('unit'),'рейс');input(manualField('price'),'-1');submit(mf);
 await wait(()=>!$('manual-error').hidden&&!mf.querySelector('[type="submit"]').disabled,'manual invalid price rejected');
 assert.equal($('rows-body').children.length,1);assert.equal(manualField('name').value,'Доставка');
 input(manualField('price'),'0');submit(mf);
 await wait(()=>$('rows-body').children.length===2&&d.querySelectorAll('.row-amount')[1].textContent==='0,00','manual explicit zero');
 input(manualField('name'),'Монтаж');input(manualField('category'),'work');input(manualField('unit'),'час');input(manualField('quantity'),'');input(manualField('price'),'');submit(mf);
 await wait(()=>$('rows-body').children.length===3&&!mf.querySelector('[type="submit"]').disabled&&!$('internal-report').disabled,'manual unknown values');
 assert.equal(d.querySelectorAll('.row-amount')[2].textContent,'—');
 // All four table columns are editable, and changed evidence needs reconfirmation.
 d.querySelector('[data-action="edit"]').click();const rf=$('row-form');
 input(rf.elements.namedItem('source'),'Собственный прайс цеха');input(rf.elements.namedItem('price_date'),'2026-09-12');input(rf.elements.namedItem('confirmed'),true);submit(rf);
 await wait(()=>!$('row-dialog').open&&!$('internal-report').disabled,'confirm manual source');
 const first=d.querySelector('#rows-body tr');
 input(first.querySelector('[data-field="name"]'),'Печать баннера, свой тариф');
 input(first.querySelector('[data-field="unit"]'),'пог. м');
 input(first.querySelector('[data-field="quantity"]'),'3');
 input(first.querySelector('[data-field="price"]'),'200,50');
 await wait(()=>first.querySelector('.row-amount').textContent==='601,50','manual table editing');
 assert.match(first.querySelector('.row-sub').textContent,/^○/);
 const beforeManualDownloads=downloads.length;$('save-project').click();
 await wait(()=>downloads.length===beforeManualDownloads+1,'save manually entered rows');
 const manualJSON=await downloads.at(-1).blob.text(), manualProject=JSON.parse(manualJSON);
 assert.equal(manualProject.rows.length,3);
 assert.equal(manualProject.rows[0].name,'Печать баннера, свой тариф');assert.equal(manualProject.rows[0].unit,'пог. м');
 assert.equal(manualProject.rows[0].confirmed,false);assert.equal(manualProject.rows[0].source,'Собственный прайс цеха');
 assert.equal(manualProject.rows[0].note,'Собственная расценка; ламинация отдельно');
 assert.equal(manualProject.rows[1].price,'0');assert.equal(manualProject.rows[2].quantity,'');assert.equal(manualProject.rows[2].price,'');
 $('export-csv').click();await wait(()=>downloads.length===beforeManualDownloads+2,'manual CSV');
 assert((await downloads.at(-1).blob.text()).includes('Собственная расценка'));
 $('internal-report').click();await wait(()=>downloads.length===beforeManualDownloads+3,'manual HTML');
 assert((await downloads.at(-1).blob.text()).includes('Печать баннера, свой тариф'));
 $('new-project').click();await fileInput('project-file','manual.json',manualJSON);
 await wait(()=>$('project-hint').textContent.includes('manual.json')&&$('rows-body').children.length===3&&!$('internal-report').disabled,'restore manual rows');
 assert.equal(d.querySelector('[data-field="unit"]').value,'пог. м');assert.equal(d.querySelectorAll('.row-amount')[2].textContent,'—');
 // A delayed manual validation must not overwrite a different opened project.
 $('quick-entry').click();input(manualField('name'),'Позиция старого заказа');input(manualField('price'),'10');
 const realFetch=w.fetch;let releaseManual;
 w.fetch=(url,opts)=>String(url)==='/api/calculate'&&JSON.parse(opts.body).rows.some(row=>row.name==='Позиция старого заказа')
   ? new Promise(resolve=>{releaseManual=()=>realFetch(url,opts).then(resolve);}) : realFetch(url,opts);
 submit(mf);await wait(()=>!!releaseManual,'manual validation held');$('new-project').click();await releaseManual();
 await wait(()=>!mf.querySelector('[type="submit"]').disabled,'stale manual validation completed');
 assert.equal($('rows-body').children.length,0);assert.equal($('project-title').value,'');w.fetch=realFetch;
 // Local workspace: catalog, orders/versions, recipes and XLSX import.
 await wait(()=>!!$('own-new'),'workspace ready');
 input($('project-title'),'Заказ для каталога');input($('project-notes'),'Проверочная продукция');
 await addRow({name:'Собственный материал',unit:'м²',price:'200'});
 d.querySelector('[data-action="save-catalog"]').click();assert.equal($('own-dialog').open,true);
 input($('own-form').elements.namedItem('supplier'),'Свой цех');submit($('own-form'));
 await wait(()=>!$('own-dialog').open&&$('own-list').textContent.includes('Собственный материал'),'save catalog row');
 d.querySelector('[data-tab="own"]').click();await wait(()=>!!d.querySelector('[data-own-action="use"]'),'own catalog list');
 d.querySelector('[data-own-action="use"]').click();assert.equal($('row-form').elements.namedItem('quantity').value,'');input($('row-form').elements.namedItem('quantity'),'2');submit($('row-form'));
 await wait(()=>$('rows-body').children.length===2&&!$('row-dialog').open,'use catalog price');
 $('top-order-save').click();await wait(()=>$('notice').textContent.includes('Версия 1')&&!$('top-order-save').disabled,'save local order');
 input(d.querySelector('[data-field="price"]'),'300');$('top-order-save').click();await wait(()=>$('notice').textContent.includes('Версия 2'),'second local version');
 d.querySelector('[data-tab="orders"]').click();await wait(()=>!!d.querySelector('[data-order-action="versions"]'),'orders list');d.querySelector('[data-order-action="versions"]').click();await wait(()=>d.querySelectorAll('[data-version]').length===2,'version history');d.querySelector('[data-version="1"]').click();
 await wait(()=>$('project-hint').textContent.includes('Восстановлена версия 1')&&d.querySelector('[data-field="price"]').value==='200','restore first version');
 d.querySelector('[data-tab="production"]').click();$('template-save').click();await wait(()=>!!d.querySelector('[data-template-action="use"]'),'save own template');
 const tf=$('template-form');for(const [k,v]of Object.entries({kind:'sign',quantity:'20',width_mm:'600',height_mm:'400',sheet_width_mm:'2030',sheet_height_mm:'3050'}))input(tf.elements.namedItem(k),v);submit(tf);
 await wait(()=>$('project-title').value==='Таблички ПВХ'&&$('rows-body').children.length===3,'recipe generation');assert.equal(d.querySelector('[data-field="quantity"]').value,'1');
 d.querySelector('[data-tab="production"]').click();const op=$('operation-form');for(const [k,v]of Object.entries({name:'Фрезеровка',quantity:'20',setup_minutes:'15',minutes_per_unit:'2',hourly_rate:'2000'}))input(op.elements.namedItem(k),v);submit(op);
 await wait(()=>$('rows-body').children.length===4&&!$('internal-report').disabled,'production operation');assert.match($('totals-list').textContent,/1\s833,33/);
 d.querySelector('[data-tab="assistant"]').click();input($('assistant-form').elements.namedItem('text'),'Нужно 20 табличек ПВХ 600 × 400 мм с печатью и монтажом');submit($('assistant-form'));await wait(()=>!!$('assistant-template'),'brief extraction');assert.equal(tf.elements.namedItem('quantity').value,'20');assert.equal(tf.elements.namedItem('width_mm').value,'600');assert.equal(tf.elements.namedItem('sheet_width_mm').value,'');assert.equal(tf.elements.namedItem('installation').checked,true);
 d.querySelector('[data-tab="own"]').click();$('own-import').click();
 const fixture=Buffer.from(process.env.ESTIMATOR_TEST_PRICEBOOK,'base64');Object.defineProperty($('xlsx-file'),'files',{configurable:true,value:[{name:'Тестовый прайс.xlsx',size:fixture.length,arrayBuffer:async()=>fixture.buffer.slice(fixture.byteOffset,fixture.byteOffset+fixture.byteLength)}]});$('xlsx-file').dispatchEvent(new w.Event('change',{bubbles:true}));
 await wait(()=>!$('xlsx-options').hidden&&!!d.querySelector('[data-map="price"]'),'xlsx read');input($('xlsx-date'),'2026-09-12');$('xlsx-preview').click();await wait(()=>!$('xlsx-apply').disabled,'xlsx preview');assert($('xlsx-changes').textContent.includes('ПВХ из прайса'));$('xlsx-apply').click();await wait(()=>$('own-list').textContent.includes('ПВХ из прайса'),'xlsx catalog import');
 // Saved variants and supplier comparison use actual calculation results.
 $('new-project').click();input($('project-title'),'Вариант 100');input($('project-notes'),'Одна работа');await addRow({price:'100'});input($('profit-percent'),'0');input($('tax-mode'),'none');
 d.querySelector('[data-tab="compare"]').click();$('variant-save').click();await wait(()=>d.querySelectorAll('[data-variant-check]').length===1,'first variant');input($('project-title'),'Вариант 200');input(d.querySelector('[data-field="price"]'),'200');$('variant-save').click();await wait(()=>d.querySelectorAll('[data-variant-check]').length===2,'second variant');for(const el of d.querySelectorAll('[data-variant-check]'))input(el,true);$('variants-calculate').click();await wait(()=>$('variants-result').textContent.includes('200,00'),'variant comparison');assert($('variants-result').textContent.includes('100,00'));
 const of=$('offer-form');for(const offer of [{supplier:'Поставщик A',price:'100',delivery:'150',increment:'4'},{supplier:'Поставщик B',price:'150',delivery:'0',increment:''}]){for(const [k,v]of Object.entries({item:'ПВХ',unit:'лист',minimum:'0',source:'Предложение поставщика',date:'2026-09-12',comparable:true,...offer}))input(of.elements.namedItem(k),v);submit(of);await wait(()=>!of.querySelector('[type="submit"]').disabled,'offer saved');}
 await wait(()=>d.querySelectorAll('[data-offer-check]').length===2,'two offers');for(const el of d.querySelectorAll('[data-offer-check]'))input(el,true);input($('offer-quantity'),'5');$('offers-calculate').click();await wait(()=>!!d.querySelector('.best-offer'),'offer comparison');assert(d.querySelector('.best-offer').textContent.includes('Поставщик B'));assert(d.querySelector('.best-offer').textContent.includes('750,00'));
 // Actual costs survive saving; direct PDF and Excel downloads are real files.
 d.querySelector('[data-tab="actual"]').click();input(d.querySelector('[data-actual-id][data-key="amount"]'),'250');input($('actual-extra'),'0');$('actual-calculate').click();await wait(()=>$('actual-result').textContent.includes('50,00'),'plan actual difference');
 d.querySelector('[data-tab="settings"]').click();input($('profile-form').elements.namedItem('company'),'Проверочная компания');submit($('profile-form'));await wait(()=>$('notice').textContent.includes('Реквизиты сохранены'),'profile saved');
 const countBeforeDocuments=downloads.length;$('pdf-client').click();await wait(()=>downloads.length===countBeforeDocuments+1,'direct PDF');assert((await downloads.at(-1).blob.text()).startsWith('%PDF'));
 $('xlsx-client').click();await wait(()=>downloads.length===countBeforeDocuments+2,'direct Excel');assert((await downloads.at(-1).blob.text()).startsWith('PK'));
 $('save-project').click();await wait(()=>downloads.length===countBeforeDocuments+3,'actuals JSON');const actualJSON=JSON.parse(await downloads.at(-1).blob.text());assert.equal(Object.values(actualJSON.extensions.actuals)[0].amount,'250');
 d.querySelector('[data-tab="orders"]').click();$('autosave-draft').checked=true;$('autosave-draft').dispatchEvent(new w.Event('change',{bubbles:true}));await wait(()=>$('draft-status').textContent.includes('Черновик сохранён'),'initial autosave');input($('project-title'),'Авточерновик');await new Promise(resolve=>setTimeout(resolve,1700));$('new-project').click();$('draft-recover').click();await wait(()=>$('project-title').value==='Авточерновик','recover autosave');
 const beforeBackup=downloads.length;$('backup-save').click();await wait(()=>downloads.length===beforeBackup+1,'workspace backup');const backupText=await downloads.at(-1).blob.text();assert(JSON.parse(backupText).history.length>=2);await fileInput('backup-file','backup.json',backupText);await wait(()=>$('notice').textContent.includes('Добавлено:'),'restore backup copies');
 // Local validation must stop keyless requests before network access.
 d.querySelector('[data-tab="search"]').click();$('clear-key').click();$('run-search').click();assert.match($('notice').textContent,/Введите API-ключ/);
 // Customer lookup uses synthetic remote responses through the real local HTTP routes.
 d.querySelector('[data-tab="estimate"]').click();
 const change=(el,value)=>{el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));};
 const registryKey='synthetic-test-key-not-a-real-credential';
 input($('customer-key'),registryKey);input($('customer-query'),'7730588444');
 await wait(()=>!$('customer-card').hidden&&$('customer-status').textContent.includes('заполнены'),'automatic DaData lookup');
 assert.equal($('project-client').value,'ООО Учебный заказчик');assert($('customer-details').textContent.includes('DaData'));assert(!$('customer-invalid').hidden);assert($('customer-pdf').hidden);
 const beforeCustomerJSON=downloads.length;$('save-project').click();await wait(()=>downloads.length===beforeCustomerJSON+1,'customer JSON');
 const customerJSON=await downloads.at(-1).blob.text(),customerProject=JSON.parse(customerJSON);
 assert.equal(customerProject.extensions.customer.inn,'7730588444');assert(!customerJSON.includes(registryKey));assert(!customerJSON.includes('api_key'));
 input($('customer-query'),'123');assert($('customer-card').hidden);assert.equal($('project-client').value,'');
 $('customer-fetch').click();assert($('customer-status').textContent.includes('контрольные'));await wait(()=>!$('internal-report').disabled,'partial identifier leaves calculation available');
 const beforeSavedLoadFetch=w.fetch;let customerRequests=0;
 w.fetch=async(url,opts)=>{if(String(url).startsWith('/api/customer/')||String(url).startsWith('/api/fns/'))customerRequests++;return beforeSavedLoadFetch(url,opts);};
 await fileInput('project-file','customer.json',customerJSON);await wait(()=>!$('customer-card').hidden,'saved card reload');
 await new Promise(resolve=>setTimeout(resolve,1400));assert.equal(customerRequests,0);w.fetch=beforeSavedLoadFetch;
 w.fetch=async(url,opts)=>String(url)==='/api/customer/dadata'?{ok:false,json:async()=>({error:'DaData: тестовая недоступность',code:'unavailable'})}:beforeSavedLoadFetch(url,opts);
 $('customer-fetch').click();await wait(()=>$('customer-status').textContent.includes('Сохранены прежние'),'failed refresh keeps card');assert(!$('customer-card').hidden);w.fetch=beforeSavedLoadFetch;
 let releaseCustomer,customerSent=false;
 w.fetch=async(url,opts)=>{if(String(url)==='/api/customer/dadata'){customerSent=true;return new Promise(resolve=>{releaseCustomer=()=>resolve({ok:true,json:async()=>({status:'ready',results:[{choice_id:'0',customer:customerProject.extensions.customer}]})});});}return beforeSavedLoadFetch(url,opts);};
 $('customer-fetch').click();await wait(()=>customerSent,'delayed customer request');$('new-project').click();releaseCustomer();
 await new Promise(resolve=>setTimeout(resolve,100));assert($('customer-card').hidden);assert.equal($('project-client').value,'');w.fetch=beforeSavedLoadFetch;
 change($('customer-provider'),'fns');input($('customer-query'),'7730588444');
 await wait(()=>!$('customer-pdf').hidden,'FNS lookup and automatic extract storage');
 assert($('customer-details').textContent.includes('ФНС'));const beforeExtract=downloads.length;$('customer-pdf').click();
 await wait(()=>downloads.length===beforeExtract+1,'original extract download');assert(downloads.at(-1).name.startsWith('FNS_'));assert((await downloads.at(-1).blob.text()).includes('Synthetic transport fixture'));
 input($('customer-query'),'7707083893');await wait(()=>$('customer-status').textContent.includes('капчу'),'captcha response');assert($('customer-card').hidden);assert($('customer-fetch').disabled===false);
 change($('customer-provider'),'dadata');input($('customer-auto'),false);$('customer-auto').dispatchEvent(new w.Event('change',{bubbles:true}));input($('customer-query'),'7730588444');
 $('customer-fetch').click();await wait(()=>!$('customer-card').hidden,'manual retry after changing source');input($('project-client'),'Название от пользователя');assert($('customer-card').hidden);
 $('customer-clear-key').click();assert.equal($('customer-key').value,'');
 // Navigation, contextual help and offline export use the actual new UI modules.
 await wait(()=>!!$('guide-query')&&!!$('navigation-search'),'new interface and guide');
 assert.equal($('top-order-save').textContent,'Сохранить смету');
 assert.equal($('advanced-docx').closest('.tab-panel').id,'advanced-tools');
 assert.equal($('advanced-stock-form').closest('.tab-panel').id,'stock');
 assert.equal($('advanced-key').closest('.tab-panel').id,'connections');
 assert.equal($('advanced-backup').closest('.tab-panel').id,'orders');
 assert.equal($('customer-disclosure').open,false);
 input($('navigation-search'),'склад');assert.equal(d.querySelector('[data-tab="stock"]').hidden,false);assert.equal(d.querySelector('[data-tab="estimate"]').hidden,true);
 input($('navigation-search'),'zzzzzz');assert.equal($('navigation-empty').hidden,false);input($('navigation-search'),'');
 d.querySelector('[data-tab="help"]').click();assert.equal($('help').hidden,false);assert.equal($('guide-topics').querySelectorAll('button').length,guideSource.articles.length);
 for(const article of guideSource.articles)for(const step of article.steps){if(step.action){assert(d.querySelector('.nav[data-tab="'+step.action.tab+'"]'));if(step.action.focus)assert($(step.action.focus),'Guide target: '+step.action.focus);}}
 input($('guide-query'),'zzzzzz');assert($('guide-content').textContent.includes('Попробуйте другое слово'));$('guide-no-reset').click();
 input($('guide-query'),'ИНН');d.querySelector('[data-guide-topic="customer"]').click();assert($('guide-content').textContent.includes('Заказчик по ИНН'));
 $('guide-content').querySelector('[data-guide-action]').click();assert.equal($('estimate').hidden,false);assert.equal($('customer-disclosure').open,true);assert.equal(d.activeElement.id,'customer-query');
 d.querySelector('[data-tab="stock"]').click();await wait(()=>!$('advanced-stock-refresh').disabled,'stock refresh before help');$('context-help').click();assert($('guide-content').textContent.includes('Остатки и резерв материалов'));
 const beforeGuide=downloads.length;$('guide-download').click();await wait(()=>downloads.length===beforeGuide+1,'offline guide download');
 const guideHTML=await downloads.at(-1).blob.text();assert(guideHTML.includes(guideSource.version));assert(guideHTML.includes('Tavily, DaData'));assert(!guideHTML.includes('TEST-NOT-A-REAL-KEY'));assert(!guideHTML.includes('<script'));
 $('navigation-toggle').click();assert.equal($('navigation-toggle').getAttribute('aria-expanded'),'true');d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal($('navigation-toggle').getAttribute('aria-expanded'),'false');
 const retainedTitle=$('project-title').value;d.querySelector('[data-tab="advanced-tools"]').click();d.querySelector('[data-tab="estimate"]').click();assert.equal($('project-title').value,retainedTitle);
 // Exercise the new supplier workflow with a local synthetic feed; no external calls.
 d.querySelector('[data-tab="supply-hub"]').click();await wait(()=>!!$('hub-source-form'),'supplier hub');
 const sourceForm=$('hub-source-form');input(sourceForm.elements.name,'UI synthetic supplier');input(sourceForm.elements.url,'https://supplier.example/feed.json');sourceForm.elements.enabled.checked=false;
 sourceForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 await wait(()=>$('hub-sources').textContent.includes('UI synthetic supplier'),'source saved');
 const hubBoot=await (await fetch(base+'/api/bootstrap')).json();
 const callHub=async(action,body)=>{const r=await fetch(base+'/api/hub/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Estimator-Token':hubBoot.token},body:JSON.stringify(body)});assert(r.ok);return r.json();};
 const hubState=await callHub('status',{});const sourceId=hubState.sources.find(s=>s.name==='UI synthetic supplier').id;
 const offer={name:'ПВХ UI fixture',article:'UI-PVC',material_key:'pvc-test',unit:'лист',price:'1000',currency:'RUB',tax_basis:'vat_included',available:true,available_quantity:'10',minimum_quantity:'1',package_step:'1'};
 assert.equal((await callHub('refresh',{id:sourceId,content:Buffer.from(JSON.stringify([offer])).toString('base64')})).status,'ok');
 const selectForm=$('hub-select-form');input(selectForm.elements.material_key,'pvc-test');input(selectForm.elements.unit,'лист');input(selectForm.elements.quantity,'3');
 selectForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>!!$('hub-add-selected'),'automatic selection');
 assert($('hub-selection').textContent.includes('UI synthetic supplier'));
 $('hub-add-selected').click();await wait(()=>$('rows-body').textContent.includes('UI synthetic supplier')||Array.from($('rows-body').querySelectorAll('input.row-name')).some(i=>i.value==='ПВХ UI fixture'),'selected row added');
 await wait(()=>!$('hub-select-form').querySelector('button').disabled,'hub operation complete');
 assert.deepEqual(errors,[]);
 console.log('PASS: complete estimator workflow, local catalog/orders/versions, production, imports, PDF/XLSX, backups and customer DaData/FNS lookup, debounce, credentials excluded from JSON, saved-card offline loading, failed refresh, stale response protection, original PDF storage/download, CAPTCHA and manual customer edits. Remote registry responses are synthetic.');
 dom.window.close();
})().catch(error=>{console.error(error);console.error(errors);dom.window.close();process.exitCode=1;});
