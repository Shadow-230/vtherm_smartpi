# Plan: przeniesienie zmian autora (`SPINew`) do naszej wersji

Plan dla dowolnej sesji. Kontekst, dowody, słownik nazw i decyzje: [`ANALIZA.md`](ANALIZA.md).
Najpierw przeczytaj w niej §1 (zasady), §2 (gałęzie), §3 (środowisko), §4 (słownik), §7 (punkty
styku) i §16 (decyzje). Nazwy w kodzie, commitach i dokumentacji tylko ze słownika (§4).

Numery linii dotyczą `dev@34c1440`; szukaj po nazwie funkcji albo po cytowanym kodzie.

Stan na: 2026-10-16.

---

## Stan prac (dla kolejnej sesji)

**Gałąź integracyjna: `dev2`** (start: `dev@34c1440`; teraz `705b3f2`, w forku). Każdy etap na własnej
gałęzi od `dev2`, **osobny PR na etap** do `dev2`. Na `dev`, `qas`, `master` nic nie wypychamy (decyzja
D10). Na `dev2` trafiają także nasze własne funkcje (np. Z1, Z2, H1), nie tylko zmiany autora. Po scaleniu gałąź etapu jest usuwana (D23): gdy właściciel potwierdził PR i scalenie, a PR jest
scalony, sesja sama usuwa gałąź lokalnie (gałąź i worktree) i na GitHubie; przed usunięciem SHA
gałęzi = głowica PR i zawiera się w `dev2`. Gdy GitHub odrzuci usunięcie (np. HTTP 403 z proxy
sesji w chmurze), sesja mówi o tym właścicielowi. Wzorzec testów `dev2@705b3f2`:
1451 passed + 84 skipped (CI), 1535 passed lokalnie, pokrycie 93,57 % (`ANALIZA.md` §3).

| Etap | Gałąź | PR do `dev2` | Stan |
|---|---|---|---|
| A1–A5 | `feat/spinew-a1-raw-slopes` … `fix/spinew-a5-off-cycle` | #17–#21 | scalone 2026-10-16 |
| Z1 | `feat/zone-recovery` | #23 | scalone 2026-10-16 |
| Z2 | `fix/zone-aw-reference` | #24 | scalone 2026-10-16 |
| B1 | `feat/spinew-b1-lifecycle` | #25 | scalone 2026-10-16 |
| B2 | `feat/spinew-b2-defaults-entry` | #26 | scalone 2026-10-16 |
| B3 | `feat/spinew-b3-service-targets` | #27 | scalone 2026-10-16 |
| B4 | `fix/spinew-b4-events` | #22 | scalone 2026-10-16 |
| H1 | `feat/humidity-events` | #28 | scalone 2026-10-16 |

Konflikty przy scalaniu były tylko w `CHANGELOG.md` i `testing.md` §4 (wpisy obu stron zostały,
podwójny wpis D11 usunięty), w teście `test_smartpi_dependencies_handler.py` (dwie funkcje
dopisane na końcu) i w `config.step.global` (`en.json`/`fr.json`: krok usunięty przez B2).
Narzędzie: `tools/union_resolve.py`.

Wszystkie gałęzie z tabeli są scalone i usunięte (lokalnie i na GitHubie, 2026-10-16).

**Następny krok:** faza C (reference governor, poprawki COOL) – dopiero po potwierdzeniu właściciela.
Przed C1: `git fetch origin`, gałąź od aktualnego `dev2`; przy C1 sprawdzić współpracę reference
governora z H1 (wstrzymana całka w czasie zdarzenia wilgotności): sprawdzenie C-j i jego testy.

Odtworzenie środowiska i pomiarów: `ANALIZA.md` §3, `tools/` (skrypty), `results/` (wyniki).

## 0. Zasady wykonania

1. Przed kodem każdego etapu krótki plan (co, gdzie, testy, dokumentacja) do akceptacji
   właściciela. Rekomendacje i odstępstwa tylko po jego zgodzie.
2. **Branch wybiera właściciel.** Od 2026-10-16: gałąź robocza od aktualnego `dev2`
   (`git fetch origin` przed pracą), commity lokalne, osobny PR na etap do `dev2`.
3. Push, PR i scalenie tylko za „tak” właściciela. Na `dev`, `qas`, `master` nic nie wypychamy;
   `qas` na razie nie dotykamy. Do `KipK/vtherm_smartpi` nic.
4. Zmiany autora przenosimy pojedynczo: `git cherry-pick -x <sha>` tam, gdzie plan tak mówi,
   w pozostałych miejscach ręcznie. Bez merge całego `SPINew`.
5. Autor commitów: `git config user.name "Shadow-230"` i
   `git config user.email "79769804+Shadow-230@users.noreply.github.com"`; w treści linia
   `Co-Authored-By` Claude. Wiadomości po angielsku, w stylu repozytorium (zdanie w trybie
   rozkazującym, bez prefiksów).
6. Każdy etap w swoim commicie zawiera: kod, testy, dokumentację EN/FR/PL (§0b) i wpis
   CHANGELOG w `## [Unreleased]`.
7. Po każdym etapie krótki raport po polsku: co zrobione, wynik testów i pokrycia, decyzje do
   podjęcia, następny krok w jednym zdaniu.
8. Decyzje otwarte: `ANALIZA.md` §16. Przyjmujemy tylko decyzje oznaczone „przyjęte”.

## 0a. Zasady dla testów

1. Każdy etap ma blok **Testy**: testy autora do przeniesienia, nasze testy do aktualizacji,
   nowe testy.
2. Istniejący test zmieniamy tylko wtedy, gdy sprawdza zachowanie, które etap celowo zmienia.
   W commicie albo raporcie: co test sprawdzał i dlaczego to już nieaktualne. Każde inne
   niepowodzenie to regresja do naprawy w kodzie.
3. Nigdy nie wyłączamy, nie pomijamy (`skip`, `xfail`) ani nie usuwamy testu, żeby przejść etap.
4. Przed PR uruchamiamy zakres CI i pełny zakres lokalny (`SMARTPI_SIM_LOCAL=1
   SMARTPI_QAS_LOCAL=1`, `ANALIZA.md` §3) i porównujemy liczby z aktualnym `dev2` (wzorzec:
   „Stan prac” wyżej i `ANALIZA.md` §3).
5. **Pokrycie** (z gałęziami): kod forka ≥ 95 % linii i ≥ 90 % gałęzi; pliki autora bez spadku.
   Każda niepokryta linia lub gałąź forka: martwy kod (usunąć osobnym commitem), przypadek do
   testu (dopisać) albo tylko na prawdziwym HA (nazwać scenariusz QAS).
6. **Test różnicowy** „bez konfiguracji nic się nie zmienia” (pusta konfiguracja i wyłączony
   punkt podstawienia dają identyczny wynik) musi przechodzić po każdym etapie.
7. **Kryteria symulacji nie mogą być kruche:** kilka wariantów (np. `control_phase_s` 0, 170,
   340 s) albo szeroki margines. Testy symulacji oceniamy najpierw jako zachowanie
   (przeregulowanie, czas dojścia, średni uchyb, nauczone a/b); oczekiwania zmieniamy dopiero po
   wyjaśnieniu różnicy, z wartościami przed i po w raporcie. Czas: scenariusze CI razem do ok.
   60 s; długie tylko lokalnie.
8. Styl jak w repozytorium: `tests/test_smartpi_*.py`, nazwa testu opisuje zachowanie, docstring
   jednym zdaniem, atrapy z `tests/fakes/`, symulator z `tests/sim`, `asyncio_mode = auto`,
   bez wewnętrznych klas VTherm w testach CI.
9. Wszystkie testy i ich opis w `testing.md` (EN, FR, PL); katalog QAS:
   `python tests/qas/run.py --list --markdown` wklejony do `testing.md` §6.5.

## 0b. Dokumentacja (każdy etap)

1. EN jest źródłem; FR i PL to tłumaczenia w tym samym commicie. Prosty język, terminy ze
   słownika (`ANALIZA.md` §4), wyjaśnione przy pierwszym użyciu.
2. Gdy etap zmienia działanie opisane w dokumencie autora, poprawiamy opis w miejscu.
3. Tłumaczenia interfejsu: `translations/en.json` **i** `fr.json` (decyzja 2026-10-15).
4. CHANGELOG: wpis w `## [Unreleased]` w commicie zmiany.
5. Kontrole przed PR:
   - `grep -rni "smart.\?boiler" custom_components tests documentation README*.md` nic nie zwraca
     (także niewydana część `CHANGELOG.md`);
   - liczba i kolejność nagłówków równe w EN, FR i PL; każdy odnośnik i `#kotwica` istnieje
     (kotwica GitHuba: małe litery, bez znaków innych niż litera, cyfra, spacja, `-`, `_`; spacje
     na `-`; powtórzony nagłówek `-1`, `-2`);
   - każdy blok Mermaid się parsuje (np. Node z `mermaid` i `jsdom`, `mermaid.parse()`).
6. Co zmienić w którym dokumencie: tabela w `ANALIZA.md` §14 i bloki „Dokumentacja” niżej.

## 1. Kolejność i zależności

### Faza A – poprawki niezależne (dowolna kolejność)

| Etap | Co | Źródło | Ryzyko |
|---|---|---|---|
| A0 | Przygotowanie: liczby testów (zakres CI i pełny lokalny) i pokrycie na `dev` jako punkt odniesienia | – | – |
| A1 | Uczenie a/b z surowych pomiarów; filtr EMA tylko do wykrywania czasu martwego. Po etapie pomiar na symulatorze do decyzji D2 (`_apply_zone_fit`) | `69da036` | niskie |
| A2 | Osobne liczniki dryfu dla a i b; stary stan z jednym `learn_seq` dalej się wczytuje | `dd62923` | niskie |
| A3 | Reset PI po restarcie w innym trybie; brak całkowania przez przerwę (brak czujnika, guard cut). Nasze rozszerzenie: ta sama poprawka po wyjściu z roli wykonawczej | `c506522` (ręcznie) | niskie |
| A4 | Zapis stanu najwyżej co 5 min (`state_store.py`); serwisy, usunięcie wpisu i reset strefy (D9) zapisują od razu | `a4624e5` | niskie |
| A5 | OFF w fazie wyłączenia PWM anuluje cykl; usunięcie setterów, które zawsze rzucały wyjątek | `4070630`, `144ff02` | niskie |

### Faza B – cykl życia i ustawienia

| Etap | Co | Źródło | Zależy od | Ryzyko |
|---|---|---|---|---|
| B1 | Cykl życia wpisów w sposobie autora (D6): fabryka i serwisy rejestrowane na stałe, przeładowanie tylko dotkniętych termostatów, `async_remove_entry`. Nasze elementy zostają (strefa, grupa jednego zapisu, migracja 1.2, czujnik diagnostyczny) | `b3c8939` + nasze | – | średnie |
| B2 | Wpis „SmartPI defaults” da się odtworzyć po usunięciu; usunięty martwy krok `global` | `b00384a` (część) | B1 | niskie |
| B3 | Walidacja celu serwisów w wersji autora (D1): błąd, gdy cel nie jest termostatem SmartPI | `b00384a` (część) | B1 | niskie |
| B4 | `bus.async_fire`, usunięta martwa gałąź w anulowaniu kalibracji, `requirements` w manifeście | `b00384a` (część) | – | niskie |

### Faza C – reference governor

| Etap | Co | Źródło | Zależy od | Ryzyko |
|---|---|---|---|---|
| C1 | Reference governor zamiast lądowania, falsyfikacja prognozy, atrybuty historii. Kluczowe jest sprawdzenie, czy nasze podstawienia (`b_eff`, `T_ref`, woda) docierają do nowych elementów. Bez korekty przy wodzie (D7); „drganie” capa mierzymy (D8). Karty diagnostyczne EN/FR z naszymi sekcjami i nowa karta PL (D4) | `c6f9f88`, `b2b0249`, `15fc13f` | A, B | **wysokie** |
| C2 | COOL: bierny czas martwy przy hamowaniu i w bliźniaku, histereza lustrzana, flaga zmiany nastawy w cyklu, FF3 bez martwego wejścia | `8823c17`, `770c444` | C1 | niskie |

**Kolejność:** A0 → A1–A5 → B1 → B2, B3 (B4 w dowolnym momencie) → C1 → C2. Każdy etap zaczyna
krótki plan do akceptacji właściciela (§0.1); push i PR tylko za jego „tak” (§0.3).

**Etapy własne** (nasza logika, poza kodem autora), opis niżej: **Z1** (dojście strefy po obniżeniu)
po A1, przed B; **Z2** (boost głównego źródła tylko w powrocie) po Z1; **H1** (zdarzenia wilgotności)
po B. Wszystkie scalone; zostaje C1 → C2.

Etapy A są od siebie niezależne. Każdy etap to gałąź robocza od aktualnego `dev2` i osobny PR
do `dev2`. Commity `b3c8939` i `b00384a` przenosimy ręcznie (logika
przepisana w B). Commity z fazy A nie są powtarzane w fazie C. Dokumentacja jest częścią każdego
etapu (bez osobnego etapu dokumentacji).

---

## Etap A0 – przygotowanie

```bash
git fetch origin dev master SPINew
git switch -c <gałąź wskazana przez właściciela> origin/dev
# środowisko: ANALIZA.md §3
PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/qas | tail -15 > $S/baseline_ci.txt
SMARTPI_SIM_LOCAL=1 SMARTPI_QAS_LOCAL=1 PYTHONPATH=$S/vt:$PWD $S/venv/bin/python -m pytest tests -q -p no:cacheprovider | tail -15 > $S/baseline_local.txt
# pokrycie bazowe: polecenie z ANALIZA.md §3
```

Zapisz liczby (passed, failed, skipped, lista failed) i pokrycie per plik. Każdy etap porównuj z
nimi. **Zrobione 2026-10-16:** `dev2` 1322 passed + 75 skipped (CI), 1397 passed lokalnie, pokrycie
93,16 % (`ANALIZA.md` §3, `results/phase-a/cov_dev2.json`).

---

## Faza A – poprawki niezależne

### A1. Nachylenia uczenia z surowych pomiarów (`69da036`)

**Cel:** okna uczenia a/b liczą nachylenie z surowej temperatury; filtr EMA zostaje tylko do
wykrywania czasu martwego (`ANALIZA.md` §9.1).

Kroki:
1. `git cherry-pick -x 69da036`. `smartpi/deadtime_estimator.py` i `smartpi/tint_filter.py` wejdą;
   konflikt w `algo.py`.
2. W `algo.py`, w obu wywołaniach `self.dt_est.update(` w `calculate` (ok. 3987 i 4059): zostaw
   `u_applied=self._deadtime_input(),` i dodaj `tin_raw=current_temp,` zaraz po `tin=t_int_lp,`.
3. `smartpi/calibration.py` bez zmian: jego `dt_est.update(tin=current_temp, …)` podaje surową
   temperaturę.
4. `learning_window.py` bez zmian: czyta `dt_est.tin_history`, które po zmianie zawiera surowe
   pomiary; odejmowanie `known_rate` nie zależy od próbek.

Kryteria: `dt_est.tin_history` dostaje `current_temp`, prywatne `_tin_lp_history` dostaje
`t_int_lp`; wykrywanie punktu przegięcia używa `_tin_lp_history`.

Po etapie: dane do decyzji D2 (czy `_apply_zone_fit` nadal nadpisuje a/b strefy). Nic tu nie
zmieniaj bez decyzji.

**Testy A1**
- Autora: `test_smartpi_deadtime_estimator.py` (z cherry-pickiem).
- Do aktualizacji:
  - `test_smartpi_dependencies_algo.py` (ok. linia 161): po krawędzi bramki także
    `_tin_lp_history` pusta;
  - `test_smartpi_sim_water.py` (`0.5 * A_REFERENCE < est.a < 1.2 * A_REFERENCE`) i
    `test_smartpi_sim_boiler_call.py` (stosunki `est.a / a`, `est.b / b`): wartości na `dev` i po
    A1; progi zawężaj tylko bliżej prawdy.
- Nowe (`tests/test_smartpi_raw_slopes.py`):
  - `test_learning_slope_uses_raw_samples_and_removes_known_rate`;
  - `test_boiler_hold_keeps_raw_history` (przy `_deadtime_input() == 0` historia dostaje surową
    temperaturę);
  - `test_gate_edge_clears_both_histories`.
- Nowy scenariusz (dowód do D2), `test_smartpi_sim_baseline.py::test_own_learning_with_a_tenth_degree_sensor`:
  SmartPI bez dopasowania strefy, czujnik 0,1 °C, nauczone `b` względem prawdy; liczby z `dev` i
  po A1 w docstringu; kilka faz (§0a.7).

**Dokumentacja A1:** `technical_doc.md` §3.2–3.3 (nachylenie z surowych pomiarów);
`zone_dependencies.md` (ok. linia 563, „half as fast”): sprawdzić i poprawić liczby; `testing.md`;
CHANGELOG.

### A2. Osobne liczniki dryfu a/b (`dd62923`)

1. `git cherry-pick -x dd62923` (czysto, `smartpi/ab_estimator.py`).
2. Zgodność wsteczna: stan z jednym `learn_seq` ładuje się do obu liczników.
3. `_apply_zone_fit` wpisuje historie bez numerów sekwencji; bez dopasowań.

Kryterium: przy 3+ pomiarach grzania między pomiarami stygnięcia kandydat dryfu b nie jest
odrzucany z powodu odstępu numerów.

**Testy A2**
- Autora: `test_smartpi_ab_drift.py` (z cherry-pickiem).
- Nowe (w `test_smartpi_ab_drift.py`):
  - `test_a_legacy_state_from_the_qas_fixture_seeds_both_counters` (`est_state` z
    `tests/qas/custom_components/smartpi_qas_helpers/fixtures/learned_room.json`, `"learn_seq": 49`);
  - `test_zone_fit_seed_does_not_block_drift`.

**Dokumentacja A2:** `technical_doc.md` (dryf); `diagnostics.md`, jeśli pokazuje licznik;
`testing.md`; CHANGELOG.

### A3. Reset PI po restarcie i przerwy w regulacji (`c506522`, ręcznie)

`c506522` odwołuje się do `_reset_causal_observation_runtime()` (powstaje w `c6f9f88`), więc
logikę przenosimy ręcznie w `algo.py`.

1. `_validate_and_handle_off` (ok. 3179), gałąź brakującego czujnika, po
   `self.ctl.last_i_mode = "I:FREEZE(missing_sensor)"`: `self._last_calculate_time = None`.
2. `calculate`, gałąź „Guard Cut” (ok. 3896), po `self._set_linear_output(0.0)`, przed `return`:
   `self._last_calculate_time = None`.
3. Blok `# --- 0. HVAC mode transition` w `calculate` (ok. 3845):
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
           # … reszta bloku bez zmian (liczniki sesji, nowy bliźniak, log) …
   ```
   W bloku `model_reset` usuń powielone resety korekty FF i `reset_command_ownership`.
4. **Nasze rozszerzenie (`ANALIZA.md` §12).** `calculate`, gałąź `if self._executive is not None:`
   (ok. 3830), przed `return`:
   ```python
   # Own regulation is suspended: the first own tick must not integrate this period.
   self._last_calculate_time = None
   ```

Kryteria: restart w trybie innym niż zapisany `est.model_hvac_mode` kasuje PI raz; po powrocie
czujnika, po „guard cut” i po wyjściu z roli wykonawczej pierwszy tick ma `dt_min == 0` i nie
zmienia całki.

**Testy A3**
- Autora: `git show c506522 -- tests | git apply` (`test_smartpi_antiwindup.py`,
  `test_smartpi_robustness.py`; czysto).
- Nowe (`tests/test_smartpi_regulation_gaps.py`):
  - `test_leaving_the_source_role_does_not_integrate_the_role_period`;
  - `test_restart_into_the_other_mode_with_a_zone_fit_resets_pi_once`.

**Dokumentacja A3:** `technical_doc.md` (zmiana trybu, przerwy w regulacji); `zone_dependencies.md`
(rola wykonawcza dodatkowego źródła: powrót do własnej regulacji); `testing.md`; CHANGELOG.

### A4. Zapis stanu najwyżej co 5 min (`a4624e5`)

1. `git cherry-pick -x a4624e5`. `state_store.py` wejdzie; konflikt w `handler.py`.
2. `handler.py`:
   - importy: bez `from homeassistant.helpers.storage import Store`, z
     `from .state_store import SmartPIStateStore`; nasze `issue_registry as ir` zostaje;
   - usuń `STORAGE_VERSION`, `STORAGE_KEY`, `LEGACY_STORAGE_KEY`; zostają `WATER_FACTOR_KEY`,
     `WATER_ISSUE_PREFIX`;
   - `self._store = SmartPIStateStore(t.hass, safe_name)`, bez `_legacy_store`;
   - `async_added_to_hass`: `data = await self._store.async_load()`; zostają
     `forget_dependencies_except(...)` i `self._apply_reset_request()`;
   - `_async_save`: `await self._store.save_now(t.prop_algorithm)`;
   - usuwanie encji (ok. 511): `t.hass.async_create_task(self._store.save_now(t.prop_algorithm))`
     w `try/except`;
   - `control_heating` (ok. 898): `if self._store and t.prop_algorithm:
     self._store.schedule_save(t.prop_algorithm)`.
3. Zapis natychmiastowy zostaje w `_cancel_calibration_if_active` i w `service_*`.
4. Decyzja D9 (2026-10-15): w `_apply_reset_request`, po `algo.reset_learning()`, zapis
   natychmiastowy (`t.hass.async_create_task(self._store.save_now(algo))`), tak jak serwis
   `reset_smartpi_learning` u autora. Token resetu strefy nie czeka 5 min; restart w tym oknie nie
   ponawia resetu.
5. `grep -n "_store\.\|Store(" custom_components/vtherm_smartpi/handler.py`: brak bezpośredniego
   `async_save`.

Kryteria: pętla regulacji planuje najwyżej jeden zapis na 5 min; serwisy i usunięcie zapisują od
razu; stan spod klucza `versatile_thermostat.smartpi.<nazwa>` nadal się wczytuje; nasze dane
(`dependencies`, `dependency_reset_token`, `water`) są w zapisie.

**Testy A4**
- Autora: `test_handler_persistence.py`, zmiana w `test_smartpi_cycle_alignment.py` (linie 321, 679).
- Do aktualizacji: testy z `handler._async_save = AsyncMock()` (lista w `ANALIZA.md` §17.2): testy
  serwisów zostają; test oczekujący zapisu po `control_heating` ma oczekiwać
  `handler._store.schedule_save`. `tests/sim/runner.py:521` i
  `test_smartpi_sim_second_source.py:262`: usunąć `handler._legacy_store = None`.
- Nowe (`tests/test_handler_persistence_extensions.py`):
  - `test_our_state_round_trips_through_the_state_store`;
  - `test_control_passes_schedule_one_write_per_window`;
  - `test_a_zone_reset_is_saved_at_once` (D9: `save_now` wołane od razu po resecie strefy).

**Dokumentacja A4:** `technical_doc.md` §5.4 „Current persistence”; `testing.md`; CHANGELOG.

### A5. OFF anuluje cykl i usunięcie setterów (`4070630`, `144ff02`)

1. `git cherry-pick -x 4070630` (czysto).
2. `git cherry-pick -x 144ff02` (czysto). Sprawdź:
   `grep -rn "\.guard_cut_active\s*=\|\.guard_kick_active\s*=" custom_components` tylko w
   `smartpi/guards.py`.

Kryterium: termostat przełączony na OFF w fazie wyłączenia PWM nie przełącza przekaźników.

**Testy A5**
- Autora: `test_smartpi_handler_off.py`.
- Nowe: `test_a_main_source_switched_off_in_the_off_phase_still_tells_its_additional_source`
  (cykl anulowany, dodatkowe źródło dostaje sygnał i polecenie 0).

**Dokumentacja A5:** CHANGELOG (oba błędy były zapisane w `PLAN_ROZWOJU.md` §6).

---

## Etap Z1 – dojście strefy po obniżeniu (nasza logika; decyzje D13–D16)

Kontekst i pomiary: `ANALIZA.md` §20. Gałąź `feat/zone-recovery` od A1 (porównanie dotyczy kodu
po A1), PR do `dev2` po scaleniu A1. Wariant V1: łatka próbna
`results/zone-recovery/experiment-v1.patch` (do przepisania czysto, bez zmiennych `ZR_*`).

**Ustawienie „Dojście po obniżeniu” (D14).**
- Klucz wspólny we wpisie „SmartPI defaults” (propozycja nazwy: `smart_pi_zone_recovery`,
  wartości `fast` (domyślnie), `economy`); dopisać do `SHARED_SETTINGS`.
- Formularz strefy (krok parametrów strefy, także przy tworzeniu wpisu): pole z opcjami
  `defaults` („Jak w SmartPI defaults”, domyślnie), `fast`, `economy`; zapis w pozycji strefy
  (`source`) we wpisie głównego źródła. Pojedynczy termostat bez strefy: pole nieużywane i
  niepokazywane.
- Wartość efektywna: pozycja strefy, a przy `defaults` wpis „SmartPI defaults”.
- Teksty (`en.json`, `fr.json`; PL w dokumentacji), z krótkim opisem każdej opcji:
  - pole: „How the zone comes back to its setpoint after a lower one (a night setback, for
    example).”
  - Fast: „during a setback the slow source keeps warm, so the zone is back at its setpoint
    sooner, with about 1–2 % more energy”;
  - Economical: „the deepest setback and the least energy; the zone takes longer to come back to
    its setpoint”.
- Migracja wpisów do wersji 1.3 (`MINOR_VERSION` 3, `VERSION` 1): wpis „SmartPI defaults” dostaje
  `fast`; 0.4.0 nadal wczytuje wpisy.

**Działanie (`smartpi/dependency_model.py`, `_drive_source`).**
- Obniżenie: nastawa niższa o ≥ 0,5 °C od najwyższej z ostatnich 24 h (HEAT; COOL lustrzanie:
  wyższa od najniższej). Pamięć (wartość, czas) w stanie zależności, żeby przetrwała restart.
  Stałe w `smartpi/const.py` (np. `SOURCE_SETBACK_MIN_C = 0.5`, `SOURCE_SETBACK_MEMORY_S = 86400`).
- `fast`: w czasie obniżenia polecenie dodatkowego źródła co najmniej równe nocnemu obciążeniu z
  dopasowania strefy `(b·(T_sp − T_ext) + Σk·(T_sp − T_j)) / a_f`, przycięte do 1, nadal
  ograniczone hamulcem (`_predictive_command`). Bez dopasowania strefy – bez zmian.
- `economy`: działanie jak dziś (bit w bit, test różnicowy).
- Diagnostyka strefy: aktywny poziom i to, czy trwa obniżenie.

**Wykonanie (2026-10-16), odstępstwa od planu powyżej:**
- „Jak w SmartPI defaults” nie jest zapisywane w pozycji strefy (brak klucza = ustawienie wspólne);
  wartość wspólna to efektywne ustawienie termostatu (wpis domyślny albo własne wartości termostatu).
- Migracja 1.3 dopisuje ustawienie też do wpisów termostatów z własnymi wartościami.
- Koniec przyspieszenia (boost) głównego źródła: opóźnienie + 2 cykle (`SOURCE_BOOST_END_CYCLES`),
  bo przy fazach 170/340 s najszybszy pokój przegrzewał się bardziej niż osobne SmartPI (§20.6).
- Kryterium dojścia z tolerancją 0,2 h (fazy 0/170/340 s); D16 jako wyjątek 0,8 h.

**Testy Z1.**
- Jednostkowe: rozpoznanie obniżenia (próg, 24 h, restart, COOL); polecenie w nocy przy `fast`
  (z hamulcem, bez dopasowania); `economy` identyczne z dziś; odczyt ustawienia (defaults /
  strefa); formularz strefy i wpisu „SmartPI defaults”; migracja 1.3.
- `test_smartpi_sim_zone_set.py` (długie): przy `fast` dojście nie wolniejsze i przegrzanie
  mniejsze niż dwa osobne SmartPI (`ANALIZA.md` §20); przy `economy` dłuższe dojście akceptowalne
  (D15), sprawdzamy stałą nastawę i przegrzanie; „fastcool-midheat, mróz” wg D16. Kilka faz
  wywołań (0, 170, 340 s) albo margines (§0a.7).
- Krótki scenariusz w CI: jedna noc, `fast` szybsze od `economy`.

**Dokumentacja Z1:** `zone_dependencies.md` (sterowanie dodatkowym źródłem, nowa podsekcja o
obniżeniu, wyniki §20 jako tabela), `vtherm_smartpi.md` (tabela ustawień, opis pola),
`testing.md`, CHANGELOG; EN/FR/PL.

---

## Faza B – cykl życia i ustawienia

### B1. Cykl życia wpisów (`__init__.py`, `const.py`) – decyzja D6

**Cel:** szkielet autora (rejestracja na stałe, przeładowanie tylko dotkniętych, nic przy
odładowaniu, `async_remove_entry`) z naszymi elementami (`ANALIZA.md` §10–11).

Bez zmian z `dev`: `_ensure_domain_data`, `_reload_smartpi_vtherms_for_target`, `_zone_mains_of`,
`_saved_together`, `_settings_to_move`, `async_migrate_entry`, `_smartpi_vtherm_unique_ids`,
`_async_move_settings`.

Do usunięcia: `_unregister_factory`, `_unregister_services`, `_reload_smartpi_vtherms`,
`DATA_SKIP_FULL_RELOAD` i jego logika, wyjątek `SOURCE_IMPORT` w `async_setup_entry`,
`_get_own_values_target_unique_ids`, flagi `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`.

Nowe lub zmienione:

```python
def _register_factory(hass) -> None:
    # jak SPINew: bez flag; rejestruje, jeśli api.get_prop_algorithm(name) is None

def _register_services(hass) -> None:
    # B3 (D1: wersja autora); do B3 ciało z dev bez flagi DATA_SERVICES_REGISTERED

async def async_setup(hass, config) -> bool:
    _register_factory(hass)
    _register_services(hass)
    return True

def _own_entries_using_defaults(hass) -> list[ConfigEntry]:
    """Wpisy termostatów z włączonym „Use the SmartPI defaults”."""
    return [e for e in hass.config_entries.async_entries(DOMAIN)
            if e.data.get(CONF_TARGET_VTHERM) and uses_defaults(entry_options(e))]

async def _reload_smartpi_vtherms_using_defaults(hass) -> None:
    # wersja autora: pomija każdy termostat z własnym wpisem (_get_dedicated_target_unique_ids)

async def _reload_affected_vtherms(hass, entry) -> None:
    target = entry.data.get(CONF_TARGET_VTHERM)
    if target:
        await _reload_smartpi_vtherms_for_target(hass, target)
        return
    # Wpis „SmartPI defaults”: wpisy z „Use the SmartPI defaults” przeładowujemy jako wpisy
    # (ich setup przeładuje termostat raz i odtworzy czujnik diagnostyczny).
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
    # … bez zmian: async_at_started → _async_move_settings przy CONF_SMART_PI_SETTINGS_TO_MOVE …
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
- `async_setup_entry` nie zapisuje już `data[entry.entry_id]`. Sprawdź
  `grep -rn "hass.data\[DOMAIN\]\|_ensure_domain_data" custom_components`.
- `const.py`: bez `DATA_FACTORY_REGISTERED`, `DATA_SERVICES_REGISTERED`; zostają
  `DATA_SOURCE_LINK`, `DATA_SAVE_GROUP`; popraw komentarz przy `DATA_DIAGNOSTIC_SENSORS`.
- Zastępuje sposób ze `specs/2026-10-14-…` §9.5 i zmiany z §8.2 (zachowanie zostaje,
  `ANALIZA.md` §11.4).

**Oczekiwane zachowanie (kryteria):**

| Zdarzenie (HA działa) | Przeładowane termostaty | Przeładowane wpisy SmartPI |
|---|---|---|
| Zapis opcji wpisu termostatu X | X raz; główne źródła strefy, dla których X jest dodatkowym źródłem, raz (o ile nie w tej samej grupie zapisu) | X |
| Zapis formularza strefy (kilka wpisów) | każdy termostat z zapisanych wpisów raz; główne źródło nie drugi raz | zapisane wpisy |
| Zapis wpisu „SmartPI defaults” | termostaty bez własnego wpisu raz; termostaty z „Use the SmartPI defaults” raz (przez ich wpisy) | „SmartPI defaults” + wpisy z „Use the SmartPI defaults” |
| Utworzenie wpisu termostatu X (UI, import z formularza strefy, migracja 1.2) | tylko X | – |
| Usunięcie wpisu termostatu X | X po zniknięciu wpisu; główne źródła strefy X | – |
| Usunięcie wpisu „SmartPI defaults” | termostaty bez własnego wpisu; termostaty z „Use the SmartPI defaults” (przez ich wpisy) | wpisy z „Use the SmartPI defaults” |
| Odładowanie wpisu, start HA | żadne | – |
| Każde z powyższych | fabryka SmartPI i 4 serwisy zostają zarejestrowane | – |

Migracja 1.2, przeniesienie ustawień i zgłoszenie naprawy działają jak w `dev`; czujnik
diagnostyczny przechodzi na własny wpis (`sensor.py` bez zmian).

**Testy B1**

Technika autora (`git show origin/SPINew:tests/test_init_lifecycle.py`): podmień
`_reload_smartpi_vtherms_for_target` i `_reload_smartpi_vtherms_using_defaults` na `AsyncMock` z
`side_effect` zapisującym wywołanie i widoczne wpisy; `hass.config_entries.async_reload` na
`AsyncMock`, który naprawdę woła `async_unload_entry` i `async_setup_entry`;
`hass.config_entries.async_forward_entry_setups = AsyncMock()`; `hass.state = CoreState.running`.

- Autora: `test_init_lifecycle.py` (`git show b3c8939 -- tests | git apply`; nazwa
  `_reload_smartpi_vtherms_using_defaults` zachowana, asercje pasują).
- Do aktualizacji: `test_config_flow_save.py::test_a_save_reloads_each_entry_once_also_when_the_zone_moves`
  (technika autora), `test_config_migration.py` (ok. 239), `test_sensor_own_entry.py` (45, 91)
  (zamiast `_reload_smartpi_vtherms` dwa helpery autora),
  `test_services_routing.py::test_services_are_registered_once_and_removed` (tylko „raz”).
- Nowe (`tests/test_init_lifecycle_zones.py`), jeden na wiersz tabeli:
  `test_a_thermostat_save_reloads_it_and_the_zone_mains_once`,
  `test_a_zone_save_reloads_each_written_thermostat_once`,
  `test_a_defaults_save_reloads_entries_using_defaults_and_thermostats_without_entry_once`,
  `test_creating_a_thermostat_entry_reloads_only_that_thermostat`,
  `test_removing_a_thermostat_entry_reloads_it_after_it_left_the_registry`,
  `test_removing_the_defaults_entry_reloads_its_users`,
  `test_unloading_reloads_nothing_and_keeps_factory_and_services`,
  `test_a_settings_move_does_not_trigger_the_listener`.

**Dokumentacja B1:** `technical_doc.md` (ok. linia 511, przeładowanie przy zapisie „SmartPI
defaults”); `zone_dependencies.md` (ok. linia 1144, `__init__.py`); `testing.md`; CHANGELOG.

### B2. Wpis „SmartPI defaults” do odtworzenia, bez kroku `global` (`b00384a`, część)

1. `config_flow.py`, `SmartPIConfigFlow.async_step_user`: `if not self._async_current_entries():`
   → `if defaults_entry(self.hass) is None:` (`defaults_entry` z `.settings`, już importowane).
   Dalej bez zmian: `async_set_unique_id(DOMAIN)`, `_abort_if_unique_id_configured()`,
   `async_create_entry(title="SmartPI defaults", data=_shared_defaults())`.
2. Usuń `async_step_global`.
3. `translations/en.json` i `fr.json`: usuń `config.step.global`.

Kryterium: po usunięciu wpisu „SmartPI defaults” przy istniejących wpisach termostatów „Dodaj
integrację” tworzy nowy; jego setup (B1) przeładowuje termostaty z „Use the SmartPI defaults” i
termostaty bez własnego wpisu.

**Testy B2**
- Autora: `test_user_step_recreates_global_entry_when_only_dedicated_entries_exist`
  (`test_config_flow.py`, wchodzi czysto); oczekiwane `data` = nasze `_shared_defaults()`.
- Do aktualizacji: `test_config_flow_heating_type.py` (ok. 110): usuń `("config", "global")`.
- Nowe: `test_a_legacy_defaults_entry_without_unique_id_is_not_duplicated`,
  `test_recreated_defaults_reload_the_thermostats_that_use_them`.

**Dokumentacja B2:** `vtherm_smartpi.md` (wpis „SmartPI defaults”); `testing.md`; CHANGELOG.

### B3. Walidacja celu serwisów (`b00384a`, część) – decyzja D1

Decyzja D1 (2026-10-15): wersja autora.
1. `_register_services` jak w `SPINew`: `_call_on_vtherms` używa
   `service_helper.async_extract_entity_ids(call)`, a gdy żaden cel nie jest termostatem SmartPI,
   rzuca `ServiceValidationError(translation_domain=DOMAIN, translation_key="no_smartpi_target")`;
   schematy `cv.make_entity_service_schema({...})`.
2. `services.yaml`: sekcja `target` z `entity: {integration: versatile_thermostat, domain: climate}`.
3. `translations/en.json` i `fr.json`: `exceptions.no_smartpi_target.message`, `services.*`.

Kryteria: wywołanie z `target` albo z `entity_id` w danych trafia do termostatu (pakiet QAS
woła z `{"entity_id": ...}`); cel bez termostatu SmartPI daje błąd widoczny w UI.

**Testy B3**
- Autora: `tests/test_services.py` z `b00384a` (czysto).
- Do aktualizacji (`test_services_routing.py`): routing tylko z `async_extract_entity_ids`;
  brak celu SmartPI → `ServiceValidationError`; termostat TPI pominięty.
- Nowe: `test_a_qas_style_call_with_entity_id_in_data_still_works`,
  `test_a_mixed_target_reaches_only_smartpi_thermostats`.

**Dokumentacja B3:** `vtherm_smartpi.md` „Services”; `testing.md`; CHANGELOG.

### B4. Zdarzenia, martwa gałąź, manifest (`b00384a`, część)

1. `handler.py`: `t.hass.bus.fire(` → `t.hass.bus.async_fire(` (6 miejsc), z naszymi danymi
   (`self._autocalib_event_data(event)`).
2. `_cancel_calibration_if_active`: tylko
   `algo.autocalib.on_calibration_complete(time.time(), algo, SmartPICalibrationResult.CANCELLED)`.
3. `manifest.json`: `"requirements": ["vtherm_api>=0.5.0"]`.

**Testy B4**
- Autora: zmiana w `tests/test_handler_attributes.py` (czysto).
- Do aktualizacji: `test_smartpi_autocalib_events.py` (`bus.async_fire`).
- Nowe: `test_every_smartpi_event_is_fired_with_the_loop_safe_api`.

**Dokumentacja B4:** CHANGELOG.

---

## Etap H1 – zdarzenia wilgotności (nasza logika; decyzje D20–D22) – scalony (PR #28)

Specyfikacja i wyniki: `ANALIZA.md` §22. Skrót:
- **Wejście:** `current_humidity` termostatu VT (funkcja wilgotności VT); handler przekazuje je przy każdym
  sterowaniu (`algo.update_humidity`). Bez czujnika: `None`, nic się nie zmienia.
- **Detektor** `smartpi/humidity_event.py`: start przy wzroście ≥ `HUMIDITY_EVENT_RISE` (5 %RH) w
  `HUMIDITY_EVENT_WINDOW_S` (10 min); koniec przy poziomie bazowym + `HUMIDITY_EVENT_END_SHARE` (0,25)
  wzrostu albo po `HUMIDITY_EVENT_MAX_S` (3 h); `HUMIDITY_EVENT_SETTLE_S` (30 min) uspokojenia.
- **Skutek w czasie zdarzenia i uspokajania:** bramka uczenia `humidity_event`; zależności strefy się nie
  uczą; całka wstrzymana (`I:HOLD`, źródło `humidity_event`); trym FF bez okna. Diagnostyka: blok `humidity`.
- **Testy:** `tests/test_smartpi_humidity_event.py`, jeden test w `test_smartpi_boiler_handler.py`;
  sonda symulacji `tools/h1_probe.py` (efekt neutralny ±0,01 °C, §22.2).
- **Dokumentacja:** `technical_doc.md` §4.7 i pozostałe miejsca z `ANALIZA.md` §14.

## Faza C – reference governor

### C1. Reference governor (`c6f9f88`, `b2b0249`, `15fc13f`)

Kroki:
1. `git cherry-pick -x c6f9f88`. Konflikty (sprawdzone na `measure/governor-water`):
   - `algo.py` import: zostaje nasz `from typing import Any, Callable, Dict, Optional`;
   - `learning_window.py` (sygnatura i docstring `update`) oraz `update_learning` w `algo.py`:
     oba zestawy, `known_rate=known_rate` **oraz** `a_learning_allowed=a_learning_block_reason is None`,
     `a_learning_block_reason=a_learning_block_reason`;
   - po A3: trójkę resetów korekty FF w bloku zmiany trybu zastąp
     `self._reset_causal_observation_runtime()`;
   - `documentation/{en,fr}`: `git checkout --ours -- documentation/`, treść w kroku 4;
     `cards/` wchodzą czysto.
2. `git cherry-pick -x b2b0249` (czysto), `git cherry-pick -x 15fc13f` (konflikt tylko w
   dokumentacji: `--ours`).
3. Obowiązkowe sprawdzenia (`algo.py`):
   - **C-a.** `filter_setpoint` w `_manage_setpoint` dostaje `b=self.control_b` i `T_ref`
     (`control_ext_temp`); tak samo `compute_ff3`, bliźniak (`twin.update_with_eta`), korekta FF
     (`try_complete_window`, `evaluate_periodic_window`).
   - **C-b.** `_record_fftrim_thermal_measurement(ext_current_temp=control_ext_temp)`; nowe
     `self._thermal_measurements.observe(...)` dostaje tę wartość.
   - **C-c.** `_record_completed_fftrim_power` woła `self._causal_power_trace.complete_applied_cycle(...)`
     z `realized_linear_power=clamp(float(e_eff), 0.0, 1.0) * self._water_factor` (potwierdzone na
     `measure/governor-water`).
   - **C-d.** `dependency_model.record_realized_power(...)` przy końcu cyklu wykonuje się jak w `dev`.
   - **C-e. Woda a reference governor:** pomiar 2026-10-15 (`ANALIZA.md` §13): bez wycofań
     epizodów, przy ograniczeniu wodą cap nie wiąże. **Bez zmian w kodzie autora (decyzja D7).**
     Korekta A (żądanie przycięte do limitu wody przed `resolve_reference_governor` i to samo
     porównanie w `_try_release_terminal_braking`) tylko za zgodą właściciela, gdyby dane z domu
     pokazały wycofania przy zimnej wodzie.
   - **C-f.** `WATER_INTEGRAL_HOLD` ustawia `ctl.integral_hold_active`, więc zwalnianie hamowania
     jest wtedy zablokowane (zamierzone).
   - **C-g.** Rola wykonawcza nie przechodzi przez reference governora: w `leave_source_role`
     `self.sp_mgr.reset()` (woła `_reset_trajectory()`, które czyści epizod; sprawdzić po
     cherry-picku).
   - **C-h.** Akcja okna u sąsiada zmienia `target_temp`: dla reference governora to zmiana
     nastawy (zamierzone); `turn_off` idzie ścieżką OFF.
   - **C-i.** Blokada uczenia autora używa `sp_mgr.setpoint_landing_active` (istnieje w nowym
     `setpoint.py`); nasza bramka `_learning_gate` zostaje przed `update_learning`.
   - **C-j. Zdarzenie wilgotności (H1) a reference governor.** W czasie zdarzenia i uspokajania
     `integrator_hold` jest ustawione przed `compute_pwm` (źródło `humidity_event`), więc
     `_try_release_terminal_braking` nie zwalnia hamowania (tak jak przy C-f). Wyłączenie okna
     korekty FF przy zdarzeniu (`humidity_event.active` przy `result.admissible`, na `dev2` ok.
     linia 1336) działa też w nowej ścieżce pomiarów autora (`_thermal_measurements`,
     `try_complete_window`, `evaluate_periodic_window`).
4. Dokumentacja (w tym samym PR): §0b i blok niżej.
5. Weryfikacja funkcjonalna na symulatorze: `tests/test_smartpi_sim_*.py`,
   `tests/test_smartpi_dependencies_algo.py` oraz pomiar
   `analysis/spinew/measure_governor_water.py` na gałęzi etapu i na `dev` (porównanie z
   `ANALIZA.md` §13.3). Testy, które padły w próbie merge (`ANALIZA.md` §17.2), oceniamy najpierw
   jako możliwe regresje.
6. Obserwacje do raportu: „drganie” capa (D8: obserwujemy, liczymy przełączenia capa w pomiarze z
   kroku 5; zmiana w kodzie autora tylko po pomiarze i za zgodą właściciela); nauczone `a` niższe o
   5–10 % (porównać po A1).

**Testy C1**
- Autora: lista `ANALIZA.md` §17.1 (`c6f9f88`, `b2b0249`, `15fc13f`); mają przejść bez zmian.
  Jeśli któryś pada, sprawdź, czy nasze podstawienie (woda, `b_eff`, `T_ref`) zmienia jego
  założenia, i opisz to przed zmianą.
- Do aktualizacji:
  - `test_smartpi_water_algo.py::test_the_feed_forward_trim_records_the_power`: podmiana
    `algo._causal_power_trace.complete_applied_cycle`;
  - `test_smartpi_dependencies_algo.py::test_substitution_reaches_every_control_component`:
    przyczyna (sygnatury `deadband_mgr.update`, `twin.update_with_eta`, `compute_ff3`), potem
    rozszerzenie;
  - `test_smartpi_sim_neighbours.py` (3), `test_smartpi_sim_water.py` (1),
    `test_smartpi_sim_harness.py::test_valve_cycle_starts_report_the_applied_opening` (2): §0a.7;
  - `test_sensor_own_entry.py`, jeśli nadal pada po B1: atrybuty czujnika po `b2b0249`.
- Nowe:
  - w teście podstawienia: szpiedzy na `algo._thermal_measurements.observe`
    (`outside_temperature == T_ref`) i na `algo.sp_mgr.filter_setpoint` dla reference governora;
  - `tests/test_smartpi_governor_water.py`:
    `test_the_causal_power_trace_records_power_at_the_reference_water` (C-c),
    `test_the_dependency_model_still_records_the_cycle_power` (C-d),
    `test_a_water_limited_approach_arms_no_constraining_cap` (C-e; scenariusz z §13 skrócony do
    1–2 dojść, kilka faz, kryterium: zero wycofań z powodem `prediction_falsified_below_target`);
  - `tests/test_smartpi_governor_source_role.py`:
    `test_leaving_the_source_role_clears_the_governor_episode` (C-g),
    `test_a_neighbour_window_preset_is_a_setpoint_change_for_the_governor` (C-h);
  - `tests/test_smartpi_governor_humidity.py` (C-j):
    `test_a_humidity_event_blocks_the_braking_release`,
    `test_a_humidity_event_keeps_its_window_out_of_the_feed_forward_trim`;
  - `test_smartpi_sim_neighbours.py::test_landing_with_a_neighbour_exchange`: przeregulowanie z
    marginesem względem `dev`, kilka faz.

**Dokumentacja C1:** `technical_doc.md` §4.4, §6.3 i pozostałe wzmianki o lądowaniu (EN 12 linii,
FR 6, PL 7); `vtherm_smartpi.md` (opis filtra nastawy; EN 8, FR 4, PL 5); `diagnostics.md`
(atrybuty `landing_*` → atrybuty reference governora i historii; EN 2, FR 1, PL 1); `README.md`
(1); `water_input.md` §3.4 i §3.6 (przy ograniczeniu wodą reference governor nie hamuje; wyniki
pomiaru); `testing.md`; CHANGELOG. Treść o reference governorze z
`git show origin/SPINew:documentation/<jęz>/<plik>`; nasze etykiety UI (zgodne z
`translations/en.json`) i nasze poprawki zostają; PL tłumaczymy. Karty
`cards/smartpi_diags-{en,fr}.md` przychodzą od autora; dodajemy w nich nasze sekcje (D4): znane
wejścia (`dependencies.*`), woda (`water.*`), kocioł (`boiler.*`). Nowa karta
`cards/smartpi_diags-pl.md`: tłumaczenie karty EN po zmianach (ta sama struktura, te same klucze i
warunki szablonu, teksty wg słownika `ANALIZA.md` §4). Kontrola: trzy karty mają te same sekcje w
tej samej kolejności.

### C2. Poprawki COOL (`8823c17`, `770c444`)

1. `git cherry-pick -x 8823c17`. Konflikty w `filter_setpoint` i `twin.update_with_eta`: zostają
   `b=self.control_b` i `control_ext_temp`; argumenty czasu martwego od autora
   (`stop_deadtime_s`, `stop_deadtime_reliable` z `self.dt_est.passive_deadtime(hvac_mode)`).
2. `git cherry-pick -x 770c444`. Przy `compute_ff3(...)`: zostaje `b=self.control_b`, znika
   `deadtime_cool_s=...`.

Kryteria: HEAT bez zmian; w COOL hamowanie i bliźniak używają biernego czasu martwego;
histereza COOL lustrzana do HEAT; zmiana nastawy w trakcie cyklu (także przez akcję okna u
sąsiada) blokuje płynne przejęcie korekty FF w tym cyklu.

**Testy C2**
- Autora (z cherry-pickami): `test_smartpi_learning_enablement.py`,
  `test_smartpi_reference_governor_wiring.py`, `test_smartpi_setpoint.py`,
  `test_smartpi_thermal_model.py`, `test_smartpi_cycle_alignment.py`,
  `test_smartpi_feedforward.py`, `test_smartpi_hysteresis_deadtime_window.py`.
- Do aktualizacji: wywołania `deadtime_cool_s=` są tylko w plikach poprawianych przez autora
  (`test_smartpi_feedforward.py`, `test_smartpi_setpoint.py`); potwierdź
  `grep -rn "deadtime_cool_s=" tests/`. `tests/qas/scenarios/qd.py` czyta tylko klucz
  diagnostyki `model.deadtime_cool_s` (nadal istnieje).
- Nowe: `test_cool_braking_uses_the_passive_deadtime_with_the_effective_model`,
  `test_a_neighbour_preset_change_in_cycle_blocks_the_bumpless_transfer`.

**Dokumentacja C2:** `technical_doc.md` (COOL); `testing.md`; CHANGELOG.

---

## 2. Definicja ukończenia (każdy etap)

- Kod, testy, dokumentacja EN/FR/PL, tłumaczenia `en.json`/`fr.json` (jeśli dotyczy) i
  CHANGELOG w jednym commicie etapu.
- Zakres CI i pełny lokalny porównane z aktualnym `dev2`; każda różnica wyjaśniona; test różnicowy przechodzi.
- Pokrycie: kod forka ≥ 95 % linii, ≥ 90 % gałęzi, pliki autora bez spadku (albo uzasadnienie).
- Kontrole z §0b.5 przechodzą.
- Testy autora z `ANALIZA.md` §17.1 przeniesione; nasze z §17.2 zaktualizowane wg §0a.2; żaden
  wyłączony; luki z §17.3 pokryte w etapach, których dotyczą.
- Raport dla właściciela; push i PR tylko za jego „tak”, na wskazany przez niego branch.

## 3. Ściąga poleceń

```bash
# co zmienił autor
git show <sha> --stat; git show <sha> -- custom_components/vtherm_smartpi/<plik>
# czy commit wejdzie czysto (na sucho)
git show <sha> -- custom_components | git apply --check -v
# stan pliku u autora
git show origin/SPINew:custom_components/vtherm_smartpi/<plik>
# przerwanie nieudanego cherry-picka
git cherry-pick --abort
# pomiar reference governora z wodą: ANALIZA.md §13.6
```
