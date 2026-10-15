# Analiza: SPINew (upstream KipK `trajectoryv2`) a nasz `dev`

Dokument roboczy dla każdej sesji, która podejmuje temat przeniesienia usprawnień autora
oryginalnego repozytorium do naszej wersji. Plan wykonania jest w
[`PLAN.md`](PLAN.md). Ten plik opisuje stan, ustalenia i ich dowody.

Stan na: 2026-10-15.

---

## 1. Zasady pracy (ustalone z właścicielem repozytorium)

1. Repozytorium robocze: `Shadow-230/vtherm_smartpi` (remote `origin`).
   Oryginał: `KipK/vtherm_smartpi`, gałąź `trajectoryv2`.
2. **Nie wolno zmieniać ani nadpisywać `dev`, `qas`, `master`.** Żadnych commitów, pushy,
   force-pushy ani merge do tych gałęzi.
3. Praca i testy **tylko na nowych branchach** w `Shadow-230/vtherm_smartpi`.
   Branch roboczy tworzymy z `origin/dev`.
4. **Nie mergujemy całego `SPINew` do niczego** i nie robimy próbnych merge bez wyraźnej
   prośby właściciela. Zmiany przenosimy pojedynczo (cherry-pick lub ręcznie) według
   `PLAN.md`.
5. Bez pull requestów, chyba że właściciel o nie poprosi.
6. Komunikacja z właścicielem: po polsku, krótko i rzeczowo, bez rozciągania.

## 2. Gałęzie i punkty odniesienia

| Gałąź | SHA | Co to jest |
|---|---|---|
| `master` | `6a131df` | Wspólny przodek `dev` i `SPINew` (merge-base). Ostatni wspólny stan z oryginałem. |
| `dev` | `34c1440` | Nasza wersja (31 commitów od `master`, ostatni: merge PR #16). |
| `SPINew` | `770c444` | Kopia `KipK/vtherm_smartpi@trajectoryv2` (13 commitów od `master`). |
| `qas` | `9be2e2f` | Nasza gałąź QAS (poza zakresem tej analizy). |
| `analysis/spinew-vs-dev` | `34c1440` + te dokumenty | Branch analizy, utworzony z `dev`. |

Ponieważ `master` jest dokładnie punktem rozejścia, porównania robimy trójstronnie:
`master → SPINew` to zmiany autora, `master → dev` to nasze zmiany.

```bash
git fetch origin dev master qas SPINew
git merge-base origin/dev origin/SPINew   # 6a131df = origin/master
git log --oneline origin/master..origin/SPINew   # zmiany autora
git log --oneline --no-merges origin/master..origin/dev   # nasze zmiany
```

Jeśli `SPINew` w przyszłości ma dostać nowsze commity autora:
`git fetch https://github.com/KipK/vtherm_smartpi.git trajectoryv2` i porównać z `origin/SPINew`
(aktualizację `SPINew` uzgodnić z właścicielem).

## 3. Odtworzenie środowiska testowego

Home Assistant 2026.8.2 wymaga Pythona >= 3.14.2, a systemowy Python to 3.13. Testy potrzebują
też integracji `versatile_thermostat` (CI klonuje ją obok repozytorium).

```bash
S=<katalog-tymczasowy>
uv venv --python 3.14 $S/venv
VIRTUAL_ENV=$S/venv uv pip install -r requirements_test.txt
git clone --depth 1 https://github.com/jmcollin78/versatile_thermostat.git $S/vt
PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/qas
```

Punkt odniesienia: na `dev@f73e7d0` (przed PR #16) wynik to **1174 passed, 75 skipped**
(~5 min). Na `dev@34c1440` trzeba wynik zmierzyć ponownie (PR #16 dodał testy).

## 4. Zmiany autora (`master → SPINew`)

Kolumna „vs dev” to wynik `git show <sha> -- custom_components | git apply --check` na
`origin/dev` (sprawdzenie na sucho, bez zmian). Konflikt często wynika tylko z tego, że commit
zależy od wcześniejszego commita autora.

| # | Commit | Co robi | Pliki (`custom_components/vtherm_smartpi/…`) | vs dev |
|---|---|---|---|---|
| 1 | `c6f9f88` | Gubernator referencji (HEAT i COOL) zamiast „landing”, zwalnianie hamowania bez kasowania I/FF, przyczynowa ścieżka mocy i pomiarów dla FF trim | `algo.py`, nowe `smartpi/braking_release.py`, `causal_power_trace.py`, `causal_thermal_balance*.py`, `reference_governor*.py`, `thermal_measurement.py`, `thermal_model.py`; zmiany w `setpoint.py`, `ff_trim.py`, `controller.py`, `deadband_output.py`, `diagnostics.py`, `ff3_predictor.py`, `learning_window.py`, `thermal_twin_1r1c.py`, `smartpi/const.py` | konflikt: `algo.py`, `learning_window.py` |
| 2 | `b2b0249` | Dodatkowe atrybuty historii diagnostyki | `smartpi/diagnostic_history.py` | czysto |
| 3 | `15fc13f` | Cap gubernatora toleruje strefę martwą; falsyfikacja prognoz (wycofanie epizodu) | `smartpi/governor_falsification.py` (nowy), `reference_governor*.py`, `setpoint.py`, `diagnostics.py`, `smartpi/const.py` | konflikt (zależy od #1) |
| 4 | `4070630` | OFF w fazie wyłączenia PWM anuluje trwający cykl | `handler.py` | czysto |
| 5 | `144ff02` | Usunięcie setterów `guard_cut_active`/`guard_kick_active` (zawsze rzucały wyjątek) | `algo.py` | czysto |
| 6 | `b3c8939` | Przeładowanie tylko termostatów, których dotyczy zmiana; fabryka i serwisy rejestrowane na stałe; `async_remove_entry` | `__init__.py`, `const.py` | konflikt |
| 7 | `dd62923` | Osobne liczniki sekwencji dryfu dla a i b | `smartpi/ab_estimator.py` | czysto |
| 8 | `c506522` | Reset PI po restarcie w innym trybie; brak całkowania przez przerwę (brak czujnika, guard cut) | `algo.py` | konflikt (kontekst z #1) |
| 9 | `a4624e5` | Zapis stanu najwyżej co 5 min z pętli regulacji | `handler.py`, nowy `state_store.py` | konflikt |
| 10 | `69da036` | Nachylenia do uczenia a/b z surowych pomiarów | `algo.py`, `smartpi/deadtime_estimator.py`, `smartpi/tint_filter.py` | konflikt tylko w `algo.py` |
| 11 | `b00384a` | Walidacja celu serwisów, `async_fire`, wpis domyślny do odtworzenia, usunięty krok `global`, tłumaczenia, `requirements` w manifeście | `__init__.py`, `config_flow.py`, `handler.py`, `manifest.json`, `services.yaml`, `translations/en.json`, `translations/fr.json` | konflikt |
| 12 | `8823c17` | COOL: bierne opóźnienie przy hamowaniu i w modelu `twin` | `algo.py`, `setpoint.py`, `thermal_twin_1r1c.py` | konflikt (zależy od #1) |
| 13 | `770c444` | COOL: histereza lustrzana; flaga zmiany nastawy w cyklu; FF3 bez martwego wejścia | `algo.py`, `smartpi/controller.py`, `smartpi/ff3.py` | konflikt (kontekst) |

Opisy autora: `git log --reverse --format='### %h %s%n%b' origin/master..origin/SPINew`.

## 5. Nasze zmiany (`master → dev`)

Funkcje, których autor nie ma i które trzeba zachować:

- **Zależności strefy** (`smartpi/dependencies.py`, `dependency_inputs.py`, `dependency_model.py`,
  `effective_model.py`): znane wejścia modelu podstawiane w regulacji (model efektywny `b_eff`,
  `T_ref`), bramka uczenia, odejmowanie `known_rate` od mierzonego nachylenia.
- **Sąsiedzi** (`coupling_estimator.py`): uczenie wymiany ciepła z sąsiednimi strefami, reakcja
  na otwarte okno u sąsiada (`turn_off`/`frost`/`eco`).
- **Drugie źródło ciepła w strefie** (`source_link.py`, `source_model.py`, `split.py`,
  `zone_fit.py`, `source_settings.py`, `source_candidates.py`): termostat w roli wykonawczej
  (`enter_source_role`), wspólne dopasowanie a/b strefy.
- **Woda zasilająca i wywołanie kotła** (`smartpi/water_input.py`, `boiler_call.py`,
  `heating_inputs.py`): współczynnik wody skaluje moc, wstrzymanie integrału przy braku ciepła,
  bramka `boiler_settling`.
- **Ustawienia wspólne i „tylko termostat”** (`settings.py`, PR #16): termostat z własnym wpisem
  może czytać wspólne ustawienia z wpisu domyślnego (`CONF_SMART_PI_USE_DEFAULTS`); migracja wpisów
  do 1.2 (`async_migrate_entry`, `_async_move_settings`); formularz opcji z jednym zapisem na końcu
  (`DATA_SAVE_GROUP`).
- **Przejęcie sensora diagnostycznego** przez własny wpis termostatu (`sensor.py`, PR #14).
- Symulator (`tests/sim`), pakiet QAS (`tests/qas`), dokumentacja EN/FR/PL.

## 6. Punkty styku w `dev@34c1440` (`algo.py`)

W tych miejscach nasze funkcje wpinają się w regulację. Autor zmieniał kod wokół nich, więc przy
przenoszeniu trzeba je sprawdzić.

| Element | Funkcja (linia) | Znaczenie |
|---|---|---|
| `control_b` | właściwość (1805) | `b` modelu efektywnego; podstawiane zamiast `est.b` w FF1 (`k_ff`), FF3, `filter_setpoint`, `twin`, FF trim |
| `control_ext_temp` | `calculate` | `T_ref` modelu efektywnego zamiast `ext_current_temp` |
| `_control_estimator()`, `_control_tau_min()` | `calculate` | widok estymatora z `b_eff` dla `deadband_mgr`, tau dla `gain_scheduler` |
| `_to_actuator_power`, `_water_command`, `_to_linear_power` | 2145, 2158, 2169 | przeliczenie moc liniowa ↔ polecenie z uwzględnieniem wody |
| `_learning_power`, `_deadtime_input` | 2242, 2254 | moc do uczenia i do estymatora opóźnienia (0 przy gorącej wodzie bez wywołania) |
| `_record_completed_fftrim_power` | 1130 | moc cyklu × `_water_factor` do FF trim |
| Saturacja z limitem wody | `_apply_soft_constraints` (3771–3776) | `upper *= max(water_factor, WATER_NO_HEAT)` |
| Rola drugiego źródła | `calculate` (3830) | wczesny `return` z poleceniem lidera, przed `_update_time_tracking` |
| `_read_known_inputs`, `_apply_neighbour_window_action` | `calculate` | odczyt zależności, bramka uczenia, akcja okna sąsiada |
| `learn_win.update(..., known_rate=...)` | `update_learning` (910) | odejmowanie wpływu znanych wejść |
| `dt_est.update(..., u_applied=self._deadtime_input())` | `calculate` (3989, 4061) | estymator opóźnienia |
| `WATER_INTEGRAL_HOLD`, `_ff_trim.freeze("boiler_not_heating")` | `calculate` | wstrzymanie integrału i FF trim bez ciepła |

## 7. Porównanie logiki w punktach styku

| Obszar | SPINew | dev | Ocena |
|---|---|---|---|
| Model w regulacji | Gubernator, hamowanie, `twin`, FF3 dostają model przez te same wywołania co w `master` | Podstawiamy model efektywny w tych wywołaniach | Zgodne: gubernator dostanie `control_b` i `control_ext_temp` przez `filter_setpoint` (potwierdzone w próbnym merge, sekcja 12) |
| Moc i woda | Gubernator pracuje w domenie mocy liniowej 0..1 | Woda ogranicza dostępną moc liniową do `water_factor` | Spójne co do jednostek; ryzyko tylko pozorne „wiążące” capy przy zimnej wodzie (sekcja 13) |
| Uczenie | Blokada uczenia A przy zmianie nastawy i lądowaniu; nachylenia z surowych pomiarów | Bramka zależności i kotła; `known_rate` | Uzupełniają się; ta sama funkcja `learn_win.update` musi przyjąć oba zestawy parametrów |
| Integrał | Reset PI po restarcie w innym trybie; brak całkowania przez przerwę | Wstrzymanie integrału przy braku ciepła z wody | Bez kolizji |
| Zapis stanu | `state_store.py`, najwyżej co 5 min | Nasze dodatkowe dane są w tym samym `save_state` | Zgodne |
| Cykl życia i ustawienia | Patrz sekcja 10 | Patrz sekcja 10 | **Największy konflikt logiki** |
| OFF, COOL | Anulowanie cyklu w OFF; COOL lustrzany do HEAT | Woda i kocioł tylko w HEAT | Niezależne |
| Dokumentacja | „Landing” zastąpiony gubernatorem | EN/FR/PL opisują „landing” | Do przepisania (sekcja 14, D5) |

## 8. Co autor robi lepiej (z dowodami)

W każdym punkcie `dev` zachowuje się jak `master`, a `SPINew` ma poprawkę.

1. **Nachylenia do uczenia a/b z surowych pomiarów (`69da036`).** W `dev` okna uczenia biorą
   próbki z `dt_est.tin_history`, do którego trafia temperatura po filtrze EMA (`tin=t_int_lp`).
   Odporny estymator nachylenia liczy różne skoki wartości, a filtr robi z jednego skoku czujnika
   wiele, więc wolne nachylenia są akceptowane za wcześnie i zaniżone. Nasz kod opisuje ten objaw:
   docstring `_apply_zone_fit` mówi, że własne okna SmartPI „mierzą wolne stygnięcie mniej więcej o
   połowę za wolno” przy czujnikach 0,1 °C. Autor usuwa przyczynę.
2. **Gubernator referencji zamiast „landing” (`c6f9f88`, `15fc13f`).** Działa w HEAT i COOL
   (landing tylko HEAT), toleruje strefę martwą za celem, sprawdza swoje prognozy i wycofuje się
   po powtarzających się chybieniach (`prediction_falsified_below_target`), zwalnia hamowanie bez
   kasowania integrału i FF.
3. **Cykl życia wpisów (`b3c8939`).** Szczegóły i problemy `dev` w sekcji 10.
4. **Zapis stanu (`a4624e5`).** `dev` zapisuje stan przy każdym `control_heating` (handler,
   linia ~898), czyli przy każdym przeliczeniu (około raz na minutę na termostat). Autor: zapis
   odroczony najwyżej co 5 min (`Store.async_delay_save`, opróżniany przy zamykaniu HA) i zapis
   natychmiastowy przy serwisach i usuwaniu. Mniej zapisów na kartę SD lub flash.
5. **Przerwy w regulacji (`c506522`).** Przy braku czujnika (`_validate_and_handle_off`, ~3179) i
   „guard cut” (`calculate`, ~3896) `dev` nie zeruje `_last_calculate_time`, więc pierwszy tick po
   przerwie całkuje błąd za cały czas przerwy (`_update_time_tracking` przycina `dt` do 1 cyklu
   dopiero, gdy przerwa przekracza 10 cykli). Autor zeruje czas jak przy OFF. Dodatkowo po
   restarcie w innym trybie (HEAT/COOL) PI jest kasowany na podstawie zapisanego
   `est.model_hvac_mode`.
6. **Dryf a i b (`dd62923`).** W `dev` jeden licznik `_learn_seq` dla obu kanałów. Detektor dryfu
   odrzuca kandydatów odległych o więcej niż 3 numery, więc 3 lub więcej pomiarów grzania między
   dwoma pomiarami stygnięcia blokuje korektę prawdziwego dryfu b (typowe zimą).
7. **Konfiguracja i serwisy (`b00384a`).**
   - Wpis domyślny da się odtworzyć po usunięciu, nawet gdy są wpisy termostatów. W `dev`
     `async_step_user` tworzy go tylko, gdy nie ma żadnych wpisów (`if not
     self._async_current_entries()`), a nasze termostaty „używające domyślnych” czytają z niego
     wspólne ustawienia.
   - Usunięty martwy krok `async_step_global`.
   - Serwisy mają standardowy `target` (tylko klimaty VT) i zgłaszają błąd walidacji, gdy żaden
     cel nie jest termostatem SmartPI. W `dev` cicho nic nie robią.
   - Zdarzenia przez `bus.async_fire` (w `dev` jeszcze 6 wywołań `bus.fire` w `handler.py`).
   - `manifest.json` deklaruje `vtherm_api>=0.5.0`.
8. **COOL (`8823c17`, `770c444`).** Bierne opóźnienie przy hamowaniu i w `twin`, histereza
   lustrzana do HEAT. Ważne tylko przy chłodzeniu.
9. **Drobne.** Zmiana nastawy w trakcie cyklu ustawia flagę i FF trim nie robi wtedy płynnego
   przejęcia (`770c444`); OFF w fazie wyłączenia PWM anuluje trwający cykl (`4070630`; w `dev`
   cykl jest anulowany tylko przy wymuszonym przeliczeniu, `handler.py` ~748); usunięte settery,
   które zawsze rzucały wyjątek (`144ff02`); atrybuty historii (`b2b0249`).

## 9. Czego autor nie ma (zachować bezwzględnie)

Wszystko z sekcji 5, a w szczególności w cyklu życia:

- `_get_own_values_target_unique_ids` i `_reload_smartpi_vtherms_using_defaults`, które wiedzą o
  `uses_defaults` (termostat z własnym wpisem może czytać wspólne ustawienia z domyślnych),
- przeładowanie własnych wpisów korzystających z domyślnych przy zmianie domyślnych (sensor
  diagnostyczny dostaje nowe „Record full diagnostics”),
- `_zone_mains_of` i `_saved_together` (główne źródła strefy, grupa jednego zapisu),
- `DATA_SKIP_UPDATE` (wpis zmieniany przez sam SmartPI nie wyzwala listenera),
- migracja 1.2: `async_migrate_entry`, `_settings_to_move`, `_async_move_settings`,
  `ISSUE_THERMOSTAT_ENTRIES_CREATED`,
- przejęcie sensora diagnostycznego przez własny wpis (`sensor.py`, `DATA_DIAGNOSTIC_SENSORS`).

## 10. Cykl życia i ustawienia – szczegóły

### 10.1 Jak działa `dev` (`__init__.py@34c1440`)

- `async_setup` i `async_setup_entry` rejestrują fabrykę i serwisy (flagi
  `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED` w `hass.data[DOMAIN]`).
- `async_setup_entry` przy działającym HA: jeśli nie ustawiono `skip_full_reload_<id>`, to dla
  wpisu z importu (`SOURCE_IMPORT`) przeładowuje tylko jego termostat, w pozostałych przypadkach
  **wszystkie** termostaty SmartPI (`_reload_smartpi_vtherms`).
- `async_unload_entry`: usuwa id wpisu z `hass.data[DOMAIN]`; jeśli nie zostały inne klucze
  poza wyjątkami (`DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`, `DATA_SOURCE_LINK`,
  `DATA_SAVE_GROUP`), wyrejestrowuje fabrykę i serwisy; **zawsze przeładowuje wszystkie
  termostaty SmartPI**.
- Brak `async_remove_entry`.
- `_async_update_options` (listener zapisu opcji): pomija wpis z `DATA_SKIP_UPDATE`; ustawia
  `skip_full_reload_<id>`; przeładowuje wpis; potem:
  - wpis termostatu: przeładowuje jego termostat i główne źródła strefy (bez tych z tej samej
    grupy zapisu),
  - wpis domyślny: przeładowuje wpisy termostatów „używających domyślnych” (z
    `skip_full_reload`), potem `_reload_smartpi_vtherms_using_defaults`.

### 10.2 Problemy `dev` wynikające z tego przepływu

1. **Każdy zapis opcji przeładowuje wszystkie termostaty SmartPI.** Przeładowanie wpisu wywołuje
   `async_unload_entry`, a ono `_reload_smartpi_vtherms`. Termostat docelowy jest przeładowany
   dwa razy (w unload i w listenerze). Przy zmianie domyślnych z N wpisami „używającymi
   domyślnych” jest N+1 pełnych przeładowań. Test `test_config_flow_save` tego nie wykrywa, bo
   podmienia `hass.config_entries.async_reload` na atrapę.
2. **Usunięty wpis działa do restartu.** HA przy usuwaniu najpierw odładowuje wpis (wtedy `dev`
   przeładowuje termostaty), a dopiero potem usuwa go z listy wpisów. Termostat przeładowany w
   unload czyta jeszcze usuwany wpis (`settings.effective_settings` przegląda
   `async_entries(DOMAIN)`) i potem nikt go nie przeładowuje.
3. **Kruche liczenie „ostatniego wpisu”.** O wyrejestrowaniu fabryki decyduje lista kluczy w
   `hass.data[DOMAIN]`; każdy nowy klucz trzeba dopisać do wyjątków (już dopisano
   `DATA_SOURCE_LINK` i `DATA_SAVE_GROUP`). Klucze `skip_full_reload_*` przypadkiem blokują
   wyrejestrowanie podczas przeładowania.
4. **Wpisu domyślnego nie da się odtworzyć**, gdy istnieją wpisy termostatów (sekcja 8, pkt 7).

### 10.3 Jak działa `SPINew`

- Fabryka i serwisy rejestrowane raz w `async_setup`, nigdy wyrejestrowywane.
- `async_setup_entry` przy działającym HA wywołuje `_reload_affected_vtherms(entry)`: dla wpisu
  termostatu tylko ten termostat, dla wpisu domyślnego termostaty **bez własnego wpisu**.
- `async_unload_entry` tylko odładowuje platformy.
- `async_remove_entry` wywołuje `_reload_affected_vtherms` po zniknięciu wpisu z rejestru.
- Listener opcji tylko przeładowuje wpis (jego setup przeładowuje dotknięte termostaty, każdy
  raz).

### 10.4 Dlaczego nie można wziąć `SPINew` wprost

Upstream przy zmianie domyślnych pomija **każdy** termostat z własnym wpisem. U nas termostat z
własnym wpisem i `CONF_SMART_PI_USE_DEFAULTS=True` czyta wspólne ustawienia z domyślnych, więc
zostałby z nieaktualnymi ustawieniami. Upstream nie ma też stref, grup zapisu, migracji 1.2 ani
przejęcia sensora.

### 10.5 Model docelowy

Szkielet upstream (sekcja 10.3) z naszymi elementami (sekcja 9). Projekt funkcji, tabela
zdarzeń i oczekiwanych przeładowań: `PLAN.md`, etap B1.

## 11. Ustalenie własne: przerwa w roli drugiego źródła

W `calculate` (linia ~3830) termostat w roli drugiego źródła wraca wcześnie z poleceniem lidera,
**przed** `_update_time_tracking` (~3911). `_last_calculate_time` zostaje z ostatniego własnego
ticku. Po wyjściu z roli (`leave_source_role`) pierwszy własny tick liczy `dt` za cały czas pracy
jako źródło (przycięte do 1 cyklu tylko, gdy to ponad 10 cykli) i całkuje błąd za ten czas. To ta
sama klasa problemu, którą autor poprawił w `c506522`. Ustalenie z lektury kodu, niesprawdzone w
działaniu. Poprawka w `PLAN.md`, etap A3.

## 12. Wcześniejsza próba merge (informacyjnie, odrzucona)

Na życzenie właściciela próby merge zostały przerwane i usunięte. Zostają jako dane:

- Merge całego `SPINew` do `dev@f73e7d0` (przed PR #16) dał konflikty w: `__init__.py`,
  `algo.py`, `const.py`, `handler.py`, `smartpi/learning_window.py`, `translations/en.json`,
  `documentation/{en,fr}/{technical_doc,vtherm_smartpi}.md`. Na `dev@34c1440` dochodzi
  `config_flow.py`.
- W `algo.py` były tylko 2 konflikty: import `typing` (`Callable`) i parametry
  `learn_win.update` (`known_rate` kontra `a_learning_allowed`). Podstawienia `b=self.control_b`
  i `control_ext_temp` przeszły automatycznie, a `complete_applied_cycle` z mnożeniem przez wodę
  trafił do `_causal_power_trace`.
- Po ręcznym rozwiązaniu: 1468 passed, 10 failed. Funkcjonalnie istotne (do zbadania przy etapie
  C1): `test_smartpi_dependencies_algo::test_substitution_reaches_every_control_component`,
  `test_smartpi_sim_neighbours` (3 testy), `test_smartpi_sim_water::test_an_unavailable_supply_sensor_is_a_gap_lasting_after_a_day`,
  `test_smartpi_sim_harness::test_valve_cycle_starts_report_the_applied_opening` (2),
  `test_sensor_own_entry::test_a_thermostat_s_own_entry_takes_over_its_diagnostic_sensor`.
  Pozostałe 2 to podmiana metod w testach (`complete_applied_cycle`, `bus.fire`).

## 13. Korekta wcześniejszej oceny: woda a gubernator

Wcześniej uznałem, że gubernator „przeszacuje grzanie przy zimnej wodzie i zahamuje za
wcześnie”. Po lekturze `reference_governor.py` to nieprecyzyjne:

- Gubernator liczy w domenie mocy liniowej (0..1) z tym samym `a`, co nasz kod (moc przy wodzie
  odniesienia). `u_ref` to `_committed_on_percent`, a `next_cycle_u_ref` to `_on_percent`; obie
  wartości już uwzględniają wodę.
- Cap gubernatora jest górną granicą; nasza saturacja w `_apply_soft_constraints` bierze potem
  minimum z limitem wody. Gdy cap jest poniżej limitu wody, prognoza dla capa jest trafna.
- Ryzyko zostaje tylko wtedy, gdy żądanie (`requested_u`) przekracza limit wody: gubernator może
  uznać cap za wiążący, choć wiąże woda. Wtedy obietnica capa się nie spełnia, falsyfikacja
  wycofuje epizod i sterowanie wraca do zwykłego PI. To łagodna degradacja, ale może cyklicznie
  uzbrajać i wycofywać epizody. Zalecenie: przekazywać gubernatorowi żądanie przycięte do limitu
  wody (`PLAN.md`, etap C1, krok C-e).

## 14. Otwarte decyzje (dla właściciela)

| Id | Decyzja | Rekomendacja |
|---|---|---|
| D1 | Serwisy: walidacja celu i błąd, gdy brak termostatu SmartPI (upstream), czy ciche ignorowanie (dev) | Wziąć upstream. Wywołania z `entity_id` w danych (np. pakiet QAS) nadal działają, bo `make_entity_service_schema` je przyjmuje. |
| D2 | Po przeniesieniu surowych nachyleń (A1): czy `_apply_zone_fit` dalej ma nadpisywać a/b strefy | Na razie bez zmian; ocenić na symulatorze po A1 |
| D3 | Czy przenosić zmiany tylko dla COOL (C2) | Tak, są małe, ale po C1 |
| D4 | Karta diagnostyczna `cards/smartpi_diags-en.md` czyta atrybuty `landing_*`, które znikają po C1 (autor jej nie zaktualizował) | Zaktualizować po C1 albo zostawić autorowi |
| D5 | Dokumentacja: przepisać „landing” na gubernator w EN/FR/PL | Tak, w C3 (linie ze wzmianką: EN 22, FR 11, PL 13, `README.md` 1) |
