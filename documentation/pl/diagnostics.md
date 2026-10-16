# Diagnostyka SmartPI

SmartPI udostępnia w Home Assistant osobny czujnik diagnostyczny. Jego stan to bieżąca
faza regulacji. Jego atrybuty mają układ zgodny ze schematem w wersji 2.

## Struktura atrybutów

```yaml
schema_version: 2
live:     # Complete live diagnostics
history:  # Stable time-series contract
```

`live` to źródło danych dla pulpitów, kart Markdown i analizy na bieżąco.
Ma tę samą zawartość w trybie normalnym i w trybie debugowania. `history` zawiera tylko
wartości przeznaczone do wykresów historii.

Nie ma osobnego bloku atrybutów `debug`.

## Profile Recordera

W trybie normalnym cały atrybut `live` jest wyłączony z zapisu przez Recorder (część
Home Assistant, która zapisuje historię). Zachowywane są tylko `schema_version`, stan czujnika i `history`.

Tryb debugowania zapisuje zarówno `live`, jak i `history`. Zawartość nie jest powielana
w `history`: uzgodniony zestaw danych historii jest taki sam w obu trybach.
Tryb debugowania zmienia więc to, co jest zachowywane, a nie to, co jest publikowane.

Blok `history` zawiera te osiem serii danych:

| Ścieżka w `history` | Znaczenie |
|---|---|
| `temperature.indoor` | Temperatura w pomieszczeniu |
| `setpoint.filtered_setpoint` | Przefiltrowana nastawa regulacji |
| `power.applied_percent` | Moc faktycznie podana |
| `power.command_percent` | Żądane wysterowanie, przed wpływem elementu wykonawczego (tego, co fizycznie włącza grzanie) |
| `power.pi_percent` | Udział części PI |
| `power.ff_percent` | Udział części wyprzedzającej (feed-forward) |
| `model.a` | Nauczone wzmocnienie grzania |
| `model.b` | Nauczony współczynnik strat ciepła |

## Diagnostyka bieżąca

Blok `live` jest podzielony według zadań:

| Blok | Zawartość |
|---|---|
| `control` | Faza, tryb regulacji, wzmocnienia, nasycenie (wysterowanie na granicy zakresu) i strefy regulacji |
| `power` | Moc w bieżącym i następnym cyklu, podział na PI i FF, limity i moc faktycznie podana |
| `temperature` | Temperatura wewnątrz i na zewnątrz, błędy i stan całki |
| `model` | Model A/B, zaufanie, stała czasowa i czasy martwe |
| `learning` | Etap uczenia, postęp fazy startowej, przyjęte aktualizacje i stan dryfu (powolnej zmiany modelu) |
| `governance` | Reżim cieplny (rodzaj bieżącej sytuacji) i decyzja o aktualizacji modelu |
| `feedforward` | Stan FF3, źródło strefy nieczułości i główna diagnostyka FFTrim (powolnej korekty części wyprzedzającej) |
| `setpoint` | Przefiltrowana nastawa, trajektoria, podbicie (boost) i podsumowanie „lądowania” |
| `autocalib` | Stan nadzoru automatycznej kalibracji |
| `calibration` | Stan kalibracji, liczba ponownych prób i czas ostatniego zakończenia |
| `analysis` | Zaawansowane pola używane przez dołączone karty diagnostyczne |
| `dependencies` | Tylko przy zależnościach stref: złożony model, bramka uczenia (czy uczenie jest teraz dozwolone), reakcja na okno, sąsiedzi i strefa z dwoma źródłami ciepła |
| `water` | Tylko dla wodnego rodzaju ogrzewania: współczynnik wody, to, skąd pochodzi, wstrzymania i źródło każdego sygnału wody |
| `boiler` | Tylko dla wodnego rodzaju ogrzewania z wybranym źródłem wezwania kotła: to źródło, wezwanie i wynikający z niego tryb uczenia |

`analysis` dzieli zaawansowane bieżące wartości na `control`, `learning`,
`trajectory`, `landing`, `deadtime`, `governance`, `feedforward` oraz, gdy jest
dostępny, `twin`.

FFTrim używa zagnieżdżonych bloków w stałym układzie:

```yaml
live:
  feedforward:
    fftrim:
      stationary: {}
      periodic: {}
      transfer: {}
      command_ownership: {}
      observation_mode: ...
      last_reject_reason: ...
      last_update_reason: ...
      last_result: ...
      last_transaction: ...
      windows_since_update: ...
```

### `dependencies`

Gdy termostat ma zależności stref (strefy sąsiednie albo strefę z dwoma źródłami
ciepła), w `live` pojawia się blok `dependencies`. Zawiera on złożony model (`b_eff`,
`t_ref`), bramkę uczenia, zastosowaną reakcję na okno i po jednym wpisie na każdego sąsiada
z nauczoną wymianą ciepła. Dla głównego źródła strefy z dwoma źródłami ciepła zawiera też
blok `second_source`: wysterowanie i ciepło dodatkowego źródła, limit jego mocy,
przyspieszenie powrotu i dopasowanie strefy. Termostat, który pracuje jako dodatkowe źródło
innego termostatu, publikuje tylko swoją rolę, główne źródło i wysterowanie, które stosuje.
Bez zależności tego bloku nie ma. Jego pola opisuje
[Zależności stref, §9](zone_dependencies.md#9-diagnostyka).

### `water`

Dla termostatu z wodnym rodzajem ogrzewania w `live` pojawia się blok `water`. Zawiera on
zastosowany współczynnik wody, to, skąd pochodzi, rodzaj ogrzewania, uśrednioną temperaturę wody
zasilającej, ostatnie powody podane przez źródło danych o wodzie, wstrzymanie na czas grzania
ciepłej wody użytkowej, CWU (`no_heat`), wstrzymanie całki, gdy woda daje bardzo mało ciepła
(`low_heat`), znacznik „woda za zimna” oraz `inputs`: dla każdego sygnału wody jego źródło
(encja, dostawca albo brak) i to, czy teraz podaje dane. Przy danych o wodzie wartości w `power`
to moc przy wodzie odniesienia, a `current_cycle_percent` i `next_cycle_percent` to wysterowanie
wysłane do emitera (grzejnika, pętli podłogówki). Jego pola opisuje
[Wejścia wody i kotła, §8](water_input.md#8-diagnostyka).

### `boiler`

Dla termostatu z wodnym rodzajem ogrzewania, gdy w jego opcjach wybrano źródło wezwania kotła
(kocioł centralny Versatile Thermostat, encje albo dostawcę), w `live` pojawia się blok `boiler`, także
wtedy, gdy źródło nie podaje danych. Zawiera on źródło, to, czy kocioł jest wezwany, a jeśli nie,
to dlaczego, jak uczenie to traktuje (`emitter_cooling`, `held`, `emitter_warming`),
zapotrzebowanie w porównaniu z progami startu kotła, gdy źródło je podaje, oraz `inputs`: skąd
pochodzi każdy sygnał. Jego pola opisuje
[Wejścia wody i kotła, §8](water_input.md#8-diagnostyka), a wyjaśnia
[§4](water_input.md#4-kocioł-który-nie-jest-wezwany-do-grzania).

### `humidity`

Gdy Versatile Thermostat podaje wilgotność pomieszczenia, w `live` pojawia się blok `humidity`,
gdy tylko pojawi się pierwszy odczyt. Zawiera wilgotność (`humidity`), stan zdarzenia wilgotności
(`idle`, `event`, `settling`), poziom bazowy (`baseline`) i szczyt (`peak`) zdarzenia oraz
`events`, liczbę zdarzeń od startu. W czasie zdarzenia i uspokajania `learning_gate` ma wartość
`humidity_event`. Wyjaśnia je
[dokumentacja techniczna, §4.7](technical_doc.md#47-zdarzenia-wilgotności).

## Częstotliwość publikacji

Czujnik diagnostyczny odświeża się przy istotnych zmianach danych wejściowych regulacji, na
granicach cykli, przy jawnie wymuszonych obliczeniach i przy usługach diagnostycznych.
Wewnętrzny zegar, który co 60 sekund liczy wszystko od nowa, niczego nie publikuje, gdy jego
dane wejściowe i zatwierdzona moc się nie zmieniły. Czujnik pomija też zapis, gdy zarówno
jego stan, jak i cały zestaw atrybutów są takie same jak przy poprzedniej publikacji.

Ogranicza to zbędne zmiany stanów w Home Assistant i zapisy w Recorderze, bez zmiany rytmu
regulacji SmartPI.

## Ścieżki dla odbiorców danych

Odbiorcy danych bieżących (np. karty na pulpicie) muszą czytać z `attributes.live`. Wykresy
historii muszą czytać z `attributes.history`. Przed odczytaniem zagnieżdżonej struktury
odbiorcy powinni sprawdzić, czy `attributes.schema_version == 2`.
