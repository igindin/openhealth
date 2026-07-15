/* oh-i18n.js — runtime RU->EN translation + language toggle (default EN).
   Shared by BOTH skins (V1 dashboard.html, V2 dashboard-v2.html) so the chosen
   language survives a skin switch instead of silently resetting to Russian.
   Dashboards are authored in Russian; EN mode translates the rendered DOM via a
   RU->EN dictionary + a MutationObserver (catches every render without editing
   render functions). RU mode = native source (no-op). localStorage key: oh_lang.
   Dictionary is progressive — expand OH_I18N to cover more strings. */
(function(){
  const OH_I18N = {
    "Сегодня":"Today","Пульс дня":"Day Pulse","Препараты и БАДы":"Meds & Supplements",
    "Методологии":"Methodologies","Отчёты":"Reports","Дайджесты":"Digests",
    "Источники данных":"Data Sources","Диагностика":"Diagnostics","Настройки":"Settings",
    "ДАННЫЕ":"DATA","ЗНАНИЕ":"KNOWLEDGE","СИСТЕМА":"SYSTEM","Данные":"Data","Знание":"Knowledge","Система":"System",
    "Журнал":"Journal","Тренды":"Trends","Состав тела":"Body Composition","Timeline":"Timeline",
    // Nav groups + sections from the registry — keep EN mode fully translated so
    // the sidebar never mixes languages (group headers + section links).
    "Сон":"Sleep","Активность":"Activity","Стресс":"Stress","Тело и анализы":"Body & Labs",
    "Аналитика":"Analytics","Знание и цели":"Knowledge & Goals","Медкарта":"Medical",
    "Нагрузка":"Strain","Тело":"Body","Привычки":"Habits","Анализы":"Labs","Хронология":"Timeline",
    "Влияние":"Correlations","Алгоритмы":"Algorithms","Девайсы":"Devices","Источники":"Sources",
    "Тренировки":"Workouts","ДНК":"DNA","Вакцинация":"Vaccination","Протоколы":"Protocols",
    "Корреляции":"Correlations","Обзор показателей":"Vitals Overview","Лента дней":"Day Feed",
    "Каскад целей":"Goal Cascade","Biomarkers (Анализы)":"Biomarkers","Research":"Research",
    "Что сделано сегодня":"What you did today","набор целевых привычек":"your target habits",
    "Прогресс":"Progress","Лечь до 23:30":"Lights out by 23:30","Свет утром 15 мин":"Morning light, 15 min",
    "Прогулка 10к шагов":"Walk 10k steps","Вода 2.5л":"Water, 2.5 L","Без кофе после 15:00":"No coffee after 15:00",
    "Настроение / энергия":"Mood / energy","Усталость":"Fatigue","Энергия":"Energy",
    "Физиологическое восстановление":"Physiological recovery",
    // Labels are authored in title case and UPPERCASED by CSS (text-transform),
    // so dictionary keys must match the title-case source, not the rendered caps.
    "Из чего сложился":"How it's built","ИЗ ЧЕГО СЛОЖИЛСЯ":"HOW IT'S BUILT",
    "Вариабельность (HRV)":"Variability (HRV)","Пульс покоя (RHR)":"Resting HR (RHR)",
    "Сон сегодня":"Sleep today","Нагрузка (Strain)":"Strain","Нагрузка за день (Strain)":"Daily strain","Нагрузка":"Strain",
    // Зонные фразы word() — без них EN-режим показывал русскую зону при
    // английских ярлыках (самый заметный языковой разнобой Today).
    "Зелёная зона — организм готов к нагрузкам":"Green zone — ready for load",
    "Красная зона — необходим глубокий покой":"Red zone — deep rest needed",
    "Восстановление":"Recovery","ВОССТАНОВЛЕНИЕ":"RECOVERY",
    // Day Pulse: заголовки карточек (тексты инструкций длинные и живут в RU).
    "Непрерывная частота сердечных сокращений (ЧСС)":"Continuous heart rate (HR)",
    "данные за последние 24 часа":"last 24 hours",
    "Распределение зон ЧСС":"HR zone distribution",
    "время нахождения в кардиозонах":"time in cardio zones",
    "Указать город в Профиле":"Set city in Profile",
    // Формы Meds/Vaccination: ярлыки с «*» — отдельные текстовые узлы.
    "Название *":"Name *","Вакцина *":"Vaccine *","Дата прививки *":"Vaccination date *",
    "Доза / серия":"Dose / series","Следующая ревакцинация":"Next booster","Добавить запись":"Add a record",
    "утро":"morning","вечер":"evening","сам назначил":"self-prescribed","назначение врача":"prescribed by doctor",
    // Демо-баннер секций (registry).
    "Раздел на демо-данных — реальные появятся после подключения источника.":"Section runs on demo data — real values appear once a source is connected.",
    "— без отметок":"— no entries",
    "Готовность к дню и n-of-1 рекомендации":"Day readiness & n-of-1 recommendations",
    "выводы на основе персонального оверлея":"insights from your personal overlay",
    "Отметить эмоцию":"Log emotion","Сделай сегодня":"Do today","Вариабельность":"Variability",
    "Редактировать":"Edit","Жёлтая зона — умеренный режим":"Yellow zone — moderate mode",
    "ПУЛЬС ПОКОЯ":"RESTING HR","СОН":"SLEEP","мс":"ms","уд/мин":"bpm","ч":"h",
    "Локально · твои данные не уходят с устройства":"Local · your data never leaves your device",
    "Данные и bridge":"Data & bridge","Пересобрать":"Rebuild","Обнови WHOOP":"Update WHOOP",
    "Снапшот данных":"Data snapshot","онлайн":"online","офлайн":"offline","данные на":"data as of",
    "recovery сегодня из твоих данных tracker":"today's recovery from your tracker data",
    "Биохимическая панель":"Biochemistry panel","НАЗВАНИЕ":"NAME","РЕЗУЛЬТАТ":"RESULT",
    "РЕФЕРЕНС":"REFERENCE","ОПТИМУМ":"OPTIMUM","СТАТУС":"STATUS","ДОВЕРИЕ":"CONFIDENCE",
    "Оптимально":"Optimal","Повышен":"High","Понижен":"Low","В норме":"Normal","Анализы не загружены":"No labs loaded",
    "записей пока нет":"no records yet","Список пуст":"List is empty","Добавить":"Add",
    "Принял":"Taken","Удалить":"Delete","Название":"Name","Тип":"Type","Доза":"Dose","Пауза":"Pause",
    "Расписание":"Schedule","Дата начала":"Start date","Источник":"Source","лекарство":"medication","БАД":"supplement",
    "Профиль":"Profile","Профиль тела":"Body profile","Возраст":"Age","Пол":"Sex",
    "Рост, см":"Height, cm","Вес, кг":"Weight, kg","Город и локации":"City & locations",
    "Параметры расчётов":"Calculation parameters","Цель по здоровью":"Health goal","Обо мне":"About me",
    "Скачать как файлы":"Download as files","Определить":"Detect","Отметить":"Mark",
    "Замеры веса":"Weight measurements","Добавить замер":"Add measurement","не указан":"not set",
    "Тема":"Theme","Тёмная":"Dark","Светлая":"Light","Брутал":"Brutalist","Оформление":"Appearance",
    "Дип-ресёрч агентом":"Deep research by agent","Сгенерировать инсайт":"Generate insight",
    "Анализ трендов агентом":"Trend analysis by agent","Пересчитать корреляции агентом":"Recompute correlations by agent",
    "Подключить источник":"Connect a source","Запусти анализ":"Run analysis","К биомаркерам":"To biomarkers",
    "Погода сегодня":"Weather today","Календарь сегодня":"Calendar today","Циркадное колесо":"Circadian wheel",
    "Энергия дня":"Energy of the day","Эмоции":"Emotions","Загрузка…":"Loading…","Готово":"Done","Ошибка":"Error"
  };
  const SKIP = {SCRIPT:1,STYLE:1,TEXTAREA:1,NOSCRIPT:1};
  // Default follows the browser locale (Russian locales get the native, fully
  // coherent RU UI; everyone else gets EN). An explicit toggle choice persists
  // and always wins. Note: EN is a progressive DOM translation — engine-authored
  // prose and parameterized strings stay Russian, so RU is the complete language.
  function localeDefault(){
    const l = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
    return l.indexOf('ru') === 0 ? 'ru' : 'en';
  }
  function lang(){ return localStorage.getItem('oh_lang') || localeDefault(); }
  function trNode(node){
    const v = node.nodeValue; if(!v) return;
    const k = v.trim(); if(!k) return;
    const en = OH_I18N[k];
    if(en && en !== k) node.nodeValue = v.replace(k, en);
  }
  function trEl(root){
    if(lang() !== 'en' || !root) return;
    if(root.nodeType === 3){ trNode(root); return; }
    if(root.nodeType !== 1) return;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: n => (n.parentNode && SKIP[n.parentNode.nodeName]) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodes=[]; let n; while(n=w.nextNode()) nodes.push(n);
    nodes.forEach(trNode);
    root.querySelectorAll && root.querySelectorAll('[placeholder],[title],[aria-label]').forEach(el=>{
      ['placeholder','title','aria-label'].forEach(a=>{
        const val = el.getAttribute(a); if(val && OH_I18N[val.trim()]) el.setAttribute(a, OH_I18N[val.trim()]);
      });
    });
  }
  function injectToggle(){
    if(document.getElementById('oh-lang-toggle')) return;
    // Toggle host: V1 footer, V2 stats-strip. Not ready yet -> start()'s timers retry.
    const footer = document.querySelector('footer');
    const strip = document.querySelector('.stats-strip-right');
    if(!footer && !strip) return;
    const right = footer ? (footer.querySelector('span:nth-child(2)') || footer) : strip;
    const cur = lang();
    const seg = document.createElement('span');
    seg.id = 'oh-lang-toggle';
    seg.title = 'Interface language';
    seg.style.cssText = 'display:inline-flex;align-items:center;border:1px solid var(--line,rgba(0,0,0,.14));'
      + 'border-radius:5px;overflow:hidden;font:600 10px/1 var(--font-mono,ui-monospace,monospace);letter-spacing:.1em;';
    [['EN','en'],['RU','ru']].forEach(([txt,code]) => {
      const b = document.createElement('button');
      b.textContent = txt;
      const active = code === cur;
      b.style.cssText = 'padding:4px 7px;border:0;cursor:pointer;font:inherit;letter-spacing:inherit;'
        + (active ? 'background:var(--accent,#1f7d57);color:#fff;' : 'background:transparent;color:var(--muted,#8a8a8a);');
      if(!active) b.onclick = () => { localStorage.setItem('oh_lang', code); location.reload(); };
      seg.appendChild(b);
    });
    right.insertBefore(seg, right.firstChild);
  }
  function start(){
    injectToggle();
    if(lang() === 'en'){
      trEl(document.body);
      [400, 1000, 2200].forEach(ms => setTimeout(() => { trEl(document.body); injectToggle(); }, ms)); // catch animation-deferred renders
      new MutationObserver(muts => {
        for(const m of muts){ m.addedNodes && m.addedNodes.forEach(node => {
          if(node.nodeType===1 || node.nodeType===3) trEl(node);
        }); }
      }).observe(document.body, {childList:true, subtree:true});
    }
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
