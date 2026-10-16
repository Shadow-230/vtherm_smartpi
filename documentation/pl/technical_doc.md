# Smart-PI: dokumentacja techniczna i naukowa

## 1. Wprowadzenie

**Smart-PI** to samodostrajający się, dyskretny regulator PI (proporcjonalno-całkujący). „Dyskretny” znaczy, że liczy w krokach, co pewien czas. Jest dostępny jako osobna integracja dla *Versatile Thermostat*. Ma zastąpić TPI ze stałymi współczynnikami pętlą regulacji, która w trakcie pracy uczy się, jak pomieszczenie naprawdę się nagrzewa i stygnie.

Obecny kod opiera się na trzech głównych pomysłach:

1. wyznaczyć prosty model cieplny pierwszego rzędu z czasem martwym (opóźnieniem, zanim temperatura zacznie reagować),
2. dopasowywać wysterowanie PI (moc w %) na podstawie tego modelu,
3. zamrażać lub ograniczać część dostrajania, gdy stan pracy nie jest uznany za fizycznie wiarygodny.

Ten dokument opisuje działanie, które jest naprawdę zaimplementowane w obecnym kodzie SmartPI.

Ten fork dodaje rozszerzenia: znane wejścia (ciepło wymieniane ze strefami sąsiednimi i ciepło
z drugiego źródła ciepła w strefie), temperaturę wody docierającej do emiterów (grzejników, pętli
podłogówki) oraz wezwanie kotła (czy kocioł dostaje polecenie grzania). Dane o wodzie i wezwanie kotła
pochodzą ze źródła, które użytkownik wybiera w opcjach termostatu: z encji Home Assistant albo od
dostawcy (integracji, która implementuje kontrakt SmartPI), a wezwanie kotła także z kotła
centralnego Versatile Thermostat. Tam, gdzie rozszerzenie zmienia model, łańcuch obliczania
wysterowania albo uczenie, sekcje poniżej to uwzględniają. Same rozszerzenia są opisane
w dokumentach [Zależności stref i drugie źródło ciepła](zone_dependencies.md)
oraz [Wejścia wody i kotła](water_input.md), a ich testy w dokumencie [Testy](testing.md).

---

## 2. Model cieplny i prawo sterowania

### 2.1 Używany model cieplny

Nauczony model cieplny to:

$$ \frac{dT_{int}}{dt} = a \cdot u(t) - b \cdot (T_{int}(t) - T_{ext}(t)) $$

Gdzie:

- $T_{int}$: temperatura wewnątrz,
- $T_{ext}$: temperatura na zewnątrz,
- $u(t) \in [0,1]$: wysterowanie aktywnego elementu wykonawczego (urządzenia, które grzeje lub chłodzi), w skali od 0 do 1,
- $a$: wzmocnienie cieplne elementu wykonawczego, ze znakiem: dodatnie w HEAT (grzanie) i ujemne w COOL (chłodzenie),
- $b > 0$: współczynnik strat ciepła, czyli biernej wymiany ciepła z otoczeniem na zewnątrz.

Termostat VTherm ze SmartPI pracuje w jednym trybie: uczy się albo modelu HEAT, albo modelu COOL.
Równanie jest takie samo w obu przypadkach. Znak $a$ mówi, w którą stronę
działa urządzenie.

Stała czasowa (im jest większa, tym wolniej zmienia się temperatura pomieszczenia) wynosi:

$$ \tau = \frac{1}{b} $$

Model jest celowo prosty. Czas martwy jest uczony osobno. Korzystają z niego reguły strojenia regulatora i zabezpieczenia.

**Znane wejścia.** Model jest rozszerzony o ciepło wymieniane ze strefami sąsiednimi i o ciepło
dodane przez drugie źródło:

$$ \frac{dT_{int}}{dt} = a \cdot u - b \cdot (T_{int} - T_{ext}) + \sum_i g_i (T_i - T_{int}) + \sum_j q_j $$

Tu `g_i` (1/min) wiąże strefę z temperaturą `T_i`, na przykład z temperaturą strefy sąsiedniej, a `q_j`
(°C/min) to ciepło dodane wprost, na przykład ciepło z drugiego źródła.

Prawo sterowania (reguła, która wylicza wysterowanie) uwzględnia te wejścia przez dwie wartości,
których już używa. Obie są liczone w jednym miejscu:

$$ b_{eff} = loss + \sum_i g_i, \quad T_{ref} = \frac{loss \cdot T_{ext} + \sum_i g_i T_i + \sum_j q_j}{b_{eff}} $$

Tu `loss` to nauczone `b` albo straty dopasowane bez sąsiadów, którzy są połączeni zawsze.
Przesunięcie `T_ref` wywołane przez składnik ciepła jest ograniczone do `KNOWN_HEAT_MAX_SHIFT_C`.
Bez znanych wejść `b_eff = b` i `T_ref = T_ext`. Uczenie nadal korzysta z niezmienionych `b` i `T_ext`
([Zależności stref, §3](zone_dependencies.md#3-model-ze-znanymi-wejściami)).

### 2.2 Łańcuch obliczania wysterowania

Obecnie wysterowanie ma taką budowę:

$$ u = u_{FF} + u_{PI} $$

gdzie:

- `u_ff1`: podstawowa część wyprzedzająca (feed-forward, FF, czyli moc wyliczona z góry z modelu), wyprowadzona z $b/a$,
- `u_ff2`: wolna korekta (stałe przesunięcie) `FFTrim`,
- `u_ff3`: ograniczona poprawka przewidująca na krótki czas naprzód; domyślnie włączona i równa zeru poza sytuacjami, w których może działać,
- `u_pi`: dyskretny składnik PI.

Do regulatora trafia faktycznie wartość `u_ff_eff + u_pi`. Potem przechodzi przez:

- ograniczenie szybkości zmian,
- anti-windup (zabezpieczenie przed nadmiernym narastaniem całki),
- ograniczenia cyklu i zabezpieczenia; przy ogrzewaniu wodnym wynik jest ograniczony do
  `max_on_percent × max(φ, 0.05)`. Tyle daje pełne wysterowanie przy współczynniku wody `φ`
  (ciepło, które emiter oddaje przy obecnej wodzie, w porównaniu z wodą odniesienia).

Przy ogrzewaniu wodnym regulator liczy moc przy temperaturze wody odniesienia. Wysterowanie wysyłane
do emitera, zanim przejdzie przez krzywą zaworu, to `power / max(φ, 0.05)`, najwyżej 1. Uczenie widzi
wysterowanie pomnożone przez `φ` ([Wejścia wody i kotła, §3.4](water_input.md#34-użycie-w-smartpi)).

---

## 3. Identyfikacja w trakcie pracy

### 3.1 Kolejność uczenia na starcie

W fazie `HYSTERESIS` (faza startowa: termostat grzeje prostą histerezą, zanim model jest gotowy)
uczenie ma ścisłą kolejność:

1. czas martwy reakcji czynnej (gdy urządzenie działa) musi być wiarygodny, zanim zacznie się
   zbieranie danych przydatnych dla `a` (`heat` w HEAT, `cool` w COOL),
2. czas martwy reakcji biernej (gdy urządzenie nie działa) musi być wiarygodny, zanim zacznie się
   zbieranie danych przydatnych dla `b` (`cool` w HEAT, `heat` w COOL),
3. `b` musi zrobić postęp wcześniej niż `a`; pilnuje tego miękka bramka (soft gate).

Reguły zapisane w kodzie:

- `AB_MIN_SAMPLES_B = 8`,
- `AB_MIN_SAMPLES_A = 6`,
- `AB_MIN_SAMPLES_A_CONVERGED = 6`,
- `AB_A_SOFT_GATE_MIN_B = 8`.

SmartPI wychodzi z `HYSTERESIS`, gdy `b` ma 8 pomiarów i `a` ma
6 pomiarów. Oba bufory (listy ostatnich pomiarów) zapełniają się dalej, aż do `AB_HISTORY_SIZE = 31`,
w czasie zwykłej regulacji SmartPI.

W strefie z dwoma źródłami ciepła strefa ma wspólne dopasowanie: jeden model dopasowany do obu
źródeł naraz. Gdy to dopasowanie zostanie opublikowane, `a`, `b` i te historie w SmartPI są z niego
ustawiane, a SmartPI wychodzi wtedy z `HYSTERESIS`. Poza `HYSTERESIS` przy każdym obliczeniu
utrzymywane są `a` i `b` z dopasowania, razem z licznikami, których używają jego sprawdzenia
wiarygodności ([Zależności stref, §5.5](zone_dependencies.md#55-uczenie)).

Zbieżność `b` (to, że jego wartość się ustaliła) jest potrzebna do uczenia `a` z wykrywaniem dryfu
(powolnych zmian modelu). Sprawdza ją `b_converged_for_a()`:

1. `learn_ok_count_b >= 11`,
2. `len(_b_hat_hist) >= 5`,
3. `MAD(b_hat)/Med(b_hat) <= 0.30`,
4. `range(last_5_b_hat)/Med(b_hat) <= 0.10`.

### 3.2 Rzeczywiste okno uczenia

Kod nie używa już stałej `WINDOW_MIN_MINUTES`. Uczenie opiera się teraz na `LearningWindowManager` i na przesuwnym oknie (odcinku czasu, z którego zbierane są pomiary). To okno:

- wymaga dostępnej temperatury zewnętrznej,
- słucha nadzoru cieplnego (governance: reguł, które decydują, kiedy wolno się uczyć i zmieniać wzmocnienia), z wyjątkiem kalibracji,
- blokuje zbieranie danych na starcie, dopóki potrzebne czasy martwe nie są wiarygodne,
- robi przerwę po wznowieniu pracy (`LEARNING_PAUSE_RESUME_MIN = 20`),
- w razie potrzeby ustawia początek okna po końcu czasu martwego,
- pilnuje stabilności mocy przez współczynnik zmienności (`U_CV_MAX`),
- wydłuża okno, dopóki nachylenie (szybkość zmiany temperatury) nie jest uznane za pewne,
- rezygnuje po `DT_MAX_MIN = 240` minutach, jeśli nie pojawi się pewny sygnał,
- wstrzymuje się, gdy ustawiona jest bramka uczenia (warunek, który wstrzymuje uczenie): otwarte drzwi
  do sąsiada albo czas uspokajania się po nich, otwarte okno u sąsiada, jeszcze nieznane ciepło
  drugiego źródła, emiter stygnący, bo kocioł nie jest wezwany do grzania, albo nagrzewający się
  znowu po starcie kotła;
  odrzuca też rozpoczęte okno uczenia za każdym razem, gdy bramka się zaczyna lub kończy,
- odejmuje od zmierzonej zmiany temperatury część, którą powodują znane wejścia: ich tempo
  `known_rate = Σ g_i·(T_i − T) + Σ q_j`, scałkowane (zsumowane) w oknie,
- liczy moc jako 0, gdy strefa woła o ciepło, a kocioł nie jest wezwany do grzania
  ([Wejścia wody i kotła, §4](water_input.md#4-kocioł-który-nie-jest-wezwany-do-grzania)).

W praktyce to, czy okno zostanie przekazane do uczenia, czy wydłużone, zależy od pewności nachylenia, a nie od stałego minimalnego czasu.

### 3.3 Odporne wyznaczanie nachylenia

`ABEstimator.robust_dTdt_per_min()` stosuje warunki ochronne dla nachylenia:

- minimalna zmiana temperatury (amplituda): `DT_DERIVATIVE_MIN_ABS = 0.05°C`,
- minimalna liczba zmian poziomu odczytu: `OLS_MIN_JUMPS = 3`,
- istotność statystyczna: `OLS_T_MIN = 2.5`.

Nachylenie jest liczone z `dt_est.tin_history`, w której jest **surowa** zmierzona temperatura
(nowa próbka, gdy odczyt się zmieni, albo co 60 s). Warunki ochronne liczą zmiany poziomu, więc
opierają się na krokach czujnika: temperatura po filtrze EMA `t_int_lp` zamieniłaby jeden krok
0.1 °C w kilka małych poziomów, a powolne nachylenie przeszłoby za wcześnie i wyszłoby za płaskie
(mniej więcej połowa prawdziwego tempa stygnięcia). Próbki po filtrze są trzymane osobno
(`_tin_lp_history`) i służą tylko do wykrywania czasu martwego (sekcja 3.5).

Sprawdzone nachylenia trafiają potem do metody `ABEstimator.learn()`, która:

- odrzuca wartości fizycznie nierealne (`max_abs_dT_per_min = 0.35`),
- uczy się `b`, gdy `u < U_OFF_MAX`,
- uczy się `a`, gdy `u > U_ON_MIN`,
- wymaga `a > 0` w HEAT i `a < 0` w COOL,
- publikuje wartości po odpornym uśrednieniu (mediana albo mediana ważona, zależnie od konfiguracji).

### 3.4 Wyznaczanie `a` i `b`

`learn()` liczy tak:

- faza OFF (urządzenie wyłączone):

$$ b = -\frac{dT/dt}{T_{int} - T_{ext}} $$

- faza ON (urządzenie włączone):

$$ a = \frac{dT/dt + b \cdot (T_{int} - T_{ext})}{u} $$

Historie są filtrowane medianą i MAD (medianą odchyleń od mediany). Publikowana wielkość `a` jest
ograniczona, ale zachowuje znak trybu; `b` jest zawsze większe od zera.

Przy ogrzewaniu wodnym `u` to moc przy wodzie odniesienia (wysterowanie pomnożone przez `φ`).

### 3.5 Wyznaczanie czasu martwego

`DeadTimeEstimator` to niezależny automat stanów (FSM). Jego kanały opisują
fizyczną reakcję pomieszczenia, a nie kierunek zmiany mocy:

- `deadtime_heat`: opóźnienie, zanim temperatura zacznie rosnąć,
- `deadtime_cool`: opóźnienie, zanim temperatura zacznie spadać.

Dlatego przejście OFF -> ON służy do uczenia `heat` w trybie HEAT, ale `cool` w trybie COOL. Przejście
ON -> OFF służy do uczenia przeciwnej, biernej reakcji.

Kanał staje się wiarygodny, gdy tylko zostanie w nim zapisany co najmniej jeden poprawny pomiar. Publikowane wartości to średnie z historii `heat` i `cool`.

Estymator (moduł, który wyznacza czas martwy) wstrzymuje się, gdy ustawiona jest bramka uczenia. Za
każdym razem, gdy bramka się zaczyna lub kończy, trwająca obserwacja jest kasowana. Przy ogrzewaniu
wodnym dostaje wysterowanie, a nie moc (0 w czasie grzania ciepłej wody użytkowej, CWU). Gdy strefa
woła o ciepło, a kocioł nie jest wezwany do grzania, dostaje 0, więc nie mierzy czasu żadnej reakcji.
Startu kotła po takim wstrzymaniu też nie mierzy: to opóźnienie wynika z wody, a nie z zaworu.

---

## 4. Regulacja Smart-PI

### 4.1 Wzmocnienia PI

`GainScheduler.calculate()` działa tak:

1. jeśli `tau`, znak `a` albo czynny czas martwy nie są wiarygodne, używa bezpiecznych wartości
   `KP_SAFE = 0.55` i `KI_SAFE = 0.010`,
2. w przeciwnym razie liczy wzmocnienia metodą IMC/SIMC (znane reguły strojenia regulatorów) z wielkości wzmocnienia czynnego:

$$ K_p = \frac{1}{|a| \cdot (\lambda + L)} $$

3. liczy $K_i = K_p / \tau$; gdy działa wymiana ciepła, $\tau = 1 / (loss + \sum_i g_i)$, więc wymiana ją skraca (sekcja 2.1),
4. przycina wzmocnienia do dozwolonego zakresu i stosuje zamrożenia z nadzoru (governance).

Czynny czas martwy to `deadtime_heat` w HEAT i `deadtime_cool` w COOL.

Ważne:

- tłumienie w paśmie przy nastawie (near band; nastawa to temperatura zadana) działa płynnie między skonfigurowaną krawędzią tego pasma a nastawą,
- blisko nastawy `NEAR_BAND_TAPER_KI_MIN` zmniejsza wzmocnienie całkujące silniej niż `NEAR_BAND_TAPER_KP_MIN` (wzmocnienie proporcjonalne).

### 4.2 Część wyprzedzająca (feed-forward)

Podstawowa część wyprzedzająca jest liczona z:

$$ k_{ff} = \frac{b_{eff}}{a} $$

oraz:

$$ u_{ff1} = clamp(k_{ff} \cdot (SP - T_{ref}), 0, 1) \cdot warmup\_scale $$

Bez znanych wejść `b_eff = b` i `T_ref = T_ext` (sekcja 2.1). FF3, trajektoria nastawy, bliźniak
cieplny (model pomieszczenia liczony równolegle, do przewidywań), pasmo przy nastawie i okna korekty
FF (FF trim) używają tych samych `b_eff` i `T_ref`.

W COOL $a < 0$, więc $k_{ff} < 0$. Gdy na zewnątrz jest cieplej niż
nastawa, dwie ujemne wartości dają razem poprawnie dodatnią moc
chłodzenia.

`warmup_scale` nie jest prostym przełącznikiem włącz/wyłącz. Zależy od:

- liczby udanych odcinków uczenia,
- liczby cykli od resetu,
- wiarygodności `tau`.

Do tego dochodzą:

- `u_ff2`: wolna korekta `FFTrim`,
- `ABConfidence`: zasady zaufania do `a/b`,
- w trybie `AB_BAD` przejście na zerową część wyprzedzającą po `AB_BAD_PERSIST_CYCLES = 3`.

`FFTrim` jest też zamrożony (`boiler_not_heating`), gdy strefa woła o ciepło, a kocioł nie jest
wezwany do grzania, oraz gdy emiter strefy uspokaja się przed takim okresem lub po nim.

### 4.3 FF3

`ff3.py` dodaje poprawkę przewidującą. Jest domyślnie włączona i można ją wyłączyć w konfiguracji:

- horyzont (jak daleko naprzód patrzy) liczy `ff3_predictor.compute_ff3_horizon()`: `deadtime_cycles + FF3_RESPONSE_LOOKAHEAD_CYCLES`, w zakresie od `FF3_MIN_HORIZON_CYCLES` do `FF3_MAX_HORIZON_CYCLES`,
- lokalna prognoza w otwartej pętli (bez sprzężenia zwrotnego) dokładnym, dyskretnym modelem 1R1C (jeden opór cieplny, jedna pojemność cieplna) z ZOH (wysterowanie stałe w każdym kroku), bez wywołania `ThermalTwin1R1C.step()` i bez aktualizacji obserwatora bliźniaka,
- największy wpływ (autorytet) `FF3_MAX_AUTHORITY = 0.20`, mnożony przez `authority_factor`,
- krok próbowania `FF3_DELTA_U = 0.05`,
- lokalna ocena kwadratowa z kosztem nadążania, kosztem końcowym, kosztem przeregulowania (przekroczenia nastawy) i kosztem ruchu (zmiany wysterowania).

FF3 nie działa, jeśli którykolwiek z tych warunków nie jest spełniony:

- FF3 włączona w konfiguracji,
- tryb grzania,
- dostępna temperatura zewnętrzna,
- wiarygodne `tau`,
- bliźniak cieplny zainicjowany, a jego prognoza nadaje się do użycia,
- bliźniak cieplny po fazie rozruchu,
- poprawny stan ustalony bliźniaka,
- brak kalibracji,
- brak zrzutu mocy,
- brak niedawnej zmiany nastawy,
- poza strefą nieczułości (deadband, bardzo wąskie pasmo wokół nastawy),
- w paśmie przy nastawie,
- zgodny stan nadzoru (governance),
- brak aktywnej trajektorii `setpoint`,
- widać wiarygodne zakłócenie z zewnątrz.

Wykrywanie zakłócenia przyjmuje model, który ma poprawną budowę i nadaje się do użycia, nawet gdy `model_reliable = False`.
Wtedy `prediction_quality = "degraded"`, a wpływ FF3 jest zmniejszany przez `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR`.
`warming_up = True` i `T_steady_valid = False` nadal blokują FF3 bezwzględnie.

To wykrywanie zakłócenia nie używa `T_steady` jako fałszywie ujemnego wyniku RMSE.
Opiera się na:

- trwałej rozbieżności między prognozą bliźniaka a tym, co faktycznie widać, uznanej za wiarygodną resztę, czyli część zachowania, której model nie tłumaczy (`bias_warning` lub `external_gain_detected` lub `external_loss_detected`),
- następnie na zgodności dynamiki, sprawdzanej przez `perturbation_dTdt` i w razie potrzeby przez zmierzone nachylenie temperatury.

Przy takich zasadach FF3 nie jest już ogólnym optymalizatorem w paśmie przy nastawie. Służy tylko do odrabiania skutków zakłóceń i nie uruchamia już osobnego restartu cyklu przy wejściu w strefę nieczułości.

### 4.4 Obsługa nastawy

`SmartPISetpointManager` prowadzi teraz analityczną trajektorię (przebieg nastawy w czasie, wyliczony wzorem) dla gałęzi P (części proporcjonalnej).
Ta trajektoria włącza się tylko wtedy, gdy pojawi się duża różnica temperatur, model jest uznany za wiarygodny, a zmiana nastawy wymaga kształtowania odniesienia dla części proporcjonalnej.

Zasada jest taka:

- gałąź I (całkująca) nadal używa niefiltrowanej nastawy,
- gałąź P dostaje `filtered_setpoint`,
- gałąź P zachowuje niefiltrowaną nastawę, dopóki nie zostanie osiągnięta przewidziana strefa hamowania,
- duża zmiana nastawy raz uzbraja późne hamowanie; w HEAT późniejsze wejście w hamowanie porównuje przewidywany wzrost temperatury z odległością do `target - LANDING_SAFETY_MARGIN_C`, bez ponownego sprawdzania progu uzbrojenia,
- wejście wywołane zakłóceniem nadal wymaga progu dużej różnicy temperatur,
- do wykrycia strefy hamowania służą: dokładny nauczony model 1R1C, `deadtime_cool`, opóźnienie do końca bieżącego cyklu i moc już zadana na ten cykl,
- potem, blisko celu, stosowana jest płynna trajektoria późnego hamowania, która zachowuje minimalne dodatnie żądanie części proporcjonalnej,
- przy trajektoriach nastawy w HEAT limit lądowania (landing cap) może ograniczyć wewnętrzne wysterowanie po obliczeniu PI, gdy model przewiduje, że zgromadzone ciepło wystarczy do osiągnięcia celu,
- gdy hamowanie nie jest już potrzebne, filtrowane odniesienie stopniowo wraca do niefiltrowanego celu, zanim trajektoria się skończy,
- w trajektorii wywołanej zmianą nastawy wejście w fazę `release` (zwalnianie) blokuje tę fazę do końca trajektorii, bez powrotu do `tracking` (nadążania),
- `trajectory_active` mówi, czy analityczna trajektoria działa,
- trajektoria kończy się dopiero wtedy, gdy przekazanie sterowania pozostaje bezuderzeniowe (bez skoku wysterowania), zmierzona temperatura jest dość blisko celu, a stan lądowania pozwala na zwolnienie; albo gdy warunki wiarygodności przestają być spełnione.

Limit lądowania używa dyskretnej postaci modelu 1R1C w wewnętrznej, liniowej skali wysterowania:

$$
\alpha = e^{-b \cdot h}
$$

$$
T_{pred} = T_{ext} + (T - T_{ext}) \cdot \alpha + \frac{a}{b}(1-\alpha) \cdot u
$$

Limit wylicza największe wysterowanie, przy którym przewidywana temperatura zostaje poniżej `target - LANDING_SAFETY_MARGIN_C`. Działa po zwykłym obliczeniu PI i przed miękkimi ograniczeniami. Dzięki temu `u_pi` w diagnostyce pokazuje surowy wynik PI, a `landing_u_cap` wyjaśnia końcowe obniżenie wysterowania.

Standardowy blok `live.setpoint` publikuje podsumowanie:

- `filtered_setpoint`,
- `trajectory_active`,
- `trajectory_source`,
- `landing_active`,
- `landing_reason`,
- `landing_u_cap`,
- `landing_coast_required`.

Te same dane, w obu trybach, pokazują szczegóły trajektorii i lądowania w
`live.analysis.trajectory` i `live.analysis.landing`:

- `start_setpoint`, `target_setpoint`, `tau_ref_min`, `elapsed_s` i `phase`,
- `pending_target_change_braking`, `braking_needed` i `model_ready`,
- `remaining_cycle_min`, `next_cycle_reference`, `bumpless_delta` i `bumpless_ready`,
- `setpoint_for_p_cap`, `predicted_temperature`, `predicted_rise` i `target_margin`,
- `release_allowed`, `time_to_target_min`, `release_blocked_by_slope`,
- `command_before_cap` i `command_after_cap`.

Kształtowanie odniesienia nastawy dotyczy tylko gałęzi P. Dzięki temu część całkująca dostaje nienaruszoną nastawę, a uczenie nie jest zakłócane. Limit lądowania to osobny ogranicznik wysterowania, działający po PI, dla trajektorii nastawy w HEAT. Nie zmienia całki ani krzywej linearyzacji zaworu.

Obecny kod ma też wyraźną blokadę dodatniego narastania całki w fazach doganiania nastawy:

- po dużej zmianie nastawy,
- po wznowieniu z `hvac_off`,
- po wznowieniu po wykrytym otwarciu okna,
- po wznowieniu po zrzucie mocy,
- w czasie trajektorii odrabiania zakłócenia.

Ta blokada nie przeszkadza całce maleć. Jeśli zapisana całka działa już przeciwko
żądanemu powrotowi do nastawy, blokada pozwala tej przeciwnej „pamięci” zejść aż do zera.
Nadal blokuje jednak każde nowe narastanie powyżej zera w chronionym kierunku. Dzięki temu
ujemna całka, która została po poprzedniej nastawie, nie trzyma pomieszczenia poniżej
nowej, wyższej nastawy.

Blokada jest zwalniana dopiero przy spełnieniu dwóch warunków:

- błąd zwolnienia (różnica między nastawą a temperaturą, sprawdzana przy zwalnianiu) wrócił blisko skonfigurowanej szerokości strefy nieczułości,
- nachylenie powrotu (ze znakiem) trwale i wyraźnie spadło.

Test nachylenia łączy dwa kryteria:

- próg względny, liczony od największego nachylenia powrotu zaobserwowanego w fazie doganiania,
- dolną granicę bezwzględną w `°C/h`, żeby bardzo małe szczytowe nachylenie nie zwalniało blokady zbyt łatwo.

Sygnał używany jako ten błąd zwolnienia zależy od tego, czy trwa trajektoria nastawy:

- w czasie aktywnej trajektorii nastawy zwolnienie opiera się na `error_p`, czyli błędzie względem filtrowanej nastawy, który faktycznie śledzi gałąź proporcjonalna,
- poza trajektorią zwolnienie opiera się na `error_i`, czyli błędzie względem niefiltrowanej nastawy.

Ten podział sprawia, że blokada nie pozostaje aktywna, gdy trajektoria już doszła do swojego filtrowanego odniesienia, a układ fizycznie ustalił się poniżej niefiltrowanej nastawy.

Surowy błąd ze znakiem nadal jest punktem odniesienia dla bezpieczeństwa: jeśli `error_i <= 0`, blokada jest zwalniana od razu.

Nachylenie powrotu jest traktowane symetrycznie:

- w trybie grzania dodatnie nachylenie oznacza ruch w stronę nastawy,
- w trybie chłodzenia nachylenie jest odwracane, więc ten sam fizyczny odczyt nadal jest poprawny.

Dopóki blokada działa, `TRAJECTORY_I_RUN_SCALE` może ograniczać dodatnie narastanie całki w czasie trajektorii. Po zwolnieniu blokady to tłumienie przestaje działać: gałąź I wraca do zwykłej dynamiki, żeby usunąć pozostały błąd ustalony.

Wznowienia po otwarciu okna i po zrzucie mocy w trybie grzania mają jeszcze jeden etap:

- przy wznowieniu SmartPI najpierw włącza jawne `I:HOLD` (wstrzymanie całki),
- to wstrzymanie trwa, dopóki reakcja na grzanie mieści się jeszcze w użytecznym oknie `deadtime_heat`,
- gdy ta faza się kończy, SmartPI ponownie ocenia pozostały błąd ze znakiem,
- włącza dodatnią blokadę tylko wtedy, gdy ten błąd jest wciąż na tyle duży, że to rzeczywiście faza doganiania,
- w przeciwnym razie gałąź I od razu wraca do zwykłej pracy.

Ta kolejność sprawia, że całka nie „uczy się” stanu przejściowego po wznowieniu, w którym nadal przeważa czas martwy grzania.

Przejściowe stany doganiania nie są przywracane po restarcie:

- aktywna trajektoria analityczna jest kasowana,
- blokada całki jest resetowana,
- każdy tymczasowy `integral_hold_mode` jest kasowany.

Po restarcie zostaje więc tylko przydatna, trwała pamięć PI. Przejściowe stany regulacji nie są przywracane, bo poza swoją pierwotną sesją pracy nie mają już fizycznego sensu. Czas zegarowy (rzeczywisty) ostatniego poprawnego kroku aktywnej regulacji jest zapisywany i nie jest odświeżany, gdy HVAC jest wyłączony (OFF). Jeśli bezczynność trwa ponad 96 godzin, przy wznowieniu pamięć PI regulatora jest czyszczona, a nauczony model A/B zostaje.

### 4.5 Strefa nieczułości, pasmo przy nastawie i zabezpieczenia

`DeadbandManager` obsługuje:

- symetryczną strefę nieczułości z bezwzględną histerezą (`DEADBAND_HYSTERESIS = 0.025`),
- niesymetryczne pasmo przy nastawie w trybie grzania,
- automatyczny dobór szerokości tego pasma, gdy `deadtime_heat` i model nadają się do użycia,
- w innym przypadku powrót do skonfigurowanych progów.

Obecne dodatkowe zabezpieczenia to:

- `SmartPIGuards`: `guard_cut` i `guard_kick`,
- anti-windup z nadążaniem (tracking),
- blokada całki ze znakiem przy wznowieniach, w fazach doganiania i przy zmianach nastawy,
- jawne `I:HOLD` w fazie `deadtime_heat` po wznowieniu, dla `window_resume` i `power_shedding_resume` w trybie grzania,
- logika wstrzymania w strefie nieczułości,
- przy ogrzewaniu wodnym wstrzymanie `no_heat`: wysterowanie i całka są wstrzymane w czasie grzania ciepłej wody użytkowej, a sama całka jest wstrzymana, gdy woda daje prawie zero ciepła ([Wejścia wody i kotła, §3.4](water_input.md#34-użycie-w-smartpi)),
- otwarte okno u sąsiada, gdy połączenie z tym sąsiadem jest aktywne: reakcja wybrana dla tego sąsiada. To albo wyłączenie (tą samą drogą co przy otwartym oknie, z chronionym wznowieniem), albo preset strefy „ochrona przed mrozem” lub „eco”. Ta reakcja nigdy nie żąda więcej grzania ani chłodzenia niż nastawa ([Zależności stref, §4.5](zone_dependencies.md#45-okno-u-połączonego-sąsiada)).

Obliczenie rozróżnia też dwie strefy wokół nastawy:

- strefę nieczułości z histerezą, która utrzymuje stabilny stan `in_deadband`,
- właściwą, wewnętrzną strefę nieczułości, określoną przez `abs(error_i) < deadband_c` (gdy stała czasowa jest już pewna). To ona naprawdę zamraża P i I.

Dzięki temu podziałowi P i I nie zostają zamrożone tylko dlatego, że histereza wciąż trzyma stan strefy nieczułości, choć błąd już z niej wyszedł.

### 4.6 Autokalibracja

`AutoCalibTrigger` nadzoruje algorytm z zewnątrz, poza prawem sterowania.

Co jest zrobione w kodzie:

- pierwszy zapis stanu (snapshot), gdy `tau`, `deadtime_heat` i `deadtime_cool` są wiarygodne,
- zapis zastępczy po `AUTOCALIB_DT_COOL_FALLBACK_DAYS = 7` dniach bez wiarygodnego `deadtime_cool`,
- okresowy zapis co `AUTOCALIB_SNAPSHOT_PERIOD_H = 120` godzin,
- sprawdzanie co godzinę,
- przerwa ochronna `AUTOCALIB_COOLDOWN_H = 24` godziny,
- progi zastoju `AUTOCALIB_A_MAD_THRESHOLD = 0.25` i `AUTOCALIB_B_MAD_THRESHOLD = 0.30`,
- udane zakończenie dopiero po co najmniej `AUTOCALIB_EXIT_NEW_OBS_MIN = 1` nowej obserwacji zarówno dla `a`, jak i dla `b`, oraz przy spójnych czasach martwych,
- do `AUTOCALIB_MAX_RETRIES = 3` prób,
- planowane opóźnienie ponownej próby `AUTOCALIB_RETRY_DELAY_H = 6` godzin.

Wymuszonym cyklem zarządza `CalibrationManager`:

- `COOL_DOWN` (stygnięcie) do `sp - 0.3°C`,
- `HEAT_UP` (grzanie),
- `COOL_DOWN_FINAL` (końcowe stygnięcie) z powrotem do `sp`.

Trwająca kalibracja jest przerywana, gdy otwarte okno u sąsiada wywoła reakcję. Jest też
przerywana, gdy strefa woła o ciepło, a kocioł nie jest wezwany do grzania, bo reakcja strefy na
grzanie by nie nastąpiła.

Decyzje nadzorcy są publikowane jako zdarzenia Home Assistant. Każde zdarzenie zawiera `entity_id` (encja climate termostatu) i `name` (nazwa termostatu):

| Zdarzenie                          | Kiedy                                                                                              | Inne pola                                                             |
| ---------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `smartpi_autocalib_snapshot_taken` | wykonany jest zapis stanu (snapshot)                                                               | `reason` (`initial`, `fallback_7d`, `rolling`), `dt_cool_unavailable` |
| `smartpi_autocalib_triggered`      | kalibracja zostaje uruchomiona przez zastój albo przez `force_smartpi_calibration`                 | `triggered_params`, `retry_count`                                     |
| `smartpi_autocalib_success`        | nadzorowana kalibracja kończy się, a wszystkie kryteria są spełnione                               | `improved_params`, `retry_count`                                      |
| `smartpi_autocalib_retry`          | nadzorowana kalibracja kończy się bez spełnienia wszystkich kryteriów i zaplanowana jest ponowna próba | `retry_count`, `missing_criteria`                                 |
| `smartpi_autocalib_degraded`       | ostatnia próba się nie udaje i model zostaje oznaczony jako zdegradowany                           | `retry_count`                                                         |

Przed tym formatem zdarzenia miały nazwę termostatu w `entity_id`, a rekorder Home Assistant (zapis
historii) ją odrzucał. Automatyzacja, która szukała nazwy w `entity_id`, powinna teraz szukać jej
w `name`.

---

## 5. Architektura oprogramowania

### 5.1 Moduły koordynujące

| Plik                      | Klasa            | Rola                                                                                  |
| ------------------------- | ---------------- | ------------------------------------------------------------------------------------- |
| `prop_algo_smartpi.py`    | `SmartPI`        | fasada algorytmu (jeden punkt wejścia), koordynacja całości                           |
| `prop_handler_smartpi.py` | `SmartPIHandler` | most do Home Assistant: trwały zapis stanu, okresowy timer, usługi, atrybuty          |

Moduł obsługi odczytuje wejścia z instalacji grzewczej przez plik wtyczki `heating_inputs.py`:
dane o wodzie i wezwanie kotła ze źródła wybranego w opcjach termostatu (encje, dostawca albo
kocioł centralny Versatile Thermostat).

Moduł obsługi odczytuje ustawienia termostatu klucz po kluczu, przez plik wtyczki `settings.py`,
gdy algorytm startuje. Najpierw wpis Versatile Thermostat, bez ustawień należących do jednego
termostatu (czasy TPI z Versatile Thermostat nie są używane). Potem ustawienia wspólne
(`SHARED_SETTINGS` w `const.py`: strefa nieczułości, histereza, współczynnik tau zwolnienia, filtr
nastawy, FF3, osłabione P, pełna diagnostyka) z wpisu „SmartPI defaults” albo z własnego wpisu
termostatu, gdy jego przełącznik „Use the SmartPI defaults” jest wyłączony. Potem własny wpis
termostatu: jego ustawienia „tylko ten termostat” (`THERMOSTAT_SETTINGS`: rodzaj ogrzewania,
minimalne czasy załączenia i wyłączenia, restart cyklu PWM, krzywa zaworu), jego sąsiedzi, strefa
i źródło danych o wodzie. Nieustawione ustawienie „tylko ten termostat” nie jest używane. Wartości
startowe z kodu (`DEFAULT_OPTIONS`) służą tylko do wypełnienia formularzy; ustawienie wspólne,
którego brakuje we wpisie zmienionym ręcznie, dostaje wartość z formularza i jest raz zapisane w
dzienniku jako błąd konfiguracji. Zapisane opcje zastępują w całości pierwsze dane wpisu, więc
ustawienie usunięte w opcjach zostaje usunięte. Zapis wpisu wartości domyślnych przeładowuje każdy
termostat, który z nich korzysta.

### 5.2 Moduły `smartpi/` faktycznie używane

| Moduł                  | Główna rola                                                 |
| ---------------------- | ----------------------------------------------------------- |
| `const.py`             | stałe, typy wyliczeniowe (enum), macierz nadzoru            |
| `learning.py`          | wyznaczanie nachylenia, `ABEstimator`, `DeadTimeEstimator`  |
| `ab_aggregator.py`     | łączenie pomiarów `a/b` medianą albo medianą ważoną         |
| `ab_drift.py`          | wykrywanie trwałego dryfu `a/b` i ponowne centrowanie       |
| `learning_window.py`   | zarządzanie oknem uczenia i jego warunki ochronne           |
| `gains.py`             | liczenie i zamrażanie wzmocnień                             |
| `controller.py`        | dyskretny PI, anti-windup, wstrzymanie, histereza           |
| `deadband.py`          | strefa nieczułości i pasmo przy nastawie                    |
| `setpoint.py`          | analityczna trajektoria nastawy i limit lądowania w HEAT    |
| `integral_guard.py`    | blokada wzrostu dodatniej całki w fazach powrotu do nastawy |
| `feedforward.py`       | koordynacja `u_ff1/u_ff2/u_ff3`                             |
| `ff_trim.py`           | przyczynowa wolna korekta (bias) i ograniczone plany przypisania między korektę FF (trim) a całkę |
| `command_ownership.py` | zamrożona projekcja wysterowania i śledzenie jej powiązania z harmonogramem cykli (scheduler) |
| `ff_trim_periodic.py`  | wybór okien okresowej równowagi, zamykanych w tej samej fazie |
| `ff_ab_confidence.py`  | zasady zaufania do `a/b`                                    |
| `ff3.py`               | ograniczona poprawka przewidująca FF3                       |
| `governance.py`        | decyzje o zamrożeniu                                        |
| `calibration.py`       | automat stanów (FSM) wymuszonej kalibracji                  |
| `autocalib.py`         | nadzór i automatyczne uruchamianie                          |
| `guards.py`            | `guard_cut` i `guard_kick`                                  |
| `thermal_twin_1r1c.py` | bliźniak cieplny i diagnostyka przewidująca                 |
| `diagnostics.py`       | standardowe bieżące dane diagnostyczne                      |
| `diagnostic_history.py` | wersjonowana koperta danych i stałe serie dla Recordera (zapisu historii) |
| `tint_filter.py`       | adaptacyjne filtrowanie temperatury wewnętrznej             |
| `timestamp_utils.py`   | przeliczanie czasu monotonicznego i zegarowego              |
| `effective_model.py`   | znane wejścia i ich złożenie w `b_eff`, `T_ref`             |
| `dependencies.py`, `dependency_inputs.py` | elementy zależności, ich sprawdzanie i stan; odczyty do jednego obliczenia |
| `dependency_model.py`  | znane wejścia, bramki uczenia, reakcja na okno u sąsiada; strefa z dwoma źródłami |
| `coupling_estimator.py` | uczenie współczynników wymiany `k`                         |
| `zone_fit.py`, `source_model.py`, `source_candidates.py` | wspólne dopasowanie strefy, ciepło i skale czasu drugiego źródła |
| `split.py`, `source_settings.py`, `source_link.py` | reguły podziału i pętla przydziału, ustawienia źródeł, łącze między źródłami |
| `water_input.py`       | współczynnik wody `φ` z danych o wodzie                     |
| `boiler_call.py`       | czy kocioł grzeje dla strefy, która woła o ciepło           |

Moduły rozszerzeń są opisane w
[Zależności stref, §6](zone_dependencies.md#6-komponenty-i-interfejsy) i
[Wejścia wody i kotła, §5](water_input.md#5-komponenty-i-interfejsy).

### 5.3 Wewnętrzna budowa `SmartPI`

Składniki tworzone przez `SmartPI.__init__()` to między innymi:

```python
self.gov = SmartPIGovernance(name)
self.sp_mgr = SmartPISetpointManager(name, enabled=use_setpoint_filter)
self.ctl = SmartPIController(name)
self.est = ABEstimator(mode=aggregation_mode)
self.learn_win = LearningWindowManager(name)
self.deadband_mgr = DeadbandManager(name, near_band_deg)
self.calibration_mgr = CalibrationManager(name)
self.gain_scheduler = GainScheduler(name)
self.tint_filter = AdaptiveTintFilter(name, enabled=ENABLE_ADAPTIVE_TINT_FILTER)
self.integral_guard = SmartPIIntegralGuard(name)
self.twin = ThermalTwin1R1C(dt_s=SMARTPI_RECALC_INTERVAL_SEC, gamma=0.1)
self.guards = SmartPIGuards()
self.autocalib = AutoCalibTrigger(name)
self._ff_trim = FFTrim()
self._fftrim_observer = CausalFFTrimObserver(cycle_min)
self._fftrim_periodic_observer = PeriodicFFTrimObserver(cycle_min)
self._command_ownership = CommandOwnershipTracker()
self._ab_confidence = ABConfidence()
self.dependency_state = DependencyState()
self.dependency_model = DependencyModel(self.dependency_state)
self.water = WaterInput()
self.boiler = BoilerCall()
```

### 5.4 Obecny trwały zapis stanu

Obserwator przyczynowy (causal observer) zestawia drugi, ważony czasem przebieg
przypisania sterowania (która część odpowiada za wysterowanie) z przebiegiem
rzeczywistej mocy. Dzięki temu ścisłe okno w strefie nieczułości może oddzielić
stałe przesunięcie całki $J=mean(K_i integral)$ od fizycznego niedoboru
$D=H-mean(u_{applied})$. Po trzech niezależnych, spójnych oknach jedna
ograniczona operacja (transakcja) stosuje
`delta_trim = alpha * lambda * (J + D)` i
`delta_u_i = -alpha * lambda * J`, a więc
`delta_u_cmd = alpha * lambda * D`. Ten sam współczynnik `alpha` pilnuje wpływu
korekty FF (trim), granic gałęzi FF, pojemności całki i zapasu wysterowania.
Operacja wykonuje się tylko na granicy cyklu, po anti-windup, i działa tak samo
w przestrzeni mocy modelu ze znakiem, w HEAT i w COOL.
Przeniesienia mniejsze niż ogólny próg dokładności elementu wykonawczego są
nadal pomijane, chyba że wyraźnie zmniejszają przeciwstawne, zapisane udziały
korekty FF i całki. To zagęszczanie przypisania zachowuje te same niepodzielne
granice i ten sam niezmiennik wysterowania.

Obserwator stanu ustalonego i obserwator okresowy korzystają z jednego wspólnego
przebiegu mocy i przypisania, ale mają osobne próbki cieplne, osobny stan
wygaszania (washout) i osobne bufory zapisu. Ścieżka okresowa uznaje nasycenie
na dolnej granicy i wstrzymanie całki za części cyklu, który wywołuje sam regulator.
Zamyka się dopiero po powrocie do tej samej fazy cieplnej, po co najmniej
`max(6 VT cycles, 2 × dead time)`, więc jej horyzont nie ma stałej dolnej granicy
trzydziestu minut. Jej przyczynowa, ważona czasem ocena nigdy nie przenosi
przypisania całki; trwałe poprawki nadal podlegają tej samej szybkości uczenia
korekty FF i temu samemu limitowi wpływu.

Obecne dane zapisywane przez `SmartPI.save_state()` zawierają między innymi:

```python
{
    "est_state": {...},
    "dt_est_state": {...},
    "gov_state": {...},
    "ctl_state": {...},
    "sp_mgr_state": {...},
    "lw_state": {...},
    "db_state": {...},
    "cal_state": {...},
    "gs_state": {...},
    "integral_guard_state": {...},
    "twin_state": {...},
    "guards_state": {...},
    "ac_state": {...},
    "ff_v2_trim": {...},
    "tint_filter_state": {...},
    # only when used:
    "dependencies": {...},          # learned exchanges, the zone's fit and the source's state
    "dependency_reset_token": "...",  # the last reset asked by a form, applied once
    "water": {...},                 # the water input's availability and averages
}
```

Klucze opcjonalne są opisane w [Zależności stref, §8](zone_dependencies.md#8-zapisywany-stan)
i [Wejścia wody i kotła, §7](water_input.md#7-zapisywany-stan). Dane bez tych kluczy wczytują się tak jak wcześniej.

Trwale zapisywana jest tylko nauczona korekta FF. Okna przypisania, oczekujące
propozycje i przeniesienia całki są przejściowe; po restarcie obserwator jest
budowany od nowa i nadal obowiązuje istniejące zamrożenie korekty FF na trzy cykle.

Przy sterowaniu przełącznikiem (switch, włącz/wyłącz) SmartPI tuż przed wysłaniem
żądania do harmonogramu cykli (scheduler) przygotowuje zamrożony zapis przypisania.
Wywołanie zwrotne na starcie cyklu (callback) wiąże go tylko wtedy, gdy zgadzają
się: tryb HVAC, przewidziane w projekcji czasy ON/OFF i faktycznie zrealizowana moc.
Zwykłe zaokrąglanie czasów do pełnych sekund jest ujęte w projekcji; ograniczenie
minimalnego czasu pozostaje odrzuconym odcinkiem przyczynowym. Gdy wywołania
zwrotnego brak albo się nie zgadza, nigdy nie jest używany bieżący stan regulatora.
Zapisy wysterowania zaworu stosują tę samą zasadę zamrożonego przypisania
w przestrzeni elementu wykonawczego, zanim zostaną przeliczone na liniowy model.

### 5.5 Publikowana diagnostyka

`diagnostics.py` udostępnia widok algorytmu i standardowe widoki bieżące:

- `build_diagnostics()`: wersja skrócona albo pełna, zależnie od `debug_mode`,
- `build_published_diagnostics()`: standardowe dane `live` dla osobnego czujnika diagnostycznego,
- `build_debug_diagnostics()`: funkcja zgodności wstecznej, która zwraca te same standardowe dane.

`diagnostic_history.py` opakowuje te dane w `schema_version`, `live` i stały
blok `history` z ośmioma seriami. Zwykłe encje czujników wyłączają `live`
z zapisu Recordera; podklasa czujnika debug zapisuje go wprost. Tryb debug
zmienia tylko czas przechowywania danych i nie dodaje drugiego bloku diagnostycznego.

`analysis.learning.learn_ok_count` i `learn_skip_count` to liczniki bieżącego
działania, liczone dla obecnego procesu Home Assistant. Zapisywane liczniki modelu
są wewnętrzne; służą do oceny jakości modelu i decyzji o fazie rozruchu (warm-up).
Gdy bliźniak cieplny nadaje się do użycia, jego publiczne pola diagnostyczne są
widoczne w `analysis.twin`.

`live` zawiera też te bloki, ale tylko wtedy, gdy są używane:

- `dependencies`: złożony model (`b_eff`, `t_ref`), bramka uczenia, reakcja na okno,
  po jednym wpisie na sąsiada oraz `second_source` dla głównego źródła w strefie z dwoma źródłami
  ciepła; termostat, który działa jako dodatkowe źródło, pokazuje tylko swoją rolę
  ([Zależności stref, §9](zone_dependencies.md#9-diagnostyka));
- `water`: współczynnik wody, to, skąd pochodzi, wstrzymania i źródło każdego sygnału wody
  (`inputs`), przy wodnym rodzaju ogrzewania;
- `boiler`: czy kocioł jest wezwany do grzania, wybrane źródło (`entities`, `provider:<name>`,
  `vtherm_central_boiler`) i jego sygnały (`inputs`), powód i zapotrzebowanie oraz tryb uczenia,
  który z tego wynika; widoczny, gdy wybrano źródło wezwania
  ([Wejścia wody i kotła, §8](water_input.md#8-diagnostyka)).

Gdy są dane o wodzie, wartości `power` to moc przy wodzie odniesienia, a
`current_cycle_percent` i `next_cycle_percent` to wysterowanie wysyłane do emitera.

`feedforward.fftrim.command_ownership` pokazuje ostatni stan powiązania, powód
odrzucenia, numer kolejny żądania, moce i czasy (żądane, z projekcji i zrealizowane),
różnicę mocy między nimi oraz flagę ograniczenia czasowego. Te wartości służą
tylko do obserwacji: wyjaśniają przyczynowe przyjęcie lub odrzucenie, ale nie
zmieniają decyzji o uczeniu.

---

## 6. Ważne stałe

### 6.1 Uczenie

| Stała                          | Wartość |
| ------------------------------ | ------- |
| `AB_HISTORY_SIZE`              | `31`    |
| `AB_MIN_SAMPLES_B`             | `8`     |
| `AB_MIN_SAMPLES_A`             | `6`     |
| `AB_MIN_SAMPLES_A_CONVERGED`   | `6`     |
| `AB_A_SOFT_GATE_MIN_B`         | `8`     |
| `AB_CONFIDENCE_MIN_SAMPLES_A`  | `11`    |
| `AB_CONFIDENCE_MIN_SAMPLES_B`  | `11`    |
| `AB_B_CONVERGENCE_MIN_SAMPLES` | `11`    |
| `AB_B_CONVERGENCE_MIN_BHIST`   | `5`     |
| `AB_B_CONVERGENCE_MAD_RATIO`   | `0.30`  |
| `AB_B_CONVERGENCE_RANGE_RATIO` | `0.10`  |
| `DT_DERIVATIVE_MIN_ABS`        | `0.05`  |
| `OLS_MIN_JUMPS`                | `3`     |
| `OLS_T_MIN`                    | `2.5`   |
| `DT_MAX_MIN`                   | `240`   |
| `U_OFF_MAX`                    | `0.05`  |
| `U_ON_MIN`                     | `0.20`  |

### 6.2 Regulacja

| Stała                         | Wartość |
| ----------------------------- | ------- |
| `KP_SAFE`                     | `0.55`  |
| `KI_SAFE`                     | `0.010` |
| `MAX_STEP_PER_MINUTE`         | `0.25`  |
| `SETPOINT_BOOST_RATE`         | `0.50`  |
| `AW_TRACK_TAU_S`              | `120.0` |
| `SMARTPI_RECALC_INTERVAL_SEC` | `60`    |
| `LEARNING_PAUSE_RESUME_MIN`   | `20`    |

### 6.3 Pasma i kształtowanie nastawy

| Stała                      | Wartość |
| -------------------------- | ------- |
| `DEFAULT_DEADBAND_C`       | `0.05`  |
| `DEADBAND_HYSTERESIS`      | `0.025` |
| `DEFAULT_NEAR_BAND_DEG`    | `0.40`  |
| `NEAR_BAND_TAPER_KP_MIN`   | `0.75`  |
| `NEAR_BAND_TAPER_KI_MIN`   | `0.45`  |
| `SETPOINT_BOOST_THRESHOLD` | `0.3`   |
| `SETPOINT_BOOST_ERROR_MIN` | `0.3`   |

### 6.4 Autokalibracja i FF

| Stała                             | Wartość |
| --------------------------------- | ------- |
| `AUTOCALIB_SNAPSHOT_PERIOD_H`     | `120`   |
| `AUTOCALIB_DT_COOL_FALLBACK_DAYS` | `7`     |
| `AUTOCALIB_COOLDOWN_H`            | `24`    |
| `AUTOCALIB_A_MAD_THRESHOLD`       | `0.25`  |
| `AUTOCALIB_B_MAD_THRESHOLD`       | `0.30`  |
| `AUTOCALIB_MAX_RETRIES`           | `3`     |
| `AUTOCALIB_RETRY_DELAY_H`         | `6`     |
| `AUTOCALIB_EXIT_NEW_OBS_MIN`      | `1`     |
| `FF_TRIM_RHO`                     | `0.15`  |
| `FF_TRIM_LAMBDA`                  | `0.05`  |
| `AB_BAD_PERSIST_CYCLES`           | `3`     |
| `FF3_DELTA_U`                     | `0.05`  |
| `FF3_MAX_AUTHORITY`               | `0.20`  |
| `FF3_NEARBAND_GAIN`               | `0.50`  |
| `FF3_MIN_HORIZON_CYCLES`          | `2`     |
| `FF3_RESPONSE_LOOKAHEAD_CYCLES`   | `2`     |
| `FF3_MAX_HORIZON_CYCLES`          | `8`     |
| `FF3_ACTION_SENSITIVITY_EPS_C`    | `1e-4`  |
| `FF3_SCORE_EPS_COST`              | `1e-4`  |
| `FF3_UNRELIABLE_MODEL_AUTHORITY_FACTOR` | `0.5` |

---

## 7. Nadzór „najpierw bezpieczeństwo” (Safety-First)

Macierz faktycznie używana w `const.py`:

| Stan pracy       | Model cieplny (`a/b`) | Wzmocnienia        |
| ---------------- | --------------------- | ------------------ |
| `WARMUP`         | `ADAPT_ON`            | `FREEZE`           |
| `EXCITED_STABLE` | `ADAPT_ON`            | `ADAPT_ON`         |
| `NEAR_BAND`      | `ADAPT_ON`            | `ADAPT_ON`         |
| `DEAD_BAND`      | `HARD_FREEZE`         | `HARD_FREEZE`      |
| `SATURATED`      | `ADAPT_ON`            | `FREEZE`           |
| `HOLD`           | `HARD_FREEZE`         | `SOFT_FREEZE_DOWN` |
| `PERTURBED`      | `HARD_FREEZE`         | `HARD_FREEZE`      |
| `DEGRADED`       | `HARD_FREEZE`         | `HARD_FREEZE`      |

Ten nadzór jest kluczowy, bo:

- nie pozwala uczyć się na danych uznanych za zanieczyszczone (niewiarygodne),
- nie zmienia wzmocnień w stanach pracy, które mało mówią o układzie,
- mimo to zostawia uczenie modelu cieplnego włączone w niektórych stanach z ograniczeniami, takich jak `SATURATED` (nasycenie).

---

## 8. Źródła

1. **Astrom K.J. i Hagglund T.**, prace o regulacji PID i stosowaniu modelu FOPDT (pierwszego rzędu z czasem martwym).
2. **Sundaresan K.R. i Krishnaswamy P.R.**, wyznaczanie opóźnienia i stałej czasowej.
3. Regresja **OLS** (metoda najmniejszych kwadratów) z testem t-Studenta do sprawdzania nachylenia.
