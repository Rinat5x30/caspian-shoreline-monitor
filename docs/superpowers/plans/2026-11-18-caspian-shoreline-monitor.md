# Caspian Shoreline Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Открытый монитор отступления береговой линии Каспия у Азербайджана по Landsat/Sentinel-2 за 2000–2025 с заранее заданной проверкой по уровню моря из G-REALM, статической картой и текстом заявки для Global Sustainability Challenge.

**Architecture:** Новый пакет `src/caspianwatch` рядом с `wheatwatch`. Уровень моря (G-REALM) и годовые водные композиты Landsat на участках → смещение берега по поперечным профилям → проверка «оставь один год» против базы «только тренд по времени» → GeoJSON и статическая карта. Кеш, атомарная запись и повторы переиспользуются из `wheatwatch`, а не копируются.

**Tech Stack:** Python 3.12, numpy, pandas, geopandas, shapely, rasterio, pystac-client, planetary-computer, requests, pytest, ruff, black, mypy strict, Leaflet.

**Spec:** `docs/superpowers/specs/2026-11-18-caspian-shoreline-monitor-design.md`

## Global Constraints

- Открытые данные без платных аккаунтов; бесплатная регистрация только после вопроса владельцу. Никаких контактов с третьими лицами, никаких выдуманных партнёров, пилотов, отзывов.
- Заявлять только то, что показала проверка; критерий, участки, сезон и период нельзя менять после просмотра связи «берег — уровень».
- Смещение берега: по участку — среднее по поперечным профилям знаковое расстояние линии воды от фиксированной опорной линии, метры, положительное = отступление моря. Годовая линия — по композиту выбранного сезона.
- Модель и база по каждому участку отдельно: модель уровень = a + b·смещение; база уровень = a + b·год; обе обучаются только на обучающих годах схемы «оставь один год»; участки в один пул не сливаются.
- Уровень G-REALM: среднее по датам внутри сезона композита того же года, не менее 3 наблюдений, иначе год пропускается; единый относительный ряд продукта; абсолютные высоты не заявлять; проверить скачки между миссиями и записать результат.
- Период 2000–2025 либо самый длинный общий отрезок; решается один раз после скачивания G-REALM и до анализа связи; пригодных лет меньше 15 — остановка и возврат к владельцу.
- Пропуски лет: приращения только между соседними доступными годами, через пропуск не считаются.
- Критерий участка «подтверждено»: RMSE модели меньше RMSE базы, корреляция Пирсона годовых разностей не ниже 0,5, нижняя граница 95%-ного бутстрэп-интервала этой корреляции больше 0. Сырая корреляция публикуется отдельно с пометкой о тренде.
- Общий вердикт: «подтверждено» — не менее двух измеримых участков подтверждены; «частично» — один; иначе «не подтверждено». Участок измерим, если линия найдена не менее чем в 20 из 26 лет не менее чем на 80% профилей; неизмеримый участок публикуется как «не измерим» с причиной. Не менее 3 участков, выбранных до анализа связи по правилу «пологий, без растительности и без дельтового заболачивания»; дельтовые допустимы с пометкой.
- Сезон снимков: непрерывное окно из двух месяцев в июле–октябре с наименьшей медианной облачностью по метаданным сцен за весь период; воду, берег и уровень при выборе не смотрим; не менее 3 пригодных сцен на участок и год.
- Контроль линии берега: Landsat против Sentinel-2 за 2017–2025; медианное расхождение по участку не более 30 м, иначе участок помечен; на карте неопределённость не менее 30 м.
- Кеш (уроки пшеничного проекта): запись атомарная; запись хранит все параметры расчёта, любое расхождение или нечитаемая запись = промах; NaN и частичные результаты не кешируются; сбой сети или чтения сцены вызывает исключение; пустой поиск вызывает исключение; повторы только на 429/5xx/OSError; sleep и сеть внедряются в тестах; пропуски маскируются явно, без молчаливого skipna.
- Код: type hints, Black, Ruff, mypy strict, минимум комментариев; тесты офлайн; видео и регистрацию делает владелец; время этапов пишется в `docs/superpowers/timing-log.md`.

## Review Focus

- Год с менее чем 3 сценами или без линии берега: пропуск (NaN), не ноль; приращение через пропуск не считается (тест в Задаче 8).
- Ловушка тренда: два независимых монотонно убывающих ряда не должны получить «подтверждено» (тест в Задаче 8); реально связанные ряды — должны.
- Утечка: параметры модели и базы подбираются только по обучающим годам; тест должен падать, если в обучение попадает отложенный год (Задача 8, с мутационной проверкой).
- Полосы Landsat 7 после сбоя SLC и смена сенсора не должны давать фальшивых скачков линии берега (синтетический тест полосатой сцены в Задаче 5; межсенсорная проверка в Задаче 7).
- Ряд G-REALM с пропусками и скачками между миссиями: сезонное среднее при менее чем 3 наблюдениях даёт NaN, скачок обнаруживается и записывается (Задача 1).

---

### Task 1: Уровень моря G-REALM (стоп-ворота)

**Files:**
- Create: `src/caspianwatch/__init__.py`, `src/caspianwatch/cache.py`, `src/caspianwatch/level.py`, `data/raw/caspian/SOURCES.md`
- Test: `tests/caspian/test_level.py`

**Interfaces:**
- Produces: `cache.atomic_write(path: Path, data: bytes) -> None` и `cache.get_with_retry(url: str, *, fetch: Callable[[str], bytes] | None = None, sleep: Callable[[float], None] = time.sleep) -> bytes` — обёртки над помощниками из `wheatwatch` (импортировать, не копировать).
- Produces: `GATE_MIN_YEARS: int = 15`; `load_grealm_caspian() -> pd.DataFrame` (`date: datetime64`, `level_m: float`, отсортирован по дате); `season_level(levels: pd.DataFrame, year: int, months: tuple[int, int], min_obs: int = 3) -> float` (NaN, если наблюдений меньше `min_obs`); `usable_years(levels: pd.DataFrame, years: Iterable[int], months: tuple[int, int]) -> list[int]`; `check_gate(usable: list[int]) -> None` (`InsufficientLevelData`, если меньше `GATE_MIN_YEARS`); `detect_jumps(levels: pd.DataFrame, threshold_m: float) -> list[pd.Timestamp]`.

- [ ] **Step 1: Найти и скачать ряд.** Открыть страницу G-REALM (USDA/NASA, `ipad.fas.usda.gov/cropexplorer/global_reservoir/`), найти текстовый файл ряда Каспия, скачать без регистрации в `data/raw/caspian/` (кеш, не коммитить, если большой), записать URL, дату, миссии, описание заголовка и поправку в `data/raw/caspian/SOURCES.md`. Если файл недоступен без регистрации после разумного поиска (около 30 вызовов), остановка: статус BLOCKED с описанием найденного.
- [ ] **Step 2: Failing tests** на синтетическом тексте в формате файла: `test_parse_grealm_returns_sorted_dates_and_levels`, `test_season_level_is_nan_below_min_obs` (2 наблюдения в окне → NaN), `test_season_level_mean_of_window_only` (наблюдения вне окна не входят), `test_check_gate_raises_below_15_years`, `test_detect_jumps_flags_step_between_missions`.
- [ ] **Step 3: Run** `pytest tests/caspian/test_level.py -v` — FAIL.
- [ ] **Step 4: Implement** функции выше в `level.py` и `cache.py`; парсинг строго по реальному формату файла из Шага 1; NaN и неразборчивые строки не молчаливо пропускаются, а считаются и логируются.
- [ ] **Step 5: Ворота.** Реальный ряд: посчитать `usable_years` для окна июль–октябрь за 2000–2025 (предварительная оценка; финальный сезон выбирается в Задаче 3); записать число и список в `SOURCES.md`; `check_gate` не проходит → остановка и возврат к владельцу.
- [ ] **Step 6: Run** тесты, ruff, black, mypy — PASS. **Commit** `feat: G-REALM Caspian level series with gate`.

### Task 2: Текст заявки и сценарий видео для Ignite (дедлайн 22.11)

**Files:**
- Create: `docs/application/caspian-proposal.md`, `docs/application/caspian-video-script.md`

**Interfaces:** потребляет факты из Задачи 1 (доступность и покрытие ряда) и спецификации; результата проверки ещё нет.

- [ ] **Step 1: proposal.md на английском** (900–1200 слов): проблема (цифры только из источников, которые исполнитель открыл через WebFetch, с URL), решение, метод, данные и их ограничения, критерий успеха, заданный заранее (дословно из спецификации), что покажет прототип, ограничения (тема изучена; относительные высоты; мелкий берег и 30 м), следующие шаги. Формулировка команды: «I am a student, building this with an AI coding assistant». Никаких результатов, партнёров и слов «partner», «pilot», «validated».
- [ ] **Step 2: video-script.md** на 2 минуты (около 280 слов) с короткими русскими пометками для владельца.
- [ ] **Step 3: Проверка:** `grep -in "partner\|pilot\|validated\|proven\|confirmed" docs/application/caspian-*.md` пусто; каждая цифра имеет источник с URL. **Commit** `docs: Caspian application draft and video script`.

### Task 3: Выбор сезона по облачности

**Files:**
- Create: `src/caspianwatch/season.py`, `data/caspian/season.json`
- Test: `tests/caspian/test_season.py`

**Interfaces:**
- Consumes: `cache.atomic_write`, `cache.get_with_retry`.
- Produces: `@dataclass(frozen=True) Season(start_month: int, end_month: int)`; `fetch_cloud_table(bbox: tuple[float, float, float, float], years: Iterable[int]) -> pd.DataFrame` (`year`, `month`, `scene_count`, `cloud_pct`; Landsat C2 L2 через Planetary Computer, только метаданные `eo:cloud_cover`); `choose_season(cloud: pd.DataFrame) -> Season` — окно из двух соседних месяцев в июле–октябре с наименьшей медианной облачностью, при равенстве — раннее; `season_months(season: Season) -> tuple[int, int]`.

- [ ] **Step 1: Failing tests:** `test_choose_season_picks_lowest_median_cloud_pair`, `test_choose_season_only_considers_jul_to_oct`, `test_choose_season_ignores_everything_but_cloud` (функция принимает только таблицу облачности; уровень и береговые данные ей недоступны), `test_empty_table_raises`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** реальный запуск один раз по bbox азербайджанского побережья Каспия за 2000–2025; результат и таблицу записать в `data/caspian/season.json` вместе с датой — до любого анализа берега. Затем один раз зафиксировать период: `usable_years` для выбранного сезона по G-REALM, самый длинный общий отрезок с годами, где в сезоне есть не менее 3 сцен Landsat; записать период в `season.json` (поле `period`); меньше 15 лет — остановка и возврат к владельцу.
- [ ] **Step 5: Run** тесты, линтеры — PASS. **Commit** `feat: season choice by cloud cover metadata`.

### Task 4: Участки и профили

**Files:**
- Create: `src/caspianwatch/sites.py`, `data/caspian/sites.json`, `docs/caspian/site-selection.md`
- Test: `tests/caspian/test_sites.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) Site(site_id: str, name: str, reference_line: LineString, seaward: tuple[float, float], deltaic: bool)` (координаты в EPSG:32639); `@dataclass(frozen=True) Profile(site_id: str, index: int, origin: tuple[float, float], direction: tuple[float, float], length_m: float)`; `load_sites(path: Path = DEFAULT_SITES) -> list[Site]`; `make_profiles(site: Site, spacing_m: float = 200.0, length_m: float = 3000.0) -> list[Profile]`; направление единичное, смотрит в море.

- [ ] **Step 1: Выбрать не менее 3 участков** по литературе (Кызылагач, Куринская зона, северо-восточный Апшерон) и пологости (Copernicus DEM `cop-dem-glo-30` через Planetary Computer: средний уклон суши в полосе 1 км у берега 2000 года), правило «пологий, без растительности и дельтового заболачивания»; записать в `docs/caspian/site-selection.md` правило, цифры уклона и причину включения или исключения каждого кандидата — до обращения к данным о береге и уровне.
- [ ] **Step 2: Failing tests:** `test_profiles_are_unit_direction_and_evenly_spaced`, `test_profiles_point_seaward`, `test_load_sites_requires_at_least_three`, `test_deltaic_flag_loaded`.
- [ ] **Step 3: Run** — FAIL. **Step 4: Implement** `load_sites` и `make_profiles`. **Step 5: Run** — PASS. **Commit** `feat: Caspian sites and transect profiles`.

### Task 5: Годовой водный композит Landsat

**Files:**
- Create: `src/caspianwatch/landsat.py`
- Test: `tests/caspian/test_landsat.py`

**Interfaces:**
- Consumes: `Site`, `Season`, `cache.*`.
- Produces: `@dataclass(frozen=True) WaterMask(water: np.ndarray, transform: Affine, crs: str, n_scenes: int)` (`water` — bool, True = вода); `MIN_SCENES: int = 3`; `season_water_mask(site: Site, year: int, season: Season) -> WaterMask | None` — `None`, если пригодных сцен меньше `MIN_SCENES`; запись кеша на участок-год с параметрами (сезон, порог, коллекция, фильтр качества).

- [ ] **Step 1: Failing tests (синтетика):** `test_mndwi_water_pixels_above_threshold`, `test_cloud_and_shadow_pixels_masked_by_qa`, `test_slc_off_stripes_do_not_create_land_strips` (полосатая сцена Landsat 7 с пропусками в композите с другими сценами не даёт ложной суши), `test_fewer_than_min_scenes_returns_none_and_is_not_cached`, `test_cache_invalidated_by_season_or_threshold_change`, `test_failed_scene_read_raises_and_caches_nothing`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** MNDWI = (green − swir1) / (green + swir1) из Landsat C2 L2 (поверхностное отражение); маска облаков, теней и пустых значений по QA_PIXEL; композит — медиана по сценам сезона; порог воды — Оцу на участке и году, ограниченный диапазоном [−0.2, 0.4] (значение порога пишется в запись кеша); сенсоры 5/7/8/9 приводятся к общим полосам green и swir1.
- [ ] **Step 4:** реальный прогон одного участка за 2000, 2012 и 2025; записать число сцен и долю воды. **Run** тесты, линтеры — PASS. **Commit** `feat: Landsat seasonal water composite`.

### Task 6: Линия берега, смещение и обнажённая суша

**Files:**
- Create: `src/caspianwatch/shoreline.py`
- Test: `tests/caspian/test_shoreline.py`

**Interfaces:**
- Consumes: `Profile`, `WaterMask`, `Site`.
- Produces: `profile_position(profile: Profile, mask: WaterMask) -> float` — метры от начала профиля до края воды, NaN, если край не единственный или не найден; `site_displacement_series(site: Site, masks: Mapping[int, WaterMask | None], min_valid_profile_share: float = 0.8) -> pd.Series` (индекс — год, значение — среднее смещение в метрах относительно опорной линии, положительное = отступление; NaN, если валидных профилей меньше 80% или маска `None`); `is_measurable(series: pd.Series, period: Sequence[int], min_years: int = 20) -> bool`; `exposed_area_km2(first: WaterMask, last: WaterMask) -> float` — площадь пикселей, бывших водой в `first` и ставших сушей в `last`.

- [ ] **Step 1: Failing tests:** `test_retreating_sea_gives_positive_displacement`, `test_missing_mask_yields_nan_not_zero`, `test_less_than_80_percent_profiles_yields_nan`, `test_is_measurable_needs_20_valid_years`, `test_exposed_area_counts_only_water_to_land_pixels` (ручной расчёт на малом растре, км²).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** по профилям: выборка значений маски вдоль профиля, край — первая смена «суша → вода» от берега в сторону моря; единственность края обязательна. **Step 4: Run** — PASS. **Commit** `feat: shoreline displacement and exposed area`.

### Task 7: Контроль линии берега по Sentinel-2

**Files:**
- Create: `src/caspianwatch/sentinel.py`
- Test: `tests/caspian/test_sentinel.py`

**Interfaces:**
- Consumes: `Site`, `Season`, `shoreline.site_displacement_series`.
- Produces: `sentinel_displacement_series(site: Site, years: Sequence[int], season: Season) -> pd.Series` (тот же формат и знак, что у Landsat; годы 2017–2025); `@dataclass(frozen=True) QCReport(site_id: str, n_years: int, median_abs_diff_m: float, ok: bool)`; `qc_against_landsat(landsat: pd.Series, s2: pd.Series, site_id: str, tolerance_m: float = 30.0) -> QCReport`; серия Sentinel-2 использует те же кеш-правила и порог воды по тому же способу (MNDWI из B03/B11).

- [ ] **Step 1: Failing tests:** `test_qc_ok_when_median_diff_within_tolerance`, `test_qc_flags_site_over_tolerance`, `test_qc_uses_only_common_years`, `test_qc_requires_at_least_three_common_years` (иначе `ValueError`).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4:** реальный прогон на выбранных участках за 2017–2025; результаты в `docs/caspian/qc.md` без исключения участков по итогам (помечаются, не удаляются). **Run** — PASS. **Commit** `feat: Sentinel-2 shoreline QC`.

### Task 8: Проверка по заранее заданному критерию

**Files:**
- Create: `src/caspianwatch/validate.py`, `scripts/run_caspian_validation.py`
- Test: `tests/caspian/test_validate.py`

**Interfaces:**
- Consumes: ряды смещения (Task 6) и `season_level` (Task 1).
- Produces: `R_DIFF_MIN: float = 0.5`, `N_BOOT: int = 2000`; `@dataclass(frozen=True) SiteReport(site_id: str, verdict: Literal["confirmed", "not_confirmed", "not_measurable"], reason: str, n_years: int, rmse_model: float, rmse_baseline: float, r_diff: float, r_diff_ci_low: float, raw_r: float)`; `validate_site(displacement: pd.Series, level: pd.Series, site_id: str, *, measurable: bool = True, seed: int = 0) -> SiteReport`; `overall_verdict(reports: Sequence[SiteReport]) -> Literal["confirmed", "partial", "not_confirmed"]`.

- [ ] **Step 1: Failing tests (синтетика):** `test_coupled_series_is_confirmed` (level = −0.01·displacement + шум, с немонотонностью); `test_independent_trending_series_not_confirmed` (смещение и уровень — независимые убывающие тренды с шумом, фиксированный seed, десятки запусков: доля «подтверждено» не выше допустимой случайности, в тесте — ни одного на 20 seed); `test_differences_not_computed_across_gaps` (пропущенный год не создаёт приращение через пропуск); `test_heldout_year_not_used_in_fit` (умножение значений отложенного года на 5 не меняет предсказание остальных фолдов; проверено на мутации «обучение на всех годах» — тест должен падать); `test_unmeasurable_site_reported_not_scored`; `test_overall_verdict_confirmed_needs_two_sites`; `test_ci_lower_bound_must_exceed_zero`; `test_raw_r_reported_separately`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** по Global Constraints: оба предсказателя (модель и база) оцениваются на обучающих годах каждого фолда; r по годовым разностям только между соседними доступными годами; бутстрэп-интервал с фиксированным seed.
- [ ] **Step 4: Мутационная проверка** тестов утечки (показать красный результат при поломке и вернуть код).
- [ ] **Step 5: Реальный прогон ровно один раз** скриптом `scripts/run_caspian_validation.py` по реальным рядам; вывести `SiteReport` для каждого участка и общий вердикт; ничего не подгонять после просмотра; записать результат в отчёт. **Run** тесты, линтеры — PASS. **Commit** `feat: pre-registered validation of shoreline vs sea level`.

### Task 9: Карта, таблица и отчёт

**Files:**
- Create: `src/caspianwatch/mapdata.py`, `web/caspian.html`, `scripts/build_caspian.py`, `docs/application/caspian-validation-report.md`
- Test: `tests/caspian/test_mapdata.py`

**Interfaces:**
- Consumes: ряды смещения, маски, `SiteReport`, `QCReport`, `exposed_area_km2`.
- Produces: `build_shoreline_geojson(site: Site, masks: Mapping[int, WaterMask | None], reports: Sequence[SiteReport]) -> dict[str, Any]` — FeatureCollection линий берега по годам с `year`, `site_id`, `uncertainty_m` (не менее 30); `build_summary_table(...) -> pd.DataFrame` (участок, вердикт, площадь обнажённой суши км², метров на сантиметр уровня из коэффициента b, только если вердикт «подтверждено», иначе пусто); `validation_note(overall: str, reports: Sequence[SiteReport]) -> str`.

- [ ] **Step 1: Failing tests:** `test_geojson_has_line_per_available_year_and_min_uncertainty`, `test_metres_per_cm_empty_unless_confirmed`, `test_not_confirmed_note_says_not_confirmed`, `test_unmeasurable_site_listed_with_reason`.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** Статическая карта Leaflet читает готовый GeoJSON; подпись всегда содержит общий вердикт, а не скрывает его; в таблице «метров на сантиметр» не выводится для участков без подтверждения.
- [ ] **Step 4:** `python scripts/build_caspian.py` строит данные карты и `validation-report.md`; открыть `web/caspian.html` в браузере и убедиться, что карта и подпись отображаются. **Run** `pytest -v && ruff check . && black --check . && mypy src` — PASS. **Commit** `feat: shoreline map and validation report`.

### Task 10: Финальная заявка и итог по процессу

**Files:**
- Modify: `docs/application/caspian-proposal.md`, `docs/application/caspian-video-script.md`, `docs/superpowers/timing-log.md`
- Read: `docs/application/caspian-validation-report.md`

- [ ] **Step 1:** Вставить в заявку и сценарий результаты из отчёта проверки дословно (вердикт, по участкам: RMSE модели против базы, r по разностям с нижней границей интервала, площадь обнажённой суши); если вердикт «частично» или «не подтверждено», текст говорит именно это; ограничения: тема изучена, относительные высоты, QC.
- [ ] **Step 2: Проверка:** каждое число в заявке найдено в `caspian-validation-report.md` (`grep` по числам); слов «partner», «pilot» нет; формулировка команды — «I am a student, building this with an AI coding assistant».
- [ ] **Step 3:** В `timing-log.md` записать итоговые времена этапов обоих проектов и короткий вывод: где superpowers помог и где замедлил.
- [ ] **Step 4: Commit** `docs: final Caspian application with validation results`.
