'use strict';
(()=>{
  let ready=false,layout=null,vector=null,layoutGeneration=0,vectorGeneration=0;
  const el=id=>document.getElementById('geometry-'+id);
  function part(data={name:'Деталь',width_mm:'300',height_mm:'200',quantity:1}){
    const tr=document.createElement('tr');tr.innerHTML=['name','width_mm','height_mm','quantity'].map(key=>`<td><input data-part="${key}" aria-label="${key}" value="${esc(data[key])}" ${key==='quantity'?'type="number" min="1" max="200"':'maxlength="100"'}></td>`).join('')+'<td><button type="button" data-remove>Удалить</button></td>';el('parts').append(tr);
  }
  const values=form=>Object.fromEntries(Array.from(form.elements).filter(node=>node.name).map(node=>[node.name,node.type==='checkbox'?node.checked:node.value]));
  async function addRow(value,button){
    if(!value)return;button.disabled=true;
    try{collectForm();const original=project,copy=structuredClone(project);copy.rows.push({...value,id:crypto.randomUUID(),confirmed:false});await api('/api/calculate',copy);if(project!==original)throw Error('Смета изменилась. Повторите добавление.');project=copy;markDirty();renderRows();scheduleCalculate(0);notify('Количество добавлено. Укажите цену и подтвердите технологию.');setTab('estimate');}
    catch(error){notify(error.message,true);button.disabled=false;}
  }
  async function calculate(event){
    event.preventDefault();const button=el('calculate');if(button.disabled)return;button.disabled=true;
    const mine=layoutGeneration;
    try{
      const body=values(el('layout-form'));body.parts=Array.from(el('parts').children).map(tr=>Object.fromEntries(Array.from(tr.querySelectorAll('[data-part]')).map(input=>[input.dataset.part,input.dataset.part==='quantity'?Number(input.value):input.value])));
      const result=await api('/api/layout/mixed',body);if(mine!==layoutGeneration)return;layout=result;
      el('layout-result').innerHTML=`<p><strong>Листов: ${result.sheets}</strong>. Заполнение готовыми деталями: ${esc(result.utilization_percent)}%.</p><p>${result.warnings.map(esc).join(' ')}</p>`+result.svgs.map((svg,i)=>`<details ${i===0?'open':''}><summary>Лист ${i+1}</summary><div style="max-width:650px">${svg}</div></details>`).join('')+'<table class="workspace-table"><tr><th>№</th><th>Деталь</th><th>Лист</th><th>X / Y, мм</th><th>Размер с вылетами, мм</th></tr>'+result.placements.map(item=>`<tr><td>${item.id}</td><td>${esc(item.name)}</td><td>${item.sheet}</td><td>${item.x_mm} / ${item.y_mm}</td><td>${item.width_mm} × ${item.height_mm}</td></tr>`).join('')+'</table>';
      el('add-sheets').disabled=false;el('save-layout').disabled=false;
    }catch(error){el('layout-result').textContent=error.message;}finally{button.disabled=false;}
  }
  async function measure(event){
    event.preventDefault();const button=el('measure');if(button.disabled)return;button.disabled=true;const mine=vectorGeneration;
    try{
      const file=el('file').files[0];if(!file||file.size>8388608)throw Error('Выберите DXF или SVG до 8 МБ.');
      const format=file.name.split('.').pop().toLowerCase();if(!['svg','dxf'].includes(format))throw Error('Поддерживаются SVG и DXF.');
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const result=await api('/api/vector/measure',{...values(el('vector-form')),content:btoa(binary),format});if(mine!==vectorGeneration)return;vector=result;
      el('vector-result').innerHTML=`<p><strong>Длина резки: ${esc(result.length_m)} пог. м</strong></p><p>Габариты: ${result.width_mm} × ${result.height_mm} мм; контуров: ${result.contours}, открытых: ${result.open_contours}.</p><p>Сумма площадей замкнутых контуров: ${esc(result.closed_area_sum_m2)} м² (отверстия не вычтены).</p><ul>${result.warnings.map(text=>'<li>'+esc(text)+'</li>').join('')}</ul>`;el('add-cut').disabled=false;
    }catch(error){el('vector-result').textContent=error.message;}finally{button.disabled=false;}
  }
  function init(){
    if(ready)return;ready=true;const nav=document.createElement('button');nav.className='nav';nav.dataset.tab='advanced-geometry';nav.textContent='Раскрой и чертежи';nav.onclick=()=>setTab('advanced-geometry');document.querySelector('.sidebar nav').append(nav);titles['advanced-geometry']='Раскрой и чертежи';
    const section=document.createElement('section');section.id='advanced-geometry';section.className='tab-panel';section.hidden=true;
    section.innerHTML=`<form id="geometry-layout-form" class="card"><h2>Детали разных размеров на листах</h2><div class="form-grid">${[['sheet_width_mm','Ширина листа, мм','2050'],['sheet_height_mm','Высота листа, мм','3050'],['edge_mm','Отступ от края, мм','10'],['gap_mm','Зазор между деталями, мм','3'],['bleed_mm','Вылет с каждой стороны, мм','0']].map(([name,label,value])=>`<label>${label}<input name="${name}" value="${value}" inputmode="decimal"></label>`).join('')}<label class="check"><input name="allow_rotate" type="checkbox">Разрешить поворот на 90° для всех деталей</label></div><p>До 200 деталей. При важном направлении материала отключите поворот.</p><table class="workspace-table"><thead><tr><th>Деталь</th><th>Ширина, мм</th><th>Высота, мм</th><th>Количество</th><th></th></tr></thead><tbody id="geometry-parts"></tbody></table><button id="geometry-add-part" type="button">Добавить размер</button> <button id="geometry-calculate" type="submit" class="primary">Рассчитать раскрой</button><div id="geometry-layout-result" role="status"></div><button id="geometry-add-sheets" type="button" disabled>Листы в смету</button> <button id="geometry-save-layout" type="button" disabled>Сохранить схему HTML</button></form>
      <form id="geometry-vector-form" class="card"><h2>Резка по DXF / SVG</h2><p>Чертёж обрабатывается на компьютере. DWG/CDR предварительно экспортируйте в плоские контуры.</p><div class="form-grid"><label>Чертёж до 8 МБ<input id="geometry-file" type="file" accept=".svg,.dxf" required></label><label>Миллиметров в единице (если в файле не указано)<input name="unit_mm" placeholder="Например: 1" inputmode="decimal"></label><label>Слой DXF (пусто — все)<input name="layer" maxlength="200"></label></div><button id="geometry-measure" type="submit" class="primary">Измерить</button><div id="geometry-vector-result" role="status"></div><button id="geometry-add-cut" type="button" disabled>Длину резки в смету</button></form>`;
    document.querySelector('main footer').before(section);part();
    const invalidateLayout=()=>{layoutGeneration++;layout=null;el('layout-result').replaceChildren();el('add-sheets').disabled=true;el('save-layout').disabled=true;};
    el('layout-form').oninput=invalidateLayout;el('add-part').onclick=()=>{if(el('parts').children.length<100){part();invalidateLayout();}};
    el('parts').onclick=event=>{if(event.target.hasAttribute('data-remove')){event.target.closest('tr').remove();invalidateLayout();}};
    el('layout-form').onsubmit=calculate;el('vector-form').onsubmit=measure;
    el('vector-form').oninput=()=>{vectorGeneration++;vector=null;el('vector-result').replaceChildren();el('add-cut').disabled=true;};
    el('add-sheets').onclick=()=>addRow(layout?.row,el('add-sheets'));el('add-cut').onclick=()=>addRow(vector?.row,el('add-cut'));
    el('save-layout').onclick=()=>{if(layout)download('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Раскрой</title><h1>Раскрой: '+layout.sheets+' листов</h1>'+el('layout-result').innerHTML+'</html>','Mixed_layout.html','text/html;charset=utf-8');};
  }
  document.addEventListener('estimator-ready',init);
})();
