# Video script, 2 minutes (about 280 spoken words)

Читает владелец: запись экрана плюс свой голос. Англоязычный текст читать как есть, в скобках только пометки для кадра.

**[0:00, заголовок и карта Каспия]**
The Caspian Sea is the world's largest inland water body, and it is falling. Reports describe a downward trend since the mid 1990s. A 2024 study at the Gizil-Aghaj reserve in Azerbaijan reports an additional 218 square kilometres of exposed land between 2014 and 2023.

**[0:20, график уровня]**
In the NASA G-REALM altimetry series that I use, the July to October sea level changed by minus 2.135 metres between 2000 and 2025. That is a relative level change in our series, not an absolute height.

**[0:35, тезисы EGU и статья Gasimov на экране]**
This topic is already studied. So I am not claiming a discovery. I am building an open, repeatable tool.

**[0:45, репозиторий и схема метода]**
I am a student, building this with an AI coding assistant. No organisation is involved. The tool maps the shoreline each year from Landsat and Sentinel-2 imagery, and compares it with altimetry sea level.

**[1:05, раздел критерия в проекте]**
The key idea is a rule fixed in advance. The shoreline model must beat a simple time trend when one year is left out. And the correlation of year-on-year changes must be at least 0.5, with a bootstrap lower bound above zero. At least two sites must be measurable.

**[1:25, карта с ползунком времени и значок вердикта]**
The result will be a static map: shorelines by year with a time slider, a card for each site, and a verdict badge that is always visible.

**[1:38, показать слот результата; пока пусто, вписать после проверки]**
[RESULT SLOT — to be filled after validation]

**[1:50, раздел ограничений]**
The limits are real. Sea-level heights are relative to a satellite datum. Landsat pixels are 30 metres on gently sloping shores. With few years, the answer may be inconclusive, and I will say so.

**[2:00, финальный кадр, быстро]**
Next, in the Build phase, I will run the test once, unchanged, and publish the verdict, whatever it is.
