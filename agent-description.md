# vector-brand-control-agent — часть экосистемы Vector

Проект экосистемы Osmosy Vector на базе Hermes Agent.

Навык GEO-контроля бренда: замер ответов нейросетей в сценариях выбора, разметка ролей бренда
и конкурентов, скоринг (SoV, win_rate, mention_rate, citation_share, покрытие, провалы) и план
правок с повторным замером. Отвечает на вопрос «почему советуют не нас», а не только «упоминают
ли нас».

## Связанные проекты

- Хаб: https://github.com/Osmosy/vector-work
- Методология: https://github.com/Osmosy/vector-agent-ready
- Проверяемый поиск: https://github.com/Osmosy/vector-search
- Знания и эссе: https://github.com/Osmosy/vector-grace

## Для агентов

- Порядок чтения: `README.md` → `skills/vector-brand-control-agent/SKILL.md` → `templates/prompts.csv`.
- Сначала базовый замер, потом правки: без `measurements.csv` до `plan.md` эффект не измерить.
- Три повтора на промпт и движок обязательны; ответы моделей недетерминированы.
- Ответ с веб-поиском и ответ из памяти — разные строки в разметке (колонка `web_search`),
  не смешивать; канал печатается отдельным срезом.
- Одна волна — одна дата: повторный замер идёт с новой `date`, иначе сводка смешивает волны.
- Платные сервисы, ключи и подписки подключаются только с явного разрешения владельца.
- Числа из промо сервисов подаются как заявление сервиса, а не как факт.
- Перед коммитом: `python3 scripts/validate.py` и `python3 -m unittest discover -s tests -q` —
  обе команды должны быть зелёными, CI гоняет их же.

## Состав

| Артефакт | Количество | Где |
|---|---|---|
| Промпты в наборе | 30 | `skills/vector-brand-control-agent/templates/prompts.csv` |
| Классы сценариев | 6 | там же, колонка `scenario_class` |
| Движки в образце | 3 | `templates/measurements-sample.csv` |
| Шаблоны | 3 | `skills/vector-brand-control-agent/templates/` |
| Скрипты замера | 1 | `skills/vector-brand-control-agent/scripts/geo_score.py` |
| Метрики в сводке | 6 | SoV, win_rate, mention_rate, citation_share, competitor_citation_share, negative_rate |
