const pptxgen = require("pptxgenjs");
const path = require("path");
const sharp = require("sharp");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const fa = require("react-icons/fa");
const SKILL = "C:/Users/ASUS/.claude/skills/synced/12f9bcf6-9119-428e-8456-bff47f4b0ed4_e0c99e70-2dd1-405b-98f5-263c62c7fc81/pptx";
const { applyTheme } = require(SKILL + "/scripts/apply_theme.js");

const PROJECT = "C:/Users/ASUS/Desktop/superpower-test";
const OUT = PROJECT + "/docs/presentation/Caspian-Shoreline-Monitor-ru.pptx";
const HERO = PROJECT + "/docs/caspian/screens/hero-1440-dark-2025.png";

const THEME = {
  name: "Caspian Monitor",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "0B1B2B", lt1: "FFFFFF", dk2: "16405F", lt2: "EAF1F6",
    accent1: "2E86DE", accent2: "F2A33A", accent3: "E5484D",
    accent4: "1F9D8A", accent5: "6B7F92", accent6: "F2C94C",
    hlink: "2E86DE", folHlink: "6B7F92",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Монитор отступления берега Каспия";
pres.author = "Студент с ИИ-ассистентом";
const C = pres.SchemeColor;

// ---------- helpers ----------
async function icon(Comp, hex, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + hex, size: String(size) }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
function lerpColor(a, b, t) {
  const pa = [0, 2, 4].map((i) => parseInt(a.substr(i, 2), 16));
  const pb = [0, 2, 4].map((i) => parseInt(b.substr(i, 2), 16));
  return pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0")).join("").toUpperCase();
}
function yearDots(slide, x, y, n, d, gap, name) {
  for (let i = 0; i < n; i++) {
    slide.addShape(pres.ShapeType.ellipse, {
      x: x + i * (d + gap), y, w: d, h: d,
      fill: { color: lerpColor("2E86DE", "E5484D", i / (n - 1)) },
      line: { color: lerpColor("2E86DE", "E5484D", i / (n - 1)), width: 0 },
      objectName: `${name}-${i}`,
    });
  }
}
async function iconCircle(slide, Comp, x, y, d, fillColor, iconHex, name) {
  slide.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fillColor }, line: { color: fillColor, width: 0 }, objectName: name + "-bg" });
  const pad = d * 0.25;
  slide.addImage({ data: await icon(Comp, iconHex), x: x + pad, y: y + pad, w: d - 2 * pad, h: d - 2 * pad, altText: name, objectName: name });
}
const FOOT = "Монитор отступления берега Каспия · первый результат, не доказательство";

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "TITLE_DARK", background: { color: C.text1 },
  objects: [],
});
pres.defineSlideMaster({
  title: "CONTENT", background: { color: C.background1 },
  objects: [
    { text: { text: FOOT, options: { x: 0.6, y: 7.0, w: 9.5, h: 0.3, fontSize: 11, color: C.accent5, margin: 0, isTextBox: true } } },
  ],
  slideNumber: { x: 12.0, y: 7.0, w: 0.7, h: 0.3, fontSize: 11, color: C.accent5, align: "right" },
});
pres.defineSlideMaster({
  title: "CONTENT_DARK", background: { color: C.text1 },
  objects: [
    { text: { text: FOOT, options: { x: 0.6, y: 7.0, w: 9.5, h: 0.3, fontSize: 11, color: C.accent5, margin: 0, isTextBox: true } } },
  ],
  slideNumber: { x: 12.0, y: 7.0, w: 0.7, h: 0.3, fontSize: 11, color: C.accent5, align: "right" },
});

function titleOn(slide, text, dark) {
  slide.addText(text, {
    x: 0.6, y: 0.4, w: 12.1, h: 0.95, fontSize: 36, bold: true, fontFace: "Cambria",
    color: dark ? C.background1 : C.text1, align: "left", valign: "middle", margin: 0, isTextBox: true, objectName: "title",
  });
}
function card(slide, x, y, w, h, name, fill) {
  slide.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.12, fill: { color: fill || C.background2 }, line: { color: fill || C.background2, width: 0 }, objectName: name });
}

(async () => {
  const ic = {};
  // =============== 1. Title ===============
  {
    const s = pres.addSlide({ masterName: "TITLE_DARK" });
    s.addText("Монитор отступления берега Каспия", { x: 0.8, y: 1.5, w: 11.7, h: 1.9, fontSize: 54, bold: true, fontFace: "Cambria", color: C.background1, margin: 0, valign: "top", isTextBox: true, objectName: "title" });
    s.addText("Открытый воспроизводимый инструмент по спутниковым снимкам 2000–2025 с проверкой, заданной заранее", { x: 0.8, y: 3.5, w: 10.5, h: 1.0, fontSize: 22, color: C.background2, margin: 0, valign: "top", isTextBox: true, objectName: "subtitle" });
    yearDots(s, 0.8, 4.85, 26, 0.3, 0.12, "dot");
    s.addText("2000", { x: 0.8, y: 5.25, w: 1.2, h: 0.35, fontSize: 14, color: C.accent1, margin: 0, isTextBox: true, objectName: "year-start" });
    s.addText("2025", { x: 0.8 + 25 * 0.42 - 0.9, y: 5.25, w: 1.2, h: 0.35, fontSize: 14, color: C.accent3, align: "right", margin: 0, isTextBox: true, objectName: "year-end" });
    s.addText("Global Sustainability Challenge 2026–27 · Adaptation & Resilience · Water", { x: 0.8, y: 6.2, w: 11.5, h: 0.4, fontSize: 16, color: C.accent5, margin: 0, isTextBox: true, objectName: "event" });
    s.addText("Студент, создающий проект вместе с ИИ-ассистентом · Азербайджан", { x: 0.8, y: 6.6, w: 11.5, h: 0.4, fontSize: 16, color: C.accent5, margin: 0, isTextBox: true, objectName: "author" });
    s.addNotes("Добрый день. Это монитор отступления берега Каспия у Азербайджана. Главная идея не в новизне темы, а в том, что проверка была зафиксирована до результатов. Цвет от синего к красному на слайде — это годы 2000–2025, такой же шкалой раскрашены береговые линии на карте.");
  }

  // =============== 2. Problem ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Каспий мелеет, тема уже изучена", false);
    s.addText("−2,135 м", { x: 0.6, y: 1.6, w: 4.6, h: 1.3, fontSize: 66, bold: true, fontFace: "Cambria", color: C.accent3, margin: 0, valign: "middle", isTextBox: true, objectName: "stat" });
    s.addText("относительное изменение сезонного уровня в нашем ряду G-REALM, 2000–2025 (июль–октябрь). Высоты даны относительно спутникового ориентира, это не абсолютная высота.", { x: 0.6, y: 3.0, w: 4.4, h: 2.2, fontSize: 16, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "stat-caption" });
    const items = [
      [fa.FaMapMarkedAlt, "218 км²", "обнажённой суши в заповеднике Кызылагач, 2014–2023 (тезисы EGU24-14488)"],
      [fa.FaWater, "−1,39 м и −35 %", "уровень и береговая линия Куринской зоны, 1996–2019 (Гасымов, 2021)"],
    ];
    for (let i = 0; i < items.length; i++) {
      const y = 1.6 + i * 1.95;
      card(s, 5.6, y, 7.1, 1.75, "card-" + i);
      await iconCircle(s, items[i][0], 5.85, y + 0.45, 0.85, C.accent1, "FFFFFF", "icon-" + i);
      s.addText(items[i][1], { x: 7.0, y: y + 0.2, w: 5.5, h: 0.65, fontSize: 30, bold: true, fontFace: "Cambria", color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "card-stat-" + i });
      s.addText(items[i][2], { x: 7.0, y: y + 0.9, w: 5.5, h: 0.75, fontSize: 14, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "card-text-" + i });
    }
    card(s, 0.6, 5.65, 12.1, 1.1, "banner", C.accent5 && C.text2);
    s.addText("Наш вклад не новизна темы, а открытый повторяемый инструмент и проверка, зафиксированная до результатов.", { x: 0.9, y: 5.7, w: 11.5, h: 1.0, fontSize: 18, bold: true, color: C.background1, margin: 0, valign: "middle", isTextBox: true, objectName: "banner-text" });
    s.addNotes("Падение уровня Каспия подтверждено источниками. Цифры слева посчитаны нашим кодом по ряду NASA G-REALM, это относительное изменение. Справа две работы, которые мы нашли и открыли. 218 квадратных километров относятся к тому же заповеднику, что и наш участок, но это другой метод и период, поэтому мы это не сравниваем с нашей цифрой.");
  }

  // =============== 3. What was built ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Что построено", false);
    const steps = [
      [fa.FaSatellite, "Снимки", "Landsat 5/7/8/9, 26 лет, июль–август; сезон выбран по облачности"],
      [fa.FaLayerGroup, "Водная маска", "индекс воды MNDWI, медиана по сценам года, порог Оцу"],
      [fa.FaRoute, "Три участка", "линии замера по нормали к берегу: 85, 35 и 74 профиля"],
      [fa.FaRuler, "Положение берега", "расстояние от неподвижной опорной линии, в метрах"],
      [fa.FaChartLine, "Сравнение с уровнем", "ряд NASA G-REALM; проверка «оставь один год»"],
    ];
    for (let i = 0; i < steps.length; i++) {
      const x = 0.6 + i * 2.45;
      card(s, x, 1.65, 2.3, 3.7, "step-" + i);
      await iconCircle(s, steps[i][0], x + 0.7, 1.9, 0.9, i === 4 ? C.accent3 : C.accent1, "FFFFFF", "step-icon-" + i);
      s.addText(String(i + 1) + ". " + steps[i][1], { x: x + 0.15, y: 3.0, w: 2.0, h: 0.6, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "step-title-" + i });
      s.addText(steps[i][2], { x: x + 0.15, y: 3.65, w: 2.0, h: 1.6, fontSize: 16, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "step-text-" + i });
    }
    card(s, 0.6, 5.65, 12.1, 1.1, "extra-bar", C.text2);
    s.addText("Плюс: контроль по Sentinel-2 (2017–2025) и интерактивная страница, которая открывается двойным кликом.", { x: 0.9, y: 5.7, w: 11.5, h: 1.0, fontSize: 18, bold: true, color: C.background1, margin: 0, valign: "middle", isTextBox: true, objectName: "extra-text" });
    s.addNotes("Конвейер из пяти шагов. Все данные открытые, без платных аккаунтов. Профили — это линии, вдоль которых мы измеряем, где проходит берег каждый год. Контроль по Sentinel-2 показывает согласие двух датчиков, а не точность относительно измерений на земле.");
  }

  // =============== 4. Pre-registered check ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Проверка, заданная заранее", false);
    s.addText("Участок считается «подтверждённым (по протоколу)», только если одновременно:", { x: 0.6, y: 1.55, w: 7.2, h: 0.8, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "lead" });
    const conds = [
      "Модель «уровень по берегу» ошибается меньше, чем простой тренд по годам (метод «оставь один год»)",
      "Корреляция годовых изменений берега и падения уровня не ниже 0,5",
      "Нижняя граница 95 %-го интервала этой корреляции выше нуля",
    ];
    for (let i = 0; i < conds.length; i++) {
      const y = 2.4 + i * 1.1;
      s.addShape(pres.ShapeType.ellipse, { x: 0.6, y: y + 0.1, w: 0.7, h: 0.7, fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 }, objectName: "num-bg-" + i });
      s.addText(String(i + 1), { x: 0.6, y: y + 0.1, w: 0.7, h: 0.7, fontSize: 24, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: "num-" + i });
      s.addText(conds[i], { x: 1.5, y, w: 6.3, h: 0.9, fontSize: 16, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "cond-" + i });
    }
    s.addText("Общий вердикт «подтверждено» — не менее двух участков; один участок — «частично».", { x: 0.6, y: 5.8, w: 7.2, h: 0.9, fontSize: 16, bold: true, color: C.accent3, margin: 0, valign: "top", isTextBox: true, objectName: "overall-rule" });
    card(s, 8.2, 1.55, 4.5, 5.2, "dark-card", C.text1);
    s.addText("1 запуск", { x: 8.5, y: 1.75, w: 3.9, h: 0.9, fontSize: 40, bold: true, fontFace: "Cambria", color: C.accent2, margin: 0, valign: "middle", isTextBox: true, objectName: "dark-stat" });
    s.addText("Правила записаны до результатов. Результат сохранён сразу, ничего не подгонялось.", { x: 8.5, y: 2.7, w: 3.9, h: 1.2, fontSize: 15, color: C.background1, margin: 0, valign: "top", isTextBox: true, objectName: "dark-text-1" });
    s.addText("Та же дисциплина уже работала. Первая идея — прогноз засухи для пшеницы: проверка не показала силы (r = 0,10 и −0,06), и мы остановили проект, а не подгоняли модель.", { x: 8.5, y: 4.0, w: 3.9, h: 2.6, fontSize: 15, color: C.background2, margin: 0, valign: "top", isTextBox: true, objectName: "dark-text-2" });
    s.addNotes("Критерий записан в протоколе до результатов. Уточнение знака корреляции тоже сделано до результатов и раскрыто. Пшеничный проект остановлен именно потому, что та же проверка показала отсутствие силы.");
  }

  // =============== 5. Result (dark) ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT_DARK" });
    titleOn(s, "Результат: частично", true);
    card(s, 0.6, 1.6, 5.2, 2.2, "badge", C.accent2);
    s.addText("ЧАСТИЧНО", { x: 0.9, y: 1.7, w: 4.6, h: 1.1, fontSize: 48, bold: true, fontFace: "Cambria", color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "badge-title" });
    s.addText("1 из 3 участков подтверждён по протоколу. Это не проверка монитора в целом.", { x: 0.9, y: 2.8, w: 4.6, h: 0.95, fontSize: 16, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "badge-text" });
    const chips = [
      [C.accent4, "Кызылагач: подтверждён (протокол)"],
      [C.accent5, "Дельта Куры: не подтверждён"],
      [C.accent5, "Лянкяран–Астара: не подтверждён"],
    ];
    for (let i = 0; i < chips.length; i++) {
      const y = 4.15 + i * 0.8;
      s.addShape(pres.ShapeType.ellipse, { x: 0.65, y: y + 0.12, w: 0.36, h: 0.36, fill: { color: chips[i][0] }, line: { color: chips[i][0], width: 0 }, objectName: "chip-dot-" + i });
      s.addText(chips[i][1], { x: 1.2, y, w: 4.7, h: 0.6, fontSize: 16, color: C.background1, margin: 0, valign: "middle", isTextBox: true, objectName: "chip-" + i });
    }
    s.addChart(pres.charts.BAR, [
      { name: "Модель по берегу", labels: ["Кызылагач", "Дельта Куры", "Лянкяран–Астара"], values: [0.19, 0.21, 0.73] },
      { name: "Простой тренд по годам", labels: ["Кызылагач", "Дельта Куры", "Лянкяран–Астара"], values: [0.26, 0.26, 0.26] },
    ], {
      x: 6.2, y: 1.5, w: 6.5, h: 5.2, barDir: "col", barGrouping: "clustered",
      chartColors: ["2E86DE", "6B7F92"],
      showTitle: true, title: "Ошибка предсказания уровня, м (меньше — лучше)", titleColor: "FFFFFF", titleFontSize: 16, titleFontFace: "+mn-lt",
      showValue: true, dataLabelPosition: "outEnd", dataLabelColor: "FFFFFF", dataLabelFontSize: 14, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: "0.00",
      catAxisLabelColor: "EAF1F6", catAxisLabelFontSize: 14, catAxisLabelFontFace: "+mn-lt",
      valAxisLabelColor: "EAF1F6", valAxisLabelFontSize: 12, valAxisLabelFontFace: "+mn-lt", valAxisMinVal: 0, valAxisMaxVal: 0.9,
      valGridLine: { color: "2B4A63", size: 0.75 }, catGridLine: { style: "none" },
      showLegend: true, legendPos: "b", legendColor: "EAF1F6", legendFontSize: 14, legendFontFace: "+mn-lt",
      objectName: "rmse-chart",
    });
    s.addNotes("Ошибка предсказания уровня методом «оставь один год»: синий столбик — модель по положению берега, серый — простой тренд по годам. Только у Кызылагача модель лучше тренда при выполнении остальных условий. У дельты Куры ошибка тоже меньше, но корреляция 0,41 ниже порога 0,5. У Лянкяран–Астара модель хуже тренда.");
  }

  // =============== 6. Table ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Цифры по участкам", false);
    const H = (t) => ({ text: t, options: { bold: true, color: "FFFFFF", fill: { color: "16405F" }, fontSize: 15, valign: "middle", align: "left" } });
    const cell = (t, o) => ({ text: t, options: Object.assign({ fontSize: 16, color: "0B1B2B", valign: "middle", align: "left" }, o || {}) });
    const rows = [
      [H("Участок"), H("Ошибка: модель / тренд, м"), H("r приращений"), H("Нижняя граница"), H("Вердикт по протоколу")],
      [cell("Кызылагач", { bold: true }), cell("0,19 / 0,26"), cell("0,56"), cell("0,27"), cell("подтверждён (протокол)", { bold: true, color: "0B6B5D", fill: { color: "D5F0EA" } })],
      [cell("Дельта Куры", { bold: true }), cell("0,21 / 0,26"), cell("0,41"), cell("0,07"), cell("не подтверждён (r < 0,5)", { color: "0B1B2B", fill: { color: "EAF1F6" } })],
      [cell("Лянкяран–Астара", { bold: true }), cell("0,73 / 0,26"), cell("−0,37"), cell("−0,66"), cell("не подтверждён", { color: "0B1B2B", fill: { color: "EAF1F6" } })],
    ];
    s.addTable(rows, { x: 0.6, y: 1.65, w: 12.1, colW: [2.9, 2.8, 1.9, 2.0, 2.5], rowH: [0.65, 0.85, 0.85, 0.85], border: { type: "solid", pt: 0.75, color: "C7D5E0" }, margin: [0.05, 0.12, 0.05, 0.12], objectName: "results-table" });
    s.addText("r — корреляция между годовым отступлением берега и годовым падением уровня; интервал построен бутстрэпом с независимой выборкой пар. Уровень — относительные высоты спутника. По 25 лет и 23 пары на участок (2003 год отсутствует).", { x: 0.6, y: 5.45, w: 12.1, h: 1.2, fontSize: 14, color: C.accent5, margin: 0, valign: "top", isTextBox: true, objectName: "table-note" });
    s.addNotes("Все цифры взяты из записи единственного запуска. Они воспроизводятся побайтно, контрольная сумма записана в отчёте. Независимый проверяющий пересчитал их своим кодом.");
  }

  // =============== 7. Screenshot ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Интерактивная страница", false);
    s.addImage({ path: HERO, x: 0.6, y: 1.5, w: 8.2, h: 5.125, altText: "Скриншот страницы монитора: спутниковая подложка, береговые линии, вердикт ЧАСТИЧНО на первом экране", objectName: "screenshot", shadow: { type: "outer", color: "000000", opacity: 0.25, blur: 8, offset: 3, angle: 90 } });
    const pts = [
      [fa.FaChartLine, "Ползунок года и анимация отступления берега 2000–2025"],
      [fa.FaBalanceScale, "Вердикт «ЧАСТИЧНО» и оговорки на первом экране"],
      [fa.FaSearch, "Карточки участков: цифры проверки и пометки о слабых местах"],
      [fa.FaCheckCircle, "Открывается двойным кликом, работает без сервера и без интернета"],
    ];
    for (let i = 0; i < pts.length; i++) {
      const y = 1.5 + i * 1.3;
      await iconCircle(s, pts[i][0], 9.1, y + 0.05, 0.7, C.accent1, "FFFFFF", "pt-icon-" + i);
      s.addText(pts[i][1], { x: 9.95, y, w: 2.8, h: 1.1, fontSize: 14, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "pt-text-" + i });
    }
    s.addNotes("Здесь покажите страницу вживую: открыть web/caspian.html двойным кликом, запустить воспроизведение, переключить участок. На скриншоте участок Кызылагач, спутниковая подложка Esri, ползунок года стоит на 2002.");
  }

  // =============== 8. How to read ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Как честно читать результат", false);
    const cells = [
      [fa.FaBalanceScale, C.accent2, "Малый запас у Кызылагача", "r = 0,56 при пороге 0,5. До 2018 года r = 0,21, сигнал в основном из 2021–2025. При фиксированном пороге воды 0,0 участок уже не проходит (0,49)."],
      [fa.FaFlask, C.accent1, "Низкая мощность проверки", "Даже при реальной связи участок проходит примерно в 30 % случаев, общий вердикт — в 8 %. «Не подтверждено» значит «не показано», а не «опровергнуто»."],
      [fa.FaRuler, C.accent3, "Лянкяран–Астара: слишком мало сигнала", "Берег сместился примерно на 39 м (около 1,3 пикселя Landsat) за 25 лет. Это предел 30-метровых снимков, а не довод против физической связи."],
      [fa.FaChartLine, C.accent4, "Оценка 20–22 м на 1 см", "Столько метров берега приходится на 1 см падения уровня, только для Кызылагача и только как условная оценка из протокола."],
    ];
    for (let i = 0; i < cells.length; i++) {
      const x = 0.6 + (i % 2) * 6.15, y = 1.6 + Math.floor(i / 2) * 2.6;
      card(s, x, y, 5.95, 2.4, "read-card-" + i);
      await iconCircle(s, cells[i][0], x + 0.25, y + 0.3, 0.8, cells[i][1], "FFFFFF", "read-icon-" + i);
      s.addText(cells[i][2], { x: x + 1.3, y: y + 0.2, w: 4.5, h: 0.55, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "read-title-" + i });
      s.addText(cells[i][3], { x: x + 1.3, y: y + 0.8, w: 4.5, h: 1.5, fontSize: 15, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "read-text-" + i });
    }
    s.addNotes("Это самый важный слайд для честности. Мощность и частоту случайных подтверждений мы оценили независимо, эти оценки получены в проверке проекта с помощью ИИ, а не внешними экспертами.");
  }

  // =============== 9. Limits ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Чего мы не утверждаем", false);
    card(s, 0.6, 1.55, 5.95, 5.2, "left-card");
    s.addText("Не утверждаем", { x: 0.9, y: 1.7, w: 5.4, h: 0.6, fontSize: 22, bold: true, fontFace: "Cambria", color: C.accent3, margin: 0, valign: "middle", isTextBox: true, objectName: "left-title" });
    s.addText([
      { text: "что монитор проверен или доказан", options: { bullet: true, breakLine: true } },
      { text: "что связь «берег — уровень» подтверждена на всех участках", options: { bullet: true, breakLine: true } },
      { text: "что 225,5 км² на Кызылагаче — это дно моря или сравнение с 218 км² из статьи", options: { bullet: true, breakLine: true } },
      { text: "что контроль по Sentinel-2 — точность относительно измерений на земле", options: { bullet: true } },
    ], { x: 0.9, y: 2.45, w: 5.4, h: 4.1, fontSize: 18, color: C.text1, margin: 0, valign: "top", paraSpaceAfter: 12, isTextBox: true, objectName: "left-list" });
    card(s, 6.75, 1.55, 5.95, 5.2, "right-card");
    s.addText("Что нужно помнить", { x: 7.05, y: 1.7, w: 5.4, h: 0.6, fontSize: 22, bold: true, fontFace: "Cambria", color: C.accent1, margin: 0, valign: "middle", isTextBox: true, objectName: "right-title" });
    s.addText([
      { text: "25 лет и 23 пары на участок, 2003 год отсутствует", options: { bullet: true, breakLine: true } },
      { text: "у Кызылагача берег уходит за конец линий замера в 2024–2025", options: { bullet: true, breakLine: true } },
      { text: "контроль по Sentinel-2 у Кызылагача предварительный, 6 общих лет", options: { bullet: true, breakLine: true } },
      { text: "правила геометрии профилей уточнены после просмотра счётчиков, но до любого результата по связи, и это записано", options: { bullet: true, breakLine: true } },
      { text: "знак корреляции уточнён до результатов и раскрыт", options: { bullet: true } },
    ], { x: 7.05, y: 2.45, w: 5.4, h: 4.1, fontSize: 16, color: C.text1, margin: 0, valign: "top", paraSpaceAfter: 10, isTextBox: true, objectName: "right-list" });
    s.addNotes("Здесь мы перечисляем то, чего не утверждаем, и то, что проект сам раскрывает. Сильная сторона работы в том, что слабые места записаны в самом результате.");
  }

  // =============== 10. Process ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Как это делалось", false);
    const stats = [["380", "тестов проходят (оба проекта)"], ["11", "задач плана проекта про Каспий"], ["9", "раундов исправлений по замечаниям проверяющих в 7 задачах из 11"]];
    for (let i = 0; i < stats.length; i++) {
      const x = 0.6 + i * 4.1;
      card(s, x, 1.6, 3.9, 1.9, "stat-card-" + i);
      s.addText(stats[i][0], { x: x + 0.25, y: 1.7, w: 1.7, h: 1.7, fontSize: 52, bold: true, fontFace: "Cambria", color: C.accent1, margin: 0, valign: "middle", isTextBox: true, objectName: "stat-num-" + i });
      s.addText(stats[i][1], { x: x + 1.95, y: 1.75, w: 1.8, h: 1.6, fontSize: 14, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "stat-label-" + i });
    }
    s.addText("К каждой задаче: исполнитель — ИИ-агент, затем независимый проверяющий. Проверяющие нашли ошибки, которые испортили бы результат:", { x: 0.6, y: 3.75, w: 12.1, h: 0.8, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "findings-lead" });
    const found = ["линии замера, начинавшиеся в море", "кеш, отдававший устаревшие числа", "отсутствие теста на утечку данных", "обещания в тексте, которых данные не поддерживали"];
    for (let i = 0; i < found.length; i++) {
      const x = 0.6 + (i % 2) * 6.15, y = 4.6 + Math.floor(i / 2) * 1.0;
      await iconCircle(s, fa.FaCheckCircle, x, y + 0.05, 0.55, C.accent4, "FFFFFF", "found-icon-" + i);
      s.addText(found[i], { x: x + 0.75, y, w: 5.2, h: 0.65, fontSize: 16, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "found-" + i });
    }
    s.addNotes("Проект сделан по процессу: идея, дизайн-документ, план, задачи с исполнителем и проверяющим. Цифры из журнала проекта. Это не внешняя экспертиза, проверяющие тоже были ИИ-агентами.");
  }

  // =============== 11. Next ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT" });
    titleOn(s, "Дальше: фаза Build (план)", false);
    const nxt = [
      [fa.FaMapMarkedAlt, "Больше участков и лет", "добавить участки и более ранние данные, проверить тем же протоколом"],
      [fa.FaSatellite, "Снимки точнее", "для крутых берегов, где 30 м не хватает, пробовать Sentinel-2"],
      [fa.FaRuler, "Данные на местности", "сверить береговую линию с промерами или мареографами, если они будут доступны"],
      [fa.FaSearch, "Показать пользователям", "найти тех, кому нужна такая карта, и собрать отзывы"],
    ];
    for (let i = 0; i < nxt.length; i++) {
      const x = 0.6 + (i % 2) * 6.15, y = 1.6 + Math.floor(i / 2) * 2.35;
      card(s, x, y, 5.95, 2.15, "next-card-" + i);
      await iconCircle(s, nxt[i][0], x + 0.25, y + 0.3, 0.8, C.accent1, "FFFFFF", "next-icon-" + i);
      s.addText(nxt[i][1], { x: x + 1.3, y: y + 0.2, w: 4.5, h: 0.55, fontSize: 18, bold: true, color: C.text1, margin: 0, valign: "middle", isTextBox: true, objectName: "next-title-" + i });
      s.addText(nxt[i][2], { x: x + 1.3, y: y + 0.8, w: 4.5, h: 1.2, fontSize: 16, color: C.text1, margin: 0, valign: "top", isTextBox: true, objectName: "next-text-" + i });
    }
    s.addText("Всё это план. Ничего из этого ещё не сделано, партнёров и пользователей у проекта пока нет.", { x: 0.6, y: 6.35, w: 12.1, h: 0.5, fontSize: 16, bold: true, color: C.accent3, margin: 0, valign: "middle", isTextBox: true, objectName: "planned-note" });
    s.addNotes("Фаза Build идёт до февраля 2027 года. Мы прямо говорим, что это план.");
  }

  // =============== 12. Conclusion (dark) ===============
  {
    const s = pres.addSlide({ masterName: "CONTENT_DARK" });
    titleOn(s, "Итог", true);
    s.addText("Первый честный результат, а не доказательство", { x: 0.6, y: 1.55, w: 12.1, h: 1.4, fontSize: 44, bold: true, fontFace: "Cambria", color: C.accent2, margin: 0, valign: "middle", isTextBox: true, objectName: "statement" });
    const lines = [
      "Инструмент, протокол проверки и страница готовы и воспроизводятся",
      "Связь «берег — уровень» показана на одном участке из трёх, с малым запасом",
      "Следующий шаг — больше данных и участков, проверка по тому же протоколу",
    ];
    for (let i = 0; i < lines.length; i++) {
      const y = 3.35 + i * 0.95;
      s.addShape(pres.ShapeType.ellipse, { x: 0.65, y: y + 0.15, w: 0.4, h: 0.4, fill: { color: lerpColor("2E86DE", "E5484D", i / 2) }, line: { color: lerpColor("2E86DE", "E5484D", i / 2), width: 0 }, objectName: "end-dot-" + i });
      s.addText(lines[i], { x: 1.3, y, w: 11.3, h: 0.75, fontSize: 22, color: C.background1, margin: 0, valign: "middle", isTextBox: true, objectName: "end-line-" + i });
    }
    s.addText("Код будет опубликован вместе с заявкой · Данные: NASA G-REALM, USGS Landsat, ESA Sentinel-2 и WorldCover, Copernicus DEM", { x: 0.6, y: 6.35, w: 12.1, h: 0.5, fontSize: 14, color: C.accent5, margin: 0, valign: "middle", isTextBox: true, objectName: "sources" });
    s.addNotes("Закончить прямой фразой: результат частичный, мы это показываем, а не скрываем. Спасибо.");
  }

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("written", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
