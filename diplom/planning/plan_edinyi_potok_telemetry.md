# Единый план реализации: mission telemetry + dynamic jammer zones

> Статус актуализации (22.04.2026): пункты плана 13–20 реализованы, а также закрыто расширение 21–22 (`PLR` в `jam_prob` + packet-loss simulation API/UI/tests).

Цель: единый mission поток (`simulation/live`) + динамические зоны глушения (`suspected_jammer` и `jammer`) с авто-реакцией на риск.

Оценка: **3-5 недель**.

---

## 1) Короткая архитектура

1. **Unified stream**: один WS контракт и один канал миссии.
2. **Fusion**: мультисигнальная оценка `jam_prob` по дрону.
3. **Spatial response**: зоны + controlled replan + optional safety-action.

Важно: IMU не является самостоятельным детектором глушения; это вспомогательный сигнал в fusion.

---

## 2) Контракт данных v1

### 2.1 Handshake

```json
{
  "protocol": "v1",
  "mode": "simulation",
  "routes": [],
  "irm": 0.84
}
```

### 2.2 Mission frame

```json
{
  "protocol": "v1",
  "source": "live",
  "telemetry": [
    { "drone_id": 1, "lat": 47.12, "lng": 39.71, "status": "ACTIVE" }
  ],
  "fusion_by_drone": {
    "1": {
      "jam_prob": 0.81,
      "state": "CONFIRMED_JAMMING",
      "fused_threat_level": 0.79,
      "auto_replan_event_id": 3
    }
  },
  "dynamic_zones": [
    {
      "zone_id": "jam-1",
      "zone_type": "jammer",
      "origin": "fusion",
      "center": { "lat": 47.12, "lng": 39.71 },
      "radius_m": 128.0,
      "severity": 0.82,
      "state": "active",
      "expires_in_sec": 31
    }
  ],
  "irm_update": 0.82,
  "message": null
}
```

---

## 3) Модель риска (минимум)

- `raw = w_gnss*gnss + w_link*link + w_imu*imu + w_swarm*swarm`
- EMA: `jam_prob_t = alpha*raw + (1-alpha)*prev`, `alpha=0.35`
- hysteresis:
  - confirm: `jam_prob >= 0.72` три кадра подряд;
  - recovery: `jam_prob <= 0.45` пять кадров подряд.
- состояния: `NORMAL -> SUSPECT -> CONFIRMED_JAMMING -> RECOVERING`.

---

## 4) Зоны риска (кратко)

- `suspected_jammer`: вручную нарисованная полупрозрачная зона (гипотеза).
- `jammer`: подтвержденная зона по fusion.
- lifecycle: `DRAWN -> OBSERVING -> CONFIRMED/REJECTED -> EXPIRED`.
- обновление: EMA-центр, адаптивный радиус, TTL, merge близких зон.
- рекомендованный стиль на карте:
  - suspected: `fillOpacity=0.15`, пунктирная граница;
  - confirmed: `fillOpacity=0.30-0.40`, сплошная граница;
  - fading: плавное уменьшение opacity по TTL.

Сценарий: дрон заходит в suspected-зону -> деградируют GNSS/link -> растет `jam_prob` -> зона подтверждается -> запускается replan.

Минимальный API для ручных зон:

- `POST /api/v1/risk-zones/suspected` (geometry, `source="operator"`, `ttl_sec`, `note`);
- `PATCH /api/v1/risk-zones/{id}/state` (опционально для ручного подтверждения/отклонения);
- в mission frame зона передается через общий `dynamic_zones` с полями:
  `zone_id`, `zone_type`, `origin`, `state`, `confidence`, `ttl_sec`, `created_at`, `updated_at`, `geometry|center+radius`.

Реакция ArduPilot (двухступенчатая):

1. Опциональный safety-action (`LOITER`/`RTL`) при `CONFIRMED_JAMMING`.
2. Затем `replan_on_new_risk_zone` -> `update_mission` -> возврат в `AUTO`.

---

## 5) Этапы реализации

### Этап 0. Контракт и протокол (0.5 дня)

- Зафиксировать `protocol=v1`, handshake и frame.
- Утвердить путь `WS /ws/telemetry/mission`.

**Done:** контракт согласован и задокументирован.

### Этап 1. Backend unified stream (2-4 дня)

- Новый `mission_telemetry_stream` для `simulation/live`.
- Кадры по всем дронам, включая `fusion_by_drone`.
- Legacy WS оставить как deprecated/thin-wrapper.

**Done:** один клиент получает мультидрон поток в обоих режимах.

### Этап 2. Runtime dynamic zones (3-5 дней)

- EMA + hysteresis + state machine.
- lifecycle зон: create/update/merge/expire.
- controlled replan + optional safety-action.
- `dynamic_zones` в mission frame.

**Done:** зоны стабильно появляются и затухают по правилам.

### Этап 3. API ручной разметки suspected зон (1-2 дня)

- `POST /api/v1/risk-zones/suspected`.
- (опционально) `PATCH /api/v1/risk-zones/{id}/state`.

**Done:** оператор создает suspected-зону, backend учитывает ее в runtime.

### Этап 4. Frontend unified stream + UI (2-4 дня)

- `useMissionTelemetryStream` вместо двух раздельных хуков.
- В store: `fusionByDrone`, `dynamicJammerZones`.
- В `MissionPanel`: одна кнопка start/stop + выбор `simulation/live` + режим рисования.
- В `MapArea`: отрисовка suspected/confirmed зон разными стилями.

**Done:** единый UX без дублирования сокетов.

### Этап 5. Mission context + replan (1-2 дня)

- Автовызов `POST /api/v1/mission/{id}/fusion-context` на старте live.
- Стабильная доставка `auto_replan_event_id` через единый поток.

**Done:** replan-события видны в одном канале.

### Этап 6. Docs/config (0.5-1 дня)

- Обновить `.env`/README (`SITL_HOSTS`, `VITE_WS_ORIGIN`, sim/live запуск).

**Done:** стенд поднимается без скрытых шагов.

### Этап 7. Тесты и регрессия (2-3 дня)

- Backend: WS тесты `sim/live`, unit для detector/zones.
- Frontend: парсинг кадра, store update, cleanup/reconnect.
- Smoke: sim без SITL, live 4 SITL, stop/start без утечек.

**Done:** ключевые сценарии проходят стабильно.

---

## 6) Ключевые файлы

Backend:

- `backend/app/api/routers/telemetry.py`
- `backend/app/services/mavlink_service.py`
- `backend/app/services/telemetry_features.py`
- `backend/app/services/threat_fusion.py`
- `backend/app/services/mission_fusion_runtime.py`
- `backend/app/services/replanner.py`
- `backend/app/core/config.py`

Frontend:

- `frontend/src/hooks/useMissionTelemetryStream.ts`
- `frontend/src/store/useMissionStore.ts`
- `frontend/src/features/MissionControl/MissionPanel.tsx`
- `frontend/src/features/MapDashboard/MapArea.tsx`
- `frontend/src/types/fusion.ts`

---

## 7) Конфиг (стартовые значения)

- `FUSION_ALPHA=0.35`
- `FUSION_T_HIGH=0.72`
- `FUSION_T_LOW=0.45`
- `FUSION_CONFIRM_STREAK=3`
- `FUSION_RECOVERY_STREAK=5`
- `DYN_ZONE_RADIUS_BASE_M=40`
- `DYN_ZONE_RADIUS_GAIN_M=120`
- `DYN_ZONE_TTL_SEC=45`
- `DYN_ZONE_MERGE_DISTANCE_M=70`
- `AUTO_REPLAN_MIN_INTERVAL_SEC=10`

---

## 8) Риски (кратко)

| Риск | Мера |
|------|------|
| Утечки сокетов | единый socket ref + cleanup |
| Флаппинг тревоги | EMA + hysteresis |
| Шторм replan | rate-limit |
| Ложные тревоги IMU | multi-signal подтверждение |

---

## 9) Критерии приемки

1. Один mission WS для `simulation/live`.
2. Единый frontend-хук и единый UI-флоу.
3. Поддержка `suspected_jammer` и `jammer` с lifecycle.
4. Подтверждение угрозы не зависит только от IMU.
5. Controlled replan работает в live-сценарии.

---

## 10) Дипломный минимум: метрики, baseline, демо

### Метрики (обязательно)

- `TTD` (time-to-detect)
- `TTR` (time-to-replan)
- `FPR` (false positive rate)
- `Mission Success Rate`

Цели MVP:

- `TTD <= 2s`
- `TTR <= 5s`
- `FPR <= 10%`

### Baseline vs Target

- Baseline: раздельные потоки.
- Target: unified stream + dynamic zones + controlled replan.

Показать разницу по 4 метрикам выше.

### Сценарии (5 ключевых)

1. live без угроз;
2. suspected-зона + GNSS деградация;
3. ложная suspected-зона (REJECTED);
4. confirmed-зона + replan;
5. stress start/stop.

---

## 11) Что показать визуально (картинки/графики)

В диплом и слайды добавить:

1. **Архитектурная схема** пайплайна:
   `MAVLink/SIM -> telemetry_features -> threat_fusion -> mission_fusion_runtime -> replanner -> WS/UI`.

2. **State machine** detector-а:
   `NORMAL -> SUSPECT -> CONFIRMED -> RECOVERING`.

3. **Скриншоты карты**:
   - suspected зона (полупрозрачная);
   - confirmed jammer зона;
   - маршрут до/после replan.

4. **График во времени**:
   - `jam_prob(t)`,
   - пороги `T_high/T_low`,
   - маркеры `confirm`, `replan`.

5. **Таблица сравнения baseline vs target**:
   - TTD, TTR, FPR, Mission Success, Operator reaction time.

6. **Скрин/лог таймлайна инцидента**:
   - момент входа в зону,
   - подтверждение,
   - отправка новой миссии.

---

## 12) Демо-скрипт на защите (7-10 минут)

1. Запуск mission stream на 2-4 дрона.
2. Рисование `suspected_jammer`.
3. Вход дрона в зону (инжекция GNSS-деградации).
4. Показ `jam_prob` и перехода в `CONFIRMED`.
5. Показ replan и нового маршрута.
6. Финальная таблица baseline vs target.

---

*Единый рабочий план · SafeAgriRoute · 2026*
