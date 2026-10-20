# Что лежит в чём (строки отрезков / задание / профиль станка)

Claude, 20.10.2026. **ПЛАН, кода ещё нет** (каркас `4ab0428`): структуры из
`techdocs/handoff/deepseek/plan_2026_10_20_step1_pc_pipeline.md` и дизайна §13.1, §17. Когда появится код —
сверить и записать SHA, как в KiCadStamp.

---

## Строка отрезков — `gerber2ldi/scan/` (Ш1)

```
ScanRow                          — неизменяемая
  y_mm: float                    — центр проезда лазера (y0 + (i + 0.5)·step_y)
  segments: ((x0, x1), ...)      — «лазер вкл», мм; отсортированы, x0 < x1, не касаются
```

Слой — последовательность `ScanRow` с равным шагом по `y` (пустые строки допустимы: компенсации нужны соседи).

## Профиль станка — `profiles/<имя>.toml`, в git (Ш3) — ТИП станка

```
[machine]      name, firmware (grbl | grblhal | fluidnc | …)
[table]        size_x_mm, size_y_mm, origin
[laser]        wavelength_nm, power_w (ОПТИЧЕСКАЯ), usable_fraction (0..1, жёсткий потолок),
               s_max ($30), pwm_hz
[scan]         scan_axis, row_step_um, speed_mm_s, overscan_mm, bidirectional
```

## Калибровка — `profiles/<имя>.calibration.toml`, в git (Ш3) — ЭКЗЕМПЛЯР станка

```
[calibration]  scale_x, scale_y, skew_deg, spot_radius_um (ИЗМЕРЕННЫЙ), measured (дата)
```

Нет файла — `scale = 1`, `skew = 0` + предупреждение; нет `spot_radius_um` — компенсация не строится.

Порта нет — и не будет (`contracts.md`, «Порт — не в профиле»).

## Задание — `gerber2ldi/job/` (Ш4)

```
Job
  profile                        — из Ш3
  side: top | bottom             — bottom = зеркало
  process: etch-resist | plating-resist   — → полярность
  power_fraction                 — ≤ usable_fraction, иначе отказ
  rows: [ScanRow]                — после компенсации, перекоса, зеркала, полярности
  passes                         — строки в порядке проезда; на каждую: начало/конец с разгоном, направление
```

Выход — `.nc` (G-code GRBL 1.1) и PNG-превью; оба строятся из `rows`, не из растра.

## Отверстия — `gerber2ldi/source/` (Ш2)

```
Hole(x_mm, y_mm, d_mm)           — из Excellon; для совмещения (Д §6), пока только загрузка
```
