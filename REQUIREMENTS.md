# shutterstock2kml — Требования

## Общее

Скилл Claude Code, который пошагово:
1. Пишет запросы в `queries.json`
2. Ищет изображения в Shutterstock API
3. Ищет рейтинги мест в Google Maps
4. Генерирует KML файл

## Shutterstock API

- Фильтровать по `image_type=photo` (исключить вектора и иллюстрации)
- Сортировка по популярности (`sort=popular`) — самые популярные вначале
- Параметр `view=full` — чтобы в ответе были keywords и categories
- Сохранять: `description`, `preview_url`, `keywords`, `categories.name`

## Промежуточные файлы

- Использовать JSON вместо MD для всех промежуточных файлов
- `queries.json` — поисковые запросы
- `places.json` — описания + URL картинок + keywords + categories
- `places_filtered.json` — только реальные места с place_name
- `places_geo.json` — места с координатами (Nominatim)
- `places_rated.json` — места с рейтингами
- `result.kml` — финальный KML

## KML стили

- `<Style>` с иконками по категориям:
  - castle / fortress / palace -> иконка замка
  - monastery / church / cathedral / temple -> иконка храма
  - mountain / lake / waterfall / cave / park -> иконка природы
  - museum / gallery -> иконка музея
  - city / square / bridge / architecture -> иконка города
  - default -> круглая иконка

- Цвет иконки зависит от того, стоит ли посещать (на основании рейтинга и количества отзывов):
  - Зеленый (must visit): rating >= 4.5 AND review_count >= 1000
  - Желтый (worth visiting): rating >= 4.0 AND review_count >= 100
  - Красный (skip or unknown): все остальное

## Рейтинги

- Использовать WebSearch для поиска рейтинга и количества отзывов в Google Maps
- Ссылка на Google Maps: `https://www.google.com/maps/search/<place name>`
- Бесплатно: без API ключей Google

## KML Placemark

Каждый Placemark содержит:
- `<name>` — название места
- `<Point><coordinates>lng,lat,0</coordinates></Point>`
- `<description>` в CDATA с HTML:
  - Ссылка на Google Maps (`<a href>`)
  - Рейтинг и количество отзывов
  - 1 `<img src>` с Shutterstock превью
- Placemarks сгруппированы в `<Folder>` по категориям
- Сортировка внутри папок по рейтингу (лучшие сверху)
