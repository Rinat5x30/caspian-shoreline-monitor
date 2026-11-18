# Wheat Drought Early Warning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Карта риска засухи для пшеницы в Азербайджане по NDVI, осадкам и температуре, с проверкой «оставь один год» на официальной статистике урожайности, плюс текст заявки и сценарий видео для Global Sustainability Challenge.

**Architecture:** Python-пакет `wheatwatch` считает признаки по регионам и годам, проверяет простую регрессию на аномалию урожайности и пишет GeoJSON. Статическая HTML-карта (Leaflet) читает готовый GeoJSON. Сети нужны только на этапе загрузки данных, результаты кешируются в `data/raw`.

**Tech Stack:** Python 3.12, pandas, numpy, geopandas, pystac-client + planetary-computer, requests, pytest, ruff, black, mypy (strict), Leaflet.

**Spec:** `docs/superpowers/specs/2026-11-18-wheat-drought-early-warning-design.md`

## Global Constraints

- Только пшеница; без ML, без AquaCrop, без мобильного приложения и платежей.
- Только открытые данные без платных аккаунтов; бесплатная регистрация (Earthdata/Copernicus) только после вопроса владельцу.
- Никаких контактов с третьими лицами; никаких выдуманных партнёров, писем о намерениях, отзывов.
- В заявку идёт только то, что показала проверка; слабый сигнал публикуется как слабый.
- Целевая переменная: аномалия урожайности в процентах после удаления линейного тренда. Меры: корреляция Пирсона, RMSE, доля верно угаданных лет с урожайностью ниже нормы. Схема «оставь один год».
- Карта показывается на том уровне, на котором проведена проверка.
- Цель: не менее 8 лет урожайности; меньше лет — один составной предиктор вместо трёх.
- Стоп-правило: нет даже региональной серии урожайности за 6 часов — остановка и возврат к владельцу.
- Код: type hints, Black, Ruff, mypy strict; минимум комментариев.
- Видео и регистрацию делает владелец; Claude пишет только сценарий.
- Время каждого этапа пишется в `docs/superpowers/timing-log.md`.

## Review Focus

- Пропуски (нет урожайности или NDVI за регион-год): строка отбрасывается и считается в отчёте, нулём не заполняется.
- Регион с менее чем 3 годами или константной урожайностью: проверка выдаёт понятную ошибку, а не NaN-корреляцию.
- Идентификаторы регионов статистики не совпадают с геометрией (смена границ): загрузчик падает со списком несовпавших.
- Утечка тренда: тренд оценивается только внутри обучающей части каждой итерации, без отложенного года.
- Сеть упала или превышен лимит: частичная загрузка не превращается в нули; повторный запуск не перекачивает уже сохранённое.
- Слабый или отрицательный результат: подпись на карте и текст заявки говорят «сигнал слабый», а не скрывают его.

---

### Task 1: Данные урожайности (стоп-ворота)

**Files:**
- Create: `pyproject.toml`, `src/wheatwatch/__init__.py`, `src/wheatwatch/yields.py`, `data/raw/yields.csv`, `docs/superpowers/timing-log.md`
- Test: `tests/test_yields.py`

**Interfaces:**
- Produces: `load_yields(path: Path) -> pd.DataFrame` со столбцами `region_id: str`, `year: int`, `yield_c_ha: float`, отсортированная по `region_id, year`.
- Produces: `detrended_anomaly_pct(df: pd.DataFrame, train_years: list[int]) -> pd.Series`, индекс как у `df`; линейный тренд по региону оценивается только по `train_years`.

- [ ] **Step 1: Инициализация.** `git init` в папке проекта (вложенный репозиторий; корневой находится в домашней папке и не подходит). Создать `pyproject.toml` с pytest, ruff, black, mypy strict и `docs/superpowers/timing-log.md` с таблицей `этап | начало | конец | что помогло | что замедлило`. Записать строку этапа «brainstorming + design».
- [ ] **Step 2: Поиск данных (ограничено 6 часами).** Найти и выгрузить серию «урожайность пшеницы, ц/га» по районам или экономическим/посевным регионам за максимально длинный период. Источники по порядку: таблицы Госкомитета по статистике (stat.gov.az), отчёт IAMO WP5, FEWS NET country book. Допустим ручной перенос из PDF/XLS и пересчёт «валовой сбор / площадь». Сохранить в `data/raw/yields.csv` и записать источник и дату в `data/raw/SOURCES.md`.
- [ ] **Step 3: Ворота.** Если нет серии минимум по 5 регионам за минимум 5 лет — остановиться, записать в timing-log и вернуться к владельцу с описанием найденного. Если лет меньше 8, пометить в `SOURCES.md` и в плане использовать один предиктор.
- [ ] **Step 4: Failing test.** `test_load_yields_rejects_duplicates_and_nans` (дубль `region_id, year` или NaN → `ValueError`), `test_detrended_anomaly_uses_only_train_years` (на искусственной линейной серии аномалия отложенного года равна ожидаемому проценту, а при изменении отложенного значения коэффициенты тренда не меняются).
- [ ] **Step 5: Run** `pytest tests/test_yields.py -v` — ожидается FAIL.
- [ ] **Step 6: Implement** обе функции в `src/wheatwatch/yields.py`; тренд через `numpy.polyfit(deg=1)` по региону.
- [ ] **Step 7: Run** `pytest tests/test_yields.py -v && ruff check . && mypy src` — PASS.
- [ ] **Step 8: Commit** `git add -A && git commit -m "feat: yield data loader and detrended anomaly"`.

### Task 2: Текст заявки и сценарий видео (дедлайн 22.11)

**Files:**
- Create: `docs/application/proposal.md`, `docs/application/video-script.md`

**Interfaces:** потребляет только факты из Task 1 (покрытие данных) и спецификации; чисел точности ещё нет.

- [ ] **Step 1: proposal.md на английском.** Разделы: проблема (с цифрами только из процитированных источников), решение, метод (индекс засухи + проверка «оставь один год»), данные и их ограничения (уровень детализации из Task 1), план проверки, что именно покажет прототип, честные ограничения. Без цифры точности и без партнёров.
- [ ] **Step 2: video-script.md** на 2 минуты (около 280 слов): проблема, метод, что покажет карта, как проверяем, что дальше; пометки для владельца, где показать экран.
- [ ] **Step 3: Проверка.** Каждое число в тексте имеет ссылку на источник; слов «partner», «pilot», «validated accuracy» нет (`grep -in "partner\|pilot\|validated accuracy" docs/application/*.md` пусто).
- [ ] **Step 4: Commit** `git commit -am "docs: application draft and video script"`; записать время в timing-log.

### Task 3: Геометрия регионов и маска пашни

**Files:**
- Create: `src/wheatwatch/regions.py`
- Test: `tests/test_regions.py`

**Interfaces:**
- Consumes: `load_yields` из Task 1.
- Produces: `load_regions(yields_ids: set[str]) -> gpd.GeoDataFrame` с `region_id: str`, `geometry`; падает с `ValueError`, перечисляющим несовпавшие id. `cropland_mask_path() -> Path` — растр ESA WorldCover, обрезанный по Азербайджану, закешированный в `data/raw`.

- [ ] **Step 1: Failing test** `test_load_regions_lists_unmatched_ids` (id из статистики, которого нет в геометрии, → `ValueError` с этим id в тексте) и `test_regions_cover_only_azerbaijan` (границы внутри bbox страны).
- [ ] **Step 2: Run** `pytest tests/test_regions.py -v` — FAIL.
- [ ] **Step 3: Implement.** Границы: geoBoundaries (ADM1/ADM2) в зависимости от уровня данных из Task 1; сопоставление названий статистики и геометрии через явный словарь `data/raw/region_aliases.csv`.
- [ ] **Step 4: Run** тесты, ruff, mypy — PASS. **Step 5: Commit.**

### Task 4: Климатические признаки сезона

**Files:**
- Create: `src/wheatwatch/climate.py`
- Test: `tests/test_climate.py`

**Interfaces:**
- Consumes: `load_regions` (Task 3).
- Produces: `season_climate(regions: gpd.GeoDataFrame, years: list[int]) -> pd.DataFrame` со столбцами `region_id, year, precip_mm: float, tmax_mean_c: float`; кеш в `data/raw/climate_{region_id}_{year}.json`.

- [ ] **Step 1: Failing test** с подменой HTTP-клиента: `test_season_climate_uses_cache_on_second_call` (второй вызов не обращается к сети), `test_failed_fetch_raises_not_zero` (ошибка сети → исключение, а не нулевые осадки).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** по центроиду региона через Open-Meteo Historical API (на основе ERA5) за сезон озимой пшеницы (окно на усмотрение строителя, по умолчанию март–июнь); осадки CHIRPS заменены на ERA5-осадки Open-Meteo, чтобы не вводить второй источник (решение Claude).
- [ ] **Step 4: Run** — PASS. **Step 5: Commit.**

### Task 5: NDVI сезона

**Files:**
- Create: `src/wheatwatch/ndvi.py`
- Test: `tests/test_ndvi.py`

**Interfaces:**
- Consumes: `load_regions`, `cropland_mask_path` (Task 3).
- Produces: `season_ndvi(regions: gpd.GeoDataFrame, years: list[int]) -> pd.DataFrame` со столбцами `region_id, year, ndvi_peak: float` — максимум медианного NDVI по пашне региона за сезон; кеш в `data/raw`.

- [ ] **Step 1: Failing test** на синтетическом растре: `test_ndvi_peak_ignores_non_cropland_pixels`, `test_missing_scenes_year_returns_nan_not_zero`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** через Planetary Computer, коллекция MODIS MOD13Q1 (с 2000 г.); токены подписи получать программно, без аккаунта.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit.**

### Task 6: Индекс засухи и проверка «оставь один год»

**Files:**
- Create: `src/wheatwatch/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `detrended_anomaly_pct` (Task 1), таблицы из Tasks 4–5.
- Produces: `build_features(climate: pd.DataFrame, ndvi: pd.DataFrame) -> pd.DataFrame` — z-аномалии `ndvi_z, precip_z, tmax_z` по региону.
- Produces: `@dataclass(frozen=True) class ValidationReport: pearson_r: float; rmse_pct: float; hit_rate: float; n_years: int; n_regions: int; n_dropped: int; predictors: tuple[str, ...]; verdict: Literal["weak", "moderate", "strong"]`.
- Produces: `loyo_validate(features: pd.DataFrame, yields: pd.DataFrame, single_predictor: bool = False) -> ValidationReport`; `ValueError` при регионе с менее чем 3 годами или константной урожайностью.

- [ ] **Step 1: Failing tests:** `test_loyo_recovers_known_relation` (синтетика: урожайность = 2·ndvi_z + шум → `pearson_r > 0.8`, `verdict` не «weak»); `test_loyo_on_random_noise_reports_weak` (чистый шум → «weak»); `test_loyo_rejects_short_or_constant_region`; `test_nans_are_dropped_and_counted` (`n_dropped` равен числу выброшенных строк); `test_heldout_year_not_in_training` (изменение отложенного года не меняет предсказание для него).
- [ ] **Step 2: Run** `pytest tests/test_validate.py -v` — FAIL.
- [ ] **Step 3: Implement.** Линейная регрессия `numpy.linalg.lstsq`; пороги вердикта фиксируются в константах модуля (weak: r < 0.3, moderate: 0.3–0.6, strong: > 0.6 — решение Claude, видно в отчёте).
- [ ] **Step 4: Run** — PASS. **Step 5: Commit.**

### Task 7: Карта риска

**Files:**
- Create: `src/wheatwatch/mapdata.py`, `web/index.html`, `scripts/build_all.py`
- Test: `tests/test_mapdata.py`

**Interfaces:**
- Consumes: `ValidationReport`, `build_features`, `load_regions`.
- Produces: `build_risk_geojson(features: pd.DataFrame, regions: gpd.GeoDataFrame, report: ValidationReport, year: int) -> dict[str, Any]` — FeatureCollection; свойства `region_id, risk_level ("low"|"medium"|"high"), index_value`; в `properties` коллекции `validation_note` с текстом вердикта.

- [ ] **Step 1: Failing test:** `test_geojson_has_one_feature_per_region_year`, `test_weak_verdict_note_says_weak` (при `verdict="weak"` `validation_note` содержит «weak»), `test_map_level_equals_validation_level` (число объектов равно числу регионов проверки).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `build_risk_geojson`; `web/index.html` подгружает `web/risk.geojson`, красно-зелёная шкала риска, подпись с `validation_note`; `scripts/build_all.py` запускает всю цепочку и пишет `web/risk.geojson` и `docs/application/validation-report.md`.
- [ ] **Step 4: Run** `pytest -v && ruff check . && black --check . && mypy src` — PASS; открыть `web/index.html` в браузере и убедиться, что карта и подпись отображаются.
- [ ] **Step 5: Commit.**

### Task 8: Финальная заявка с результатом

**Files:**
- Modify: `docs/application/proposal.md`, `docs/application/video-script.md`
- Read: `docs/application/validation-report.md`

- [ ] **Step 1:** Если проверка готова к 21.11, вставить в заявку и сценарий значения `pearson_r`, `rmse_pct`, `hit_rate`, `n_years`, `n_regions` ровно из отчёта; иначе оставить метод и пометить «preliminary».
- [ ] **Step 2:** Раздел «Limitations» формулируется по `verdict` и `n_dropped`; при «weak» текст так и говорит.
- [ ] **Step 3: Проверка:** каждая цифра в заявке находится в `validation-report.md` (`grep` по числам); слов «partner», «pilot» нет.
- [ ] **Step 4: Commit**, записать итоговое время в timing-log и коротко описать, где superpowers помог и где замедлил.
