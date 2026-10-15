# Plan: przeniesienie usprawnień autora (SPINew) do naszej wersji

Plan dla dowolnej sesji. Kontekst, dowody i numery linii są w [`ANALIZA.md`](ANALIZA.md).
Najpierw przeczytaj jej sekcje 1–3 (zasady, gałęzie, środowisko) i 6 (punkty styku).

Numery linii dotyczą `dev@34c1440` i mogą się przesunąć. Szukaj po nazwie funkcji lub po
cytowanym fragmencie kodu.

---

## 0. Zasady wykonania

1. **Nigdy nie zmieniaj `dev`, `qas`, `master`.** Żadnego push, merge ani force-push do nich.
2. Pracuj na nowych branchach w `Shadow-230/vtherm_smartpi`. Proponowany układ (łańcuch, każda
   faza na poprzedniej, żeby właściciel mógł przyjąć fazę A bez reszty):
   - `port/a-poprawki` z `origin/dev`,
   - `port/b-cykl-zycia` z `port/a-poprawki`,
   - `port/c-gubernator` z `port/b-cykl-zycia`.
3. Nie mergujemy `SPINew` w całości. Commity autora przenosimy pojedynczo:
   `git cherry-pick -x <sha>` tam, gdzie plan tak mówi, w pozostałych miejscach ręcznie.
4. Jeden etap = jeden commit (albo cherry-pick plus commit dopasowania). Wiadomości commitów po
   angielsku, w stylu repozytorium (zdanie w trybie rozkazującym, bez prefiksów), z liniami
   przypisania zgodnymi z instrukcjami sesji.
5. Push tylko gałęzi `port/*`: `git push -u origin port/<nazwa>`. Bez PR, chyba że właściciel
   poprosi.
6. Po każdej fazie krótki raport po polsku dla właściciela: co przeniesione, wynik testów, co
   wymaga decyzji.
7. Decyzje otwarte (D1–D5) są w `ANALIZA.md`, sekcja 14. Bez zgody właściciela przyjmij
   rekomendację i zaznacz to w raporcie.

## 0a. Zasady dla testów

Dane, na których opiera się ta część, są w `ANALIZA.md`, sekcja 15.

1. Każdy etap ma blok **Testy** w trzech częściach:
   - testy autora do przeniesienia (przy cherry-picku przychodzą same, przy etapach ręcznych
     przenieś je przez `git show <sha> -- tests | git apply`),
   - nasze testy do aktualizacji,
   - nowe testy do napisania.
2. Test i zmiana kodu w tym samym commicie etapu.
3. Istniejący test zmieniaj tylko wtedy, gdy sprawdza zachowanie, które etap **celowo** zmienia.
   W commicie albo raporcie zapisz, co test sprawdzał i dlaczego to już nieaktualne. Każde inne
   niepowodzenie to regresja do naprawy w kodzie.
4. Nigdy nie wyłączaj, nie pomijaj (`skip`, `xfail`) ani nie usuwaj testu, żeby przejść etap.
5. Testy symulacji (`tests/test_smartpi_sim_*.py`) oceniaj najpierw jako zachowanie: przekroczenie
   nastawy, czas dojścia, średni błąd, nauczone a/b. Oczekiwania zmieniaj dopiero po wyjaśnieniu
   różnicy, z wartościami przed i po w raporcie.
6. Styl nowych testów jak w repozytorium: nazwa testu opisuje zachowanie (`test_<co się dzieje>`),
   docstring jednym zdaniem, `pytest` z `asyncio_mode = "auto"`, fixture z `tests/conftest.py`
   (`fake_runtime`, `fake_handler_runtime`, `hass`), symulator z `tests/sim`.
7. Po każdej fazie zaktualizuj `documentation/{en,fr,pl}/testing.md` (rozdziały „Tests by
   feature” i „Simulation scenarios”) o nowe testy.

## 1. Kolejność i zależności

| Etap | Zakres | Źródło | Zależy od | Ryzyko |
|---|---|---|---|---|
| A0 | Przygotowanie, pomiar bazowy | – | – | – |
| A1 | Nachylenia z surowych pomiarów | `69da036` | – | niskie |
| A2 | Osobne liczniki dryfu a/b | `dd62923` | – | niskie |
| A3 | Reset PI po restarcie, brak całkowania przez przerwy (+ rola drugiego źródła) | `c506522` (ręcznie) | – | niskie |
| A4 | Zapis stanu najwyżej co 5 min | `a4624e5` | – | niskie |
| A5 | OFF anuluje cykl; usunięcie setterów | `4070630`, `144ff02` | – | niskie |
| B1 | Cykl życia wpisów | `b3c8939` + nasz model | A | średnie |
| B2 | Odtwarzalny wpis domyślny, bez kroku `global` | `b00384a` (część) | B1 | niskie |
| B3 | Walidacja celu serwisów (decyzja D1) | `b00384a` (część) | B1 | niskie |
| B4 | `bus.async_fire`, martwa gałąź, manifest | `b00384a` (część) | – | niskie |
| C1 | Gubernator referencji + dopasowanie do wody/stref | `c6f9f88`, `b2b0249`, `15fc13f` | A, B | **wysokie** |
| C2 | Poprawki COOL | `8823c17`, `770c444` | C1 | niskie |
| C3 | Dokumentacja „landing” → gubernator (EN/FR/PL) | teksty z `SPINew` | C1 | niskie |

Commity `b3c8939` i `b00384a` **nie** są cherry-pickowane, bo ich logika jest przepisywana w B.
Commity z fazy A nie są powtarzane w fazie C.

---

## Etap A0 – przygotowanie

```bash
git fetch origin dev master SPINew
git switch -c port/a-poprawki origin/dev
# środowisko testowe: ANALIZA.md, sekcja 3
PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/qas \
  | tail -15 > $S/baseline.txt
```

Zapisz wynik bazowy (liczba passed, failed, skipped i lista failed). Każdy etap porównuj z
nim: nowe niepowodzenia to regresje albo testy do świadomej zmiany (wtedy opisz to w raporcie).

---

## Faza A – poprawki niezależne (branch `port/a-poprawki`)

### A1. Nachylenia uczenia z surowych pomiarów (`69da036`)

**Cel:** okna uczenia a/b liczą nachylenie z surowej temperatury, a filtr EMA zostaje tylko do
wykrywania opóźnienia (ANALIZA §8.1).

Kroki:
1. `git cherry-pick -x 69da036`. `smartpi/deadtime_estimator.py` i `smartpi/tint_filter.py`
   wejdą bez konfliktu, konflikt będzie w `algo.py`.
2. W `algo.py` są dwa wywołania `self.dt_est.update(` w `calculate` (~3987 i ~4059). W obu:
   - zostaw nasze `u_applied=self._deadtime_input(),`,
   - dodaj `tin_raw=current_temp,` zaraz po `tin=t_int_lp,`.
3. Nie zmieniaj `smartpi/calibration.py`: jego `dt_est.update(tin=current_temp, …)` już podaje
   surową temperaturę (bez `tin_raw` historia dostaje `tin`).
4. Nie zmieniaj `learning_window.py`: dalej czyta `dt_est.tin_history`, które po zmianie zawiera
   surowe pomiary. Nasze odejmowanie `known_rate` (`_without_known_inputs`) nie zależy od próbek.
5. `git cherry-pick --continue`.

Kryteria:
- `dt_est.tin_history` dostaje `current_temp`, a prywatne `_tin_lp_history` dostaje `t_int_lp`.
- Wykrywanie opóźnienia (punkt przegięcia) używa `_tin_lp_history`.

Po etapie zanotuj do raportu decyzję D2: czy `_apply_zone_fit` nadal ma nadpisywać a/b strefy.
Nic tu nie zmieniaj.

**Testy A1**
- Autora: `test_smartpi_deadtime_estimator.py` (przychodzi z cherry-pickiem).
- Do aktualizacji:
  - `test_smartpi_dependencies_algo.py` (sprawdzenie pustej `tin_history` po krawędzi bramki,
    ok. linia 161): dodaj, że `_tin_lp_history` też jest pusta;
  - `test_smartpi_sim_water.py` (`0.5 * A_REFERENCE < est.a < 1.2 * A_REFERENCE` i wynik
    `results[water_data]`) i `test_smartpi_sim_boiler_call.py` (stosunki `est.a / a`, `est.b / b`):
    uruchom na `dev` i po A1, zapisz wartości. Progi zawężaj tylko wtedy, gdy wynik jest bliżej
    prawdy.
- Nowe (`tests/test_smartpi_raw_slopes.py`):
  - `test_learning_slope_uses_raw_samples_and_removes_known_rate`: okno uczenia ze znanym
    `known_rate` i schodkowymi surowymi próbkami 0,1 °C daje nachylenie z surowych próbek minus
    średni `known_rate`;
  - `test_boiler_hold_keeps_raw_history`: przy `_deadtime_input() == 0` (kocioł bez ciepła)
    `tin_history` nadal dostaje surową temperaturę;
  - `test_gate_edge_clears_both_histories`: krawędź bramki uczenia (`_read_known_inputs`) czyści
    obie historie.
- Nowy test symulacji, dowód do decyzji D2
  (`test_smartpi_sim_baseline.py::test_own_learning_with_a_tenth_degree_sensor`): SmartPI bez
  dopasowania strefy, czujnik `resolution_c=0.1`. Porównaj nauczone `b` z prawdą symulatora; po A1
  błąd ma być wyraźnie mniejszy niż na `dev`. Zmierz najpierw na `dev` i wpisz obie liczby do
  docstringu.

### A2. Osobne liczniki dryfu a/b (`dd62923`)

1. `git cherry-pick -x dd62923` (wchodzi czysto, dotyczy tylko `smartpi/ab_estimator.py`).
2. Sprawdź zgodność wsteczną: stan z jednym `learn_seq` (np.
   `tests/qas/custom_components/smartpi_qas_helpers/fixtures/learned_room.json`) ładuje się do obu
   liczników (`load_state`: `learn_seq_a`/`learn_seq_b` z wartością domyślną `learn_seq`).
3. Nasz `_apply_zone_fit` wpisuje historie `a_meas_hist`/`b_meas_hist` bez numerów sekwencji. To
   się nie zmienia, nic nie dopasowuj.

Kryterium: przy 3+ pomiarach grzania między pomiarami stygnięcia kandydat dryfu b nie jest
odrzucany z powodu odstępu numerów.

**Testy A2**
- Autora: zmiany w `test_smartpi_ab_drift.py` (z cherry-pickiem; zastępują sprawdzenie
  `_learn_seq` z ok. linii 153).
- Do aktualizacji: brak.
- Nowe (w `test_smartpi_ab_drift.py`):
  - `test_a_legacy_state_from_the_qas_fixture_seeds_both_counters`: `est_state` z
    `tests/qas/custom_components/smartpi_qas_helpers/fixtures/learned_room.json`
    (`"learn_seq": 49`) daje `_learn_seq_a == _learn_seq_b == 49`;
  - `test_zone_fit_seed_does_not_block_drift`: po zasianiu historii przez `_apply_zone_fit`
    kolejne pomiary b z dryfem przesuwają historię mimo przeplatanych pomiarów a.

### A3. Reset PI po restarcie i przerwy w regulacji (`c506522`, ręcznie)

`c506522` nie wejdzie czysto: odwołuje się do `_reset_causal_observation_runtime()`, które
powstaje dopiero w `c6f9f88`. Przenieś logikę ręcznie w `algo.py`.

1. W `_validate_and_handle_off` (~3179), w gałęzi brakującego czujnika, zaraz po
   `self.ctl.last_i_mode = "I:FREEZE(missing_sensor)"` dodaj:
   ```python
   self._last_calculate_time = None
   ```
2. W `calculate`, w gałęzi „Guard Cut” (~3896), po `self._set_linear_output(0.0)` i przed
   `return` dodaj:
   ```python
   self._last_calculate_time = None
   ```
3. Blok `# --- 0. HVAC mode transition` w `calculate` (~3845) zamień na:
   ```python
   if hvac_mode in (VThermHvacMode_HEAT, VThermHvacMode_COOL):
       # The persisted model mode covers the first tick after a restart.
       previous_mode = self._last_hvac_mode or self.est.model_hvac_mode
       mode_changed = previous_mode is not None and str(hvac_mode) != str(previous_mode)
       if mode_changed:
           self.ctl.reset()
           self._recovery_hold_armed = False
           self._ff_trim.clear_pending()
           self._ff_trim.clear_last_transaction()
           _LOGGER.info(
               "%s - HVAC mode changed (%s → %s): PI state reset",
               self._name, previous_mode, hvac_mode
           )
       self._last_hvac_mode = hvac_mode
       model_reset = self.est.ensure_hvac_mode(hvac_mode)
       self.dt_est.ensure_hvac_mode(hvac_mode)
       if mode_changed or model_reset is True:
           self._fftrim_observer.reset_runtime()
           self._fftrim_periodic_observer.reset_runtime()
           self._fftrim_observation_mode = "stationary"
           self.reset_command_ownership("ownership_context_changed")
       if model_reset is True:
           self._ab_confidence.reset()
           self._ff_trim.reset()
           # … reszta bloku model_reset bez zmian (liczniki sesji, nowy twin, log) …
   ```
   W bloku `model_reset` usuń powielone `_fftrim_observer.reset_runtime()`,
   `_fftrim_periodic_observer.reset_runtime()`, `_fftrim_observation_mode = …` i
   `reset_command_ownership(…)`, bo robi to już wspólny warunek.
4. **Nasze rozszerzenie (ANALIZA §11).** W `calculate`, w gałęzi
   `if self._executive is not None:` (~3830), przed `return` dodaj:
   ```python
   # Own regulation is suspended: the first own tick must not integrate this period.
   self._last_calculate_time = None
   ```
   Skutek: pierwszy własny tick po `leave_source_role` ma `dt_min = 0` (`_update_time_tracking`
   traktuje go jak pierwszy przebieg; `_last_restart_reason` zostaje `"none"`, więc bez
   strażnika wznowienia).
5. Commit, np. `Reset the PI state across a restart into the other mode and skip regulation gaps`.

Kryteria:
- Restart w trybie innym niż zapisany `est.model_hvac_mode` kasuje PI przy pierwszym ticku.
- Po powrocie czujnika, po „guard cut” i po wyjściu z roli drugiego źródła pierwszy tick ma
  `dt_min == 0` i nie zwiększa integrału.

**Testy A3**
- Autora: `git show c506522 -- tests | git apply` (zmiany w `test_smartpi_antiwindup.py` i
  `test_smartpi_robustness.py`; na `dev` wchodzą czysto).
- Do aktualizacji: brak spodziewanych.
- Nowe (`tests/test_smartpi_regulation_gaps.py`):
  - `test_leaving_the_source_role_does_not_integrate_the_role_period`: SmartPI z ustalonym
    błędem; `enter_source_role("lead")`; ticki `calculate` przez 60 min (podmieniony
    `time.monotonic`); `leave_source_role()`; pierwszy własny tick: integrał zmienia się nie
    więcej niż w jednym zwykłym ticku, a `_last_calculate_time` był `None` przed tym tickiem;
  - `test_restart_into_the_other_mode_with_a_zone_fit_resets_pi_once`: stan zapisany w HEAT z
    aktywnym dopasowaniem strefy, `load_state`, pierwszy tick w COOL: `ctl` zresetowany raz,
    kolejne ticki bez resetu.

### A4. Zapis stanu najwyżej co 5 min (`a4624e5`)

1. `git cherry-pick -x a4624e5`. `state_store.py` (nowy) wejdzie, konflikt będzie w `handler.py`.
2. Rozwiąż `handler.py`:
   - importy: usuń `from homeassistant.helpers.storage import Store`, dodaj
     `from .state_store import SmartPIStateStore`, zostaw nasze
     `from homeassistant.helpers import issue_registry as ir`;
   - stałe: usuń `STORAGE_VERSION`, `STORAGE_KEY`, `LEGACY_STORAGE_KEY` (są w `state_store.py`);
     zostaw `WATER_FACTOR_KEY` i `WATER_ISSUE_PREFIX`;
   - `__init__`: `self._store: SmartPIStateStore | None = None`, usuń `_legacy_store`; tworzenie:
     `self._store = SmartPIStateStore(t.hass, safe_name)`;
   - `async_added_to_hass`: `data = await self._store.async_load()` (migrację z klucza legacy
     robi `state_store`); **zostaw** nasze `forget_dependencies_except(...)` i
     `self._apply_reset_request()`;
   - `_async_save`: `await self._store.save_now(t.prop_algorithm)` (zapis natychmiastowy);
   - usuwanie encji (~511): `t.hass.async_create_task(self._store.save_now(t.prop_algorithm))`
     w `try/except` jak u autora;
   - `control_heating` (~898): zamiast `await self._async_save()` dodaj
     `if self._store and t.prop_algorithm: self._store.schedule_save(t.prop_algorithm)`.
3. Zapis natychmiastowy (`_async_save`) zostaje w `_cancel_calibration_if_active` i we wszystkich
   `service_*`. Zalecane dopasowanie: w `_apply_reset_request` po `algo.reset_learning()` wywołaj
   zapis natychmiastowy (`t.hass.async_create_task(self._store.save_now(algo))`), żeby token
   resetu strefy nie czekał 5 min na zapis. Bez tego restart w tym oknie ponowi reset.
4. `grep -n "_store\.\|Store(" custom_components/vtherm_smartpi/handler.py`: nie może zostać
   bezpośrednie `self._store.async_save(...)`.

Kryteria:
- Pętla regulacji planuje najwyżej jeden zapis na 5 min (`schedule_save` nie przedłuża
  oczekującego).
- Serwisy i usunięcie encji zapisują od razu.
- Stan z `versatile_thermostat.smartpi.<nazwa>` jest nadal wczytywany i przenoszony.
- Nasze dane (`dependencies`, `dependency_reset_token`, `water`) są w zapisie, bo idą przez
  `algo.save_state()`.

**Testy A4**
- Autora: `test_handler_persistence.py` i zmiana w `test_smartpi_cycle_alignment.py` (z
  cherry-pickiem).
- Do aktualizacji:
  - `test_smartpi_cycle_alignment.py` (linie 321, 679: `handler._store = AsyncMock()`): zmiana
    autora na `MagicMock(save_now=AsyncMock())` przychodzi z cherry-pickiem;
  - testy podmieniające `handler._async_save` (`test_handler_attributes`,
    `test_smartpi_boiler_handler`, `test_smartpi_cycle_alignment`, `test_smartpi_guards`,
    `test_smartpi_settings`, `test_smartpi_valve_runtime_sync`, `test_smartpi_water_handler`):
    sprawdź każdy. Testy serwisów (np. `test_handler_attributes.py:79`) zostają, bo serwisy dalej
    zapisują od razu. Test, który oczekuje zapisu po `control_heating`, ma oczekiwać
    `handler._store.schedule_save`;
  - `tests/sim/runner.py:521` i `test_smartpi_sim_second_source.py:262`: usuń
    `handler._legacy_store = None`.
- Nowe (`tests/test_handler_persistence_extensions.py`):
  - `test_our_state_round_trips_through_the_state_store`: `save_now` i `async_load` zachowują
    `dependencies`, `dependency_reset_token` i `water`;
  - `test_control_passes_schedule_one_write_per_window`: kilka `control_heating` w ciągu 5 min
    daje jedno `Store.async_delay_save`;
  - `test_a_zone_reset_is_saved_at_once` (jeśli przyjęto dopasowanie z kroku 3).

### A5. OFF anuluje cykl i usunięcie setterów (`4070630`, `144ff02`)

1. `git cherry-pick -x 4070630` (czysto). W gałęzi OFF `control_heating`
   (`End of cycle (HVAC_MODE_OFF)`): gdy urządzenie już nieaktywne, a planista ma trwający cykl,
   wywołaj `cancel_cycle()`.
2. `git cherry-pick -x 144ff02` (czysto). Usuwa settery `guard_cut_active`/`guard_kick_active`
   z `SmartPI`. Sprawdź: `grep -rn "\.guard_cut_active\s*=\|\.guard_kick_active\s*=" custom_components`
   nie może znaleźć użyć poza `smartpi/guards.py`.

Kryterium: termostat przełączony na OFF w fazie wyłączenia PWM nie przełącza już przekaźników.

**Testy A5**
- Autora: `test_smartpi_handler_off.py` (z cherry-pickiem `4070630`).
- Do aktualizacji: brak (`test_smartpi_hysteresis_force.py` ustawia flagi na atrapie, nie na
  `SmartPI`).
- Nowe (w `test_smartpi_handler_off.py`):
  - `test_a_lead_switched_off_in_the_off_phase_still_tells_its_source`: lider z drugim źródłem
    przechodzi w OFF w fazie wyłączenia PWM; cykl jest anulowany, a źródło dalej dostaje sygnał
    (heartbeat) i polecenie 0.

### Zakończenie fazy A

Uruchom pełne testy, porównaj z bazą, `git push -u origin port/a-poprawki`, krótki raport.

---

## Faza B – cykl życia i ustawienia (branch `port/b-cykl-zycia` z `port/a-poprawki`)

### B1. Cykl życia wpisów (`__init__.py`, `const.py`)

**Cel:** szkielet autora (rejestracja na stałe, przeładowanie tylko dotkniętych, nic przy
odładowaniu, `async_remove_entry`) plus nasze elementy (ANALIZA §9, §10).

#### Model docelowy

Funkcje **bez zmian** z `dev`:
`_ensure_domain_data`, `_reload_smartpi_vtherms_for_target`, `_zone_mains_of`, `_saved_together`,
`_settings_to_move`, `async_migrate_entry`, `_smartpi_vtherm_unique_ids`, `_async_move_settings`.

Funkcje **do usunięcia**: `_unregister_factory`, `_unregister_services`, `_reload_smartpi_vtherms`
(przeładowanie wszystkich), stała i logika `DATA_SKIP_FULL_RELOAD`, specjalny przypadek
`SOURCE_IMPORT` w `async_setup_entry`, `_get_own_values_target_unique_ids`.

Funkcje **nowe lub zmienione**:

```python
def _register_factory(hass) -> None:
    # jak SPINew: bez flag; rejestruje, jeśli api.get_prop_algorithm(name) is None

def _register_services(hass) -> None:
    # B3 (decyzja D1); do czasu B3 zostaw ciało dev, usuń tylko flagę DATA_SERVICES_REGISTERED

async def async_setup(hass, config) -> bool:
    _register_factory(hass)
    _register_services(hass)
    return True

def _own_entries_using_defaults(hass) -> list[ConfigEntry]:
    """Wpisy termostatów, które biorą wspólne ustawienia z wpisu domyślnego."""
    return [e for e in hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_TARGET_VTHERM) and uses_defaults(entry_options(e))]

async def _reload_smartpi_vtherms_using_defaults(hass) -> None:
    # wersja z SPINew: pomija KAŻDY termostat z własnym wpisem (_get_dedicated_target_unique_ids).
    # Nazwa autora celowo zostaje, żeby jego test_init_lifecycle.py działał bez zmian;
    # termostaty z wpisem „używa domyślnych” przeładowuje _reload_affected_vtherms przez ich wpisy.

async def _reload_affected_vtherms(hass, entry) -> None:
    target = entry.data.get(CONF_TARGET_VTHERM)
    if target:
        await _reload_smartpi_vtherms_for_target(hass, target)
        return
    # Wpis domyślny. Wpisy „używające domyślnych” przeładowujemy jako wpisy: ich setup
    # przeładuje ich termostat (raz) i odtworzy sensor diagnostyczny (Record full diagnostics).
    for plugin_entry in _own_entries_using_defaults(hass):
        await hass.config_entries.async_reload(plugin_entry.entry_id)
    await _reload_smartpi_vtherms_using_defaults(hass)

async def _reload_zone_mains(hass, entry, reloading: set[str]) -> None:
    target = entry.data.get(CONF_TARGET_VTHERM)
    if not target:
        return
    # A zone's main source reads its additional source's entry (heating type) at start.
    for main_unique_id in _zone_mains_of(hass, target):
        if main_unique_id not in reloading:
            await _reload_smartpi_vtherms_for_target(hass, main_unique_id)

async def _async_update_options(hass, entry) -> None:
    data = _ensure_domain_data(hass)
    if data.pop(f"{DATA_SKIP_UPDATE}_{entry.entry_id}", False):
        return
    reloading = _saved_together(data, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)  # setup → _reload_affected_vtherms
    await _reload_zone_mains(hass, entry, reloading)

async def async_setup_entry(hass, entry) -> bool:
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    # … bez zmian: async_at_started → _async_move_settings, gdy CONF_SMART_PI_SETTINGS_TO_MOVE …
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if hass.state == CoreState.running:
        await _reload_affected_vtherms(hass, entry)
    return True

async def async_unload_entry(hass, entry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

async def async_remove_entry(hass, entry) -> None:
    """Wpis zniknął już z rejestru: termostaty przestają używać jego ustawień."""
    await _reload_affected_vtherms(hass, entry)
    await _reload_zone_mains(hass, entry, set())
```

Uwagi:
- `async_setup_entry` nie zapisuje już `data[entry.entry_id]` (nie ma liczenia „ostatniego
  wpisu”). Sprawdź `grep -rn "hass.data\[DOMAIN\]\|_ensure_domain_data" custom_components`, czy
  nic nie czyta tych kluczy.
- `_reload_affected_vtherms` przeładowuje inne wpisy z wnętrza `async_setup_entry` wpisu
  domyślnego. To bezpieczne, bo dzieje się tylko przy `CoreState.running`, ale trzeba to
  sprawdzić w HA (krok weryfikacji 3).
- `const.py`: usuń `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`; zostaw
  `DATA_SOURCE_LINK`, `DATA_SAVE_GROUP`. Popraw komentarz `DATA_DIAGNOSTIC_SENSORS` (nie ma już
  liczenia wpisów po kluczach `hass.data[DOMAIN]`).

#### Oczekiwane zachowanie (kryteria akceptacji)

| Zdarzenie (HA działa) | Przeładowane termostaty VT | Przeładowane wpisy SmartPI |
|---|---|---|
| Zapis opcji wpisu termostatu X | X raz; główne źródła strefy, dla których X jest dodatkowym źródłem, raz, jeśli nie były w tej samej grupie zapisu | X |
| Zapis formularza zapisujący kilka wpisów (strefa) | każdy termostat z zapisanych wpisów raz; główne źródło nie drugi raz | zapisane wpisy |
| Zapis opcji wpisu domyślnego | termostaty bez własnego wpisu raz; termostaty z wpisem „używa domyślnych” raz (przez przeładowanie ich wpisu) | domyślny + wpisy „używa domyślnych” |
| Utworzenie wpisu termostatu X (UI, import z formularza strefy, migracja 1.2) | tylko X | – |
| Usunięcie wpisu termostatu X | X po zniknięciu wpisu (wraca do domyślnych); główne źródła strefy X | – |
| Usunięcie wpisu domyślnego | termostaty bez własnego wpisu; termostaty „używające domyślnych” (przez ich wpisy) | wpisy „używa domyślnych” |
| Odładowanie wpisu (wyłączenie, restart) | żadne | – |
| Start HA | żadne (VT inicjuje algorytm sam) | – |
| Dowolne z powyższych | fabryka SmartPI i 4 serwisy zostają zarejestrowane | – |

Migracja 1.2, przeniesienie ustawień i powiadomienie naprawy działają jak w `dev`. Sensor
diagnostyczny przechodzi na własny wpis (logika `sensor.py` bez zmian).

#### Testy B1

Technika z testu autora (`git show origin/SPINew:tests/test_init_lifecycle.py`):
- podmień `_reload_smartpi_vtherms_for_target` i `_reload_smartpi_vtherms_using_defaults` na
  `AsyncMock` z `side_effect`, który zapisuje wywołanie i listę wpisów widocznych w tej chwili;
- `hass.config_entries.async_reload` podmień na `AsyncMock(side_effect=...)`, który **naprawdę**
  woła `async_unload_entry` i `async_setup_entry` dla danego wpisu (a nie atrapę bez efektu);
- `hass.config_entries.async_forward_entry_setups = AsyncMock()`, `hass.state = CoreState.running`.

Tylko tak widać podwójne przeładowania.

- Autora: `test_init_lifecycle.py` (przenieś plik bez zmian: `git show b3c8939 -- tests | git apply`;
  dzięki zachowanej nazwie `_reload_smartpi_vtherms_using_defaults` jego asercje pasują).
- Do aktualizacji:
  - `test_config_flow_save.py::test_a_save_reloads_each_entry_once_also_when_the_zone_moves`:
    `async_reload` ma wołać setup (technika wyżej); oczekiwanie bez zmian (`vt-floor`,
    `vt-radiator` po razie);
  - `test_config_migration.py` (ok. linia 239), `test_sensor_own_entry.py` (linie 45, 91): zamiast
    usuniętego `_reload_smartpi_vtherms` podmieniaj dwa helpery z techniki wyżej;
  - `test_services_routing.py::test_services_are_registered_once_and_removed`: zostaje tylko
    „zarejestrowane raz”, bez `DATA_SERVICES_REGISTERED` i `_unregister_services`.
- Nowe (`tests/test_init_lifecycle_zones.py`), jeden test na wiersz tabeli kryteriów:
  - `test_a_thermostat_save_reloads_it_and_the_zone_mains_once`;
  - `test_a_zone_save_reloads_each_written_thermostat_once` (grupa `DATA_SAVE_GROUP`, główne
    źródło nie drugi raz);
  - `test_a_defaults_save_reloads_entries_using_defaults_and_thermostats_without_entry_once`
    (wpis „używa domyślnych” przeładowany jako wpis, termostat z własnymi wartościami nie
    przeładowany wcale);
  - `test_creating_a_thermostat_entry_reloads_only_that_thermostat` (źródło `user` i `import`);
  - `test_removing_a_thermostat_entry_reloads_it_after_it_left_the_registry` (lista widocznych
    wpisów w chwili przeładowania nie zawiera usuwanego; przeładowane główne źródła strefy);
  - `test_removing_the_defaults_entry_reloads_its_users`;
  - `test_unloading_reloads_nothing_and_keeps_factory_and_services`;
  - `test_a_settings_move_does_not_trigger_the_listener` (`DATA_SKIP_UPDATE`, migracja 1.2).

Commit, np. `Reload only the thermostats an entry affects and keep the factory and services registered`.

### B2. Odtwarzalny wpis domyślny i usunięcie kroku `global` (`b00384a`, część)

1. `config_flow.py`, `SmartPIConfigFlow.async_step_user`: warunek
   `if not self._async_current_entries():` zamień na
   `if defaults_entry(self.hass) is None:` (z `.settings`; rozpoznaje też stary wpis domyślny bez
   `unique_id`). Reszta bez zmian: `async_set_unique_id(DOMAIN)`, `_abort_if_unique_id_configured()`,
   `async_create_entry(title="SmartPI defaults", data=_shared_defaults())`.
2. Usuń `async_step_global` (nic go nie wywołuje).
3. `translations/en.json` i `translations/fr.json`: usuń `config.step.global`.
4. Kryterium: po usunięciu wpisu domyślnego przy istniejących wpisach termostatów „Dodaj
   integrację” tworzy nowy wpis domyślny. Jego setup (B1) przeładowuje termostaty „używające
   domyślnych” i te bez własnego wpisu.

**Testy B2**
- Autora: `test_user_step_recreates_global_entry_when_only_dedicated_entries_exist` z
  `b00384a` (`tests/test_config_flow.py`). Plik zmieniany po obu stronach, ale zmiana autora
  wchodzi czysto. Oczekiwane `data` dopasuj do naszego `_shared_defaults()` (u autora jest
  `DEFAULT_OPTIONS`).
- Do aktualizacji: `test_config_flow_heating_type.py` (ok. linia 110): usuń parę
  `("config", "global")`.
- Nowe (w `tests/test_config_flow.py`):
  - `test_a_legacy_defaults_entry_without_unique_id_is_not_duplicated`: wpis domyślny bez
    `unique_id` (rozpoznawany przez `defaults_entry`) – krok `user` przechodzi do wyboru
    termostatu, nie tworzy drugiego wpisu domyślnego;
  - `test_recreated_defaults_reload_the_thermostats_that_use_them` (z techniką z B1).

### B3. Walidacja celu serwisów (`b00384a`, część; decyzja D1)

Przy rekomendacji D1 („przyjąć upstream”):
1. `_register_services` jak w `SPINew`: `_call_on_vtherms` używa tylko
   `service_helper.async_extract_entity_ids(call)`, liczy dopasowane termostaty SmartPI i gdy 0,
   rzuca `ServiceValidationError(translation_domain=DOMAIN, translation_key="no_smartpi_target")`;
   schematy `cv.make_entity_service_schema({...})` (dla `set_smartpi_learning`:
   `{vol.Required(ATTR_LEARNING_ENABLED): cv.boolean}`).
2. `services.yaml`: zamiast pola `entity_id` sekcja `target` z
   `entity: {integration: versatile_thermostat, domain: climate}` dla 4 serwisów (jak w `SPINew`).
3. `translations/en.json` (i `fr.json`): dodaj `exceptions.no_smartpi_target.message`; uzupełnij
   `services.*` nazwami i opisami z `SPINew`.
4. Kryteria:
   - wywołanie z `target` albo z `entity_id` w danych trafia do termostatu (pakiet QAS woła z
     `{"entity_id": ...}`, co `make_entity_service_schema` przyjmuje);
   - cel bez termostatu SmartPI daje błąd walidacji widoczny w UI.

Przy decyzji przeciwnej pomiń krok 1 i zostaw ciche ignorowanie, ale bez flag z B1.

**Testy B3**
- Autora: zmiany w `tests/test_services.py` z `b00384a` (wchodzą czysto).
- Do aktualizacji, `test_services_routing.py`:
  - `test_a_call_names_thermostats_by_target_and_data_and_reaches_the_handler`: identyfikatory
    przychodzą już tylko z `async_extract_entity_ids`; test ma sprawdzać, że każdy termostat z
    wyniku dostaje wywołanie raz (bez ręcznego łączenia `call.target` i `call.data`);
  - `test_without_the_climate_component_or_a_target_nothing_is_called`: teraz oczekuje
    `ServiceValidationError` z `translation_key="no_smartpi_target"`;
  - `test_thermostats_that_cannot_take_the_service_are_skipped`: zostaje (termostat TPI
    pominięty; jeśli żaden nie pasuje – błąd walidacji).
- Nowe (w `test_services_routing.py`):
  - `test_a_qas_style_call_with_entity_id_in_data_still_works`: wywołanie
    `{"entity_id": "climate.living"}` w danych, jak w `tests/qas/scenarios/qa.py` i `qb.py`;
  - `test_a_mixed_target_reaches_only_smartpi_thermostats`.

### B4. Zdarzenia, martwa gałąź, manifest (`b00384a`, część)

1. `handler.py`: wszystkie `t.hass.bus.fire(` zamień na `t.hass.bus.async_fire(` (6 miejsc), z
   zachowaniem naszych danych zdarzeń (`self._autocalib_event_data(event)` przy wymuszonej
   kalibracji).
2. `_cancel_calibration_if_active`: zamiast bloku `event = algo.autocalib.on_calibration_complete(...)`
   / `if event: …bus.fire…` zostaw samo
   `algo.autocalib.on_calibration_complete(time.time(), algo, SmartPICalibrationResult.CANCELLED)`
   (dla CANCELLED nigdy nie zwraca zdarzenia).
3. `manifest.json`: dodaj `"requirements": ["vtherm_api>=0.5.0"]` (w środowisku testowym jest
   0.6.1).

**Testy B4**
- Autora: zmiana w `tests/test_handler_attributes.py` z `b00384a` (wchodzi czysto).
- Do aktualizacji: `test_smartpi_autocalib_events.py`
  (`test_forced_calibration_event_identifies_thermostat_entity` i pozostałe): podmieniaj
  `hass.bus.async_fire` zamiast `bus.fire`; dane zdarzenia nadal z `entity_id` termostatu.
- Nowe (w `test_smartpi_autocalib_events.py`):
  `test_every_smartpi_event_is_fired_with_the_loop_safe_api`: wszystkie ścieżki zdarzeń handlera
  (serwisy, kalibracja, AutoCalib) używają `async_fire`, a `bus.fire` nie jest wołany.

### Zakończenie fazy B

Pełne testy, `git push -u origin port/b-cykl-zycia`, raport z tabelą B1 (co sprawdzone).

---

## Faza C – gubernator (branch `port/c-gubernator` z `port/b-cykl-zycia`)

### C1. Gubernator referencji (`c6f9f88`, `b2b0249`, `15fc13f`)

Kroki:
1. `git cherry-pick -x c6f9f88`. Spodziewane konflikty i rozwiązania:
   - `algo.py` importy: suma obu stron (`isfinite`, `Callable`, `evaluate_braking_release`,
     `CausalPowerTrace`, `ThermalMeasurementSource`, `project_cycle_command` oraz nasze
     `water_input`, `boiler_call`, `dependencies`, `effective_model`…);
   - `learning_window.py` sygnatura i docstring `update`: oba zestawy parametrów, czyli
     `known_rate: float = 0.0` oraz `a_learning_allowed: bool = True`,
     `a_learning_block_reason: str | None = None`;
   - `algo.py` `update_learning` → `self.learn_win.update(...)`: przekaż `known_rate=known_rate`
     **oraz** `a_learning_allowed=a_learning_block_reason is None`,
     `a_learning_block_reason=a_learning_block_reason`;
   - blok zmiany trybu (po A3): zostaw strukturę z A3, a trójkę
     `_fftrim_observer.reset_runtime()` / `_fftrim_periodic_observer.reset_runtime()` /
     `_fftrim_observation_mode = "stationary"` zastąp `self._reset_causal_observation_runtime()`;
   - `smartpi/const.py`, `smartpi/diagnostics.py`: suma (nasze klucze zależności, wody i kotła oraz
     klucze gubernatora autora; klucze `landing_*` znikają);
   - `documentation/{en,fr}/*.md`: na czas cherry-picka weź naszą wersję
     (`git checkout --ours -- documentation/`) i przepisz treść w C3. `cards/` wchodzą czysto.
2. `git cherry-pick -x b2b0249` (czysto), potem `git cherry-pick -x 15fc13f` (po `c6f9f88`
   powinien wejść czysto; ewentualny konflikt w `smartpi/const.py` lub `diagnostics.py` rozwiąż
   sumą).
3. Obowiązkowe sprawdzenia po cherry-pickach (`algo.py`). Każde musi być spełnione:
   - **C-a. Model efektywny dociera do nowych elementów.** `self.sp_mgr.filter_setpoint(...)` w
     `_manage_setpoint` dostaje `b=self.control_b`, a `ext_current_temp` w tej funkcji to
     `control_ext_temp` (przekazywane przez `calculate`). Tak samo `compute_ff3(b=self.control_b,
     ext_temp=…)`, `twin.update_with_eta(b=self.control_b, text=control_ext_temp)`,
     `_fftrim_observer.try_complete_window(b=self.control_b)` i `evaluate_periodic_window(...)`.
   - **C-b. Pomiar dla FF trim.** `_record_fftrim_thermal_measurement(...)` jest wołane z
     `ext_current_temp=control_ext_temp`; nowe `self._thermal_measurements.observe(...)` dostaje
     tę wartość.
   - **C-c. Moc w ścieżce przyczynowej.** `_record_completed_fftrim_power` woła
     `self._causal_power_trace.complete_applied_cycle(...)` z
     `realized_linear_power=clamp(float(e_eff), 0.0, 1.0) * self._water_factor`.
     `start_applied_cycle` i `update_applied_power` dostają `linear_on_percent` z
     `_set_committed_actuator_output` (już po wodzie).
   - **C-d. Nasz zapis mocy zależności.** `self.dependency_model.record_realized_power(...)` przy
     końcu cyklu nadal się wykonuje: nowe `mark_discontinuity(...)` nie może wcześniej przerwać
     funkcji.
   - **C-e. Limit wody w gubernatorze (zalecane, ANALIZA §13).** Wydziel z `_apply_soft_constraints`:
     ```python
     def _linear_output_upper(self) -> float:
         """Najwyższa moc liniowa, jaką emiter da teraz (max_on, a z danymi wody jej współczynnik)."""
         upper = self._max_on_percent if self._max_on_percent is not None else 1.0
         if self._water_hold_actuator is None and self._water_factor < 1.0:
             upper *= max(self._water_factor, WATER_NO_HEAT)
         return upper
     ```
     i użyj go: w saturacji (zachowując `self._water_too_cold`), w `calculate` przy
     `resolve_reference_governor(requested_u=min(u_cmd, self._linear_output_upper()), ...)` oraz w
     `_try_release_terminal_braking` zamiast porównania z `self._max_on_percent`
     (`raw_command > self._linear_output_upper()`).
   - **C-f. Wstrzymanie integrału przez wodę.** `WATER_INTEGRAL_HOLD` ustawia
     `ctl.integral_hold_active`, więc zwalnianie hamowania jest wtedy zablokowane. To zamierzone,
     tylko odnotuj.
   - **C-g. Rola drugiego źródła.** Termostat w roli wykonawczej nie przechodzi przez
     gubernatora. W `leave_source_role` dodaj `self.sp_mgr.reset()`. W `SPINew` `reset()` woła
     `_reset_trajectory()`, które czyści trajektorię i epizod gubernatora
     (`_reference_governor_authority.reset()`, flagi `…_authority_selected/_episode`); sprawdź, że
     tak zostało po cherry-picku.
   - **C-h. Akcja okna sąsiada** (`_apply_neighbour_window_action`) zmienia `target_temp`. Dla
     gubernatora to zmiana nastawy, czyli zachowanie zamierzone. Sprawdź tylko, że `turn_off`
     idzie ścieżką OFF.
   - **C-i. Blokada uczenia autora** używa `self.sp_mgr.setpoint_landing_active`; ta właściwość
     istnieje w nowym `setpoint.py`. Nasza bramka `_learning_gate` zostaje przed `update_learning`.
4. Weryfikacja funkcjonalna na symulatorze:
   - uruchom `tests/test_smartpi_sim_*.py` i `tests/test_smartpi_dependencies_algo.py`;
   - te, które padły w próbie (ANALIZA §12), zbadaj jako możliwe regresje zachowania (przekroczenie
     nastawy, czas dojścia, otwarcie zaworu), a nie tylko jako testy do poprawienia;
   - porównaj metryki symulatora (`tests/sim/metrics.py`) dla scenariuszy baseline, sąsiadów,
     wody i dwóch źródeł: `dev` kontra `port/c-gubernator`;
   - w raporcie podaj różnice.

**Testy C1**
- Autora (z cherry-pickami `c6f9f88`, `b2b0249`, `15fc13f`; lista w ANALIZA §15.1): nowe pliki
  gubernatora, hamowania, ścieżki mocy, pomiarów i modelu oraz zmiany w
  `test_smartpi_setpoint.py`, `test_smartpi_cycle_alignment.py`, `test_smartpi_ff_trim_observer.py`,
  `test_smartpi_heartbeat_learning.py`, `test_smartpi_deadband_output.py`,
  `test_smartpi_setpoint_change.py`, `test_smartpi_valve_runtime_sync.py`,
  `test_diagnostic_history.py`, `test_sensor.py`. Wszystkie mają przejść bez zmian. Jeśli któryś
  pada, to zwykle znak, że nasze podstawienie (woda, model efektywny) zmienia jego założenia;
  opisz to, zanim go zmienisz.
- Do aktualizacji:
  - `test_smartpi_water_algo.py::test_the_feed_forward_trim_records_the_power`: podmieniaj
    `algo._causal_power_trace.complete_applied_cycle` (zamiast `_fftrim_observer`);
  - `test_smartpi_dependencies_algo.py::test_substitution_reaches_every_control_component`:
    najpierw ustal przyczynę niepowodzenia z próby (najpewniej zmienione sygnatury
    `deadband_mgr.update`, `twin.update_with_eta` lub `compute_ff3` po `c6f9f88`; popraw lambdy
    wyciągające argumenty); potem rozszerz o nowe elementy (niżej);
  - `test_smartpi_sim_neighbours.py` (3), `test_smartpi_sim_water.py` (1),
    `test_smartpi_sim_harness.py::test_valve_cycle_starts_report_the_applied_opening` (2): wg
    zasady 0a.5;
  - `test_sensor_own_entry.py`: jeśli nadal pada po B1, sprawdź atrybuty sensora po `b2b0249`.
- Nowe:
  - w `test_substitution_reaches_every_control_component`: szpiedzy na
    `algo._thermal_measurements.observe` (`outside_temperature == model.ext_temp`) i na
    `algo.sp_mgr.resolve_reference_governor`, z oczekiwaniem C-e (`requested_u <=
    algo._linear_output_upper()`);
  - `tests/test_smartpi_governor_water.py`:
    - `test_requested_power_is_limited_to_the_water_before_the_governor` (C-e);
    - `test_braking_release_respects_the_water_limit` (C-e, `_try_release_terminal_braking`);
    - `test_the_causal_power_trace_records_power_at_the_reference_water` (C-c);
    - `test_the_dependency_model_still_records_the_cycle_power` (C-d:
      `dependency_model.record_realized_power` jest wołane przy zakończonym cyklu z `e_eff`
      w tych samych przypadkach co na `dev`; nowe `mark_discontinuity` niczego nie pomija);
  - `tests/test_smartpi_governor_source_role.py`:
    - `test_leaving_the_source_role_clears_the_governor_episode` (C-g: po `leave_source_role`
      `sp_mgr.reference_governor_authority_selected` jest `False` i nie ma epizodu);
    - `test_a_neighbour_window_preset_is_a_setpoint_change_for_the_governor` (C-h);
  - nowy scenariusz symulacji `test_smartpi_sim_water.py::test_cold_water_does_not_cycle_governor_episodes`:
    zimna woda ogranicza moc poniżej żądania; liczba wycofań epizodów z powodem
    `prediction_falsified_below_target` jest zerowa (przed C-e niezerowa – zapisz obie liczby);
  - nowy scenariusz `test_smartpi_sim_neighbours.py::test_landing_with_a_neighbour_exchange`:
    dojście do nastawy z aktywną wymianą z sąsiadem; przekroczenie nastawy nie większe niż na
    `dev`.

Commit dopasowań, np. `Fit the reference governor to the water limit and the second source role`.

### C2. Poprawki COOL (`8823c17`, `770c444`)

1. `git cherry-pick -x 8823c17`. Konflikty w wywołaniach `filter_setpoint` i
   `twin.update_with_eta`: zostaw `b=self.control_b` i `control_ext_temp`, weź argumenty
   opóźnień autora (`stop_deadtime_s` i `stop_deadtime_reliable` z
   `self.dt_est.passive_deadtime(hvac_mode)`; opóźnienia aktywne i bierne w `twin`).
2. `git cherry-pick -x 770c444`. Konflikt przy `compute_ff3(...)`: zostaw `b=self.control_b`,
   usuń `deadtime_cool_s=...`. Flaga `self._setpoint_changed_in_cycle = True` przy zmianie nastawy
   wejdzie z autora.
3. Kryteria:
   - HEAT bez zmian zachowania;
   - w COOL hamowanie i `twin` używają opóźnienia biernego;
   - histereza COOL: start przy nastawa + próg on, stop przy nastawa − próg off;
   - zmiana nastawy (także przez akcję okna sąsiada) w trakcie cyklu blokuje płynne przejęcie FF
     trim w tym cyklu.

**Testy C2**
- Autora (z cherry-pickami): `8823c17` zmienia `test_smartpi_learning_enablement.py`,
  `test_smartpi_reference_governor_wiring.py`, `test_smartpi_setpoint.py`,
  `test_smartpi_thermal_model.py`; `770c444` zmienia `test_smartpi_cycle_alignment.py`,
  `test_smartpi_feedforward.py`, `test_smartpi_hysteresis_deadtime_window.py`.
- Do aktualizacji: wywołania z `deadtime_cool_s=` są w `test_smartpi_feedforward.py` i
  `test_smartpi_setpoint.py`. Oba pliki zmienia autor (`770c444`, `c6f9f88`/`8823c17`), więc
  poprawki przychodzą z cherry-pickami; potwierdź `grep -rn "deadtime_cool_s=" tests/`.
  `tests/qas/scenarios/qd.py` czyta tylko klucz diagnostyki `model.deadtime_cool_s`, który nadal
  istnieje.
- Nowe:
  - `test_smartpi_dependencies_algo.py::test_cool_braking_uses_the_passive_deadtime_with_the_effective_model`:
    w COOL `filter_setpoint` dostaje `stop_deadtime_s` z `dt_est.passive_deadtime(COOL)` oraz
    `b == model.b` i `ext == model.ext_temp`;
  - `test_smartpi_cycle_alignment.py::test_a_neighbour_preset_change_in_cycle_blocks_the_bumpless_transfer`:
    akcja okna sąsiada `eco`/`frost` w trakcie cyklu ustawia `_setpoint_changed_in_cycle`.

### C3. Dokumentacja (decyzja D5)

1. Pliki ze wzmianką o „landing” (liczba linii):
   - `documentation/en/`: `technical_doc.md` (12), `vtherm_smartpi.md` (8), `diagnostics.md` (2);
   - `documentation/fr/`: `technical_doc.md` (6), `vtherm_smartpi.md` (4), `diagnostics.md` (1);
   - `documentation/pl/`: `technical_doc.md` (7), `vtherm_smartpi.md` (5), `diagnostics.md` (1);
   - `README.md` (1).
2. EN i FR: treść o gubernatorze weź z `git show origin/SPINew:documentation/<jęz>/<plik>`
   (sekcje zmienione w `c6f9f88`, `15fc13f`, `8823c17`). Zostaw nasze etykiety UI (zgodne z
   `translations/en.json`, np. „Use setpoint filter”, „Allow damped P near deadband edge”) i
   nasze opisy po przeglądzie kodu.
3. PL: przetłumacz te same fragmenty.
4. `diagnostics.md` (wszystkie języki): zamień atrybuty `landing_*` na atrybuty gubernatora z
   `smartpi/diagnostics.py` po C1 oraz nowe atrybuty historii (`b2b0249`).
5. Karty `cards/smartpi_diags-{en,fr}.md`: autor zaktualizował je w `c6f9f88`, więc przyszły z
   cherry-pickiem. Ewentualne dodanie naszych sekcji (zależności, woda, kocioł) tylko po decyzji
   D4.
6. `documentation/{en,fr,pl}/testing.md`: dopisz nowe testy z faz A–C (zasada 0a.7) i nowe
   scenariusze symulacji (C1).

**Testy C3**
- Nowe: brak. Sprawdź tylko, że `grep -rn -i landing documentation README.md` znajduje już wyłącznie
  wzmianki historyczne (jeśli jakieś celowo zostały).

### Zakończenie fazy C

Pełne testy oraz symulator, `git push -u origin port/c-gubernator`, raport z różnicami metryk i
listą decyzji.

---

## 2. Definicja ukończenia

- Fazy A, B i C wypchnięte jako osobne gałęzie `port/*`. `dev`, `qas`, `master` nietknięte.
- Każde kryterium z etapów spełnione albo opisane w raporcie, dlaczego nie.
- Wynik testów porównany z bazą z A0; każda różnica wyjaśniona.
- Testy autora z tabeli ANALIZA §15.1 przeniesione i przechodzą (albo opisano, dlaczego nie).
- Nasze testy z tabeli ANALIZA §15.2 zaktualizowane zgodnie z zasadą 0a.3; żaden nie jest
  wyłączony ani pominięty.
- Nowe testy z bloków „Testy” napisane; luki z ANALIZA §15.3 pokryte.
- `documentation/{en,fr,pl}/testing.md` zaktualizowane.
- Decyzje D1–D5 podjęte przez właściciela albo przyjęte rekomendacje oznaczone w raporcie.
- PR do `dev` tylko na prośbę właściciela.

## 3. Ściąga poleceń

```bash
# co zmienił autor w danym commicie
git show <sha> --stat; git show <sha> -- custom_components/vtherm_smartpi/<plik>
# czy commit wejdzie czysto na bieżący stan (na sucho, bez zmian)
git show <sha> -- custom_components | git apply --check -v
# stan pliku u autora
git show origin/SPINew:custom_components/vtherm_smartpi/<plik>
# przerwanie nieudanego cherry-picka
git cherry-pick --abort
```
