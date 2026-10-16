# Analiza: nowa wersja autora (`SPINew` = `trajectoryv2`) a nasz `dev`

Dokument roboczy dla każdej sesji, która podejmuje przeniesienie zmian autora do naszej wersji.
Plan wykonania: [`PLAN.md`](PLAN.md). Ten plik opisuje stan, ustalenia, dowody i decyzje.

Stan na: 2026-10-16 (fazy A i B, etapy Z1, Z2 i H1 scalone do `dev2` = `705b3f2`: §19–§22).

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
   **Od 2026-10-16 gałęzią integracyjną tej pracy jest `dev2`** (kopia `dev@34c1440`): każda
   gałąź robocza od `dev2`, **osobny PR na każdy etap** do `dev2`. Na `dev`, `qas` i `master` nie
   wypychamy nic (`dev` zostaje na `34c1440` jako zapas). `qas` na razie nie dotykamy.
   Na `dev2` trafiają także nasze własne funkcje (np. Z1, Z2, H1), nie tylko zmiany autora. (2026-10-16)
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
    `process-docs` jest osobna (bez wspólnej historii); jej `CLAUDE.md` i `PLAN_ROZWOJU.md` wskazują
    na te dokumenty i są aktualizowane razem z nimi.
13. **Po scaleniu PR etapu gałąź etapu jest usuwana** (2026-10-16): lokalnie (gałąź i worktree)
    robi to sesja; na GitHubie usuwa ją właściciel, bo proxy sesji w chmurze odrzuca
    `git push --delete` (HTTP 403). Przed usunięciem: SHA gałęzi = głowica PR i zawiera się w `dev2`.

## 2. Gałęzie i punkty odniesienia

| Gałąź | SHA | Co to jest |
|---|---|---|
| `master` | `6a131df` | Wspólny przodek `dev` i `SPINew`; ostatni wspólny stan z autorem. Nietknięty. |
| `dev` | `34c1440` | Poprzednia gałąź integracyjna (31 commitów od `master`, ostatni: PR #16); zapas, nic tu nie wypychamy. |
| `SPINew` | `770c444` | Kopia `KipK/vtherm_smartpi@trajectoryv2` (13 commitów od `master`). |
| `qas` | `9be2e2f` | Stabilny kod do testów (pełne scenariusze na GitHubie). |
| `process-docs` | aktualny `origin/process-docs` | Osobna gałąź (bez wspólnej historii) z dokumentami procesu: `CLAUDE.md`, `PLAN_ROZWOJU.md`, `specs/`. |
| `dev2` | `705b3f2` | **Gałąź integracyjna tej pracy** (od 2026-10-16, start: kopia `dev@34c1440`). Zawiera A1–A5, Z1, Z2, B1–B4, H1 (PR #17–#28). |
| `analysis/spinew-vs-dev` | od `34c1440` | Ta analiza, plan, skrypty pomiaru (`tools/`) i wyniki (`results/`). |
| gałęzie etapów | od `dev2` | Usuwane po scaleniu (§1 p. 13); historia w `PLAN.md` „Stan prac”. |
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
- `dev@34c1440` = `dev2` (zmierzone 2026-10-16, A0): **1322 passed + 75 skipped** (zakres CI),
  **1397 passed** lokalnie, pokrycie 93,16 % (linie 94,7 %, gałęzie 88,0 %); dane:
  `results/phase-a/cov_dev2.json`. Skrypt: `tools/run_suite.sh`, porównanie pokrycia:
  `tools/covcmp.py`, kontrola dokumentacji: `tools/doccheck.py`.
- `dev2@705b3f2` (po A1–A5, Z1, Z2, B1–B4, H1; zmierzone 2026-10-16 na `823c8da`, to samo drzewo):
  **1451 passed + 84 skipped** (zakres CI), **1535 passed** lokalnie, pokrycie 93,57 % (linie 95,1 %,
  gałęzie 88,6 %); dane: `results/h1/cov_dev2_705b3f2.json`.
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
| C1 | `cards/smartpi_diags-{en,fr}.md`, nowa `cards/smartpi_diags-pl.md` | EN i FR przychodzą od autora; dodajemy nasze sekcje (znane wejścia `dependencies.*`, woda `water.*`, kocioł `boiler.*`) (D4). Karta PL: tłumaczenie karty EN po zmianach, ta sama struktura i te same klucze, teksty wg słownika §4 |
| C2 | `technical_doc.md` (COOL) | bierny czas martwy; histereza lustrzana |
| H1 (zrobione) | `technical_doc.md` §4.7 (nowy) i §3.2, §4.5, §5.2, §5.5; `diagnostics.md` (`humidity`); `vtherm_smartpi.md` („Showers and cooking”); `zone_dependencies.md` (`learning_gate`); `testing.md` §4.10, §7.3 | zdarzenia wilgotności (§22) |
| każdy | `testing.md` | nowe testy, pokrycie, katalog QAS (§6.5) |

## 15. Zgodność z dokumentami procesu (`process-docs`)

| Zasada w `CLAUDE.md` / `PLAN_ROZWOJU.md` | Stan w tej pracy |
|---|---|
| Nowe commity autora tylko scaleniem `upstream/master` | Odstępstwo z decyzji 2026-10-15 (§1 p. 2) |
| Odstępstwa zgłaszamy przed kodem | Przyjęte (§1 p. 3) |
| Push/PR/scalenie za „tak” | Przyjęte; branch wybiera właściciel (§1 p. 4) |
| Gałąź integracyjna `dev` | Dla tej pracy `dev2` (D10, §1 p. 4); `CLAUDE.md` i `PLAN_ROZWOJU.md` to opisują (2026-10-16) |
| Usuwanie gałęzi po scaleniu | D23 (§1 p. 13) |
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
| D2 | Po A1: czy `_apply_zone_fit` nadal nadpisuje a/b strefy | przyjęte 2026-10-15: decyzja po pomiarze na symulatorze po A1. Dane z A1 (§19.1): własne a/b SmartPI 0,95–1,04 prawdy, dopasowanie strefy 0,99–1,03; do rozstrzygnięcia przy Z1 |
| D3 | Zmiany tylko dla COOL (C2) | przyjęte (D0) |
| D4 | Nasze sekcje (znane wejścia, woda, kocioł) w kartach diagnostycznych | przyjęte 2026-10-15: dodajemy (C1); nowa karta PL `cards/smartpi_diags-pl.md` |
| D5 | Dokumentacja EN/FR/PL razem z kodem | przyjęte 2026-10-15 |
| D6 | Cykl życia: sposób autora (B1) | przyjęte 2026-10-15 (§11.4) |
| D7 | Korekta reference governora przy wodzie | pomiar 2026-10-15: niepotrzebna, opcja D (§13.4) |
| D8 | „Drganie” capa reference governora (§13.5) | przyjęte 2026-10-15: obserwujemy w C1; ewentualna zmiana (kod autora) po pomiarze i za zgodą właściciela |
| D9 | Zapis stanu od razu po resecie strefy (`_apply_reset_request`, etap A4) | przyjęte 2026-10-15: zapis natychmiastowy, jak przy serwisie `reset_smartpi_learning` u autora |
| D10 | Gałąź integracyjna `dev2` (zmiany autora i nasze własne funkcje), osobny PR na etap; nic na `dev`, `qas`, `master` | przyjęte 2026-10-16 |
| D11 | Okna nauki wymiany z sąsiadem nie przepadają przy „guard cut” (`_dependency_learning_allowed`), §19.3 | przyjęte 2026-10-16; w A1 i A3 (ta sama zmiana) |
| D12 | Dodatkowe źródło, które przestaje pracować, anuluje trwający cykl (jak poprawka autora dla OFF), §19.5 | przyjęte 2026-10-16; w A5 |
| D13 | Optymalizacja dojścia strefy po obniżeniu: cel „dojście nie wolniejsze i mniejsze przegrzanie niż dwa osobne SmartPI”, §20 | przyjęte 2026-10-16 (cel właściciela) |
| D14 | Ustawienie „Dojście po obniżeniu”: Szybkie (V1, domyślne) / Oszczędne (jak dziś); wartość we wpisie „SmartPI defaults”, nadpisanie w formularzu strefy („Jak w SmartPI defaults” domyślnie); krótki opis każdej opcji w oknie wyboru | przyjęte 2026-10-16 (rekomendacja: 2 poziomy, wariant pośredni odrzucony) |
| D15 | Kryterium testu dla „Oszczędne”: dłuższe dojście akceptowalne (właściciel: celowo wolniej) | przyjęte 2026-10-16 |
| D16 | Pokój „fastcool-midheat, mróz” przy „Szybkie” +0,7 h: opisany wyjątek w teście | przyjęte 2026-10-16: wyjątek 0,8 h w `RECOVERY_SLOWER_H` |
| D16a | Koniec przyspieszenia (boost) głównego źródła: opóźnienie + 2 cykle (§20.6) | przyjęte 2026-10-16 |
| D17 | Poranne podbicie krzywej grzewczej (strona kotła, nie SmartPI), §20.5 | informacyjnie; ewentualnie osobna funkcja smart boilera |
| D18 | Strefa `fastcool-fastheat`, mróz, fazy 170/340 s: błąd ustalony 0,20 °C także na `dev2` (§20.6) | przyjęte 2026-10-16: osobny etap (Z2), po Z1; zrobione (§20.7) |
| D19 | Anti-windup autora liczony względem wyjścia modelu z początku cyklu (§20.7) | nie wdrożone: pomagało w Z2, ale spowalniało pokój D16 o 0,17 h; kod autora bez zmian |
| D20 | H1: SmartPI używa wilgotności z VT (`current_humidity`) automatycznie, bez ustawienia; bez czujnika nic się nie zmienia | przyjęte 2026-10-16 (właściciel) |
| D21 | H1: progi sprawdzone na danych właściciela (§22): start 5 %RH w 10 min, koniec przy 25 % wzrostu albo po 3 h, 30 min uspokojenia | przyjęte 2026-10-16 (dane właściciela jako odniesienie; scalone w PR #28) |
| D22 | H1: w czasie zdarzenia całka wstrzymana (`I:HOLD`), nie ochrona w jedną stronę (§22.1) | przyjęte 2026-10-16 (znalezisko z symulacji, scalone w PR #28 za „tak” właściciela) |
| D23 | Gałęzie etapów usuwane po scaleniu; na GitHubie usuwa właściciel (§1 p. 13) | przyjęte 2026-10-16 |

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

## 19. Faza A – wyniki i znaleziska (2026-10-16)

Wszystkie etapy A na osobnych gałęziach od `dev2`, commity lokalne (stan i SHA: `PLAN.md`
„Stan prac”). Pełne testy (zakres CI i lokalny z pokryciem) po każdym etapie; nic nie
wypchnięte.

| Etap | Zakres CI | Pełny lokalny | Pokrycie | Uwagi |
|---|---|---|---|---|
| A1 | 1342 passed | 1 failed (`test_rooms[base-slowheat-cold]`) | 93,18 %; kod forka 99,2 % / 97,7 % | porażka to temat Z1 (§20) |
| A2 | zielony | zielony | bez spadków | – |
| A3 | 1328 passed | 1 failed (`test_rooms[fastcool-midheat-cold]`) | 93,18 % | porażka to temat Z1 (§20) |
| A4 | zielony | zielony | 93,17 % | – |
| A5 | 1327 passed | 1402 passed | bez spadków | – |

Drobne spadki pokrycia w plikach autora (0,2–1,7 pkt: `ff3.py`, `diagnostics.py`, `ff_trim.py`)
wynikają z innego przebiegu symulacji, nie z nowego kodu.

### 19.1 A1 – nachylenia z surowych pomiarów (pomiar `tools/a1_measure.py`)

Jedna strefa, czujnik 0,1 °C, 48 h uczenia, potem 4 doby z obniżeniem 17 °C nocą, 3 pokoje × 3
fazy (`results/phase-a/a1_*.json`):
- nauczone a/b na końcu: `dev2` a 0,51–1,15, b 0,44–1,11; **A1 a 0,95–1,04, b 0,98–1,02**;
- regulacja (średni |uchyb| w dzień): bez zmian w pokoju typowym i wolnym, lepsza w szybkim
  (0,07–0,10 zamiast 0,13–0,16 °C);
- **koszt: dłuższa pierwsza faza uczenia** – test z czujnikiem 0,1 °C 6,1 → 14,9 h, scenariusz z
  wodą 6,9 → 28,7 h (więcej zmian poziomu potrzeba na wiarygodne nachylenie);
- wcześniejsza obawa z `PLAN_ROZWOJU.md` §9.2 p. 23 (dokładny model → gorsza regulacja) w tym
  pomiarze się nie potwierdziła.

Testy poprawione z powodu dłuższej fazy uczenia (commit `013f292`): zawór
(`test_valve_cycle_starts_report_the_applied_opening` trwa do końca fazy uczenia), woda
(`test_an_unavailable_supply_sensor_is_a_gap_lasting_after_a_day` – przerwa od 36 h), test
podstawienia (porównanie z `b` z początku ticku; pomija wywołania, gdy FF1 i wzmocnienia czekają
na wiarygodną stałą czasową).

### 19.2 A3 – przerwa w roli wykonawczej (potwierdzenie §12)

Przed poprawką: 2 h w roli dodatkowego źródła podniosły całkę z 1,0 do 11,0 w pierwszym własnym
ticku; po poprawce zostaje 1,0 (`tests/test_smartpi_regulation_gaps.py`).

### 19.3 N3/N7 – nauka `k` sąsiada i „guard cut” (decyzja D11)

Po A1 „guard cut” działa ok. 3,5× częściej (w N3 1604 wobec 459 ticków w 30–66 h), a okna nauki
`k` przy nim przepadały: sąsiad „always” nie dostawał `k` (N3, także `dev2` przy fazie 170 s),
w A3 `k` w N7 +49 %. Sama regulacja SmartPI w N3 bez pogorszenia (36–66 h: 0,10 / 0,22 °C wobec
0,10 / 0,27 °C). Poprawka D11: okna nie przepadają przy „guard cut” (wysterowanie 0 jest znane);
wynik `k` 0,00154 przy prawdzie 0,0015, także przy fazie 170 s; testy sąsiadów zielone.
Dopasowanie strefy już wcześniej nie zatrzymywało się przy „guard cut”.

### 19.4 Porównanie z dwoma osobnymi SmartPI w `test_smartpi_sim_zone_set.py`

Nasza strefa po A1/A3 działa tak samo; zmienia się punkt odniesienia (kod autora):
- A1, „base-slowheat, mróz”: osobne dochodzą szybciej (5,1 → 4,7 h), strefa 6,0 h (próg +1 h);
- A3, „fastcool-midheat, mróz”: dwa osobne SmartPI (ich a i b to 0,7 i 0,36 prawdy, częste
  „guard cut”) nie dochodzą do nastawy po 2. i 3. nocy; porównanie przegrzania traci sens.
Decyzja właściciela: nie zmieniamy progu, tylko poprawiamy logikę strefy (§20, etap Z1).

### 19.5 A5 – ten sam błąd w ścieżce dodatkowego źródła (decyzja D12)

`handler._control_as_source`, gdy źródło przestaje pracować: wyłączało urządzenie, ale nie
anulowało trwającego cyklu w fazie wyłączenia. Poprawione jak u autora (`cancel_cycle`), test
padał przed poprawką.

## 20. Dojście strefy po obniżeniu (2026-10-16, etap Z1)

Pomiar: `tools/zr_measure.py`, `tools/gas_measure.py`, tabele `tools/zr_table.py`,
`tools/m_table.py`; wyniki: `results/zone-recovery/`; wariant próbny jako łatka
`results/zone-recovery/experiment-v1.patch` (na `feat/zone-recovery`, sterowany zmienną
`ZR_VARIANT`, `ZR_NIGHT_SHARE`). 12 pokoi × (łagodnie 3 °C, mróz −8 °C) i 3 instalacje (zawór,
wylewka dwustopniowa, woda na krzywej), czujnik 0,1 °C, 3 noce (6, 8, 10 h) z 17 °C.

### 20.1 Stan wyjściowy (kod po A1)

Strefa dochodzi wolniej niż dwa osobne SmartPI w 14 z 15 przypadków (+0,1…+1,3 h), ale ma dużo
mniejsze przegrzanie (0,05–0,3 wobec 0,25–0,84 °C) i 2–5× lepszą stałą nastawę.

### 20.2 Przyczyna

Nie poranne sterowanie (od zmiany nastawy oba źródła w obu wersjach na 100 %), tylko noc: dwa
osobne SmartPI schodzą poniżej 17 °C (0,3–0,6 °C) i nadrabiają oboma źródłami (~70–100 %), więc
rano wolna podłoga jest rozgrzana. Nasza strefa trzyma 17 °C dokładnie, z kaloryferem na 60–90 %
i podłogą na 25–60 % (hamulec zakłada oddanie obciążenia przez kaloryfer). W szybkich pokojach
dodatkowo hamulec wyłącza podłogę rano o kilkadziesiąt minut za wcześnie.

### 20.3 Warianty

- **V1:** w czasie obniżenia (nastawa niższa o ≥ 0,5 °C od najwyższej z 24 h) polecenie podłogi
  co najmniej równe nocnemu obciążeniu z dopasowania strefy
  `(b·(T_sp − T_ext) + Σk·(T_sp − T_j)) / a_f`, ograniczone hamulcem.
- **V1b:** to samo, ale dopiero po wystygnięciu do nocnej nastawy i ponad hamulcem.
- **V1c:** V1 + V1b. **Pośredni:** V1 z połową nocnego obciążenia.

| wariant | spełnia cel (z 15) | uwagi |
|---|---|---|
| dziś | 1 | – |
| V1 | **14** | wyjątek: fastcool-midheat mróz 3,6 h wobec 2,9 h |
| V1b | 8 | traci zysk z ładowania podłogi w czasie stygnięcia |
| V1c | 10 z 12 pokoi | – |
| pośredni | 10 | brakuje 0,0–0,6 h w 5 przypadkach |

Przykłady V1 (dojście osobne / dziś / V1): base-slowheat mróz 4,7 / 6,0 / 4,2 h; base mróz 2,5 /
2,8 / 1,5 h; woda 6,0 / 6,7 / 3,6 h; przegrzanie zawsze mniejsze niż u osobnych. Noc średnio
cieplejsza o 0,2–0,6 °C (płytsze obniżenie).

### 20.4 Zużycie (bilans ciepła pokoju, stała sprawność kotła)

Względem dwóch osobnych SmartPI: strefa dziś −1,4…+0,5 %; pośredni −0,8…+1,6 %; V1 −0,6…+2,0 %.
V1 względem dziś: +0,1…+2,6 %.

### 20.5 Poranne podbicie krzywej grzewczej (strona kotła)

Woda na krzywej, przesunięcie +5 K do dojścia (najwyżej 3 h): osobne 6,1 → 3,9 h (+1,7 % ciepła),
V1 3,6 → 1,9 h (+2,4 % ciepła względem osobnych). Działa niezależnie od sterowania strefy, tylko z
kotłem sterowanym krzywą; nie uwzględnia spadku sprawności na gorętszej wodzie. Nie w SmartPI
(D17).

### 20.6 Wdrożenie Z1 i odporność na fazę (2026-10-16)

Kod Z1 (`feat/zone-recovery`) daje przy fazie 0 wyniki identyczne z V1 (`results/zone-recovery/zr_impl.txt`).
Sprawdzenie z fazą wywołania regulacji 170 i 340 s (`tools/zr_phase.py`, §0a.7 planu):

- `fastcool-fastheat` łagodnie: przegrzanie „Szybkie” 0,53–0,57 °C wobec 0,45 u osobnych
  (`zr_phase_all.txt`). Przyczyna (`tools/zr_morning.py`, `tools/zr_brake.py`): ciepła podłoga
  przyspiesza wzrost, a odpowiedź kaloryfera w cyklu po końcu przyspieszenia (boost) wynosi strefę
  ponad nastawę; hamulec liczy kaloryfer na jego zapotrzebowaniu, nie na 100 % z boostu.
- Warianty (`experiment-boost.patch`, `zr_fix1.txt`): D (bez boostu, gdy podłoga sama niesie
  obciążenie) – bez efektu; **E (koniec boostu: opóźnienie + 2 cykle zamiast 1)** – przegrzanie
  0,11–0,37; F (pasmo 0,4 °C) – gorsze dojście w mrozie. Wdrożone E jako
  `SOURCE_BOOST_END_CYCLES = 2` (dotyczy też „Oszczędne”).
- Z E, 3 fazy × 15 przypadków (`zr_final.txt`, `zr_phase_E.txt`): przegrzanie zawsze mniejsze niż
  u osobnych; dojście średnio 1,90 h (osobne 2,53, „Oszczędne” 2,96); wolniej tylko D16
  (≤ +0,77 h) i `fastcool-fastheat` mróz (+0,03…+0,16 h). Kryterium testu: dojście ≤ osobne
  + 0,2 h (tolerancja fazy), D16 0,8 h, przegrzanie ściśle mniejsze.
- Ciepło (`gE_*.json`): „Szybkie” wobec osobnych −0,6…+2,0 %, „Oszczędne” −1,3…+0,4 %,
  „Szybkie” wobec „Oszczędne” 0,0…+2,7 %.
- **Nowe, poza Z1:** `fastcool-fastheat` mróz przy fazach 170/340 – błąd ustalony strefy
  0,20 °C (faza 0: 0,044); to samo na `dev2` (`zr_phase2.txt`), więc istniejący problem
  (przyczyna niezbadana). Osobny temat (D18).

### 20.7 Etap Z2: przyspieszenie (boost) tylko w powrocie (2026-10-16)

Diagnoza (`tools/z2_steady.py`, `tools/z2_int.py`, `results/zone-z2/`): w `fastcool-fastheat`,
mróz, przy fazach 170/340 s całka kaloryfera stała na −0,3 przez dziesiątki godzin, choć strefa
była 0,1–0,3 °C poniżej nastawy. Każdy spadek o 0,2 °C włączał boost, który przełączał FF
kaloryfera z ~0,4 na 1,0; anti-windup autora (`update_realized_power`) porównuje moc z
zakończonego cyklu z bieżącym wyjściem modelu, więc taki skok w trakcie cyklu „wysysał” całkę
(np. −0,045 naraz). Faza 0 omija to kolejnością koniec cyklu → obliczenie.

Warianty (12 pokoi × fazy 0/170/340, `z2_sweep.txt`):
- AW względem modelu z początku cyklu (D19): pomaga, ale pokój D16 3,63 → 3,80 h (+0,93 > 0,8);
- próg uzbrojenia 0,5 °C bez „powrotu”: wolniejsze dojście (brak ponownego włączenia po wczesnym
  końcu boostu);
- **wdrożone:** powrót zaczyna się 0,5 °C poniżej nastawy (`SOURCE_BOOST_ARM_C`) i trwa do jej
  osiągnięcia; boost działa w nim jak dotąd (od 0,2 °C). 36/36 przypadków spełnia kryteria, błąd
  ustalony ≤ 0,059 °C.

Wynik (faza 0, `z2_final.txt`): zmienia się tylko `fastcool-fastheat` mróz (dojście 1,13 wobec
1,00 h, w tolerancji 0,2 h; błąd ustalony 0,030). Ciepło (`gZ2_*.json`): „Szybkie” wobec osobnych
−0,7…+2,0 %, „Oszczędne” −1,3…+0,4 %; „Oszczędne” dojście 2,97 h.

## 21. Faza B (2026-10-16)

B1–B4 według planu (`PLAN.md`, Faza B), lokalnie. Odstępstwa i znaleziska:
- B1: test `test_config_flow_save.py::test_a_save_reloads_each_entry_once_also_when_the_zone_moves`
  przepisany na technikę autora; zastępstwo `async_reload` musi oddać sterowanie
  (`asyncio.sleep(0)`) i usuwać listenery, inaczej HA iteruje po rosnącej liście listenerów
  (nieskończona pętla w teście, nie w kodzie).
- B2: przy braku wpisu „SmartPI defaults” dodanie integracji tworzy go (także gdy są wpisy
  termostatów); test `test_config_flow_paths.py` dostał wpis defaults.
- B3: wywołania QAS (`entity_id` w danych) działają (`test_a_qas_style_call_with_entity_id_in_data_still_works`).

## 22. H1: wilgotność z VT (2026-10-16)

VT (`FeatureHumidityManager`) czyta czujnik wilgotności (wskazany albo wykryty przy czujniku
temperatury) i podaje `current_humidity` termostatu. Decyzja właściciela: funkcja działa
automatycznie, gdy VT podaje wilgotność.

Dane właściciela (`history_10.csv`, surowe próbki 21.08–16.10.2026, co 2,5–4 min;
`tools/hum_events.py`), zdarzenie = wzrost ≥ 5 %RH w 10 min:

| czujnik | zdarzeń/dzień | wzrost (mediana) | do szczytu | po szczycie do 50 % / 25 % wzrostu |
|---|---|---|---|---|
| łazienka parter | 1,0 | 20 %RH | 11 min | 35 / 51 min |
| łazienka góra | 0,7 | 22 %RH | 16 min | 39 / 88 min |
| kuchnia | 0,4 | 11 %RH | 9 min | 37 / 40 min |

Próg 3 %RH daje w łazienkach prawie te same zdarzenia, w kuchni 3× więcej (szum) – zostaje 5 %RH.
Koniec „poziom wyjściowy + 3 %RH” trwa zwykle > 2 h; przyjęte: poniżej poziomu wyjściowego + 25 %
wzrostu, najpóźniej 3 h, potem 30 min uspokojenia.

Odtworzenie danych przez detektor (`tools/hum_replay.py`): łazienka parter 0,99 zdarzenia/dzień,
uczenie wstrzymane 1,7 h/dzień; łazienka góra 0,67/dzień, 1,6 h; kuchnia 0,44/dzień, 0,6 h.

### 22.1 Implementacja (gałąź `feat/humidity-events`, od `dev2` `7023334`)

- `smartpi/humidity_event.py` (`HumidityEvent`): idle → event → settling; start przy wzroście
  ≥ 5 %RH w 10 min, koniec przy poziomie bazowym + 25 % wzrostu albo po 3 h, 30 min uspokojenia;
  po nim historia od nowa. Odczyt brak/NaN/bool nic nie zaczyna.
- `algo.py`: bramka uczenia `humidity_event` (po bramkach zależności i kotła), brak uczenia
  zależności, trym FF bez okna, **całka wstrzymana** (`integrator_hold`, źródło
  `humidity_event`, tylko gdy nic innego jej nie trzyma).
- `handler.py`: `algo.update_humidity(t.current_humidity)` przy każdym sterowaniu.
- Diagnostyka: blok `humidity` (od pierwszego odczytu).

Pierwsza wersja blokowała całkę flagą `block_negative_integral` (w chłodzeniu
`block_positive_integral`). Ślad z symulacji pokazał, że to nie działa: flaga blokuje tylko
nowe narastanie w danym kierunku, a dodatnią całkę grzania pozwala „odwijać” do zera
(`I:GUARD_UNWIND`), czyli dokładnie spadek, który miał być zatrzymany. Zamienione na pełne
wstrzymanie.

Scalone: PR #28 do `dev2` (`705b3f2`), CI zielone, lokalnie 1535 passed; `humidity_event.py`
pokryty w 100 %.

### 22.2 Symulacja (`tools/h1_probe.py`, wyniki w `results/h1/`)

Łazienka a=0,03, b=0,001, czujnik 0,1 °C, 3 °C na zewnątrz; prysznic = źródło 40 °C przez
przewodność g przez 15–60 min o 7:00, wilgotność +25 %RH (narastanie 10 min, zanik τ 40 min).
Porównanie z wilgotnością i bez (a/b względem prawdziwych, średni błąd, szczyt i dołek po prysznicu):

| scenariusz | bez wilgotności | z wilgotnością |
|---|---|---|
| g=0,004, 15 min, dni 3–7 | a/b 0,98/0,98, szczyt +1,0, dołek −0,07 | to samo, dołek −0,06 |
| g=0,0008, 60 min | szczyt +0,3, dołek −0,06…−0,08 | szczyt +0,3, dołek −0,06 |
| g=0,0002, 60 min (ciepło poniżej guard_cut) | dołek −0,01, całka spada 376,9 → 374,1 | dołek 0,00, całka stoi (376,04) |
| g=0,006, 30 min, od dnia 0 (uczenie) | a/b 0,98/1,00 | a/b 0,98/0,99 |

Wniosek: w symulatorze efekt jest neutralny (±0,01 °C). Przy silnym prysznicu `guard_cut` i
strefa nieczułości już trzymają całkę, a estymator a/b nie łapie okien z prysznicem. Zysk
widać tylko w słabym cieple (całka nie schodzi). Realny zysk zależy od rzeczy, których
symulator nie ma (czujnik temperatury ogrzany parą, ciepło oddawane przez ściany). Ryzyko: brak
regresji w symulacji; jedyny koszt to 1,6–1,7 h/dzień bez uczenia w łazience.

