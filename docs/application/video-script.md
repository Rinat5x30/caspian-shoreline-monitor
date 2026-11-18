# 2-minute video script (owner records screen + voice)

Spoken text is the plain paragraphs, about 280 words. Bracketed Russian lines are stage cues, not spoken.

[Экран: заставка с названием проекта, карта Азербайджана]

Hi, I am a student, building this with an AI coding assistant. My idea for food resilience in Azerbaijan is a district drought-risk map for wheat.

[Экран: слайд с цифрой 55%]

In May 2026 the President said Azerbaijan is only fifty-five percent self-sufficient in wheat. Domestic output is about 1.6 million tons, and imports are about 1.3 million. When a dry season hits the harvest, imports may have to cover more. I am not aware of an open district-level drought-risk view for wheat.

[Экран: схема метода]

My method is simple and explainable, no black box. For each district I take satellite vegetation greenness from MODIS, plus rainfall and temperature from ERA5, and measure how far this season departs from normal. I combine these into one drought index.

[Экран: таблица yields.csv, затем карта]

I compare it with official yields from the State Statistical Committee: sixty-six districts and cities, six years. The map will show a seasonal drought index by district, next to the official yield anomaly.

[Экран: раздел Validation plan]

Here is the honest part. The back-test is not finished. I will leave out one year at a time, predict it from the other five, and report correlation, error, and how often we flag a bad year. If the signal is weak, I will say so.

[Экран: раздел Limitations]

The limits: only six years of data, one combined predictor, some liberated districts with unusual yields, coarse satellite pixels, and no field-level checks. No external organisations were involved. It is just me and an AI coding assistant.

[Экран: раздел Next steps]

Next, I finish the back-test, publish the numbers whatever they are, and build the interactive map. Thank you.

[Проверь длительность по секундомеру: должно выйти около 2 минут]
