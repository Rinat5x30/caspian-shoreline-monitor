# Video script, 2 minutes (about 130 words per minute)

Читает владелец: запись экрана плюс свой голос. Англоязычный текст читать как есть; в скобках только пометки для кадра. Бюджет времени: 1:45 основной текст (около 225 слов) плюс 0:15 на слот результата.

ВАЖНО: на экране во время рассказа о карте показывать только эскиз-макет и подпись PLANNED / DESIGN SKETCH, не готовую карту. Готовой карты и результатов пока нет.

**[0:00-0:15, заголовок и карта Каспия]**
The Caspian Sea, which Euronews calls the world's largest lake, is falling. A 2024 study at the Gizil-Aghaj reserve in Azerbaijan reports an additional 218 square kilometres of exposed land between 2014 and 2023.

**[0:15-0:30, график уровня]**
In the NASA G-REALM altimetry series I use, the July to October sea level changed by minus 2.135 metres between 2000 and 2025. That is a relative level change in our series, not an absolute height.

**[0:30-0:40, тезисы EGU и статья Gasimov на экране]**
This topic is already studied, so I am not claiming a discovery. I am building an open, repeatable tool.

**[0:40-0:55, репозиторий и схема метода]**
I am a student, building this with an AI coding assistant. No organisation is involved. The tool I am building will map the shoreline each year from Landsat and Sentinel-2 imagery, and compare it with altimetry sea level.

**[0:55-1:15, раздел критерия в проекте]**
The rule is fixed in advance. The shoreline model must beat a simple time trend when one year is left out. Year-on-year changes must correlate at least 0.5, with a bootstrap lower bound above zero. At least two sites must be measurable.

**[1:15-1:30, эскиз-макет карты, подпись PLANNED на экране]**
This is a design sketch of the planned map: shorelines by year, a time slider, site cards, and a verdict badge that is always visible. It is not built yet.

**[1:30-1:35, пауза, слот пуст]**
At the time of this draft, results do not exist yet.

**[1:35-1:50, слот результата, бюджет 0:15]**
[RESULT SLOT — to be filled after validation]

**[1:50-2:00, раздел ограничений]**
Limits: heights are relative to a satellite datum, Landsat pixels are 30 metres, and with few years the answer may be inconclusive. I will say so.
