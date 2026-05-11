# Структура диплома

## Исходные материалы

- `sources/ТЗ.md` — техническое задание на SafeAgriRoute; пока не включено целиком в LaTeX.
- `sources/Пример с оформлением.docx` — пример оформления.

Markdown-дубли основного текста и задания удалены: актуальные редактируемые версии находятся в `latex/`, готовые Word-файлы — в `docx/`.

## LaTeX

- `latex/diploma.tex` — полный LaTeX-документ: титульник, задание, основная часть, приложения.
- `latex/diploma_main_part.tex` — только основная часть: титульник, задание, содержание, основной текст без приложений.
- `latex/diploma_body.tex` — основной текст без приложений.
- `latex/appendices.tex` — приложения, вынесенные отдельно.
- `latex/zadanie_vkrs.tex` — задание на ВКР в LaTeX.

## Готовые DOCX

- `docx/diploma_main_part.docx` — основная часть: титульник, задание, содержание и основной текст без приложений.
- `docx/appendices.docx` — приложения отдельным файлом.

В основной части рисунки встроены по смыслу в раздел `1.5` и подписаны как `Рисунок 1.2`–`Рисунок 1.5`; `Рисунок 1.1` уже используется для архитектуры платформы.

## Сборка PDF из LaTeX при необходимости

```bash
cd /home/user/projects/safe-agri-route/diplom/core/latex
latexmk -xelatex -interaction=nonstopmode -halt-on-error diploma.tex
latexmk -xelatex -interaction=nonstopmode -halt-on-error diploma_main_part.tex
```

Временные файлы сборки (`*.aux`, `*.log`, `*.toc`, `*.out`, `*.fls`, `*.fdb_latexmk`, `*.xdv`) можно удалять.
