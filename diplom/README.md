# Документы диплома — структура папки `diplom/`

Единый навигатор по документам диплома и конкурса.

Код проекта находится в корне репозитория (`backend/`, `frontend/`), а в `diplom/` — только документация.

## 1) Основные документы (ядро)

| Файл | Роль |
|---|---|
| [`core/diploma_main.md`](core/diploma_main.md) | Основной текст ВКР (рабочая markdown-версия) |
| [`core/diploma_main.docx`](core/diploma_main.docx) | Версия для сдачи/печати в формате Word |
| [`core/zadanie_vkrs.md`](core/zadanie_vkrs.md) | Официальное задание на ВКР |
| [`core/ТЗ.md`](core/%D0%A2%D0%97.md) | Техническое задание на систему SafeAgriRoute |
| [`planning/roadmap_task.md`](planning/roadmap_task.md) | План-график и дорожная карта работ |
| [`planning/План обдуманный.md`](planning/%D0%9F%D0%BB%D0%B0%D0%BD%20%D0%BE%D0%B1%D0%B4%D1%83%D0%BC%D0%B0%D0%BD%D0%BD%D1%8B%D0%B9.md) | Черновые аналитические наброски/структура разделов |

## 2) Отчеты и валидация

| Файл | Роль |
|---|---|
| [`reports/validation_prompt20_report.md`](reports/validation_prompt20_report.md) | Актуальные метрики и визуализация Prompt 20 (после PLR) |
| [`requirements/требования_видео_презентация.md`](requirements/%D1%82%D1%80%D0%B5%D0%B1%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F_%D0%B2%D0%B8%D0%B4%D0%B5%D0%BE_%D0%BF%D1%80%D0%B5%D0%B7%D0%B5%D0%BD%D1%82%D0%B0%D1%86%D0%B8%D1%8F.md) | Требования к видео/презентации для подачи |

## 3) Конкурсные материалы

| Файл | Роль |
|---|---|
| [`contest/подсказки_конкурс.md`](contest/%D0%BF%D0%BE%D0%B4%D1%81%D0%BA%D0%B0%D0%B7%D0%BA%D0%B8_%D0%BA%D0%BE%D0%BD%D0%BA%D1%83%D1%80%D1%81.md) | Шаблоны и ограничения по полям заявки конкурса |

## 4) Техническая база знаний

Папка [`knowledge/`](knowledge/) содержит инженерную документацию:

- `architecture.md`
- `algorithms.md`
- `api-reference.md`
- `data-models.md`
- `deployment.md`
- `testing.md`
- `sitl-debugging.md`

## 5) Правило размещения новых файлов

- **Официальные документы ВКР** -> `diplom/core/`
- **Рабочие планы/черновики** -> `diplom/planning/`
- **Техдок по реализации** -> `diplom/knowledge/`
- **Метрики/эксперименты/отчеты** -> `diplom/reports/`
- **Конкурс/подача** -> `diplom/contest/` и `diplom/requirements/`
