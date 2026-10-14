# SmartPI

- [SmartPI](#smartpi)
  - [Co robi SmartPI](#co-robi-smartpi)
  - [Zanim zaczniesz](#zanim-zaczniesz)
  - [Uruchomienie](#uruchomienie)
    - [Dodanie integracji SmartPI](#dodanie-integracji-smartpi)
    - [Wybór SmartPI w Versatile Thermostat](#wybór-smartpi-w-versatile-thermostat)
      - [Ustawienia dla jednego termostatu](#ustawienia-dla-jednego-termostatu)
  - [Zawory grzejnikowe i linearyzacja krzywej](#zawory-grzejnikowe-i-linearyzacja-krzywej)
    - [Dlaczego zawór bywa trudny w regulacji](#dlaczego-zawór-bywa-trudny-w-regulacji)
    - [Co robi linearyzacja](#co-robi-linearyzacja)
    - [Kiedy ją włączyć](#kiedy-ją-włączyć)
    - [Dobór wartości](#dobór-wartości)
  - [Fazy pracy](#fazy-pracy)
    - [Faza uczenia](#faza-uczenia)
      - [Jak grzeje faza startowa](#jak-grzeje-faza-startowa)
      - [Krok 1 — Pomiar czasów martwych](#krok-1--pomiar-czasów-martwych)
      - [Krok 2 — Uczenie strat ciepła (`b`)](#krok-2--uczenie-strat-ciepła-b)
      - [Krok 3 — Uczenie wzmocnienia grzania (`a`)](#krok-3--uczenie-wzmocnienia-grzania-a)
      - [Koniec fazy startowej](#koniec-fazy-startowej)
    - [Faza stabilna](#faza-stabilna)
    - [Automatyczna ponowna kalibracja](#automatyczna-ponowna-kalibracja)
  - [Zalecane ustawienia](#zalecane-ustawienia)
  - [Konfiguracja](#konfiguracja)
  - [Diagnostyka i karta Markdown](#diagnostyka-i-karta-markdown)
  - [Panel regulacji w Equinox](#panel-regulacji-w-equinox)
  - [Usługi](#usługi)
    - [`set_smartpi_learning`](#set_smartpi_learning)
    - [`reset_smartpi_learning`](#reset_smartpi_learning)
    - [`force_smartpi_calibration`](#force_smartpi_calibration)
    - [`reset_smartpi_integral`](#reset_smartpi_integral)

## Co robi SmartPI

SmartPI to alternatywa dla klasycznego algorytmu TPI (sterowania ze stałymi współczynnikami) w Versatile Thermostat.

Cel jest prosty. Zamiast działać zawsze tak samo, SmartPI uczy się, jak twój pokój naprawdę się nagrzewa i stygnie. Potem sam dopasowuje regulację. Każdy VTherm ze SmartPI pracuje w jednym trybie: grzanie (HEAT) albo chłodzenie (COOL), nigdy w obu naraz.

W praktyce SmartPI uczy się:

- jak mocno pracujące urządzenie grzeje lub chłodzi pokój,
- jak szybko pokój sam się nagrzewa lub wychładza pod wpływem warunków na zewnątrz,
- ile czasu pokój potrzebuje na reakcję po włączeniu lub wyłączeniu urządzenia.

Na tej podstawie SmartPI wylicza wysterowanie, czyli moc grzania lub chłodzenia w %. Zwykle jest ono
dokładniejsze niż w TPI ze stałymi współczynnikami:

- poprawia bieżący błąd temperatury (różnicę między temperaturą zadaną a zmierzoną),
- szacuje moc potrzebną do utrzymania temperatury zadanej,
- blisko nastawy (temperatury zadanej) stosuje zabezpieczenia. Zmniejszają one przeregulowanie (przekroczenie celu) i ograniczają zbędne wahania.

Nie musisz znać teorii regulatorów PI, żeby dobrze korzystać ze SmartPI. Ważne jest tylko jedno: SmartPI potrzebuje najpierw okresu uczenia. Dopiero potem reguluje w swoim normalnym trybie.

## Zanim zaczniesz

Żeby SmartPI uczył się poprawnie, sprawdź, czy termostat ma:

- wiarygodny pomiar temperatury w pomieszczeniu,
- źródło temperatury zewnętrznej,
- dość czasu, żeby zobaczyć zwykłe grzanie i stygnięcie.

W pierwszej fazie uczenia staraj się unikać:

- otwierania okien na długo,
- dużych zmian w harmonogramie,
- nietypowych zysków ciepła, np. mocnego słońca, palenia w kominku albo wielu gości,
- zmieniania wielu ustawień SmartPI, gdy uczenie jeszcze trwa.

Bardzo pomagają dwie praktyczne rady:

- przez pierwszy dzień lub dwa pozwól SmartPI pracować bez przerw,
- przy grzaniu (HEAT) ustaw nastawę wyraźnie wyższą od temperatury na zewnątrz; przy chłodzeniu (COOL) pomaga temperatura na zewnątrz wyraźnie wyższa od nastawy. Dzięki temu widać, jak pokój reaguje na pracę urządzenia i na samo otoczenie.

W praktyce uczenie może trwać od kilku godzin do 48 godzin w wolnych instalacjach albo takich o dużej bezwładności (które wolno się nagrzewają i wolno stygną).

## Uruchomienie

Zainstaluj integrację przez HACS (albo ręcznie), jak opisano w [README](../../README.pl.md). Potem uruchom ponownie Home Assistant.

Po ponownym uruchomieniu potrzebne są dwa kroki: dodanie integracji SmartPI w Home Assistant, a potem włączenie jej w Versatile Thermostat.

### Dodanie integracji SmartPI

Wejdź w **Ustawienia → Integracje → Dodaj integrację** (Settings → Integrations → Add integration) i wyszukaj *SmartPI*.

Przy pierwszej instalacji SmartPI sam tworzy wpis „SmartPI defaults” (wartości domyślne). Są w nim ustawienia wspólne dla termostatów (oznaczone „wspólne” w [tabeli konfiguracji](#konfiguracja)) i sygnały kotła. Możesz je później zmienić w **Ustawienia → Integracje → SmartPI → Konfiguruj** (Settings → Integrations → SmartPI → Configure).

### Wybór SmartPI w Versatile Thermostat

Po dodaniu integracji SmartPI otwórz konfigurację urządzenia Versatile Thermostat, którym ma sterować SmartPI. W kroku **Urządzenia** (w angielskim interfejsie **Underlyings**) znajdź wybór algorytmu i wybierz **SmartPI**.

![VT underlyings — SmartPI algorithm selection](../../assets/screens/config_algo.png)

Powtórz ten krok dla każdego termostatu, który ma pracować ze SmartPI.

#### Ustawienia dla jednego termostatu

Własny wpis termostatu nie jest obowiązkowy. Termostat na SmartPI bez własnego wpisu bierze ustawienia wspólne z wartości domyślnych. Pracuje bez ustawień, które należą do jednego termostatu: bez rodzaju ogrzewania, bez minimalnych czasów załączenia i wyłączenia, bez krzywej zaworu i bez restartu cyklu PWM, tak jak w wersji 0.4.0.

Aby dać termostatowi własne ustawienia, otwórz konfigurację integracji SmartPI i dodaj nowy wpis termostatu. Wybierz termostat z listy, a potem dostosuj parametry według potrzeb.

![SmartPI — per-thermostat configuration](../../assets/screens/config_smartpi.png)

Wpis termostatu zawiera ustawienia oznaczone „tylko ten termostat” w [tabeli konfiguracji](#konfiguracja) i przełącznik **Użyj wartości domyślnych SmartPI** (Use the SmartPI defaults). Gdy jest włączony, ustawienia wspólne pochodzą z wartości domyślnych, a ich późniejsza zmiana dociera do tego termostatu. Gdy jest wyłączony, wpis ma własne wartości ustawień wspólnych; na początku to kopia wartości domyślnych. Ustawienia, którego wpis nie ma, SmartPI nigdy nie bierze z wartości zapisanych w kodzie: wspólne pochodzi z wartości domyślnych, a „tylko ten termostat” nie jest używane. Wpis utworzony, zanim pojawił się przełącznik, zachowuje własne wartości (przełącznik wyłączony).

Opcje wpisu globalnego „SmartPI defaults” otwierają menu z pozycjami: ustawienia regulatora (Regulator settings) oraz sygnały kotła (Boiler signals: encje, które mówią SmartPI, co robi kocioł, wspólne dla każdego termostatu, który odczytuje encje). Opcje wpisu termostatu otwierają menu z pozycjami: ustawienia regulatora (Regulator settings); strefa z dwoma źródłami ciepła (Zone with two heat sources); zależności stref (Zone dependencies); dla wodnego rodzaju ogrzewania **woda i kocioł** (Water and boiler: skąd pochodzą dane o wodzie i wezwanie kotła); a dla zaworu z linearyzacją także krzywa zaworu (Valve curve). Strefa z dwoma źródłami ciepła to pomieszczenie grzane przez dwa termostaty ze SmartPI, na przykład grzejnik i pętlę ogrzewania podłogowego. Ustawia się ją z poziomu termostatu nadrzędnego (Master thermostat). To Versatile Thermostat, którego urządzeniami są te dwa termostaty. Zależności stref to strefy sąsiednie, które wymieniają ciepło z tą strefą, przez drzwi albo stale. Termostat, który pracuje jako dodatkowe źródło strefy, otwiera zamiast tego inny formularz (przy wodnym rodzaju ogrzewania — menu z tym formularzem i jego danymi o wodzie). Ten formularz pokazuje, gdzie ustawiono jego strefę, oraz ustawienia, które nadal go dotyczą: minimalne czasy załączenia i wyłączenia, rodzaj ogrzewania i krzywą zaworu. Jak SmartPI się tego wszystkiego uczy i jak z tego korzysta, opisuje dokument [Zależności stref i drugie źródło ciepła](zone_dependencies.md).

Ustawienia termostatu zaczynają się od **rodzaju ogrzewania** (Heating type): nieustawiony (Not set, domyślnie), grzejnik wodny (Water radiator), wodne ogrzewanie podłogowe (Water underfloor heating), elektryczna mata grzewcza (Electric heating mat) albo szybki grzejnik elektryczny (Fast electric heater). Nieustawiony oznacza brak danych o wodzie i brak wezwania kotła, tak jak w wersji 0.4.0. Rodzaj wodny korzysta z danych o wodzie i z wezwania kotła ze źródeł wybranych w pozycji **Woda i kocioł** (Water and boiler). Dane o wodzie to temperatura wody zasilającej (temperatura wody wysyłanej do obiegu termostatu) i sygnały kotła; pochodzą z encji Home Assistant (z dowolnej integracji albo z czujnika na rurze zasilającej) albo od dostawcy (integracji, która implementuje kontrakt SmartPI). Wezwanie kotła mówi, czy kocioł dostał polecenie grzania; pochodzi z kotła centralnego Versatile Thermostat (domyślnie tam, gdzie obsługuje on termostat), z encji albo od dostawcy. Bez danych o wodzie wysterowanie jest takie samo jak wcześniej. Gdy strefa woła o ciepło, a ono nie przychodzi, SmartPI w uczeniu nie traktuje jej wysterowania jako ciepła. Dla elektrycznego emitera (urządzenia, które oddaje ciepło do pokoju) wybierz rodzaj elektryczny: nie odczytuje on ani danych o wodzie, ani wezwania kotła. Zobacz [Wejścia wody i kotła](water_input.md), zwłaszcza [sekcję 4](water_input.md#4-kocioł-który-nie-jest-wezwany-do-grzania).

Strefę sąsiednią najlepiej ustawić we wpisach obu stref, żeby każda widziała drugą. Gdy u sąsiada jest otwarte okno, SmartPI stosuje wybraną reakcję w swojej własnej regulacji. Versatile Thermostat strefy dalej pokazuje własny stan okna, tryb i preset, a diagnostyka pokazuje reakcję, która właśnie obowiązuje.

## Zawory grzejnikowe i linearyzacja krzywej

Ta część dotyczy tylko termostatów, które bezpośrednio sterują zaworem, np. głowicą termostatyczną grzejnika (TRV).

### Dlaczego zawór bywa trudny w regulacji

Zawór grzejnika nie zawsze zachowuje się jak grzejnik elektryczny.

W grzejniku elektrycznym `40%` daje zwykle mniej więcej dwa razy więcej ciepła niż `20%`. W wielu zaworach grzejnikowych reakcja jest mniej równa. Pierwsze kilka procent może nie dawać prawie nic, a potem małe dodatkowe otwarcie przepuszcza dużo gorącej wody.

W niektórych instalacjach zawór otwarty tylko na około `20%` przepuszcza już większość przepływu. Dalsze otwieranie zmienia wtedy przepływ dużo mniej.

W praktyce może to wyglądać tak:

| Wysterowanie wysłane do zaworu | Możliwy efekt na grzejniku                                |
| ------------------------------ | --------------------------------------------------------- |
| od `0%` do kilku procent       | brak widocznego ciepła                                    |
| małe otwarcie                  | grzejnik dopiero zaczyna grzać                            |
| około `15%` do `25%`           | jest już większość przepływu                              |
| powyżej                        | mniejsza zmiana, czasem głównie więcej szumu wody w rurach |

Dokładne wartości zależą od zaworu, korpusu zaworu, zrównoważenia grzejników (ustawienia przepływów) i instalacji wodnej. Nie próbuj znaleźć idealnej wartości za pierwszym razem.

### Co robi linearyzacja

Linearyzacja krzywej zaworu (wyrównanie jego nierównej reakcji) przelicza zapotrzebowanie SmartPI na położenie zaworu.

SmartPI nadal pracuje na prostym zapotrzebowaniu na grzanie:

- `0%` oznacza brak grzania,
- `50%` oznacza średnie zapotrzebowanie,
- `100%` oznacza największe zapotrzebowanie.

Linearyzacja zamienia potem to zapotrzebowanie na otwarcie zaworu lepiej dopasowane do tego, jak grzejnik naprawdę się zachowuje. Chodzi o to, żeby dokładniej wykorzystać ten mały zakres, w którym zawór naprawdę zmienia przepływ, zamiast wysyłać surowe zapotrzebowanie wprost do zaworu.

Uproszczony przykład:

| Zapotrzebowanie SmartPI | Otwarcie wysłane do zaworu                              |
| ----------------------- | ------------------------------------------------------- |
| `0%`                    | `0%`                                                    |
| małe zapotrzebowanie    | około minimalnego użytecznego otwarcia                  |
| `80%`                   | około otwarcia w punkcie załamania krzywej (knee)       |
| `100%`                  | największe dozwolone otwarcie                           |

Ta korekta nie zastępuje uczenia SmartPI. Pomaga tylko SmartPI lepiej „dogadać się” z zaworem, który reaguje nierówno (nieliniowo).

### Kiedy ją włączyć

Włącz tę opcję, jeśli:

- twój termostat VTherm jest termostatem zaworowym (steruje zaworem),
- SmartPI bezpośrednio ustawia otwarcie zaworu,
- grzejnik przy kilku procentach otwarcia szybko przechodzi od zimnego do bardzo gorącego,
- małe zmiany wysterowania wywołują zbyt silne albo zbyt nieregularne reakcje.

Opcja jest dostępna tylko dla termostatów, które udostępniają sterowanie zaworem.

### Dobór wartości

Wartości domyślne są rozsądnym punktem wyjścia. Zmieniaj je tylko wtedy, gdy już wiesz z obserwacji, jak zachowuje się twój grzejnik.

| Parametr                                                       | Co oznacza                                                                                  | Wartość na start |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------- |
| **Minimalne otwarcie zaworu** (Minimum valve opening)          | Pierwsze otwarcie, przy którym grzejnik naprawdę zaczyna grzać.                             | `7%`             |
| **Zapotrzebowanie w punkcie załamania** (Demand at knee)       | Zapotrzebowanie SmartPI, przy którym uznaje się, że zawór wchodzi w zakres dużego przepływu. | `80%`            |
| **Otwarcie zaworu w punkcie załamania** (Valve opening at knee) | Fizyczne położenie zaworu przy tym zapotrzebowaniu.                                        | `15%`            |
| **Maksymalne otwarcie zaworu** (Maximum valve opening)         | Największe dozwolone otwarcie.                                                              | `100%`           |

Najprościej znaleźć minimalne otwarcie, obserwując grzejnik:

1. Poczekaj, aż grzejnik ostygnie.
2. Włącz zapotrzebowanie na grzanie.
3. Powoli zwiększaj otwarcie zaworu, małymi krokami.
4. Między próbami odczekaj co najmniej minutę.
5. Zapisz pierwszą wartość, przy której rura albo grzejnik naprawdę zaczyna się nagrzewać.

Jeśli nie chcesz dokładnie testować, zostaw wartości domyślne. Jeśli twój zawór reaguje bardzo szybko, rozsądną próbą może być otwarcie w punkcie załamania około `20%` do `25%`. Jeśli grzejnik przy pełnym otwarciu zaczyna szumieć, zmniejsz maksymalne otwarcie.

Po zmianie wartości pozwól SmartPI popracować przez kilka cykli, zanim ocenisz wynik. Jeden cykl grzania nie zawsze wystarcza do wniosków.

## Fazy pracy

### Faza uczenia

SmartPI zaczyna od fazy startowej (bootstrap). W tej fazie grzeje w prosty sposób, żeby zobaczyć, jak zachowuje się pokój. Potem wylicza z tego parametry fizyczne, których potrzebuje do dobrej regulacji.

#### Jak grzeje faza startowa

W fazie startowej SmartPI nie próbuje dokładnie trzymać temperatury. Zamiast tego na przemian grzeje pełną mocą i całkiem przestaje grzać, żeby zobaczyć wyraźne reakcje cieplne pokoju:

- grzanie włącza się, gdy temperatura spadnie poniżej `setpoint - 0.3°C` (nastawa minus 0,3°C),
- grzanie wyłącza się, gdy temperatura wzrośnie powyżej `setpoint + 0.5°C` (nastawa plus 0,5°C).

Daje to widoczne wahania temperatury. Na tym etapie są one oczekiwane i zamierzone.

#### Krok 1 — Pomiar czasów martwych

Najpierw SmartPI musi się nauczyć, **ile czasu pokój potrzebuje na reakcję** po włączeniu lub wyłączeniu grzania. Te opóźnienia to *czasy martwe*:

- **czas martwy grzania**: czas od chwili, gdy SmartPI wyśle polecenie grzania, do chwili, gdy temperatura w pokoju zacznie rosnąć,
- **czas martwy stygnięcia**: czas od chwili wyłączenia grzania do chwili, gdy temperatura zacznie spadać.

Czasy martwe zależą od rodzaju emitera (grzejnik, pętla podłogówki), wielkości pokoju i miejsca czujnika. Trzeba je zmierzyć, zanim SmartPI będzie mógł poprawnie odczytywać obserwacje grzania i stygnięcia.

#### Krok 2 — Uczenie strat ciepła (`b`)

Gdy czasy martwe zostaną uznane za wiarygodne, SmartPI zaczyna zbierać **obserwacje stygnięcia**: mierzy, jak szybko pokój traci ciepło, gdy grzanie jest wyłączone.

Z tych obserwacji wylicza `b`, czyli współczynnik strat ciepła. Mówi on, jak szybko pokój stygnie w zależności od różnicy między temperaturą wewnątrz i na zewnątrz.

SmartPI potrzebuje co najmniej 8 poprawnych obserwacji stygnięcia, zanim uzna `b` za przydatne.

#### Krok 3 — Uczenie wzmocnienia grzania (`a`)

Gdy `b` ma dość obserwacji, żeby uznać je za wiarygodne, SmartPI zaczyna zbierać **obserwacje grzania**: mierzy, jak szybko pokój się nagrzewa przy danym wysterowaniu.

Z tych obserwacji wylicza `a`, czyli wzmocnienie grzania. Mówi ono, jak skutecznie twoje ogrzewanie podnosi temperaturę w pokoju.

Do `a` SmartPI potrzebuje co najmniej 6 poprawnych obserwacji grzania.

#### Koniec fazy startowej

SmartPI kończy fazę startową i przechodzi do normalnej regulacji, gdy zarówno `a`, jak i `b` mają dość obserwacji, żeby opublikować pierwszy model cieplny (opis tego, jak pokój się grzeje i stygnie).

Potem, w czasie normalnej regulacji, bufory obserwacji (listy zapamiętanych pomiarów) dalej się zapełniają, aż do 31 próbek. Dzięki temu model nadal się utrwala po fazie startowej. Pełne zaufanie do modelu ma surowsze wymagania niż samo wyjście z fazy startowej. Dopóki nie ma dość obserwacji do pełnego zaufania, SmartPI reguluje z opublikowanym modelem, ale niektóre wewnętrzne korekty pozostają zamrożone.

Czego się spodziewać w fazie startowej:

- wahania temperatury są normalne i oczekiwane,
- regulacja jest na tym etapie celowo prosta,
- diagnostyka jest szczególnie przydatna do śledzenia postępu,
- `model.confidence` pokazuje `ab_bootstrap`, dopóki nie ma pierwszego modelu cieplnego,
- tempo zależy od jakości rzeczywistych obserwacji, a nie tylko od upływu czasu.

Przy zależnościach stref uczenie robi przerwę, dopóki drzwi do sąsiada są otwarte i przez 10 minut po ich zamknięciu, oraz gdy u sąsiada jest otwarte okno. W strefie z dwoma źródłami ciepła SmartPI zaczyna natomiast od wspólnego dopasowania strefy (jednego modelu dopasowanego do obu źródeł razem); zobacz [Zależności stref, §5.5](zone_dependencies.md#55-uczenie).

### Faza stabilna

Gdy model cieplny staje się wiarygodny, SmartPI przechodzi do normalnej regulacji.

Od tej chwili SmartPI:

- sam wylicza wzmocnienia PI (współczynniki, które mówią, jak mocno regulator reaguje) z nauczonego modelu,
- dodaje część wyprzedzającą (feed-forward, FF), która podtrzymuje temperaturę na podstawie warunków w pokoju i na zewnątrz,
- blisko nastawy dopasowuje działanie: używa strefy nieczułości (deadband, małego zakresu tolerancji wokół nastawy) i dodatkowych zabezpieczeń.

Blisko temperatury zadanej SmartPI stara się unikać ciągłych drobnych poprawek. Efektem powinna być spokojniejsza regulacja, z mniejszą liczbą zbędnych korekt niż w TPI ze stałymi współczynnikami.

Gdy nastawa grzania rośnie, filtr nastawy używa też nauczonego modelu, żeby pokierować ostatnim odcinkiem dojścia do celu. Część proporcjonalna (reagująca na bieżący błąd) śledzi przefiltrowaną (wygładzoną) wartość odniesienia. Część całkująca (całka, która zbiera dawne błędy) nadal widzi surową nastawę. Blisko celu SmartPI może ograniczyć wewnętrzne zapotrzebowanie na grzanie, gdy model przewiduje, że ciepło już dostarczone wystarczy do osiągnięcia nastawy. To „lądowanie” (landing, łagodne dojście do celu) pomaga pokojowi zwolnić przed celem. Bez niego ogrzewanie grzałoby dalej tylko dlatego, że część wyprzedzająca albo zamrożony stan PI są nadal dodatnie.

Z `FF3`, które jest domyślnie włączone, SmartPI może też blisko nastawy dodać małą korektę przewidującą, gdy rozpozna wiarygodną sytuację zewnętrznego zakłócenia.

### Automatyczna ponowna kalibracja

SmartPI cały czas pilnuje jakości swojego modelu.

Jeśli jakość uczenia przestaje się wystarczająco poprawiać, SmartPI może sam uruchomić ponowną kalibrację, żeby odświeżyć model i czasy martwe.

Warto wiedzieć:

- gdy model staje się wiarygodny, SmartPI zapisuje migawkę odniesienia (zapis stanu modelu z tej chwili),
- bieżąca (krocząca) migawka jest z czasem odświeżana,
- jeśli przez długi czas nie da się nauczyć czasu martwego stygnięcia, SmartPI może dalej pracować z częściową migawką,
- po kilku nieudanych próbach ponownej kalibracji SmartPI nadal działa i w diagnostyce zgłasza model o obniżonej jakości (degraded).

## Zalecane ustawienia

Ustawienia domyślne pasują do większości instalacji.

Zacznij prosto:

- zostaw domyślne progi histerezy (progi włączania i wyłączania grzania w fazie startowej),
- zostaw `FF3` włączone, chyba że masz konkretny powód, żeby je wyłączyć,
- zostaw filtr nastawy włączony, tak jak domyślnie,
- jeśli temperatura za bardzo waha się wokół celu, najpierw zmień strefę nieczułości.

W pierwszym okresie uczenia nie dostrajaj kilku parametrów naraz. Lepiej pozwolić SmartPI przejść czysty pierwszy cykl uczenia, a potem poprawić tylko to, co naprawdę trzeba.

## Konfiguracja

Kolumna „Gdzie się ustawia” mówi, do czego należy każde ustawienie. **Wspólne**: we wpisie „SmartPI defaults”; wpis termostatu ma własną wartość tylko wtedy, gdy jego przełącznik **Użyj wartości domyślnych SmartPI** (Use the SmartPI defaults) jest wyłączony. **Tylko ten termostat**: we własnym wpisie termostatu; gdy nie jest ustawione, nie jest używane.

| Parametr | Rola | Gdzie się ustawia | Wartość domyślna |
| -------- | ---- | ----------------- | ---------------- |
| **Rodzaj ogrzewania** (Heating type) | Nieustawiony, grzejnik wodny, wodne ogrzewanie podłogowe, elektryczna mata grzewcza albo szybki grzejnik elektryczny. Rodzaj wodny korzysta z danych o wodzie i z wezwania kotła ze źródeł wybranych w pozycji Woda i kocioł (Water and boiler) ([Wejścia wody i kotła, §2](water_input.md#2-konfiguracja)). | tylko ten termostat | nieustawiony |
| **Minimalny czas załączenia** (Minimal activation delay) | Najkrótszy czas, przez jaki grzanie pozostaje włączone po załączeniu. | tylko ten termostat | `0 s` |
| **Minimalny czas wyłączenia** (Minimal deactivation delay) | Najkrótszy czas, przez jaki grzanie pozostaje wyłączone po wyłączeniu. | tylko ten termostat | `0 s` |
| **Zezwól na restart cyklu PWM przy zmianie pasma** (Allow PWM cycle restart on band transitions) | Pozwala SmartPI zacząć bieżący cykl PWM (powtarzany cykl, w którym urządzenie jest włączone przez część czasu) od nowa przy wejściu do strefy nieczułości lub pasma przy nastawie (near band) albo przy wyjściu z nich. | tylko ten termostat | `disabled` (wyłączone) |
| **Włącz linearyzację krzywej zaworu** (Enable valve curve linearization) | Dopasowuje zapotrzebowanie SmartPI do zaworów grzejnikowych, które reagują nieliniowo (widoczne dla zaworu). | tylko ten termostat | `disabled` (wyłączone) |
| **Minimalne otwarcie zaworu** (Minimum valve opening) | Pierwsze użyteczne otwarcie, gdy linearyzacja jest włączona. | tylko ten termostat | `7%` |
| **Zapotrzebowanie w punkcie załamania** (Demand at knee) | Zapotrzebowanie SmartPI, przy którym zmienia się nachylenie krzywej zaworu. | tylko ten termostat | `80%` |
| **Otwarcie zaworu w punkcie załamania** (Valve opening at knee) | Fizyczne otwarcie zaworu w miejscu zmiany nachylenia. | tylko ten termostat | `15%` |
| **Maksymalne otwarcie zaworu** (Maximum valve opening) | Największe dozwolone otwarcie, gdy linearyzacja jest włączona. | tylko ten termostat | `100%` |
| **Użyj wartości domyślnych SmartPI** (Use the SmartPI defaults) | Włączone: ustawienia wspólne pochodzą z wpisu „SmartPI defaults”. Wyłączone: ten wpis ma własne wartości. | tylko ten termostat | `enabled` (włączone; wpis sprzed przełącznika: wyłączone) |
| **Strefa nieczułości** (Deadband) | Zakres tolerancji wokół nastawy. | wspólne | `0.05°C` |
| **Histereza włączenia** (Hysteresis on) | Próg ponownego włączenia grzania podczas uczenia w fazie startowej. | wspólne | `0.3°C` |
| **Histereza wyłączenia** (Hysteresis off) | Próg wyłączenia grzania podczas uczenia w fazie startowej. | wspólne | `0.5°C` |
| **Współczynnik tau zwolnienia** (Release tau factor) | Ustala opóźnienie zwolnienia całki w stosunku do nauczonej stałej czasowej (miary tego, jak szybko pokój reaguje). | wspólne | `0.5` |
| **Użyj filtra nastawy** (Use setpoint filter) | Włącza kształtowanie nastawy dla części proporcjonalnej i kontrolę „lądowania” przy grzaniu blisko celu. | wspólne | `enabled` (włączone) |
| **Użyj FF3** (Use FF3) | Włącza korektę przewidującą na krótki czas naprzód, blisko nastawy, gdy temperatura wraca do normy po zakłóceniu. | wspólne | `enabled` (włączone) |
| **Zezwól na osłabione P przy krawędzi strefy nieczułości** (Allow damped P near deadband edge) | W strefie nieczułości zostawia słabsze działanie części proporcjonalnej blisko jej krawędzi. Gdy wyłączone, część proporcjonalna nie działa w całej strefie nieczułości. | wspólne | `disabled` (wyłączone) |
| **Zapisuj pełną diagnostykę (debug)** (Record full diagnostics (debug)) | Zapisuje pełne bieżące dane diagnostyczne do późniejszej analizy historii. | wspólne | `disabled` (wyłączone) |

Minimalne czasy załączenia i wyłączenia należą do SmartPI. Versatile Thermostat ma czasy o tych samych nazwach w swoich ustawieniach TPI; dla termostatu na SmartPI nie są one używane.

Pozycja **Woda i kocioł** (Water and boiler) w opcjach termostatu oraz pozycja **Sygnały kotła** (Boiler signals) we wpisie globalnym „SmartPI defaults” ([Wejścia wody i kotła, §2](water_input.md#2-konfiguracja)):

| Pole | Rola | Wartość domyślna |
| ---- | ---- | ---------------- |
| **Dane o wodzie** (Water data) | Skąd pochodzą dane o wodzie: brak (None), encje Home Assistant (Home Assistant entities) albo dostawca, pokazany na liście pod swoją nazwą. | brak (None) |
| **Wezwanie kotła** (Boiler's call) | Skąd pochodzi wezwanie kotła: brak (None), kocioł centralny Versatile Thermostat (Versatile Thermostat's central boiler, do wyboru, gdy obsługuje termostat), encje Home Assistant albo dostawca. | kocioł centralny Versatile Thermostat tam, gdzie obsługuje termostat, w przeciwnym razie brak |
| **Temperatura wody zasilającej** (Supply water temperature) | Czujnik temperatury wody, która dociera do obiegu tego termostatu (dane o wodzie z encji). | — |
| **Sygnały kotła** (Boiler signals) | Jak we wpisie „SmartPI defaults” (As in SmartPI defaults) albo własne tego termostatu (This thermostat's own). | Jak we wpisie „SmartPI defaults” |
| **Kocioł może grzać** (Boiler can heat) | Ma stan on, gdy kocioł może teraz grzać obiegi. | — |
| **Ciepła woda ma priorytet** (Hot water has priority) | Ma stan on, gdy kocioł grzeje ciepłą wodę użytkową zamiast obiegów. | — |
| **Awaria kotła** (Boiler fault) | Ma stan on, gdy kocioł zgłasza awarię. | — |
| **Kocioł jest wezwany do grzania** (Boiler is called for heat) | Ma stan on, gdy kocioł dostał polecenie grzania. | — |
| **Dlaczego kocioł nie jest wezwany** (Why the boiler is not called) | Czujnik z tekstem: `below_threshold`, `no_demand`, `activation_delay` albo `boiler_fault`. | — |

Zmiana danych o wodzie albo czujnika zasilania wywołuje pytanie, czy zresetować nauczony model (zalecane), bo nauczona moc ma wtedy inne jednostki.

### Restart cyklu PWM przy zmianie pasma

Gdy ta opcja jest wyłączona, wejście do strefy nieczułości lub pasma przy nastawie albo wyjście z nich zmienia następne wysterowanie SmartPI, ale nie przerywa trwającego cyklu PWM. To zalecane działanie przy krótkich cyklach, bo zbyt częsty restart cykli może powodować zbędne przełączanie.

Włącz ją tylko w bardzo wolnych instalacjach z długimi cyklami PWM. Tam czekanie do następnej granicy cyklu może za bardzo opóźnić rzeczywistą reakcję po wejściu do strefy nieczułości lub pasma przy nastawie albo po wyjściu z nich.

## Diagnostyka i karta Markdown

Opis schematu diagnostyki i profili Recordera (części Home Assistant, która zapisuje historię) znajdziesz w dokumencie [Diagnostyka](./diagnostics.md).

Czujnik diagnostyczny SmartPI publikuje atrybuty w stałym układzie z numerem wersji. Pulpity i
karty Markdown czytają pełną diagnostykę z `attributes.live`. Wykresy historii czytają stały,
uzgodniony zestaw ośmiu serii danych z `attributes.history`.

To główne miejsce, w którym sprawdzisz:

- czy SmartPI nadal się uczy, czy już pracuje stabilnie,
- czy model jest uznany za wiarygodny,
- czy zgłoszono ponowną kalibrację albo tryb obniżonej jakości (degraded).

W czasie uczenia najbardziej przydatny jest bieżący blok `live.learning`.

Ważne pola:

- `stage`: ogólny stan, np. `bootstrap`, `learning`, `monitoring` albo `degraded`,
- `bootstrap_progress_percent`: postęp fazy startowej,
- `bootstrap_status`: bieżący krok fazy startowej,
- `accepted_updates_a`: przyjęte aktualizacje modelu grzania,
- `accepted_updates_b`: przyjęte aktualizacje modelu strat ciepła,
- `history_target`: docelowa liczba próbek w buforach A/B,
- `last_reason`: powód ostatniego przyjęcia lub odrzucenia w uczeniu.

Inne przydatne bloki w `live`:

- `control`: bieżąca faza i tryb regulacji,
- `power`: wysterowanie w bieżącym i następnym cyklu,
- `temperature`: zmierzona temperatura, błąd, stan całki,
- `model`: nauczone `a` i `b`, zaufanie do modelu i czasy martwe,
- `feedforward`: stan części wyprzedzającej i FF3,
- `setpoint`: przefiltrowana nastawa i informacje o „lądowaniu”,
- `autocalib`: stan automatycznego nadzoru,
- `calibration`: stan wymuszonej kalibracji,
- `dependencies`: zależności stref i strefa z dwoma źródłami ciepła, gdy są ustawione ([Zależności stref, §9](zone_dependencies.md#9-diagnostyka)),
- `water`: dane o wodzie, dla wodnego rodzaju ogrzewania, i to, skąd pochodzi każdy z ich sygnałów ([Wejścia wody i kotła, §8](water_input.md#8-diagnostyka)),
- `boiler`: czy kocioł jest wezwany do grzania, z jakiego źródła i jak uczenie traktuje strefę, która woła o ciepło, a go nie dostaje ([Wejścia wody i kotła, §4](water_input.md#4-kocioł-który-nie-jest-wezwany-do-grzania)).

W trybie normalnym blok `setpoint` może pokazywać:

- `filtered_setpoint`: wartość odniesienia, którą śledzi część proporcjonalna,
- `trajectory_active`: czy działa trajektoria nastawy,
- `trajectory_source`: dlaczego trajektoria działa,
- `landing_active`: czy działa kontrola „lądowania” przy grzaniu,
- `landing_reason`: powód stanu „lądowania”,
- `landing_u_cap`: wewnętrzny limit zapotrzebowania na grzanie w czasie „lądowania”,
- `landing_coast_required`: czy SmartPI pozwala pokojowi dojść do celu bez grzania, bo model przewiduje, że zgromadzone ciepło wystarczy.

Zaawansowane wartości, z których korzysta dołączona karta, są zawsze dostępne w
`live.analysis`. Tryb debugowania nie dodaje ani nie usuwa publikowanych pól. Pozwala
tylko, żeby Recorder w Home Assistant zachowywał cały blok `live`. W trybie normalnym
Recorder zachowuje tylko `history`, a wszystkie bieżące wartości nadal są widoczne.

Jest też karta Markdown dla Home Assistant, która pokazuje diagnostykę SmartPI na pulpicie w prostszej formie.

https://github.com/KipK/vtherm_smartpi/tree/master/cards

## Panel regulacji w Equinox

SmartPI publikuje też w encji termostatu podsumowanie `specific_states.smart_pi` i odnośnik do encji `specific_states.regulation_diagnostics`. Gdy SmartPI korzysta z danych o wodzie, zapisuje też `specific_states.water_factor`. To współczynnik wody, którego używa: ile ciepła daje emiter przy obecnej wodzie w porównaniu z wodą odniesienia. To wyjście SmartPI dla każdej integracji, która je odczytuje ([Wejścia wody i kotła, §3.4](water_input.md#34-użycie-w-smartpi)). [Karta Lovelace Equinox](https://github.com/KipK/equinox) używa tych atrybutów, żeby w menu karty termostatu (climate) pokazać osobny panel **Regulation** (regulacja).

W Equinox możesz śledzić postęp uczenia SmartPI, zaufanie do modelu, rozbicie mocy na składniki, „lądowanie” przy nastawie, wykresy historii i dostępne działania serwisowe. Nie musisz przy tym kopiować karty diagnostycznej Markdown.

![Equinox SmartPI Regulation dashboard overview](https://github.com/KipK/equinox/raw/master/assets/screens/regul-overview.png)

![Equinox SmartPI Regulation learning section](https://github.com/KipK/equinox/raw/master/assets/screens/regul-learning.png)

Zainstaluj Equinox z jego repozytorium albo przez HACS. Ustaw go dla tej samej encji climate Versatile Thermostat i zostaw `additional_dashboards` na domyślnej wartości `auto`. Equinox sam wykryje SmartPI, gdy opublikowana diagnostyka jest dostępna.

## Usługi

SmartPI udostępnia cztery usługi (akcje Home Assistant) w domenie `vtherm_smartpi`.

### `set_smartpi_learning`

Ta usługa wstrzymuje lub wznawia uczenie cieplne jednego termostatu SmartPI. W wymaganym polu `learning_enabled` ustaw `false`, żeby wstrzymać uczenie, albo `true`, żeby je wznowić.

Gdy uczenie jest wstrzymane, regulacja działa dalej z już nauczonym modelem, ale SmartPI nie zbiera obserwacji A/B ani obserwacji czasów martwych. Wstrzymanie nie kasuje nauczonych parametrów ani historii czasów martwych. Po zmianie tego ustawienia SmartPI zaczyna obserwacje od nowa (nowe odcinki pomiarów). Ustawienie zostaje zachowane po ponownym uruchomieniu Home Assistant.

Bieżące ustawienie widać jako `specific_states.smartpi_learning_enabled` w encji climate oraz jako `live.learning.enabled` w atrybutach czujnika diagnostycznego.

### `reset_smartpi_learning`

Użyj tej usługi, gdy pokój zaczął się wyraźnie inaczej nagrzewać i stygnąć, np. po dociepleniu albo po wymianie emiterów (grzejników, pętli podłogówki).

Ta usługa kasuje to, czego nauczył się SmartPI, i wymusza powrót do fazy startowej.

Kasuje też to, czego nauczyły się zależności stref (wymianę ciepła z sąsiadami, dopasowanie strefy z dwoma źródłami ciepła). Zostawia średnie z wejścia wody. Reset dostępny w formularzu strefy, w formularzu Woda i kocioł (Water and boiler) oraz w zgłoszeniu naprawy wejścia wody w Home Assistant (w sekcji „Naprawy”) to ten sam reset; zobacz [Zależności stref, §2.2](zone_dependencies.md#22-reset-nauczonego-modelu).

### `force_smartpi_calibration`

Użyj tej usługi, gdy chcesz, żeby SmartPI przeprowadził cykl kalibracji bez czekania na automatyczne uruchomienie.

Ta usługa przydaje się, gdy:

- podawane czasy martwe wydają się niespójne,
- regulacja działa gorzej niż wcześniej,
- chcesz odświeżyć uczenie po dużej zmianie rzeczywistych warunków.

Jeśli SmartPI jest jeszcze w fazie startowej, żądanie jest pomijane.

### `reset_smartpi_integral`

Użyj tej usługi, gdy po wyjątkowym zdarzeniu część całkująca zachowała nieodpowiednią wartość.

Typowe przykłady:

- długa przerwa w ogrzewaniu,
- okno zostawione otwarte na długo,
- każda sytuacja, w której chcesz zachować nauczony model, ale zacząć od neutralnego stanu całki.
