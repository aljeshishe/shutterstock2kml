# shutterstock2kml — Требования

## Общее

Скилл Claude Code, который пошагово:
1. Пишет запросы в `queries.json`
2. Скрапит Shutterstock через Playwright (patchright + system Chrome + persistent profile + headful) — обходит DataDome
3. Эвристически фильтрует явный шум (флаги, иконки, vector/illustration, country maps и т. п.) -> `places_filtered.json`
4. Резолвит каждое описание в реальное место через Google Maps (Playwright) — название, координаты, рейтинг, review_count в одном проходе
5. Генерирует KML файл

## Shutterstock (Playwright scraping)

- Через **patchright** (Playwright fork с патчами anti-detection) + system Chrome (`channel="chrome"`) + persistent profile + headful — обходит DataDome.
- URL шаблон: `https://www.shutterstock.com/search/<q>?image_type=photo` (page 1) или `…&page=N` (N>1). `?page=1` нормализуется -> ERR_ABORTED, поэтому опускаем.
- Warmup: сначала зайти на `https://www.shutterstock.com/`, подвигать мышью, подождать ~2.5s — DataDome успевает поставить cookies.
- Извлекаем `description = img.alt`, `preview_url = img.currentSrc || img.src` из `main img[alt]` (НЕ через `closest('a[href*="/image-photo/"]')` — anchor у Shutterstock сосед, не предок). Дроп пустых alt < 4 символов и `data:` urls.
- `keywords` / `categories` остаются пустыми массивами — на странице поиска их нет.

## Промежуточные файлы

- Использовать JSON вместо MD для всех промежуточных файлов
- `queries.json` — поисковые запросы
- `places.json` — описания + URL превью (сырой Shutterstock-скрап)
- `places_filtered.json` — только описания, относящиеся к реальным посещаемым местам (после LLM-фильтра)
- `places_dropped.json` — отфильтрованные на стадии 2b записи (для аудита решений фильтра)
- `places_rated.json` — резолвленные места: `place_name`, `lat`, `lng`, `rating`, `review_count`, `google_maps_link` + поля из `places_filtered.json` (записи, не резолвленные Google Maps, отбрасываются)
- `result.kml` — финальный KML

## KML стили

- `<Style>` с иконками по категориям:
  - castle / fortress / palace -> иконка замка
  - monastery / church / cathedral / temple -> иконка храма
  - mountain / lake / waterfall / cave / park -> иконка природы
  - museum / gallery -> иконка музея
  - city / square / bridge / architecture -> иконка города
  - default -> круглая иконка

- Цвет иконки зависит от **количества отзывов** Google Maps:
  - Зелёный: review_count > 1000
  - Жёлтый: 100 ≤ review_count ≤ 1000
  - Красный: review_count < 100 или review_count не найден

- Записи с **rating < 3.8 отбрасываются** (записи без рейтинга остаются).

## Резолвинг через Google Maps (Playwright)

- Использовать Playwright (Chromium, headless) для рендера страниц Google Maps
- URL: `https://www.google.com/maps/search/<URL-encoded query>`, где query строится из `description` Shutterstock + регион (например, " Romania")
- На странице ждать появления `h1.DUwDvf` (название) и `div.F7nice` (рейтинг). Из них и из URL после редиректа извлекать:
  - `place_name` — innerText из `h1.DUwDvf` (каноническое название Google Maps)
  - `rating` — текст в `span[aria-hidden="true"]` внутри `F7nice` (например `4.7`)
  - `review_count` — целое из `aria-label` у `span[aria-label*="review"]` (например `73,417 reviews` → `73417`)
  - `lat`, `lng` — из URL после goto, регулярка `@(-?\d+\.\d+),(-?\d+\.\d+)`
- Если в течение ~10s `h1.DUwDvf` / `div.F7nice` не появились — Shutterstock-описание не резолвится в реальное место, **запись отбрасывается** (нет отдельной LLM-фильтрации, нет Nominatim — Google Maps делает и то и другое сам)
- Дедупликация по `place_name` после резолвинга
- Параллелизация: 4 контекста (`browser.new_context`) в asyncio
- Бесплатно, без API ключей; **только Playwright + Chromium**

## Фильтрация (Stage 2b)

- LLM-фильтрация: оставляем только описания, относящиеся к конкретным реальным посещаемым местам (достопримечательности, здания, природные объекты, города и т. д.).
- Отбрасываются: общие пейзажи без идентифицируемого места, студийные кадры/портреты/еда, абстрактные/художественные изображения.
- Также убирается суффикс `Stock Photo` / `Stock Image` из alt-текста.

## KML Placemark

Каждый Placemark содержит:
- `<name>` — название места
- `<Point><coordinates>lng,lat,0</coordinates></Point>`
- `<description>` в CDATA с HTML:
  - Ссылка на Google Maps (`<a href>`)
  - Рейтинг (если есть) и количество отзывов (если есть)
  - 1 `<img src>` с Shutterstock превью
- **Без `<Folder>`** — все placemarks одним плоским списком
- Сортировка по `review_count` убыв.; записи без `review_count` — в конец
