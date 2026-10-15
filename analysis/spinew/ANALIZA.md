# Analiza: nowa wersja autora (`SPINew` = `trajectoryv2`) a nasz `dev`

Dokument roboczy dla każdej sesji, która podejmuje przeniesienie zmian autora do naszej wersji.
Plan wykonania: [`PLAN.md`](PLAN.md). Ten plik opisuje stan, ustalenia, dowody i decyzje.

Stan na: 2026-10-15 (po decyzjach właściciela i po pomiarze z §13).

---

## 1. Zasady pracy

Zasady łączą dokumenty procesu właściciela (`CLAUDE.md` i `PLAN_ROZWOJU.md` na gałęzi
`process-docs`) z decyzjami z 2026-10-15. Przy rozbieżności obowiązują decyzje z 2026-10-15
(zaznaczone „(2026-10-15)”).

1. Do oryginalnego repozytorium `KipK/vtherm_smartpi` nie wysyłamy absolutnie nic (push, PR,
   issue, komentarz).
2. **Dopasowujemy się do nowego kodu autora** z gałęzi `trajectoryv2` (u nas `SPINew`), a nie tylko do
   `upstream/master` (2026-10-15). To świadome odstępstwo od zasady „nowe commity autora tylko
   osobnym scaleniem `upstream/master`”.
3. **Rekomendacje i odstępstwa tylko po zgodzie właściciela** (2026-10-15). Odstępstwo od ustaleń
   zgłaszamy przed kodem i czekamy na „tak”.
4. **Push, PR i scalenie tylko za „tak” właściciela; branch wybiera właściciel** (2026-10-15).
   Domyślnie: gałąź robocza od aktualnego `dev`, potem PR do `dev`, chyba że właściciel wskaże
   inaczej. `dev`, `qas` i `master` nie zmieniamy sami. Do `qas` tylko z `dev`, na wniosek
   właściciela.
5. Bez merge całego `SPINew` i bez próbnych merge do istniejących gałęzi. Testy i próby tylko na
   nowych gałęziach.
6. **Dokumentacja we wszystkich językach** (2026-10-15): EN jest źródłem, FR i PL to tłumaczenia,
   w tym samym PR co kod. Dotyczy także tłumaczeń interfejsu: zmieniamy `translations/en.json`
   **i** `fr.json` (zastępuje dla tej pracy zasadę „`fr.json` bez zmian”). Język prosty, terminy
   wyjaśnione przy pierwszym użyciu. Gdy zmieniamy działanie opisane w dokumencie autora,
   poprawiamy opis w miejscu.
7. **Dokumentacja razem z kodem każdej części**, nie odkładana na osobny etap (2026-10-15).
8. **CHANGELOG:** wpis w commicie zmiany, w sekcji `## [Unreleased]` (2026-10-15).
9. **Autor commitów:** `Shadow-230 <79769804+Shadow-230@users.noreply.github.com>` z linią
   `Co-Authored-By` Claude (2026-10-15). Starsze commity z autorem „Claude” zostają bez zmian.
10. **Nazewnictwo z kodu i dokumentacji repozytorium** (2026-10-15). Nowych nazw nie wymyślamy;
    nowa nazwa tylko dla pojęcia, którego w repozytorium jeszcze nie ma (wtedy nazwa z kodu
    autora). Słownik w §4.
11. Komunikacja z właścicielem po polsku, krótko i rzeczowo. Kod, commity i PR po angielsku.
12. Te dokumenty zostają w `analysis/spinew/` na gałęzi analizy (2026-10-15). Gałąź
    `process-docs` jest osobna i sesje w chmurze jej nie widzą.

## 2. Gałęzie i punkty odniesienia

| Gałąź | SHA | Co to jest |
|---|---|---|
| `master` | `6a131df` | Wspólny przodek `dev` i `SPINew`; ostatni wspólny stan z autorem. Nietknięty. |
| `dev` | `34c1440` | Nasza gałąź integracyjna (31 commitów od `master`, ostatni: PR #16). |
| `SPINew` | `770c444` | Kopia `KipK/vtherm_smartpi@trajectoryv2` (13 commitów od `master`). |
| `qas` | `9be2e2f` | Stabilny kod do testów (pełne scenariusze na GitHubie). |
| `process-docs` | `823810f` | Osobna gałąź (bez wspólnej historii) z dokumentami procesu: `CLAUDE.md`, `PLAN_ROZWOJU.md`, `specs/`. |
| `analysis/spinew-vs-dev` | od `34c1440` | Ta analiza, plan i skrypty pomiaru. |
| `measure/governor-water` | lokalna, niewypchnięta | `dev` + `c6f9f88`, `b2b0249`, `15fc13f` do pomiaru z §13. Odtworzenie: §13.6. |

Porównania robimy trójstronnie: `master → SPINew` to zmiany autora, `master → dev` to nasze.

```bash
git fetch origin dev master qas SPINew
git merge-base origin/dev origin/SPINew          # 6a131df = origin/master
git log --oneline origin/master..origin/SPINew   # zmiany autora
git log --oneline --no-merges origin/master..origin/dev   # nasze zmiany
```

Nowsze commity autora: `git fetch https://github.com/KipK/vtherm_smartpi.git trajectoryv2` i
porównanie z `origin/SPINew`. Aktualizacja `SPINew` tylko za zgodą właściciela.

## 3. Środowisko testowe

Home Assistant 2026.8.2 wymaga Pythona >= 3.14.2. Testy potrzebują integracji
`versatile_thermostat` (CI klonuje ją obok repozytorium).

```bash
S=<katalog-tymczasowy>
uv venv --python 3.14 $S/venv
VIRTUAL_ENV=$S/venv uv pip install -r requirements_test.txt
git clone --depth 1 https://github.com/jmcollin78/versatile_thermostat.git $S/vt
# zakres CI
PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/qas
# pełny zakres lokalny (długie scenariusze symulacji i pakiet QAS w procesie)
SMARTPI_SIM_LOCAL=1 SMARTPI_QAS_LOCAL=1 PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider
# pokrycie (gałęzie włączone)
COVERAGE_FILE=$S/.coverage SMARTPI_SIM_LOCAL=1 SMARTPI_QAS_LOCAL=1 PYTHONPATH=$S/vt:$PWD \
  $S/venv/bin/python -m pytest tests -q -p no:cacheprovider \
  --cov=custom_components/vtherm_smartpi --cov-branch --cov-report=json:$S/cov.json
```

Wzorce:
- `dev@f73e7d0`: 1174 passed + 75 skipped (zakres CI), lokalnie 1249 passed.
- `dev@34c1440` (po PR #16): zmierzyć przed pierwszym etapem.
- Pokrycie (`process-docs: specs/2026-10-15-coverage-5e9f853.json`): 93,16 % ogółem.
- Cele: kod forka ≥ 95 % linii i ≥ 90 % gałęzi; pliki autora bez spadku pokrycia.

## 4. Słownik nazw (z kodu i dokumentacji repozytorium)

| W kodzie | W dokumentacji PL | Uwagi |
|---|---|---|
| `lead` | główne źródło | rola w strefie z dwoma źródłami |
| `source`, `enter_source_role`, `is_executive_source` | dodatkowe źródło, rola wykonawcza | „Drugie źródło ciepła” to tylko nazwa funkcji |
| master strefy (`zone_master`) | termostat nadrzędny | |
| wpis bez `CONF_TARGET_VTHERM` | wpis „SmartPI defaults” | |
| `CONF_SMART_PI_USE_DEFAULTS` | „Użyj wartości domyślnych SmartPI” (Use the SmartPI defaults) | |
| `SHARED_SETTINGS`, `THERMOSTAT_SETTINGS` | ustawienia wspólne, ustawienia „tylko termostat” | |
| `on_percent`, `u_cmd`, `committed_on_percent` | wysterowanie | |
| `ctl.integral`, integral hold | całka, wstrzymanie całki | |
| `deadtime_heat_s`, `deadtime_cool_s` | czas martwy grzania, czas martwy stygnięcia | |
| `passive_deadtime(hvac_mode)` (nowe u autora) | bierny czas martwy (w HEAT: czas martwy stygnięcia) | nowe pojęcie |
| `dt_est` (`DeadTimeEstimator`) | estymator czasu martwego | |
| deadband, near band | strefa nieczułości (deadband), pasmo przy nastawie (near band) | |
| FF trim | korekta FF (FF trim) | |
| `ThermalTwin1R1C` | bliźniak | |
| landing, landing cap | lądowanie, limit lądowania | znika po etapie C1 |
| `reference_governor*.py` (nowe u autora) | reference governor (gubernator referencji) | FR u autora: „gouverneur de référence” |
| episode, `governor_falsification.py` (nowe u autora) | epizod, falsyfikacja prognozy | |
| known inputs, `b_eff`, `T_ref` | znane wejścia, `b_eff`, `T_ref` | |
| learning gate | bramka uczenia | |
| water factor | współczynnik wody; woda odniesienia | |
| `boiler_call.py` | wezwanie kotła; „kocioł, który nie jest wezwany do grzania” | |
| diagnostic sensor | czujnik diagnostyczny | |
| repair issue | zgłoszenie naprawy | |
| overshoot | przeregulowanie (przekroczenie nastawy) | |

## 5. Zmiany autora (`master → SPINew`)

Kolumna „vs dev”: `git show <sha> -- custom_components | git apply --check` na `origin/dev` (na
sucho). Konflikt często wynika tylko z zależności od wcześniejszego commita autora.

| # | Commit | Co robi | Pliki (`custom_components/vtherm_smartpi/…`) | vs dev |
|---|---|---|---|---|
| 1 | `c6f9f88` | Reference governor (HEAT i COOL) zamiast lądowania; zwalnianie hamowania bez kasowania całki i FF; przyczynowa ścieżka mocy i pomiarów dla korekty FF | `algo.py`; nowe `smartpi/braking_release.py`, `causal_power_trace.py`, `causal_thermal_balance*.py`, `reference_governor*.py`, `thermal_measurement.py`, `thermal_model.py`; zmiany w `setpoint.py`, `ff_trim.py`, `controller.py`, `deadband_output.py`, `diagnostics.py`, `ff3_predictor.py`, `learning_window.py`, `thermal_twin_1r1c.py`, `smartpi/const.py`; także `documentation/{en,fr}` i `cards/` | konflikt: `algo.py`, `learning_window.py` |
| 2 | `b2b0249` | Atrybuty historii diagnostyki | `smartpi/diagnostic_history.py`, dokumentacja | czysto |
| 3 | `15fc13f` | Cap reference governora toleruje strefę nieczułości; falsyfikacja prognozy (wycofanie epizodu) | `smartpi/governor_falsification.py` (nowy), `reference_governor*.py`, `setpoint.py`, `diagnostics.py`, `smartpi/const.py` | zależy od #1 |
| 4 | `4070630` | OFF w fazie wyłączenia PWM anuluje trwający cykl | `handler.py` | czysto |
| 5 | `144ff02` | Usunięcie setterów `guard_cut_active`/`guard_kick_active` (zawsze rzucały wyjątek) | `algo.py` | czysto |
| 6 | `b3c8939` | Przeładowanie tylko dotkniętych termostatów; fabryka i serwisy na stałe; `async_remove_entry` | `__init__.py`, `const.py` | konflikt |
| 7 | `dd62923` | Osobne liczniki sekwencji dryfu dla a i b | `smartpi/ab_estimator.py` | czysto |
| 8 | `c506522` | Reset PI po restarcie w innym trybie; bez całkowania przez przerwę | `algo.py` | konflikt (kontekst z #1) |
| 9 | `a4624e5` | Zapis stanu najwyżej co 5 min z pętli regulacji | `handler.py`, nowy `state_store.py` | konflikt |
| 10 | `69da036` | Nachylenia do uczenia a/b z surowych pomiarów | `algo.py`, `smartpi/deadtime_estimator.py`, `smartpi/tint_filter.py` | konflikt tylko w `algo.py` |
| 11 | `b00384a` | Walidacja celu serwisów, `async_fire`, wpis „SmartPI defaults” do odtworzenia, bez kroku `global`, tłumaczenia, `requirements` w manifeście | `__init__.py`, `config_flow.py`, `handler.py`, `manifest.json`, `services.yaml`, `translations/{en,fr}.json` | konflikt |
| 12 | `8823c17` | COOL: bierny czas martwy przy hamowaniu i w bliźniaku | `algo.py`, `setpoint.py`, `thermal_twin_1r1c.py` | zależy od #1 |
| 13 | `770c444` | COOL: histereza lustrzana; flaga zmiany nastawy w cyklu; FF3 bez martwego wejścia | `algo.py`, `smartpi/controller.py`, `smartpi/ff3.py` | konflikt (kontekst) |

Opisy autora: `git log --reverse --format='### %h %s%n%b' origin/master..origin/SPINew`.

## 6. Nasze zmiany (`master → dev`)

Funkcje, których autor nie ma i które trzeba zachować:

- **Znane wejścia** (`smartpi/dependencies.py`, `dependency_inputs.py`, `dependency_model.py`,
  `effective_model.py`): `b_eff` i `T_ref` w regulacji, bramka uczenia, odejmowanie `known_rate`
  od mierzonego nachylenia.
- **Sąsiednie strefy** (`coupling_estimator.py`): uczenie wymiany ciepła, reakcja na okno u
  sąsiada (`turn_off`/`frost`/`eco`).
- **Strefa z dwoma źródłami** (`source_link.py`, `source_model.py`, `split.py`, `zone_fit.py`,
  `source_settings.py`, `source_candidates.py`): główne źródło liczy oba polecenia, dodatkowe
  źródło w roli wykonawczej, wspólne dopasowanie a/b strefy.
- **Wejścia wody i kotła** (`smartpi/water_input.py`, `boiler_call.py`, `heating_inputs.py`):
  współczynnik wody, wstrzymanie całki bez ciepła, bramka `boiler_settling`.
- **Ustawienia wspólne i „tylko termostat”** (`settings.py`, PR #16): „Use the SmartPI defaults”,
  migracja wpisów do 1.2, formularz z jednym zapisem (`DATA_SAVE_GROUP`).
- **Przejęcie czujnika diagnostycznego** przez własny wpis termostatu (`sensor.py`, PR #14).
- Symulator (`tests/sim`), pakiet QAS (`tests/qas`), dokumentacja EN/FR/PL.

## 7. Punkty styku w `dev@34c1440` (`algo.py`)

Numery linii mogą się przesunąć; szukaj po nazwie.

| Element | Funkcja (linia) | Znaczenie |
|---|---|---|
| `control_b` | właściwość (1805) | `b_eff` zamiast `est.b` w FF1 (`k_ff`), FF3, `filter_setpoint`, bliźniaku, korekcie FF |
| `control_ext_temp` | `calculate` | `T_ref` zamiast `ext_current_temp` |
| `_control_estimator()`, `_control_tau_min()` | `calculate` | widok estymatora z `b_eff` dla pasma przy nastawie i doboru wzmocnień |
| `_to_actuator_power`, `_water_command`, `_to_linear_power` | 2145, 2158, 2169 | przeliczenie wysterowania z uwzględnieniem wody |
| `_learning_power`, `_deadtime_input` | 2242, 2254 | wysterowanie do uczenia i do estymatora czasu martwego |
| `_record_completed_fftrim_power` | 1130 | wysterowanie cyklu × `_water_factor` do korekty FF |
| saturacja z limitem wody | `_apply_soft_constraints` (3771–3776) | `upper *= max(water_factor, WATER_NO_HEAT)` |
| rola wykonawcza | `calculate` (3830) | wczesny `return` z poleceniem głównego źródła, przed `_update_time_tracking` |
| `_read_known_inputs`, `_apply_neighbour_window_action` | `calculate` | znane wejścia, bramka uczenia, akcja okna u sąsiada |
| `learn_win.update(..., known_rate=...)` | `update_learning` (910) | odejmowanie wpływu znanych wejść |
| `dt_est.update(..., u_applied=self._deadtime_input())` | `calculate` (3989, 4061) | estymator czasu martwego |
| `WATER_INTEGRAL_HOLD`, `_ff_trim.freeze("boiler_not_heating")` | `calculate` | wstrzymanie całki i korekty FF bez ciepła |

## 8. Porównanie logiki w punktach styku

| Obszar | SPINew | dev | Ocena |
|---|---|---|---|
| Model w regulacji | Reference governor, hamowanie, bliźniak i FF3 dostają model przez te same wywołania co w `master` | Podstawiamy `b_eff` i `T_ref` w tych wywołaniach | Zgodne (potwierdzone na `measure/governor-water`) |
| Wysterowanie i woda | Reference governor liczy w tej samej domenie co my | Woda ogranicza wysterowanie do `water_factor` | Zgodne; pomiar (§13): bez wycofań epizodów, korekta niepotrzebna |
| Uczenie | Blokada uczenia A przy zmianie nastawy; nachylenia z surowych pomiarów | Bramka znanych wejść i kotła; `known_rate` | Uzupełniają się; `learn_win.update` przyjmuje oba zestawy |
| Całka | Reset PI po restarcie w innym trybie; brak całkowania przez przerwę | Wstrzymanie całki bez ciepła z wody | Bez kolizji |
| Zapis stanu | `state_store.py`, najwyżej co 5 min | Nasze dane w tym samym `save_state` | Zgodne |
| Cykl życia | Patrz §11 | Patrz §11 | Decyzja 2026-10-15: sposób autora |
| OFF, COOL | Anulowanie cyklu w OFF; COOL lustrzany | Woda i kocioł tylko w HEAT | Niezależne |
| Dokumentacja | Lądowanie zastąpione reference governorem | EN/FR/PL opisują lądowanie | Do przepisania razem z C1 (§14) |

## 9. Co autor robi lepiej (z dowodami)

W każdym punkcie `dev` działa jak `master`, a `SPINew` ma poprawkę.

1. **Nachylenia do uczenia a/b z surowych pomiarów (`69da036`).** W `dev` okna uczenia biorą
   próbki z `dt_est.tin_history`, do którego trafia temperatura po filtrze EMA (`tin=t_int_lp`).
   Odporny estymator nachylenia liczy różne skoki wartości, a filtr robi z jednego skoku czujnika
   wiele, więc wolne nachylenia są akceptowane za wcześnie i zaniżone. Nasze dokumenty mierzyły
   ten objaw: `PLAN_ROZWOJU.md` §6 (E0) – czujnik 0,1 °C daje `a` i `b` od −52 % do +24 % prawdy,
   okno zamyka się po 6–7 min; `documentation/en/zone_dependencies.md` (ok. linia 563) – własne
   okna SmartPI mierzą wolne stygnięcie „mniej więcej o połowę za wolno”, co obchodzi
   `_apply_zone_fit`. Autor usuwa przyczynę.
2. **Reference governor zamiast lądowania (`c6f9f88`, `15fc13f`).** HEAT i COOL (lądowanie tylko
   HEAT), tolerancja strefy nieczułości za celem, falsyfikacja prognozy (wycofanie epizodu po
   powtarzających się chybieniach), zwalnianie hamowania bez kasowania całki i FF. Pomiar §13:
   dojście bez danych o wodzie 1,97 h zamiast 2,28 h, przy wodzie 60 °C mniejsze średnie
   przeregulowanie (0,07 zamiast 0,09 °C).
3. **Cykl życia wpisów (`b3c8939`).** §11.
4. **Zapis stanu (`a4624e5`).** `dev` zapisuje stan przy każdym `control_heating` (ok. raz na
   minutę na termostat). Autor: zapis odroczony najwyżej co 5 min (opróżniany przy zamykaniu HA),
   natychmiastowy przy serwisach i usuwaniu. Mniej zapisów na kartę SD.
5. **Przerwy w regulacji (`c506522`).** Przy braku czujnika i „guard cut” `dev` nie zeruje
   `_last_calculate_time`, więc pierwszy tick po przerwie całkuje błąd za cały czas przerwy
   (do 10 cykli; dłuższa przerwa jest przycinana do 1 cyklu). Po restarcie w innym trybie PI jest
   kasowane na podstawie zapisanego `est.model_hvac_mode`.
6. **Dryf a i b (`dd62923`).** W `dev` jeden licznik `_learn_seq` dla obu kanałów, więc 3 lub
   więcej pomiarów grzania między pomiarami stygnięcia blokuje korektę prawdziwego dryfu b.
7. **Konfiguracja i serwisy (`b00384a`).** Wpis „SmartPI defaults” da się odtworzyć po
   usunięciu (w `dev` nie, gdy są wpisy termostatów); usunięty martwy krok `async_step_global`;
   serwisy ze standardowym celem i błędem walidacji; `bus.async_fire` (w `dev` 6 wywołań
   `bus.fire`); `vtherm_api>=0.5.0` w manifeście.
8. **COOL (`8823c17`, `770c444`).** Bierny czas martwy przy hamowaniu i w bliźniaku, histereza
   lustrzana.
9. **Drobne.** Flaga zmiany nastawy w cyklu (korekta FF bez płynnego przejęcia w tym cyklu);
   OFF anuluje cykl w fazie wyłączenia PWM; usunięte settery (błąd zapisany też w
   `PLAN_ROZWOJU.md` §6); martwa gałąź `event.data` w `_cancel_calibration_if_active` (też w
   `PLAN_ROZWOJU.md` §6).

## 10. Czego autor nie ma (zachować)

Wszystko z §6, a w cyklu życia:
- przeładowanie termostatów z „Use the SmartPI defaults” przy zmianie wpisu „SmartPI defaults”
  (czujnik diagnostyczny dostaje nowe „Record full diagnostics”);
- `_zone_mains_of` i `_saved_together` (główne źródła strefy, grupa jednego zapisu);
- `DATA_SKIP_UPDATE` (wpis zmieniany przez sam SmartPI nie wyzwala listenera);
- migracja 1.2: `async_migrate_entry`, `_settings_to_move`, `_async_move_settings`,
  `ISSUE_THERMOSTAT_ENTRIES_CREATED`;
- przejęcie czujnika diagnostycznego przez własny wpis (`sensor.py`, `DATA_DIAGNOSTIC_SENSORS`).

## 11. Cykl życia i ustawienia

### 11.1 `dev` dziś (`__init__.py@34c1440`)

- `async_setup` i `async_setup_entry` rejestrują fabrykę i serwisy (flagi
  `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`).
- `async_setup_entry` przy działającym HA, bez `skip_full_reload_<id>`: wpis z importu
  (`SOURCE_IMPORT`) przeładowuje tylko swój termostat, inne przypadki **wszystkie** termostaty
  SmartPI (`_reload_smartpi_vtherms`).
- `async_unload_entry`: przy „ostatnim wpisie” wyrejestrowuje fabrykę i serwisy; **zawsze
  przeładowuje wszystkie termostaty SmartPI**.
- Brak `async_remove_entry`.
- Listener opcji: pomija wpis z `DATA_SKIP_UPDATE`; ustawia `skip_full_reload`; przeładowuje
  wpis; potem termostat i główne źródła strefy (wpis termostatu) albo wpisy z „Use the SmartPI
  defaults” i `_reload_smartpi_vtherms_using_defaults` (wpis „SmartPI defaults”).

### 11.2 Problemy `dev`

1. **Każdy zapis opcji przeładowuje wszystkie termostaty SmartPI**, docelowy dwa razy. Łamie to
   zasadę `PLAN_ROZWOJU.md` §1 „przeładowania tylko dotkniętych instancji, nigdy wszystkich”.
   `test_config_flow_save` tego nie widzi, bo podmienia `async_reload` na atrapę.
2. **Usunięty wpis działa do restartu**: termostat przeładowany w unload czyta jeszcze usuwany wpis.
3. **Kruche liczenie „ostatniego wpisu”** po kluczach `hass.data[DOMAIN]`.
4. **Wpisu „SmartPI defaults” nie da się odtworzyć**, gdy są wpisy termostatów.

### 11.3 `SPINew`

Fabryka i serwisy rejestrowane raz w `async_setup`; setup przeładowuje tylko dotknięte termostaty
(wpis termostatu: ten termostat; wpis „SmartPI defaults”: termostaty bez własnego wpisu);
unload niczego nie przeładowuje; `async_remove_entry` przeładowuje po zniknięciu wpisu; listener
tylko przeładowuje wpis.

### 11.4 Decyzja 2026-10-15: sposób autora

Właściciel przyjął sposób autora (usprawnia proces). Skutki:
- `_reload_smartpi_vtherms_using_defaults` wraca do wersji autora (pomija każdy termostat z
  własnym wpisem). Termostaty z „Use the SmartPI defaults” przeładowujemy przez przeładowanie ich
  wpisu SmartPI (setup tego wpisu przeładuje termostat raz i odtworzy czujnik diagnostyczny).
  **Zastępuje to sposób z `specs/2026-10-14-…` §9.5 („funkcja rozszerzona”)**; zachowanie (kto i
  kiedy jest przeładowany) zostaje.
- Znikają zmiany z etapu 0 tej specyfikacji (§8.2): wyjątek `SOURCE_IMPORT` w setup i
  pomijanie `DATA_SOURCE_LINK` przy „ostatnim wpisie”. Poprawka czujnika diagnostycznego
  (przejęcie przez własny wpis, `0d77269`) zostaje.
- Znikają flagi `skip_full_reload`, `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`.
- Do odnotowania w dokumentach procesu (robi to sesja edytująca `PLAN_ROZWOJU.md`).

Projekt funkcji i tabela zdarzeń: `PLAN.md`, etap B1.

## 12. Ustalenie własne: przerwa w roli wykonawczej

W `calculate` (ok. linia 3830) dodatkowe źródło w roli wykonawczej wraca wcześnie z poleceniem
głównego źródła, przed `_update_time_tracking` (ok. 3911). `_last_calculate_time` zostaje z
ostatniego własnego ticku. Po `leave_source_role` pierwszy własny tick liczy `dt` za cały czas
roli wykonawczej (przycięte do 1 cyklu tylko powyżej 10 cykli) i całkuje błąd za ten czas. Ta sama
klasa problemu co w `c506522`. Z lektury kodu, niesprawdzone w działaniu. Poprawka: `PLAN.md`, A3.

## 13. Pomiar: reference governor a woda (2026-10-15)

### 13.1 Pytanie

Czy reference governor autora, nie znając limitu wody, uzbraja i wycofuje epizody (falsyfikacja
prognozy), gdy wysterowanie ogranicza zimna woda, i czy trzeba to korygować w kodzie autora.

### 13.2 Metoda

- Kod: `measure/governor-water` = `dev@34c1440` + `c6f9f88`, `b2b0249`, `15fc13f` (rozwiązanie
  konfliktów jak w `PLAN.md` C1; dokumentacja `--ours`). Porównanie z `dev` (lądowanie).
- Symulator: jedna strefa z kaloryferem (`a`=0,06, `b`=0,001, czas martwy 10 min, czujnik 0,1 °C),
  na zewnątrz 3 ± 3 °C. 48 h uczenia przy 20 °C, potem 4 doby: 17 °C od 22:00, 21 °C od 6:00.
- Warianty: bez wejścia wody; woda 60 °C, 45 °C, 40 °C z danymi o wodzie (dostawca); woda 45 °C
  bez danych. Każdy w 3 przesunięciach fazy wywołań (0, 170, 340 s), czyli 12 porannych dojść.
- Miary w oknie 6:00–14:00: czas dojścia do 20,9 °C, przeregulowanie ponad 21 °C, minuty z
  wiążącym capem (lądowanie albo reference governor), przełączenia capa, minuty ograniczenia
  wodą (`_water_too_cold`), uzbrojenia i wycofania epizodów (powody).
- Skrypty: `analysis/spinew/measure_governor_water.py`, `summarize_measurement.py`; wyniki:
  `analysis/spinew/results/`, podsumowanie `measurement_2026-10-15.txt`.

### 13.3 Wyniki (średnie z 12 dojść)

| Wariant | Wersja | Współczynnik wody | Dojście (h) | Przeregulowanie śr./max (°C) | Cap (min) | Ograniczenie wodą (min) | Wycofania |
|---|---|---|---|---|---|---|---|
| bez wejścia wody | `dev` | 1,00 | 2,28 | 0,14 / 0,23 | 63 | 0 | – |
| | governor | 1,00 | 1,97 | 0,14 / 0,27 | 3 | 0 | 0 |
| woda 60 °C | `dev` | 0,97 | 2,05 | 0,09 / 0,18 | 116 | 0 | – |
| | governor | 0,97 | 1,99 | 0,07 / 0,18 | 8 | 0 | 0 |
| woda 45 °C | `dev` | 0,52 | 4,48 | 0,06 / 0,15 | 24 | 212 | – |
| | governor | 0,52 | 4,43 | 0,06 / 0,15 | 0 | 212 | 0 |
| woda 40 °C | `dev` | 0,38 | 7,53 | 0,02 / 0,08 | 12 | 423 | – |
| | governor | 0,38 | 7,56 | −0,01 / 0,08 | 0 | 424 | 0 |
| woda 45 °C bez danych | `dev` | 1,00 | 4,70 | 0,04 / 0,32 | 28 | 0 | – |
| | governor | 1,00 | 4,77 | 0,03 / 0,31 | 0 | 0 | 0 |

Przy 40 °C jedno dojście z 12 (governor) i przy 45 °C bez danych po jednym w obu wersjach nie
osiągnęło 20,9 °C w 8 h.

### 13.4 Wnioski

1. **Zero wycofań epizodów** (`prediction_falsified_below_target`) we wszystkich wariantach.
2. **Przy ograniczeniu wodą reference governor nie zakłada capa.** Cap wiąże tylko wtedy, gdy
   wysterowanie mogłoby dać przeregulowanie; gdy wiąże woda, cap wychodzi wyżej, nic nie obiecuje
   i nie ma czego falsyfikować. Dodatkowo po wycofaniu ten sam cel nie uzbraja się ponownie
   (`ReferenceGovernorRuntime.retire`), więc cykliczne uzbrajanie i wycofywanie nie jest możliwe.
3. **Decyzja (opcja D): bez korekty w kodzie autora.** W dokumentacji opisujemy, że przy
   ograniczeniu wodą reference governor nie hamuje. Korekta A (żądanie przycięte do limitu wody)
   zostaje tylko jako zapas, gdyby dane z domu pokazały inaczej, i tylko za zgodą właściciela.
4. **Dłuższe dojścia z wodą to fizyka, nie regulacja.** Moc kaloryfera rośnie z różnicą
   temperatur woda–pokój (wykładnik 1,3; 100 % przy 60/20 °C) i maleje, gdy pokój się nagrzewa.
   Przyrost przy pełnym wysterowaniu minus straty (na zewnątrz 0 °C): 60 °C: +2,9 → +2,3 °C/h;
   45 °C: +1,3 → +0,7 °C/h; 40 °C: +0,7 → +0,2 °C/h (pokój 17 → 20,5 °C). Obie wersje i wariant bez
   danych o wodzie wydłużają się tak samo. W pomiarze woda była stała; krzywa grzewcza kotła
   skraca to w praktyce. Jedyna odpowiedź SmartPI: start z wyprzedzeniem (E7 w `PLAN_ROZWOJU.md`).

### 13.5 Obserwacje poza pytaniem

- W jednym dojściu (woda 60 °C, faza 170 s) cap reference governora przełączył się 60 razy w ok.
  1 h („drganie” przy nastawie). Zachowanie kodu autora, do obserwacji w C1.
- Na `measure/governor-water` nauczone `a` jest o 5–10 % niższe niż na `dev` (zmiany autora w
  uczeniu, bez A1). Sprawdzić po A1.

### 13.6 Odtworzenie

```bash
git switch -c measure/governor-water origin/dev
git cherry-pick -x c6f9f88 b2b0249 15fc13f   # konflikty: PLAN.md C1; dokumentacja: --ours
PYTHONPATH=$S/vt python analysis/spinew/measure_governor_water.py <checkout> governor res_governor.json
PYTHONPATH=$S/vt python analysis/spinew/measure_governor_water.py <checkout-dev> dev res_dev.json
python analysis/spinew/summarize_measurement.py <katalog z res_*.json>
```
Czas: ok. 15 min na wersję (15 symulacji po 6 dób).

## 14. Wpływ na dokumentację repozytorium

Zasada (§1 p. 6–7): EN jako źródło, FR i PL w tym samym PR co kod; CHANGELOG w `[Unreleased]`.
Liczba i kolejność nagłówków równe w trzech językach; odnośniki i kotwice działają; bloki Mermaid
się parsują.

| Etap | Dokument (EN → FR, PL) | Co zmienić |
|---|---|---|
| A1 | `technical_doc.md` §3.2–3.3 (okno uczenia, odporne nachylenie) | nachylenie z surowych pomiarów, filtr EMA tylko do czasu martwego |
| A1 | `zone_dependencies.md` (ok. linia 563, „half as fast”) | sprawdzić stwierdzenie po A1 (decyzja D2) i poprawić liczby |
| A2 | `technical_doc.md` (dryf), `diagnostics.md` (jeśli pokazuje licznik) | osobne liczniki a i b |
| A3 | `technical_doc.md` (zmiana trybu, przerwy), `zone_dependencies.md` (rola wykonawcza) | reset PI po restarcie; brak całkowania przez przerwę, także po roli wykonawczej |
| A4 | `technical_doc.md` §5.4 „Current persistence” | zapis najwyżej co 5 min, natychmiastowy przy serwisach i usuwaniu |
| A5 | `CHANGELOG.md` | OFF anuluje cykl; usunięte settery |
| B1 | `technical_doc.md` (ok. linia 511, przeładowanie przy zapisie „SmartPI defaults”), `zone_dependencies.md` (ok. linia 1144, `__init__.py`), `testing.md` | nowy model przeładowań; usunięcie wpisu |
| B2 | `vtherm_smartpi.md` (wpis „SmartPI defaults”) | odtworzenie po usunięciu |
| B3 | `vtherm_smartpi.md` „Services”, `services.yaml`, `translations/{en,fr}.json` | cel standardowy, błąd walidacji (decyzja D1) |
| B4 | `CHANGELOG.md` | `async_fire`, `requirements` |
| C1 | `technical_doc.md` §4.4, §6.3 i pozostałe wzmianki o lądowaniu; `vtherm_smartpi.md` (opis filtra nastawy); `diagnostics.md` (atrybuty `landing_*` → atrybuty reference governora, atrybuty historii); `README.md` (1 wzmianka) | lądowanie → reference governor; treść z `git show origin/SPINew:documentation/<jęz>/<plik>`, nasze etykiety UI i nasze poprawki zostają |
| C1 | `water_input.md` §3.4 i §3.6 | przy ograniczeniu wodą reference governor nie hamuje; wyniki §13 |
| C1 | `cards/smartpi_diags-{en,fr}.md` | przychodzą od autora; dodajemy nasze sekcje (znane wejścia `dependencies.*`, woda `water.*`, kocioł `boiler.*`) w EN i FR (D4). Karty PL nie ma; nowa tylko po decyzji właściciela |
| C2 | `technical_doc.md` (COOL) | bierny czas martwy; histereza lustrzana |
| każdy | `testing.md` | nowe testy, pokrycie, katalog QAS (§6.5) |

## 15. Zgodność z dokumentami procesu (`process-docs`)

| Zasada w `CLAUDE.md` / `PLAN_ROZWOJU.md` | Stan w tej pracy |
|---|---|
| Nowe commity autora tylko scaleniem `upstream/master` | Odstępstwo z decyzji 2026-10-15 (§1 p. 2) |
| Odstępstwa zgłaszamy przed kodem | Przyjęte (§1 p. 3) |
| Push/PR/scalenie za „tak” | Przyjęte; branch wybiera właściciel (§1 p. 4) |
| Nowe klucze tylko w `en.json` | Zmienione decyzją 2026-10-15: aktualizujemy wszystkie języki (§1 p. 6) |
| Dokumentacja EN/FR/PL razem z kodem, prosty język | Przyjęte (§1 p. 6–7, §14) |
| CHANGELOG w `[Unreleased]` | Przyjęte (§1 p. 8) |
| Autor commitów Shadow-230 | Przyjęte od teraz (§1 p. 9) |
| Role „główne źródło”, „dodatkowe źródło” | Przyjęte; słownik §4 |
| Przeładowania tylko dotkniętych instancji | Realizuje B1 |
| Specyfikacja §9.5 (rozszerzona funkcja przeładowania) | Zastąpione sposobem autora (§11.4) |
| Zasada E6: FF3, bliźniak i trajektoria nastawy bez zmian | Dotyczy naszych zmian; zmiany autora przyjmujemy (§1 p. 2). Pomiar §13: korekta w kodzie autora niepotrzebna |
| Kolejność prac E5 → E6 | Plan E6 opiera się na korekcie FF i trajektorii autora, które zmienia C1: E6 sprawdzić po C1 |
| Cele pokrycia ≥ 95 % / ≥ 90 %, kontrole przed PR | W `PLAN.md` §0a |
| Kryteria scenariuszy nie mogą być kruche | W `PLAN.md` §0a |

## 16. Decyzje

| Id | Decyzja | Stan |
|---|---|---|
| D0 | Dopasowanie do nowego kodu autora (`trajectoryv2`) | przyjęte 2026-10-15 |
| D1 | Serwisy: walidacja celu i błąd, gdy brak termostatu SmartPI (autor) albo ciche ignorowanie (dev) | przyjęte 2026-10-15: wersja autora (wywołania z `entity_id` w danych, np. pakiet QAS, nadal działają) |
| D2 | Po A1: czy `_apply_zone_fit` nadal nadpisuje a/b strefy | przyjęte 2026-10-15: decyzja po pomiarze na symulatorze po A1 |
| D3 | Zmiany tylko dla COOL (C2) | przyjęte (D0) |
| D4 | Nasze sekcje (znane wejścia, woda, kocioł) w kartach diagnostycznych | przyjęte 2026-10-15: dodajemy (C1) |
| D5 | Dokumentacja EN/FR/PL razem z kodem | przyjęte 2026-10-15 |
| D6 | Cykl życia: sposób autora (B1) | przyjęte 2026-10-15 (§11.4) |
| D7 | Korekta reference governora przy wodzie | pomiar 2026-10-15: niepotrzebna, opcja D (§13.4) |
| D8 | „Drganie” capa reference governora (§13.5) | przyjęte 2026-10-15: obserwujemy w C1; ewentualna zmiana (kod autora) po pomiarze i za zgodą właściciela |
| D9 | Zapis stanu od razu po resecie strefy (`_apply_reset_request`, etap A4) | przyjęte 2026-10-15: zapis natychmiastowy, jak przy serwisie `reset_smartpi_learning` u autora |

## 17. Testy – stan i wpływ

### 17.1 Testy autora (przychodzą z jego commitami)

Przy cherry-picku testy przychodzą same. Przy etapach ręcznych (A3, B1–B4):
`git show <sha> -- tests | git apply`.

| Commit | Nowe pliki testów | Zmienione pliki testów | vs dev |
|---|---|---|---|
| `c6f9f88` | `test_causal_power_trace.py`, `test_causal_thermal_balance.py`, `test_causal_thermal_balance_admission.py`, `test_reference_governor_braking_release.py`, `test_smartpi_braking_release.py`, `test_smartpi_learning_enablement.py`, `test_smartpi_learning_window_setpoint_transient.py`, `test_smartpi_reference_governor.py`, `test_smartpi_reference_governor_authority.py`, `test_smartpi_reference_governor_runtime.py`, `test_smartpi_reference_governor_wiring.py`, `test_smartpi_thermal_model.py`, `test_thermal_measurement.py` | `test_smartpi_cycle_alignment.py`, `test_smartpi_deadband_output.py`, `test_smartpi_ff_trim_observer.py`, `test_smartpi_heartbeat_learning.py`, `test_smartpi_setpoint.py`, `test_smartpi_setpoint_change.py`, `test_smartpi_valve_runtime_sync.py` | czysto |
| `b2b0249` | – | `test_diagnostic_history.py`, `test_sensor.py` | czysto |
| `15fc13f` | `test_smartpi_governor_falsification.py` | testy reference governora | czysto |
| `4070630` | `test_smartpi_handler_off.py` | – | czysto |
| `b3c8939` | `test_init_lifecycle.py` | – | czysto |
| `dd62923` | – | `test_smartpi_ab_drift.py` | czysto |
| `c506522` | – | `test_smartpi_antiwindup.py`, `test_smartpi_robustness.py` | czysto |
| `a4624e5` | `test_handler_persistence.py` | `test_smartpi_cycle_alignment.py` | czysto |
| `69da036` | – | `test_smartpi_deadtime_estimator.py` | czysto |
| `b00384a` | – | `test_config_flow.py`, `test_handler_attributes.py`, `test_services.py` | czysto |
| `8823c17` | – | `test_smartpi_learning_enablement.py`, `test_smartpi_reference_governor_wiring.py`, `test_smartpi_setpoint.py`, `test_smartpi_thermal_model.py` | zależy od `c6f9f88` |
| `770c444` | – | `test_smartpi_cycle_alignment.py`, `test_smartpi_feedforward.py`, `test_smartpi_hysteresis_deadtime_window.py` | zależy od `c6f9f88` |

Na `measure/governor-water` (po `c6f9f88`, `b2b0249`, `15fc13f`) testy wody i reference governora:
60 passed, 1 failed (`test_smartpi_water_algo::test_the_feed_forward_trim_records_the_power`,
podmienia przeniesioną metodę).

### 17.2 Nasze testy dotknięte przenoszeniem

| Test | Dlaczego | Etap | Zmiana |
|---|---|---|---|
| `test_smartpi_sim_water.py`, `test_smartpi_sim_boiler_call.py` (nauczone a, b) | surowe nachylenia zmieniają nauczone a/b | A1 | progi tylko bliżej prawdy; wartości przed i po |
| `test_smartpi_dependencies_algo.py` (ok. linia 161, pusta `tin_history`) | dwie historie po A1 | A1 | dodać `_tin_lp_history` |
| `test_smartpi_ab_drift.py` (ok. linia 153, `_learn_seq`) | dwa liczniki | A2 | od autora |
| testy z `handler._async_save = AsyncMock()` (`test_handler_attributes`, `test_smartpi_boiler_handler`, `test_smartpi_cycle_alignment`, `test_smartpi_guards`, `test_smartpi_settings`, `test_smartpi_valve_runtime_sync`, `test_smartpi_water_handler`) | `control_heating` woła `schedule_save` | A4 | testy serwisów zostają; `test_smartpi_cycle_alignment.py` (linie 321, 679) poprawia autor |
| `tests/sim/runner.py:521`, `test_smartpi_sim_second_source.py:262` (`_legacy_store`) | atrybut znika | A4 | usunąć linie |
| `test_config_flow_save.py` | atrapa `async_reload` ukrywa podwójne przeładowania | B1 | technika autora (setup naprawdę wołany) |
| `test_config_migration.py` (ok. 239), `test_sensor_own_entry.py` (45, 91) | podmieniają usuwane `_reload_smartpi_vtherms` | B1 | podmieniać dwa helpery autora |
| `test_services_routing.py` | rejestracja na stałe; walidacja celu (D1) | B1, B3 | kontrakt autora |
| `test_config_flow_heating_type.py` (ok. 110, `("config", "global")`) | krok `global` znika | B2 | usunąć parę |
| `test_smartpi_autocalib_events.py` | `bus.fire` | B4 | `bus.async_fire` |
| `test_smartpi_water_algo.py::test_the_feed_forward_trim_records_the_power` | metoda przeniesiona do `_causal_power_trace` | C1 | nowa podmiana |
| `test_smartpi_dependencies_algo.py::test_substitution_reaches_every_control_component` | nowe elementy regulacji | C1 | znaleźć przyczynę, rozszerzyć |
| `test_smartpi_sim_neighbours.py` (3), `test_smartpi_sim_water.py` (1), `test_smartpi_sim_harness.py::test_valve_cycle_starts_report_the_applied_opening` (2) | zmiana zachowania w próbie merge (`dev@f73e7d0`) | C1 | najpierw ocenić jako regresję |
| `tests/qas/scenarios/*` | klucze diagnostyki | C1 | używane klucze istnieją u autora; sprawdzić |

### 17.3 Luki w testach

1. Żaden test nie liczy rzeczywistych przeładowań (§11.2).
2. Brak testu przerwy w roli wykonawczej (§12).
3. Brak scenariusza wody z reference governorem w zestawie testów (pomiar §13 jest skryptem).
4. Brak testu naszych danych w `SmartPIStateStore` (`dependencies`, `dependency_reset_token`,
   `water`).
5. Brak testu błędu nachylenia przy czujniku 0,1 °C względem prawdy (D2).
6. Test podstawienia nie obejmuje nowych elementów (reference governor, pomiary korekty FF,
   ścieżka mocy).
7. Test różnicowy „bez konfiguracji nic się nie zmienia” (`PLAN_ROZWOJU.md` §1) trzeba utrzymać po C1.

## 18. Wcześniejsza próba merge (informacyjnie, odrzucona)

Merge całego `SPINew` do `dev@f73e7d0` (przed PR #16), przerwany i usunięty na życzenie
właściciela. Konflikty: `__init__.py`, `algo.py` (2: import `Callable`, parametry
`learn_win.update`), `const.py`, `handler.py`, `learning_window.py`, `translations/en.json`,
`documentation/{en,fr}/{technical_doc,vtherm_smartpi}.md` (na `dev@34c1440` także
`config_flow.py`). Wynik testów po rozwiązaniu: 1468 passed, 10 failed (lista w §17.2).
